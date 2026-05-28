from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.app_service import get_chat_accessible_app
from app.services.chat_service import (
    chat_once,
    chat_stream,
    get_conversation_for_user,
    list_messages,
)

router = APIRouter(tags=["chat"])


@router.post("/apps/{app_id}/chat", response_model=ChatResponse | None)
async def chat(
    app_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_chat_accessible_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    if payload.stream:
        """
            写在chat_stream入口：
                AgentScopeAdapter 负责“把 AgentScope 事件翻译成项目事件”
                WorkflowExecutor 负责“按 workflow 执行，并把事件写进 run log”
                chat_stream 负责“把事件再翻译成 SSE 发给前端，同时落库消息”
                前端负责“边收边画气泡，结束后保留会话 id”
        """
        return StreamingResponse(
            chat_stream(db, app, payload.query, current_user.id, payload.conversation_id),
            media_type="text/event-stream",
        )
    try:
        return await chat_once(db, app, payload.query, current_user.id, payload.conversation_id)
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
