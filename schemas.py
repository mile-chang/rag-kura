"""Pydantic request/response schemas for the RAG Knowledge Assistant API."""

from pydantic import BaseModel


class MessageRequest(BaseModel):
    """Payload for sending a message within a conversation."""

    message: str
    base_model: str = "qwen3.5:2b"
    use_reasoning: bool = False
    use_rag: bool = False
    has_image: bool = False
    stream: bool = False  # When True, returns SSE StreamingResponse instead of JSON


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


class UserCreate(BaseModel):
    """Payload for user registration."""
    username: str
    password: str


class UserLogin(BaseModel):
    """Payload for user login."""
    username: str
    password: str


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Safe user profile response (excluding password hash)."""
    id: int
    username: str
    created_at: str
