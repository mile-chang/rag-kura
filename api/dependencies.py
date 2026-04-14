"""Shared FastAPI dependencies for the API layer."""

from fastapi import Header, HTTPException


def require_client_id(x_client_id: str | None = Header(None)) -> str:
    """Validate the X-Client-ID header and return its stripped value.

    Raises:
        HTTPException(400): Header is missing or blank.
    """
    if not x_client_id or not x_client_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-Client-ID header.")
    return x_client_id.strip()
