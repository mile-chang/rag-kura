<div align="center">

  <samp>Local AI. Smart Routing. Your Knowledge.</samp>
  <br><br>
  <a href="https://github.com/mile-chang/rag-kura">
    <img src="assets/logo.svg" alt="RAG-Kura Logo" width="500">
  </a>
</div>

> A local-first RAG knowledge assistant with dynamic model routing, capability guards, and multi-model support — powered by Ollama.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg)](https://fastapi.tiangolo.com/)
[![Ollama](https://img.shields.io/badge/Ollama-local_LLM-black.svg)](https://ollama.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

---

## Overview

RAG-Kura is a local-first knowledge assistant backend built with FastAPI and Ollama. It features a **Model Registry** that enables intelligent request routing — automatically selecting the right model variant, injecting parameters, and guarding against unsupported capabilities — all without manual intervention.

## Key Features

- **Retrieval-Augmented Generation (RAG)** — Integrates ChromaDB to answer questions based on local document knowledge.
- **Modern Chat GUI (SPA)** — A responsive single-page application built with HTML/CSS/JS featuring multi-conversation management.
- **Stop/Interrupt Generation** — Instantly stop model responses to improve user control.
- **Dynamic Reasoning (Thinking Mode)** — Dedicated toggles for reasoning models with `parameter` and `model_switch` strategies.
- **Smart Loading UI** — Real-time detection of model VRAM status with "Waking up engine" indicators for cold starts.
- **Conversation Persistence** — SQLite-backed history with automatic titling and manual title editing support.
- **Model Registry** — Centralized declaration of model capabilities for intelligent routing.
- **Security & Capability Guards** — Automatic rejection of unsupported requests (e.g., vision requests to text-only models).

## System Architecture

```mermaid
graph TB
    subgraph "Frontend"
        UI[Chat SPA - HTML/JS]
    end

    subgraph "FastAPI Backend"
        ROUTER[REST API / Router]
        DB[(SQLite - Conversations)]
        REG[Model Registry]
    end

    subgraph "Vector Store (RAG)"
        CHROMA[(ChromaDB)]
    end

    subgraph "Ollama Engine"
        Q2[qwen3.5:2b]
        Q4[qwen3.5:4b]
        L3[llama3.2:3b]
        P4[phi4-mini]
        P4R[phi4-mini-reasoning]
    end

    UI -->|API Request| ROUTER
    ROUTER --> DB
    ROUTER -->|Similarity Search| CHROMA
    ROUTER --> REG
    REG -->|Auto-routing| Q2
    REG -->|Auto-routing| Q4
    REG -->|Direct Inference| L3
    REG -->|model_switch| P4
    REG -->|Thinking Mode| P4R

    style ROUTER fill:#009688
    style DB fill:#795548
    style REG fill:#2196F3
```

## Supported Models (Ollama)

Optimized configurations for specific model inference characteristics:

| Model | Strategy | Notes |
|-------|----------|-------|
| [**qwen3.5:2b**](https://ollama.com/library/qwen3.5) | `parameter` | Supports `Think` mode via parameter injection |
| [**qwen3.5:4b**](https://ollama.com/library/qwen3.5) | `parameter` | Same as above |
| [**llama3.2:3b**](https://ollama.com/library/llama3.2) | `none` | Standard chat, no reasoning toggle |
| [**phi4-mini**](https://ollama.com/library/phi4-mini) | `model_switch` | Swaps to `phi4-mini-reasoning` during inference |

## Prerequisites

- Python 3.12+
- [**Ollama**](https://ollama.com/) installed with required models pulled.
- **PyTorch (CPU version)**: Defaults to CPU to save VRAM for Ollama; if you have sufficient VRAM, you may install the GPU version.

## Local Development

```bash
# Clone and setup
git clone https://github.com/mile-chang/rag-kura.git
cd rag-kura

# Setup surroundings
python3 -m venv .venv
source .venv/bin/activate

# 💡 Resource Planning: Defaults to CPU-only PyTorch to save VRAM. If your hardware is sufficient, skip this line and install via -r directly.
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Ingest Knowledge (Optional)
# Place Markdown files in docs/, then run:
python ingest.py

# Start the server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Open browser at: http://localhost:8000
```

## Roadmap

- [x] Phase 1: Environment Setup & SSD Optimization
- [x] Phase 2: AI Backend Foundation (FastAPI & Ollama)
- [x] Phase 3: Vector Database & Document Processing (Local ChromaDB)
- [x] Phase 4: RAG Core Logic Integration (Retriever & Generator)
- [x] Phase 5: Modern Chat GUI (Custom SPA implementation)
- [ ] Phase 6: Containerization & Deployment (Docker Compose)

Detailed tasks are in `TODO.md`.

## API Endpoints (RESTful)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/conversations` | List all conversations |
| POST | `/api/conversations` | Create a new session |
| GET | `/api/conversations/{id}`| Get session history |
| DELETE | `/api/conversations/{id}`| Delete a session |
| PATCH | `/api/conversations/{id}/title` | Update session title |
| POST | `/api/conversations/{id}/messages`| Send message (with model/reasoning) |
| GET | `/api/models/check_loaded` | Check if model is in VRAM |

## Project Structure

```
rag-kura/
├── main.py              # FastAPI core & routing
├── database.py          # SQLite persistence layer
├── chat_history.db      # SQLite database (gitignored)
├── ingest.py            # Knowledge ingestion script
├── prompts.py           # System prompt templates
├── tools.py             # External tool definitions
├── static/              # Frontend (index.html, script.js)
├── docs/                # Knowledge source directory
├── chroma_db/           # ChromaDB vector store (gitignored)
├── requirements.txt     # Python dependencies
├── README.md            # English documentation
└── TODO.md              # Project roadmap
```

## Tech Stack

| Category | Tech / Model | Description |
|----------|--------------|-------------|
| **Chat Interface** | Vanilla JS, Tailwind CSS | Modern SPA with stop-generation support |
| **Backend Core** | FastAPI, SQLite | Async execution, dynamic routing, and persistence |
| **Retrieval (RAG)** | LangChain, ChromaDB | Local vector store, Markdown-based knowledge base |
| **Embedding Model** | [**bge-small-zh-v1.5**](https://huggingface.co/BAAI/bge-small-zh-v1.5) | **CPU Only**, SOTA Chinese embeddings, VRAM-efficient |
| **Inference Engine** | [**Ollama**](https://ollama.com/) | Local model runtime with GPU acceleration |

## Security

- Browser-level isolation via `X-Client-ID` header.
- Title length enforcement (100 chars) with backend sanitization.
- Unauthorized path traversal protection for uploads.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
