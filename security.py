"""Security utilities and authentication dependencies."""

from typing import Annotated
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import config
from database import get_user_by_username

security_scheme = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if the provided plain password matches the hash."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    # bcrypt requires bytes and returns bytes, so we encode/decode to string
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict) -> str:
    """Generate a JWT token with an expiration time."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.JWT_SECRET_KEY, algorithm="HS256")

async def get_current_user_optional(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)]) -> dict | None:
    """Validate JWT token and return the user profile, or None if not logged in."""
    if not auth:
        return None
    try:
        token = auth.credentials
        payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=["HS256"])
        username: str | None = payload.get("sub")
        return get_user_by_username(username) if username else None
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")