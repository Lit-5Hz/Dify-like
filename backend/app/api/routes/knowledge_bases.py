from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import KnowledgeBaseCreate, KnowledgeBaseOut, KnowledgeBaseUpdate, KnowledgeDocumentOut
from app.services.knowledge_database_service import (
    create_knowledge_base,
    delete_knowledge_base,
    delete_knowledge_document,
    get_knowledge_base,
    list_knowledge_bases,
    list_knowledge_documents,
    rebuild_knowledge_base,
    save_knowledge_document,
    update_knowledge_base,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.post("", response_model=KnowledgeBaseOut)
def create(payload: KnowledgeBaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return create_knowledge_base(db, payload, current_user.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[KnowledgeBaseOut])
def list_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return list_knowledge_bases(db, current_user.id)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
def get_one(kb_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    return kb


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
def update(
    kb_id: str,
    payload: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    return update_knowledge_base(db, kb, payload)


@router.delete("/{kb_id}")
def delete(kb_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    delete_knowledge_base(db, kb)
    return {"ok": True}


@router.post("/{kb_id}/documents", response_model=KnowledgeDocumentOut)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    try:
        return await save_knowledge_document(db, kb, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{kb_id}/documents", response_model=list[KnowledgeDocumentOut])
def list_documents(kb_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    return list_knowledge_documents(db, kb)


@router.delete("/{kb_id}/documents/{document_id}")
def delete_document(
    kb_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    if not delete_knowledge_document(db, kb, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


@router.post("/{kb_id}/rebuild", response_model=KnowledgeBaseOut)
def rebuild(kb_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = get_knowledge_base(db, kb_id, current_user.id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge database not found")
    try:
        return rebuild_knowledge_base(db, kb)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
