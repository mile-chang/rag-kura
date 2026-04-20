"""Messages route — inference entry point for /api/conversations/{id}/messages."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.responses import StreamingResponse

from api.dependencies import require_client_id
from database import add_message, get_conversation, update_conversation_title, edit_message_and_truncate
from inference import run_inference, run_inference_stream
from security import get_current_user_optional
from schemas import MessageRequest, MessageResponse

router = APIRouter(prefix="/api/conversations", tags=["messages"])


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    responses={
        200: {
            "description": (
                "JSON response (stream=false) **or** text/event-stream SSE (stream=true). "
                "External API consumers should omit `stream` or set it to false."
            )
        }
    },
)
async def api_send_message(
    conversation_id: str,
    payload: MessageRequest,
    request: Request,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Send a user message, run provider-agnostic inference, and persist both turns.

    Dual-track behaviour
    --------------------
    * ``stream=False`` (default) — waits for the full response and returns a
      ``MessageResponse`` JSON object.  Used by external API consumers.
    * ``stream=True`` — returns a ``StreamingResponse`` with
      ``Content-Type: text/event-stream``.  Event types:
      ``status`` (tool executing), ``chunk`` (text token), ``done``
      (completion + metadata), ``error`` (exception).

    Common behaviour
    ----------------
    * The conversation title is auto-generated from the first user message.
    * The user turn is persisted immediately before inference begins.
    * The assistant turn is always persisted — in the SSE path this happens
      inside the generator's ``finally`` block, so it survives disconnects.
    """
    user_id = user["id"] if user else None
    conv = get_conversation(conversation_id, cid, user_id=user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Persist the user turn immediately so it appears in history even if
    # the following inference step fails.
    add_message(conversation_id, "user", payload.message)

    # Auto-generate a title from the first message in the conversation
    if conv["title"] == "New Chat":
        title = payload.message[:30] + ("..." if len(payload.message) > 30 else "")
        update_conversation_title(conversation_id, title)

    # ------------------------------------------------------------------
    # SSE streaming path  (stream=True)
    # ------------------------------------------------------------------
    if payload.stream:
        generator = run_inference_stream(
            conversation_id=conversation_id,
            user_message=payload.message,
            base_model=payload.base_model,
            use_reasoning=payload.use_reasoning,
            use_rag=payload.use_rag,
            has_image=payload.has_image,
            history=conv["messages"],
        )
        return StreamingResponse(generator, media_type="text/event-stream")

    # ------------------------------------------------------------------
    # JSON path  (stream=False, default)
    # ------------------------------------------------------------------
    inference_task = asyncio.create_task(
        asyncio.to_thread(
            run_inference,
            user_message=payload.message,
            base_model=payload.base_model,
            use_reasoning=payload.use_reasoning,
            use_rag=payload.use_rag,
            has_image=payload.has_image,
            history=conv["messages"],
        )
    )

    # Poll for client disconnect while inference runs in the background
    while not inference_task.done():
        if await request.is_disconnected():
            return Response(status_code=499)  # 499 = Client Closed Request
        await asyncio.sleep(0.5)

    result = inference_task.result()

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


@router.put(
    "/{conversation_id}/messages/{message_id}",
    responses={200: {"description": "Returns a StreamingResponse (SSE) with the new AI answer."}},
)
async def api_edit_message(
    conversation_id: str,
    message_id: int,
    payload: MessageRequest,
    request: Request,
    cid: str = Depends(require_client_id),
    user: dict | None = Depends(get_current_user_optional),
):
    """Edit a user message, truncate subsequent history, and trigger a new inference stream."""
    user_id = user["id"] if user else None
    conv = get_conversation(conversation_id, cid, user_id=user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Update DB and delete subsequent messages
    edit_message_and_truncate(conversation_id, message_id, payload.message)
    
    # Re-fetch the truncated conversation history
    conv_updated = get_conversation(conversation_id, cid, user_id=user_id)
    
    # The last message in the truncated history is the newly edited user message.
    # We pass the preceding messages as `history` for the inference engine.
    history = conv_updated["messages"][:-1] if len(conv_updated["messages"]) > 0 else []

    generator = run_inference_stream(
        conversation_id=conversation_id,
        user_message=payload.message,
        base_model=payload.base_model,
        use_reasoning=payload.use_reasoning,
        use_rag=payload.use_rag,
        has_image=payload.has_image,
        history=history,
    )
    return StreamingResponse(generator, media_type="text/event-stream")
