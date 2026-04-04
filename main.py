"""RAG Knowledge Assistant — FastAPI Backend (v2).

RESTful API for conversation management, multi-model inference with
dynamic routing, optional RAG retrieval, and tool use.  Serves the
static frontend from the static/ directory on the same port.

All conversation endpoints require an X-Client-ID header for per-browser
isolation.  The frontend generates this UUID on first visit and stores it
in localStorage.
"""

import asyncio
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ollama
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

# -- App setup ---------------------------------------------------------------

app = FastAPI(
    title="RAG Knowledge Assistant API",
    description="Multi-model RAG backend with RESTful conversation management.",
    version="2.0.0",
)

init_db()

# How long Ollama keeps the model in GPU memory after the last request.
OLLAMA_KEEP_ALIVE = "30m"

# -- RAG vector store (loaded once at startup) --------------------------------

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

RAG_TOP_K = 2

# -- Model registry ----------------------------------------------------------
# Central source of truth for model capabilities and reasoning strategies.

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

# -- Pydantic schemas --------------------------------------------------------

class MessageRequest(BaseModel):
    """Payload for sending a message within a conversation."""
    message: str
    base_model: str = "qwen3.5:2b"
    use_reasoning: bool = False
    use_rag: bool = False
    use_tools: bool = True
    has_image: bool = False


class MessageResponse(BaseModel):
    """AI response returned after inference."""
    role: str = "assistant"
    content: str
    model: str
    elapsed_seconds: float
    tools_used: list[str]
    use_rag: bool


class ConversationSummary(BaseModel):
    """Lightweight view used in conversation lists."""
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetail(BaseModel):
    """Full conversation including message history."""
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict]


class TitleUpdate(BaseModel):
    """Payload for updating a conversation title."""
    title: str


# -- Client ID helper --------------------------------------------------------

def _require_client_id(x_client_id: str | None) -> str:
    """Validate and return the X-Client-ID header, or raise 400."""
    if not x_client_id or not x_client_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-Client-ID header.")
    return x_client_id.strip()


# -- Inference engine (synchronous, runs in a thread) ------------------------

_MAX_TOOL_ROUNDS = 5


def _run_inference(
    user_message: str,
    base_model: str,
    use_reasoning: bool,
    use_rag: bool,
    use_tools: bool,
    has_image: bool = False,
) -> dict:
    """Execute the full inference pipeline (blocking).

    This function is intentionally synchronous because ollama.chat() is
    blocking.  It should be called via asyncio.to_thread() to avoid
    stalling the FastAPI event loop.

    Returns dict with keys: response, model, elapsed_seconds, tools_used.
    """
    # Validate model
    model_config = MODEL_REGISTRY.get(base_model)
    if model_config is None:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{base_model}' not registered. Available: {list(MODEL_REGISTRY.keys())}",
        )

    # Vision capability guard
    if has_image and not model_config["vision"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{base_model}' does not support image input. "
                f"Use a vision-capable model such as qwen3.5:4b."
            ),
        )

    # Resolve actual model name based on reasoning strategy
    actual_model = base_model
    strategy = model_config["reasoning_strategy"]
    enable_think: bool | None = None

    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", base_model)
    elif strategy == "parameter":
        enable_think = use_reasoning

    # Resolve tool definitions
    tools_enabled = use_tools and model_config.get("tools", False)
    
    # Force disable tools if the actual switched model doesn't support them
    # For example, phi4-mini-reasoning lacks tool support currently
    if strategy == "model_switch" and use_reasoning and "reasoning" in actual_model:
        tools_enabled = False

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    # Build message list with system prompt
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(today=today)},
    ]

    # Conditionally inject RAG context
    if use_rag:
        chunks = _vectorstore.similarity_search(user_message, k=RAG_TOP_K)
        if chunks:
            context = "\n---\n".join(c.page_content for c in chunks)
            augmented = (
                f"Please refer to the following knowledge base excerpts:\n"
                f"{context}\n\n"
                f"Based on the above, answer the question: {user_message}"
            )
        else:
            augmented = user_message
    else:
        augmented = user_message

    messages.append({"role": "user", "content": augmented})

    # Call Ollama with iterative tool-use loop
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

        for _ in range(_MAX_TOOL_ROUNDS):
            tool_calls = result["message"].get("tool_calls")
            if not tool_calls:
                break

            messages.append(result["message"])
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                handler = TOOL_MAP.get(name)

                if handler is None:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = handler(**args)
                    except Exception as e:
                        output = f"Error executing {name}: {e}"

                tools_used.append(name)
                messages.append({"role": "tool", "content": str(output)})

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

    # Format the display name to clearly indicate thinking mode for parameter-based models
    display_model = actual_model
    if use_reasoning and strategy == "parameter":
        display_model = f"{actual_model} (Think)"

    return {
        "response": result["message"]["content"],
        "model": display_model,
        "elapsed_seconds": elapsed,
        "tools_used": tools_used,
    }


# ============================================================================
# RESTful API endpoints
# ============================================================================

# -- Conversations -----------------------------------------------------------

@app.get("/api/conversations", response_model=list[ConversationSummary])
async def api_list_conversations(x_client_id: str = Header(None)):
    """List all conversations for the requesting client."""
    cid = _require_client_id(x_client_id)
    return list_conversations(cid)


@app.post("/api/conversations", response_model=ConversationSummary, status_code=201)
async def api_create_conversation(x_client_id: str = Header(None)):
    """Create a new empty conversation."""
    cid = _require_client_id(x_client_id)
    return create_conversation(cid)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def api_get_conversation(conversation_id: str, x_client_id: str = Header(None)):
    """Retrieve a conversation with its full message history."""
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def api_delete_conversation(conversation_id: str, x_client_id: str = Header(None)):
    """Delete a conversation and all its messages."""
    cid = _require_client_id(x_client_id)
    if not delete_conversation(conversation_id, cid):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return None


@app.patch("/api/conversations/{conversation_id}/title")
async def api_update_title(conversation_id: str, payload: TitleUpdate, x_client_id: str = Header(None)):
    """Update the title of an existing conversation."""
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    clean_title = payload.title.strip()[:100]
    update_conversation_title(conversation_id, clean_title)
    return {"status": "success", "title": clean_title}


# -- Messages (inference) ----------------------------------------------------

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
    """Send a user message, run inference, persist both turns, and respond.

    The conversation title is auto-generated from the first user message.
    Inference runs in a separate thread to avoid blocking the event loop.
    """
    cid = _require_client_id(x_client_id)
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Persist user message
    add_message(conversation_id, "user", payload.message)

    # Auto-title on first message
    if conv["title"] == "New Chat":
        title = payload.message[:30] + ("..." if len(payload.message) > 30 else "")
        update_conversation_title(conversation_id, title)

    # Run inference in a thread so we don't block the event loop
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

    # Poll client connection status while inference completes in the background
    while not inference_task.done():
        if await request.is_disconnected():
            # If client disconnects, we cancel the endpoint cleanly without interacting with SQLite.
            # The background thread running Ollama will just finish normally and discard its result.
            return Response(status_code=499) # 499 indicates Client Closed Request
        await asyncio.sleep(0.5)

    result = inference_task.result()

    # Persist assistant response
    add_message(
        conversation_id, "assistant", result["response"],
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


# -- File upload (knowledge base) -------------------------------------------

@app.post("/api/upload")
async def api_upload_files(files: list[UploadFile] = File(...)):
    """Upload .md/.pdf files to the knowledge base and trigger ingestion.

    Files are sanitised and saved to docs/, then ingest.py rebuilds the
    ChromaDB vector store.
    """
    allowed = {".md", ".pdf"}
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        # Sanitise: strip directory components to prevent path traversal
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

    # Run ingestion using the same Python interpreter as the current process
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

    # Reload vector store so new documents are available immediately
    global _vectorstore
    _vectorstore = Chroma(
        persist_directory=str(_CHROMA_DIR),
        embedding_function=_embeddings,
        collection_name="rag_knowledge",
    )

    return {"files": saved, "message": f"{len(saved)} file(s) ingested successfully."}


# -- Health & Model Status ----------------------------------------------------

@app.get("/api/models/check_loaded")
async def api_check_loaded(base_model: str, use_reasoning: bool = False):
    """Check if the requested model (or its reasoning variant) is loaded in VRAM."""
    model_config = MODEL_REGISTRY.get(base_model)
    if not model_config:
        return {"is_loaded": True, "actual_model": base_model}
        
    actual_model = base_model
    strategy = model_config.get("reasoning_strategy")
    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", base_model)
        
    try:
        ps_data = ollama.ps()
        # ollama.ps() returns list of dicts like: {"name": "llama3.2:3b", "model": "llama3.2:3b", ...}
        running_models = [m.get("model", "") for m in ps_data.get("models", [])]
        is_loaded = any(actual_model in m for m in running_models)
    except Exception:
        is_loaded = True  # Silently fallback to true if Ollama is unreachable via ps
        
    return {"is_loaded": is_loaded, "actual_model": actual_model}


@app.get("/api/health")
async def health_check():
    """Liveness probe returning service status and registered models."""
    return {"status": "ok", "registered_models": list(MODEL_REGISTRY.keys())}


# -- Static file serving (must be mounted AFTER API routes) ------------------

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
