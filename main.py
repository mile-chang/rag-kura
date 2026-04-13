"""RAG Knowledge Assistant — FastAPI Backend (v3).

RESTful API for conversation management, multi-provider inference
(Ollama for local models, Google Gemini for cloud models), optional
RAG retrieval, and tool use.  Serves the static frontend from the
static/ directory on the same port.

Provider availability
---------------------
* Ollama  — available when the ``ollama`` Python package is installed
            and the local Ollama daemon is running.
* Gemini  — available when the ``GEMINI_API_KEY`` environment variable
            is present and the ``google-genai`` package is installed.

All conversation endpoints require an ``X-Client-ID`` header for
per-browser session isolation.  The frontend generates this UUID on
first visit and persists it in localStorage.
"""

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional: load .env file into os.environ (falls back gracefully if absent)
# ---------------------------------------------------------------------------
# python-dotenv is listed in requirements.txt but we guard against import
# failure so the server works even without it.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # .env loading is a convenience; not a hard requirement

# ---------------------------------------------------------------------------
# Optional dependency: Ollama (local model inference)
# ---------------------------------------------------------------------------
# ``try/except ImportError`` is the canonical Python idiom for optional
# dependencies — the same pattern used by pandas, boto3, requests, etc.
# If the package is missing the server starts normally; Ollama models are
# simply reported as unavailable to the frontend.
try:
    import ollama as _ollama_lib  # noqa: F401

    OLLAMA_AVAILABLE = True
except ImportError:
    _ollama_lib = None  # type: ignore[assignment]
    OLLAMA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Optional dependency: Google Gemini API
# ---------------------------------------------------------------------------
# Requires GEMINI_API_KEY to be set in the environment (via .env or the
# system).  If the key is absent or the package is not installed, Gemini
# models are silently disabled — no crash, no noisy warning.
try:
    from google import genai as _genai
    from google.genai import types as _genai_types

    _GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    if _GEMINI_API_KEY:
        _gemini_client = _genai.Client(api_key=_GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    else:
        _gemini_client = None
        GEMINI_AVAILABLE = False
except ImportError:
    _genai = None  # type: ignore[assignment]
    _genai_types = None  # type: ignore[assignment]
    _gemini_client = None
    GEMINI_AVAILABLE = False

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from database import (
    init_db,
    create_conversation,
    list_conversations,
    get_conversation,
    delete_conversation,
    update_conversation_title,
    add_message,
)
from prompts import build_system_prompt
from tools import TOOL_FUNCTIONS, TOOL_MAP

# ---------------------------------------------------------------------------
# App bootstrapping
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RAG Knowledge Assistant API",
    description=(
        "Multi-provider RAG backend (Ollama + Google Gemini) "
        "with RESTful conversation management."
    ),
    version="3.0.0",
)

init_db()

# How long Ollama keeps the model warm in GPU memory after the last request.
OLLAMA_KEEP_ALIVE = "30m"

# ---------------------------------------------------------------------------
# RAG vector store — loaded once at startup to amortise initialisation cost
# ---------------------------------------------------------------------------

_CHROMA_DIR = Path(__file__).parent / "chroma_db"
_DOCS_DIR = Path(__file__).parent / "docs"
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

RAG_TOP_K = 2  # Number of document chunks to retrieve per query

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
# Central source of truth for model capabilities and inference routing.
# The ``provider`` field controls which backend handles the request.
#
# reasoning_strategy values
# -------------------------
# "none"         — model has no built-in reasoning / thinking mode
# "parameter"    — enable/disable thinking via the `think` param (Ollama)
# "model_switch" — swap to an alternate model variant when reasoning is on
# "thinking"     — pass thinking_config to the Gemini API

MODEL_REGISTRY: dict[str, dict] = {
    # -- Ollama local models --------------------------------------------------
    "qwen3.5:2b": {
        "provider": "ollama",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "parameter",
    },
    "qwen3.5:4b": {
        "provider": "ollama",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "parameter",
    },
    "llama3.2:3b": {
        "provider": "ollama",
        "vision": False,
        "tools": True,
        "reasoning_strategy": "none",
    },
    "phi4-mini": {
        "provider": "ollama",
        "vision": False,
        "tools": True,
        "reasoning_strategy": "model_switch",
        "switch_to": "phi4-mini-reasoning",
    },
    # -- Google Gemini cloud models ------------------------------------------
    # Model IDs confirmed against official Gemini API docs (2026-03-31).
    "gemini-3-flash-preview": {
        "provider": "gemini",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "none",
        "display_name": "Gemini 3 Flash",
    },
    "gemini-3.1-pro-preview": {
        "provider": "gemini",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "thinking",  # supports thinking_config
        "display_name": "Gemini 3.1 Pro",
    },
}

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class MessageRequest(BaseModel):
    """Payload for sending a message within a conversation."""

    message: str
    base_model: str = "qwen3.5:2b"
    use_reasoning: bool = False
    use_rag: bool = False
    use_tools: bool = True
    has_image: bool = False


class MessageResponse(BaseModel):
    """AI response returned to the frontend after inference."""

    role: str = "assistant"
    content: str
    model: str
    elapsed_seconds: float
    tools_used: list[str]
    use_rag: bool


class ConversationSummary(BaseModel):
    """Lightweight conversation descriptor used in list responses."""

    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetail(BaseModel):
    """Full conversation including all persisted messages."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict]


class TitleUpdate(BaseModel):
    """Payload for renaming a conversation."""

    title: str


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_client_id(x_client_id: str | None) -> str:
    """Return a validated X-Client-ID header value, or raise HTTP 400."""
    if not x_client_id or not x_client_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-Client-ID header.")
    return x_client_id.strip()


def _build_rag_message(user_message: str) -> str:
    """Augment the user query with top-K chunks from ChromaDB.

    Returns the original message unchanged when no relevant chunks are found,
    so the RAG path degrades gracefully to a plain inference call.
    """
    chunks = _vectorstore.similarity_search(user_message, k=RAG_TOP_K)
    if not chunks:
        return user_message
    context = "\n---\n".join(c.page_content for c in chunks)
    return (
        f"Please refer to the following knowledge base excerpts:\n"
        f"{context}\n\n"
        f"Based on the above, answer the question: {user_message}"
    )


# ---------------------------------------------------------------------------
# Inference engine — Ollama (local)
# ---------------------------------------------------------------------------

_MAX_TOOL_ROUNDS = 5  # Maximum tool-use iterations before forcing a final answer


def _run_inference_ollama(
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    use_tools: bool,
    has_image: bool = False,
) -> dict:
    """Handles inference using the local Ollama daemon.

    Resolves the reasoning mode, injects RAG contexts optimally,
    and runs an automated loop to handle function calling (tool use).

    Returns:
        dict: Contains response, model name, elapsed_seconds, and tools_used.
    """
    if not OLLAMA_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Ollama is not installed on this server.",
        )

    # Reject image input for vision-incapable models early
    if has_image and not model_config["vision"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{base_model}' does not support image input. "
                "Use a vision-capable model such as qwen3.5:4b."
            ),
        )

    # Resolve the final model name and thinking flag based on the user's reasoning strategy
    actual_model = base_model
    strategy = model_config["reasoning_strategy"]
    enable_think: bool | None = None

    if strategy == "model_switch" and use_reasoning:
        # Swap to a dedicated reasoning variant (e.g. phi4-mini-reasoning)
        actual_model = model_config.get("switch_to", base_model)
    elif strategy == "parameter":
        # Toggle thinking via the boolean parameter
        enable_think = use_reasoning

    # Temporarily disable tools if the selected model variant doesn't support them
    tools_enabled = use_tools and model_config.get("tools", False)
    if strategy == "model_switch" and use_reasoning and "reasoning" in actual_model:
        tools_enabled = False

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    # Load initial system prompt and chat history
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(today=today)},
    ]

    # Optionally augment the user's message with retrieved knowledge-base context
    augmented = _build_rag_message(user_message) if use_rag else user_message
    messages.append({"role": "user", "content": augmented})

    tools_used: list[str] = []
    try:
        start = time.perf_counter()

        # Initial model request
        result = _ollama_lib.chat(
            model=actual_model,
            messages=messages,
            tools=active_tools,
            think=enable_think,
            keep_alive=OLLAMA_KEEP_ALIVE,
            options={"num_ctx": 4096},
        )

        # Function calling loop (max iterations to prevent infinite loops)
        for _ in range(_MAX_TOOL_ROUNDS):
            tool_calls = result["message"].get("tool_calls")
            
            # If the model didn't call any tools, the final answer is ready
            if not tool_calls:
                break

            # Append the model's tool call request to the chat history
            messages.append(result["message"])

            # Execute the requested tools
            for tc in tool_calls:
                name = tc["function"].get("name", "")
                safe_args = tc["function"].get("arguments") or {}
                handler = TOOL_MAP.get(name)

                if handler is None:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = handler(**safe_args)
                    except Exception as exc:
                        output = f"Error executing {name}: {exc}"

                tools_used.append(name)
                # Append the tool execution results back to the chat history
                messages.append({"role": "tool", "content": str(output)})

            # Send the updated history back to the model or for final answer
            result = _ollama_lib.chat(
                model=actual_model,
                messages=messages,
                tools=active_tools,
                think=enable_think,
                keep_alive=OLLAMA_KEEP_ALIVE,
                options={"num_ctx": 4096},
            )

        elapsed = round(time.perf_counter() - start, 2)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ollama error: {exc}") from exc

    # Append a helpful suffix to the model name if thinking mode was active
    display_model = actual_model
    if use_reasoning and strategy == "parameter":
        display_model = f"{actual_model} (Think)"

    return {
        "response": result["message"].get("content", ""),
        "model": display_model,
        "elapsed_seconds": elapsed,
        "tools_used": tools_used,
    }


# ---------------------------------------------------------------------------
# Inference engine — Google Gemini (cloud)
# ---------------------------------------------------------------------------


def _run_inference_gemini(
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    use_tools: bool,
) -> dict:
    """Handles inference using the Google Gemini cloud API.

    Runs an automated loop to handle function calling (tool use) if needed.
    Unlike Ollama, Gemini requires the system prompt to be passed separately
    via the content config, not as a standard chat message.

    Returns:
        dict: Contains response, model name, elapsed_seconds, and tools_used.
    """
    if not GEMINI_AVAILABLE or _gemini_client is None:
        raise HTTPException(
            status_code=503,
            detail="Gemini is unavailable. Please set GEMINI_API_KEY as an environment variable.",
        )

    strategy = model_config["reasoning_strategy"]
    tools_enabled = use_tools and model_config.get("tools", False)

    # Prepare system prompt and optional RAG context
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    system_prompt = build_system_prompt(today=today)
    augmented = _build_rag_message(user_message) if use_rag else user_message

    # Chat history starts with the user's message.
    # Note: System prompt is excluded here; it's passed via config instead.
    contents: list = [
        _genai_types.Content(
            role="user",
            parts=[_genai_types.Part(text=augmented)],
        )
    ]

    # Enable advanced thinking mode for supported models
    # setting thinking_budget=-1 allows the model to dynamically decide 
    # the amount of time spent reasoning.
    thinking_cfg = None
    if use_reasoning and strategy == "thinking":
        thinking_cfg = _genai_types.ThinkingConfig(thinking_budget=-1)

    # Initialize tool settings
    # The modern google-genai SDK natively accepts Python callables.
    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    config = _genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=active_tools,
        thinking_config=thinking_cfg,
    )

    tools_used: list[str] = []
    try:
        start = time.perf_counter()

        # Initial model request
        response = _gemini_client.models.generate_content(
            model=base_model,
            contents=contents,
            config=config,
        )

        # Function calling loop (max iterations to prevent infinite loops)
        for _ in range(_MAX_TOOL_ROUNDS):
            if not response.candidates:
                break

            # Filter parts to find any function calls
            candidate_parts = response.candidates[0].content.parts
            fn_call_parts = [
                p for p in candidate_parts if p.function_call is not None
            ]

            # If the model didn't call any tools, the final answer is ready
            if not fn_call_parts:
                break

            # Append the model's tool call request to the chat history
            contents.append(response.candidates[0].content)

            # Execute the requested tools
            tool_result_parts: list = []
            for part in fn_call_parts:
                fc = part.function_call
                handler = TOOL_MAP.get(fc.name)

                if handler is None:
                    result_text = f"Error: unknown tool '{fc.name}'"
                else:
                    try:
                        # Ensure we safely unpack args, handling None cases
                        safe_args = fc.args or {}
                        result_text = str(handler(**safe_args))
                    except Exception as exc:
                        result_text = f"Error executing {fc.name}: {exc}"

                tools_used.append(fc.name)
                tool_result_parts.append(
                    _genai_types.Part(
                        function_response=_genai_types.FunctionResponse(
                            name=fc.name,
                            response={"output": result_text},
                        )
                    )
                )

            # Append the tool execution results back to the chat history
            # Gemini expects tool responses to be marked under the "user" role
            contents.append(
                _genai_types.Content(role="user", parts=tool_result_parts)
            )

            # Send the updated history back to the model or for final answer
            response = _gemini_client.models.generate_content(
                model=base_model,
                contents=contents,
                config=config,
            )

        elapsed = round(time.perf_counter() - start, 2)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gemini error: {exc}") from exc

    # response.text conveniently filters out thoughts, code execution,
    # and function call parts to return only standard text.
    final_text = response.text or ""

    # Append a helpful suffix to the model name if thinking mode was active
    display_model = model_config.get("display_name", base_model)
    if use_reasoning and strategy == "thinking":
        display_model = f"{display_model} (Think)"

    return {
        "response": final_text,
        "model": display_model,
        "elapsed_seconds": elapsed,
        "tools_used": tools_used,
    }


# ---------------------------------------------------------------------------
# Inference dispatcher
# ---------------------------------------------------------------------------


def _run_inference(
    user_message: str,
    base_model: str,
    use_reasoning: bool,
    use_rag: bool,
    use_tools: bool,
    has_image: bool = False,
) -> dict:
    """Route an inference request to the correct provider backend.

    This function is intentionally synchronous because both ollama.chat()
    and the Gemini SDK's generate_content() are blocking calls.  Always
    invoke via ``asyncio.to_thread()`` to keep the FastAPI event loop free.

    Raises:
        HTTPException(400) when the requested model is not registered.
    """
    model_config = MODEL_REGISTRY.get(base_model)
    if model_config is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{base_model}' is not registered. "
                f"Available: {list(MODEL_REGISTRY.keys())}"
            ),
        )

    provider = model_config.get("provider", "ollama")

    if provider == "gemini":
        return _run_inference_gemini(
            user_message=user_message,
            base_model=base_model,
            model_config=model_config,
            use_reasoning=use_reasoning,
            use_rag=use_rag,
            use_tools=use_tools,
        )
    else:
        # Default to Ollama for all non-Gemini providers
        return _run_inference_ollama(
            user_message=user_message,
            base_model=base_model,
            model_config=model_config,
            use_reasoning=use_reasoning,
            use_rag=use_rag,
            use_tools=use_tools,
            has_image=has_image,
        )


# ============================================================================
# RESTful API endpoints
# ============================================================================

# ---------------------------------------------------------------------------
# Conversations — CRUD
# ---------------------------------------------------------------------------


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def api_list_conversations(x_client_id: str = Header(None)):
    """List all conversations owned by the requesting client."""
    cid = _require_client_id(x_client_id)
    return list_conversations(cid)


@app.post("/api/conversations", response_model=ConversationSummary, status_code=201)
async def api_create_conversation(x_client_id: str = Header(None)):
    """Create a new empty conversation and return its descriptor."""
    cid = _require_client_id(x_client_id)
    return create_conversation(cid)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def api_get_conversation(
    conversation_id: str,
    x_client_id: str = Header(None),
):
    """Retrieve a conversation with its full message history."""
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def api_delete_conversation(
    conversation_id: str,
    x_client_id: str = Header(None),
):
    """Permanently delete a conversation and all of its messages."""
    cid = _require_client_id(x_client_id)
    if not delete_conversation(conversation_id, cid):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return None


@app.patch("/api/conversations/{conversation_id}/title")
async def api_update_title(
    conversation_id: str,
    payload: TitleUpdate,
    x_client_id: str = Header(None),
):
    """Rename a conversation (max 100 characters, trimmed at the backend)."""
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    clean_title = payload.title.strip()[:100]
    update_conversation_title(conversation_id, clean_title)
    return {"status": "success", "title": clean_title}


# ---------------------------------------------------------------------------
# Messages — inference
# ---------------------------------------------------------------------------


@app.post(
    "/api/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
)
async def api_send_message(
    conversation_id: str,
    payload: MessageRequest,
    request: Request,
    x_client_id: str = Header(None),
):
    """Send a user message, run provider-agnostic inference, and persist both turns.

    Behaviour notes
    ---------------
    * The conversation title is auto-generated from the first user message.
    * Inference runs in a worker thread so the FastAPI event loop stays free.
    * Client disconnection is polled every 0.5 s; on disconnect the endpoint
      returns HTTP 499 (Client Closed Request) without writing to the DB.
      The background thread finishes naturally and its result is discarded.
    """
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Persist the user turn immediately so it appears in the history even
    # if the following inference step fails.
    add_message(conversation_id, "user", payload.message)

    # Auto-generate a title from the first message in the conversation
    if conv["title"] == "New Chat":
        title = payload.message[:30] + ("..." if len(payload.message) > 30 else "")
        update_conversation_title(conversation_id, title)

    # Dispatch inference to the appropriate provider backend in a thread
    inference_task = asyncio.create_task(
        asyncio.to_thread(
            _run_inference,
            user_message=payload.message,
            base_model=payload.base_model,
            use_reasoning=payload.use_reasoning,
            use_rag=payload.use_rag,
            use_tools=payload.use_tools,
            has_image=payload.has_image,
        )
    )

    # Poll for client disconnect while inference runs in the background
    while not inference_task.done():
        if await request.is_disconnected():
            return Response(status_code=499)  # 499 = Client Closed Request
        await asyncio.sleep(0.5)

    result = inference_task.result()

    # Persist the assistant turn
    add_message(
        conversation_id,
        "assistant",
        result["response"],
        model=result["model"],
        elapsed_seconds=result["elapsed_seconds"],
        tools_used=result["tools_used"],
        use_rag=payload.use_rag,
    )

    return MessageResponse(
        content=result["response"],
        model=result["model"],
        elapsed_seconds=result["elapsed_seconds"],
        tools_used=result["tools_used"],
        use_rag=payload.use_rag,
    )


# ---------------------------------------------------------------------------
# Knowledge base — file upload and ingestion
# ---------------------------------------------------------------------------


@app.post("/api/upload")
async def api_upload_files(files: list[UploadFile] = File(...)):
    """Upload .md or .pdf files to the knowledge base and trigger re-ingestion.

    Files are sanitised (directory components stripped to prevent path
    traversal) then saved to docs/.  The ingest.py script rebuilds the
    ChromaDB vector store; the in-memory reference is hot-reloaded so
    subsequent queries immediately reflect the new documents.
    """
    allowed = {".md", ".pdf"}
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        # Strip directory separators to neutralise path-traversal payloads
        safe_name = Path(f.filename).name
        ext = Path(safe_name).suffix.lower()
        if ext not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. Allowed: {allowed}",
            )
        dest = _DOCS_DIR / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved.append(safe_name)

    # Re-run ingestion in the same Python environment as the server process
    proc = subprocess.run(
        [sys.executable, "ingest.py"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent),
    )

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {proc.stderr}",
        )

    # Hot-reload the vector store so new documents are queryable immediately
    global _vectorstore
    _vectorstore = Chroma(
        persist_directory=str(_CHROMA_DIR),
        embedding_function=_embeddings,
        collection_name="rag_knowledge",
    )

    return {"files": saved, "message": f"{len(saved)} file(s) ingested successfully."}


# ---------------------------------------------------------------------------
# Model status  (GET /api/models/{model_id}/status)
# ---------------------------------------------------------------------------


@app.get("/api/models/{model_id}/status")
async def api_model_status(model_id: str, use_reasoning: bool = False):
    """Return the warm/cold state of the specified model.

    * **Ollama models** — queries ``ollama.ps()`` to check whether the
      model is currently resident in VRAM.  Returns ``is_loaded=false``
      when Ollama is not installed so the UI shows the loading indicator.
    * **Gemini models** — always returns ``is_loaded=true`` because cloud
      APIs have no local warm-up phase.

    The ``model_id`` path segment must be URL-encoded when it contains
    special characters such as colons or dots (e.g. ``qwen3.5%3A2b``).
    FastAPI automatically decodes the value before matching.
    """
    model_config = MODEL_REGISTRY.get(model_id)

    # Unknown model — respond optimistically so the UI isn't blocked
    if not model_config:
        return {"is_loaded": True, "actual_model": model_id}

    provider = model_config.get("provider", "ollama")

    # Cloud models need no local warm-up
    if provider == "gemini":
        return {"is_loaded": True, "actual_model": model_id}

    # Resolve the actual model variant (handles phi4-mini → phi4-mini-reasoning)
    actual_model = model_id
    strategy = model_config.get("reasoning_strategy")
    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", model_id)

    if not OLLAMA_AVAILABLE:
        # Ollama not installed — report as not loaded so the UI shows a hint
        return {"is_loaded": False, "actual_model": actual_model}

    try:
        ps_data = _ollama_lib.ps()
        running_models = [m.get("model", "") for m in ps_data.get("models", [])]
        is_loaded = any(actual_model in m for m in running_models)
    except Exception:
        # Ollama daemon unreachable — fall back to True so the UI doesn't stall
        is_loaded = True

    return {"is_loaded": is_loaded, "actual_model": actual_model}


# ---------------------------------------------------------------------------
# Service status  (GET /api/status)
# ---------------------------------------------------------------------------


@app.get("/api/status")
async def api_status():
    """Return service health and per-provider availability.

    The frontend calls this endpoint on startup to determine which models
    to enable in the model-picker UI.  No credentials are exposed.
    """
    return {
        "status": "ok",
        "ollama_available": OLLAMA_AVAILABLE,
        "gemini_available": GEMINI_AVAILABLE,
        "registered_models": list(MODEL_REGISTRY.keys()),
    }


# ---------------------------------------------------------------------------
# Static file serving — MUST be mounted after all /api routes
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
