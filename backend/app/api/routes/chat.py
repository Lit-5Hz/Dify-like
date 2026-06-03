from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.chat_service import (
    chat_once,
    chat_stream,
    get_conversation_for_user,
    list_messages,
)
from app.services.workflow_service import get_published_workflow_for_chat

router = APIRouter(tags=["chat"])


@router.post("/workflows/{workflow_id}/chat", response_model=ChatResponse | None)
async def chat(
    workflow_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resolved = get_published_workflow_for_chat(db, workflow_id)
    if not resolved:
        raise HTTPException(status_code=400, detail="Workflow is not published")
    workflow, app, workflow_version = resolved
    if payload.stream:
        return StreamingResponse(
            chat_stream(db, app, workflow, workflow_version, payload.query, current_user.id, payload.conversation_id),
            media_type="text/event-stream",
        )
    try:
        return await chat_once(db, app, workflow, workflow_version, payload.query, current_user.id, payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = get_conversation_for_user(db, conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_messages(db, conversation_id, current_user.id)
