"""RAG Knowledge Assistant — FastAPI Backend with Dynamic Model Routing & Tool Use.

Includes ChromaDB-backed retrieval-augmented generation (RAG) that injects
relevant knowledge chunks into the prompt before forwarding to Ollama.
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import BaseModel

from prompts import build_system_prompt
from tools import TOOL_FUNCTIONS, TOOL_MAP

app = FastAPI(
    title="RAG Knowledge Assistant API",
    description="Multi-model RAG backend with dynamic routing and capability guards.",
    version="1.0.0",
)

# How long Ollama keeps the model loaded in GPU after the last request.
# Prevents cold-start reload delays (~4 min on GTX 1060 for a 4 GB model).
# Set to "0" to unload immediately, or "-1" to keep forever.
OLLAMA_KEEP_ALIVE = "30m"

# ---------------------------------------------------------------------------
# RAG Vector Store (global initialisation)
# ---------------------------------------------------------------------------
# Loaded once at startup so all requests share the same connection.
# The embedding model is forced to CPU to avoid errors on GPU-less hosts.

_CHROMA_DIR = Path(__file__).parent / "chroma_db"
_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

_embeddings = HuggingFaceEmbeddings(
    model_name=_EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

_vectorstore = Chroma(
    persist_directory=str(_CHROMA_DIR),
    embedding_function=_embeddings,
    collection_name="rag_knowledge",
)

# Number of most-relevant chunks to retrieve per query.
RAG_TOP_K = 2

# ---------------------------------------------------------------------------
# Model Registry
# ---------------------------------------------------------------------------
# Central source of truth for model capabilities and reasoning strategies.
#
# Fields:
#   vision             — Whether the model supports multimodal (image) input.
#   tools              — Whether the model supports function calling (tool use).
#   reasoning_strategy — How to toggle chain-of-thought reasoning:
#       "parameter"    : Use the `think` API parameter to control <think> blocks.
#       "model_switch" : Swap to a dedicated reasoning variant (see `switch_to`).
#       "none"         : Model has no reasoning toggle; always runs in default mode.
#   switch_to          — Target model name when strategy is "model_switch".
# ---------------------------------------------------------------------------
MODEL_REGISTRY: dict[str, dict] = {
    "qwen3.5:2b": {
        "vision": True,
        "tools": True,
        "reasoning_strategy": "parameter",
    },
    "qwen3.5:4b": {
        "vision": True,
        "tools": True,
        "reasoning_strategy": "parameter",
    },
    "llama3.2:3b": {
        "vision": False,
        "tools": True,
        "reasoning_strategy": "none",
    },
    "phi4-mini": {
        "vision": False,
        "tools": True,
        "reasoning_strategy": "model_switch",
        "switch_to": "phi4-mini-reasoning",
    },
}


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """Incoming chat request from the frontend."""

    message: str
    base_model: str = "qwen3.5:2b"
    use_reasoning: bool = False
    use_tools: bool = True
    has_image: bool = False


class ChatResponse(BaseModel):
    """Structured response returned to the frontend."""

    model: str
    response: str
    tools_used: list[str]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# POST /chat — Core inference endpoint
# ---------------------------------------------------------------------------
# Maximum tool-call round-trips before we force a final answer.
# Prevents infinite loops if the model keeps requesting tools.
_MAX_TOOL_ROUNDS = 5


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Handle a chat request with capability validation, dynamic routing,
    and optional tool use (function calling).

    Pipeline:
        1. Validate that the requested model exists in the registry.
        2. Guard: reject image requests sent to non-vision models.
        3. Route: resolve the final model name and options based on
           the model's reasoning strategy.
        4. Forward the request to Ollama.  If the model emits tool calls,
           execute them and feed the results back (up to ``_MAX_TOOL_ROUNDS``).
        5. Return the final text response.
    """

    # 1. Registry lookup
    model_config = MODEL_REGISTRY.get(request.base_model)
    if model_config is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{request.base_model}' is not registered. "
                f"Available: {list(MODEL_REGISTRY.keys())}"
            ),
        )

    # 2. Vision capability guard
    if request.has_image and not model_config["vision"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{request.base_model}' does not support image input. "
                f"Use a vision-capable model such as qwen3.5:4b."
            ),
        )

    # 3. Reasoning strategy routing
    actual_model = request.base_model
    messages: list[dict] = []
    strategy = model_config["reasoning_strategy"]

    # Determine whether to enable the `think` parameter.
    # ── Why `think` instead of `/no_think` system prompt? ──
    # Testing confirmed the `/no_think` prompt is unreliable on Ollama 0.18+:
    # the model still generates a full thinking trace (~57 s for a trivial
    # question), whereas `think=False` suppresses it entirely (<1 s).
    enable_think: bool | None = None  # None = let the model decide

    if strategy == "model_switch" and request.use_reasoning:
        # Swap to the dedicated reasoning variant (e.g. phi4-mini → phi4-mini-reasoning)
        actual_model = model_config["switch_to"]

    elif strategy == "parameter":
        # Qwen3.5 supports the `think` API parameter natively.
        enable_think = request.use_reasoning

    # strategy == "none" → no-op; use the base model as-is

    # Resolve tools: only pass tool definitions when enabled and supported.
    tools_enabled = request.use_tools and model_config.get("tools", False)
    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    # System prompt: ground the model with today's date and tool-use rules.
    # Centralised in prompts.py so it can be tuned without touching routing logic.
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    messages.append({"role": "system", "content": build_system_prompt(today=today)})

    # ------------------------------------------------------------------
    # RAG retrieval: query ChromaDB for the most relevant document chunks
    # and inject them as context into the user prompt so the model answers
    # grounded in the knowledge base.
    # ------------------------------------------------------------------
    rag_chunks = _vectorstore.similarity_search(request.message, k=RAG_TOP_K)

    if rag_chunks:
        # Join chunk contents with a separator for readability.
        context = "\n---\n".join(chunk.page_content for chunk in rag_chunks)
        augmented_message = (
            f"Please refer to the following knowledge base excerpts:\n"
            f"{context}\n\n"
            f"Based on the above, answer the question: {request.message}"
        )
    else:
        # No relevant documents found; fall back to the raw question.
        augmented_message = request.message

    # Append the (possibly augmented) user message.
    messages.append({"role": "user", "content": augmented_message})

    # 4. Call Ollama (with tool-calling loop)
    tools_used: list[str] = []
    try:
        start = time.perf_counter()
        result = ollama.chat(
            model=actual_model,
            messages=messages,
            tools=active_tools,
            think=enable_think,
            keep_alive=OLLAMA_KEEP_ALIVE,
            options={"num_ctx": 4096},
        )

        # Tool-call loop: model may request one or more tool invocations.
        # Each round we execute the requested tools, append their results,
        # and let the model generate a follow-up (which may be another tool
        # call or the final answer).
        for _ in range(_MAX_TOOL_ROUNDS):
            tool_calls = result["message"].get("tool_calls")
            if not tool_calls:
                break  # No more tool calls → model gave its final answer.

            # Record the assistant message that contains the tool call request.
            messages.append(result["message"])

            for tc in tool_calls:
                func_name = tc["function"]["name"]
                func_args = tc["function"]["arguments"]

                # Execute the tool
                handler = TOOL_MAP.get(func_name)
                if handler is None:
                    tool_result = f"Error: unknown tool '{func_name}'"
                else:
                    try:
                        tool_result = handler(**func_args)
                    except Exception as e:
                        tool_result = f"Error executing {func_name}: {e}"

                tools_used.append(func_name)
                messages.append({"role": "tool", "content": str(tool_result)})

            # Call the model again with tool results
            result = ollama.chat(
                model=actual_model,
                messages=messages,
                tools=active_tools,
                think=enable_think,
                keep_alive=OLLAMA_KEEP_ALIVE,
                options={"num_ctx": 4096},
            )

        elapsed = round(time.perf_counter() - start, 2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

    return ChatResponse(
        model=actual_model,
        response=result["message"]["content"],
        tools_used=tools_used,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# GET /health — Liveness probe
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    """Return service status and the list of registered models."""
    return {"status": "ok", "registered_models": list(MODEL_REGISTRY.keys())}
