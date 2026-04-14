"""Application configuration — provider clients, model registry, and RAG setup.

All other modules should import from this module rather than re-initialising
these resources.  For mutable singletons (e.g. _vectorstore), always access
them via ``import config; config._vectorstore`` — never ``from config import
_vectorstore`` — so that hot-reload reassignments propagate correctly.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env file into os.environ (graceful fallback when absent)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Optional dependency: Ollama (local model inference)
# ---------------------------------------------------------------------------
try:
    import ollama as ollama_lib  # noqa: F401
    OLLAMA_AVAILABLE = True
except ImportError:
    ollama_lib = None  # type: ignore[assignment]
    OLLAMA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Optional dependency: Google Gemini API
# ---------------------------------------------------------------------------
try:
    from google import genai as _genai
    from google.genai import types as genai_types

    _GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    if _GEMINI_API_KEY:
        gemini_client = _genai.Client(api_key=_GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    else:
        gemini_client = None
        GEMINI_AVAILABLE = False
except ImportError:
    _genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    gemini_client = None
    GEMINI_AVAILABLE = False

# ---------------------------------------------------------------------------
# RAG vector store — loaded once at startup to amortise initialisation cost
# ---------------------------------------------------------------------------
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

CHROMA_DIR = Path(__file__).parent / "chroma_db"
DOCS_DIR = Path(__file__).parent / "docs"
_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

_embeddings = HuggingFaceEmbeddings(
    model_name=_EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

# NOTE: Use ``import config; config._vectorstore`` everywhere.
# Never ``from config import _vectorstore`` — that captures the initial
# reference and won't see hot-reload reassignments.
_vectorstore = Chroma(
    persist_directory=str(CHROMA_DIR),
    embedding_function=_embeddings,
    collection_name="rag_knowledge",
)

RAG_TOP_K = 2  # Number of document chunks to retrieve per query

# ---------------------------------------------------------------------------
# Ollama tuning
# ---------------------------------------------------------------------------
OLLAMA_KEEP_ALIVE = "30m"  # How long the model stays warm in GPU memory

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
    "gemini-3-flash-preview": {
        "provider": "gemini",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "thinking_level",
        "display_name": "Gemini 3 Flash",
    },
    "gemma-4-31b-it": {
        "provider": "gemini",
        "vision": True,
        "tools": True,
        "reasoning_strategy": "thinking_level_optional",
        "display_name": "Gemma 4 31B",
    },
}
