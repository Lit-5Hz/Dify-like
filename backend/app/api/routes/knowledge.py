from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import DocumentOut
from app.services.app_service import get_app
from app.services.rag_service import list_documents, save_document

router = APIRouter(prefix="/apps/{app_id}/documents", tags=["knowledge"])


@router.post("", response_model=DocumentOut)
async def upload_document(
    app_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not get_app(db, app_id, current_user.id):
        raise HTTPException(status_code=404, detail="App not found")
    try:
        return await save_document(db, app_id, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[DocumentOut])
def list_app_documents(
    app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    if not get_app(db, app_id, current_user.id):
        raise HTTPException(status_code=404, detail="App not found")
    return list_documents(db, app_id)
