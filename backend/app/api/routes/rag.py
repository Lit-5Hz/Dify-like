from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import RuntimeKnowledgeDocumentUploadOut
from app.services.app_service import get_app, get_rag_node
from app.services.chat_service import get_conversation_for_user, get_or_create_conversation
from app.services.rag_service import (
    get_or_create_runtime_knowledge_base,
    list_runtime_knowledge_documents,
    rag_node_allows_user_upload,
    save_knowledge_document,
)

router = APIRouter(tags=["rag"])


@router.post("/apps/{app_id}/rag/documents", response_model=RuntimeKnowledgeDocumentUploadOut)
async def upload_runtime_rag_document(
    app_id: str,
    conversation_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    rag_node = get_rag_node(app.workflow_spec)
    if not rag_node_allows_user_upload(rag_node):
        raise HTTPException(status_code=400, detail="RAG node runtime upload is not enabled.")
    try:
        conversation = get_or_create_conversation(db, app.id, current_user.id, conversation_id)
        kb = get_or_create_runtime_knowledge_base(db, app, conversation.id, rag_node)
        document = await save_knowledge_document(db, kb, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"conversation_id": conversation.id, "knowledge_base": kb, "document": document}


@router.get("/apps/{app_id}/rag/documents")
def list_runtime_rag_documents(
    app_id: str,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    if not conversation_id:
        return []
    if not get_conversation_for_user(db, conversation_id, current_user.id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_runtime_knowledge_documents(db, app.id, conversation_id, app.owner_user_id)
