"""RAG Knowledge Assistant — FastAPI entry point (v3).

This file is intentionally thin: it only wires together the application.
All business logic lives in dedicated modules:
  - config.py       → provider clients, model registry, RAG setup
  - schemas.py      → Pydantic request/response models
  - inference/      → Ollama & Gemini inference engines
  - api/            → FastAPI routers (conversations, messages, knowledge, models)
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db
from api import conversations, messages, knowledge, models

app = FastAPI(
    title="RAG Knowledge Assistant API",
    description=(
        "Multi-provider RAG backend (Ollama + Google Gemini) "
        "with RESTful conversation management."
    ),
    version="3.0.0",
)

init_db()

app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(knowledge.router)
app.include_router(models.router)

# Static file serving — MUST be mounted after all /api routes
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
