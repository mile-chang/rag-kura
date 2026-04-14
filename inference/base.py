"""Shared helpers for all inference backends.

Kept intentionally small — only utilities that are genuinely used by more than
one provider module belong here.
"""

import json

import config


_MAX_TOOL_ROUNDS = 5  # Maximum tool-use iterations before forcing a final answer


def _sse(type_: str, content: str = "", **extra) -> str:
    """Serialise a server-sent event to the ``data: ...\\n\\n`` wire format."""
    payload = {"type": type_, "content": content, **extra}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_rag_message(user_message: str) -> str:
    """Augment the user query with top-K chunks from ChromaDB.

    Returns the original message unchanged when no relevant chunks are found,
    so the RAG path degrades gracefully to a plain inference call.

    Always reads ``config._vectorstore`` at call-time so hot-reload
    reassignments (after document uploads) are picked up automatically.
    """
    chunks = config._vectorstore.similarity_search(user_message, k=config.RAG_TOP_K)
    if not chunks:
        return user_message
    context = "\n---\n".join(c.page_content for c in chunks)
    return (
        f"Please refer to the following knowledge base excerpts:\n"
        f"{context}\n\n"
        f"Based on the above, answer the question: {user_message}"
    )
