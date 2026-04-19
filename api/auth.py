"""Authentication and Session routes — /api/users and /api/sessions."""

from fastapi import APIRouter, Depends, HTTPException, Header
from schemas import UserCreate, UserLogin, Token, UserResponse
from security import get_password_hash, verify_password, create_access_token, get_current_user_optional
from database import create_user, get_user_by_username, bind_guest_conversations

router = APIRouter(tags=["auth"])

@router.post("/api/users", response_model=UserResponse, status_code=201)
async def api_register_user(user: UserCreate):
    """Register a new user."""
    db_user = get_user_by_username(user.username)
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(user.password)
    return create_user(user.username, hashed_password)

@router.post("/api/sessions", response_model=Token)
async def api_login_and_bind(
    user: UserLogin, 
    x_client_id: str | None = Header(default=None, alias="X-Client-ID")
):
    """Authenticate user and bind anonymous history to their account."""
    db_user = get_user_by_username(user.username)
    if not db_user or not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    # Trigger Data Merging: Assign guest history to this logged-in user
    if x_client_id:
        bind_guest_conversations(x_client_id, db_user["id"])
        
    access_token = create_access_token(data={"sub": db_user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/api/users/me", response_model=UserResponse)
async def api_get_current_user(user: dict | None = Depends(get_current_user_optional)):
    """Return the profile of the currently logged-in user."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user