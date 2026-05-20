from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.services.auth_service import get_user_by_token


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = _parse_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _parse_bearer_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value:
        return ""
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()
