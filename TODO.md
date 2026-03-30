# TODO

## Phase 5: Streamlit Frontend Interface
- [ ] Set up Streamlit environment
- [ ] Implement Chat UI (input field & history)
- [ ] Connect frontend to FastAPI endpoints

## Phase 6: Containerization & Deployment
- [ ] Write Dockerfiles for FastAPI and Streamlit
- [ ] Configure `docker-compose.yml` for Vector DB, FastAPI, and Streamlit
- [ ] Add GitHub Actions for linting (flake8/ruff) and basic tests

## Pending / Future Features
- [ ] Add SSE streaming response (`StreamingResponse`) to `/chat` endpoint
  - Non-thinking mode: token-by-token typing effect
  - Thinking mode: expand `<think>` bubble, then stream final response
  - Implement together with frontend UI

## Completed
- [x] Phase 1: Environment Setup & SSD Optimization
- [x] Phase 2: AI Backend Foundation (FastAPI & Ollama)
- [x] Phase 3: Vector Database & Document Processing (Local ChromaDB)
- [x] Phase 4: RAG Core Logic Integration (Retriever & Generator)
