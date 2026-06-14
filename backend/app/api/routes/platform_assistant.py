from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
    PlatformAssistantApplyRequest,
    PlatformAssistantApplyResponse,
    PlatformAssistantChatRequest,
    PlatformAssistantChatResponse,
)
from app.services.platform_assistant_service import apply_platform_assistant_plan, chat_with_platform_assistant


router = APIRouter(tags=["platform-assistant"])


@router.post("/platform-assistant/chat", response_model=PlatformAssistantChatResponse)
def chat(
    payload: PlatformAssistantChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return chat_with_platform_assistant(db, current_user.id, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Platform assistant failed: {exc}") from exc


@router.post("/platform-assistant/apply", response_model=PlatformAssistantApplyResponse)
def apply(
    payload: PlatformAssistantApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return apply_platform_assistant_plan(db, current_user.id, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Platform assistant apply failed: {exc}") from exc
