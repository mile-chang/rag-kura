"""Conversation CRUD routes — /api/conversations."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.dependencies import require_client_id
from database import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
    gc_guest_conversations,
)
from security import get_current_user_optional
from schemas import ConversationDetail, ConversationSummary, TitleUpdate

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
async def api_list_conversations(
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """List all conversations owned by the requesting client."""
    user_id = user["id"] if user else None
    return list_conversations(cid, user_id=user_id)


@router.post("", response_model=ConversationSummary, status_code=201)
async def api_create_conversation(
    background_tasks: BackgroundTasks,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Create a new empty conversation and return its descriptor."""
    user_id = user["id"] if user else None
    
    if user_id is None:
        # Execute GC safely in the background so the response is immediate
        background_tasks.add_task(gc_guest_conversations)
        
    return create_conversation(cid, user_id=user_id)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def api_get_conversation(
    conversation_id: str,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Retrieve a conversation with its full message history."""
    user_id = user["id"] if user else None
    conv = get_conversation(conversation_id, cid, user_id=user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@router.delete("/{conversation_id}", status_code=204)
async def api_delete_conversation(
    conversation_id: str,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Permanently delete a conversation and all of its messages."""
    user_id = user["id"] if user else None
    if not delete_conversation(conversation_id, cid, user_id=user_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return None


@router.patch("/{conversation_id}/title")
async def api_update_title(
    conversation_id: str,
    payload: TitleUpdate,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Rename a conversation (max 100 characters, trimmed at the backend)."""
    user_id = user["id"] if user else None
    conv = get_conversation(conversation_id, cid, user_id=user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    clean_title = payload.title.strip()[:100]
    update_conversation_title(conversation_id, clean_title)
    return {"status": "success", "title": clean_title}
