"""Conversation CRUD routes — /api/conversations."""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import require_client_id
from database import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
)
from schemas import ConversationDetail, ConversationSummary, TitleUpdate

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
async def api_list_conversations(cid: str = Depends(require_client_id)):
    """List all conversations owned by the requesting client."""
    return list_conversations(cid)


@router.post("", response_model=ConversationSummary, status_code=201)
async def api_create_conversation(cid: str = Depends(require_client_id)):
    """Create a new empty conversation and return its descriptor."""
    return create_conversation(cid)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def api_get_conversation(
    conversation_id: str,
    cid: str = Depends(require_client_id),
):
    """Retrieve a conversation with its full message history."""
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@router.delete("/{conversation_id}", status_code=204)
async def api_delete_conversation(
    conversation_id: str,
    cid: str = Depends(require_client_id),
):
    """Permanently delete a conversation and all of its messages."""
    if not delete_conversation(conversation_id, cid):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return None


@router.patch("/{conversation_id}/title")
async def api_update_title(
    conversation_id: str,
    payload: TitleUpdate,
    cid: str = Depends(require_client_id),
):
    """Rename a conversation (max 100 characters, trimmed at the backend)."""
    conv = get_conversation(conversation_id, cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    clean_title = payload.title.strip()[:100]
    update_conversation_title(conversation_id, clean_title)
    return {"status": "success", "title": clean_title}
