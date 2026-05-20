from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import ModelCredentialCreate, ModelCredentialOut
from app.services.model_credential_service import (
    create_model_credential,
    delete_model_credential,
    get_model_credential,
    list_model_credentials,
    to_model_credential_out,
)

router = APIRouter(prefix="/model-credentials", tags=["model-credentials"])


@router.get("", response_model=list[ModelCredentialOut])
def list_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    credentials = list_model_credentials(db, current_user.id)
    return [to_model_credential_out(credential) for credential in credentials]


@router.post("", response_model=ModelCredentialOut)
def create(
    payload: ModelCredentialCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        credential = create_model_credential(db, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_model_credential_out(credential)


@router.delete("/{credential_id}")
def delete(credential_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    credential = get_model_credential(db, credential_id, current_user.id)
    if not credential:
        raise HTTPException(status_code=404, detail="Model credential not found")
    try:
        delete_model_credential(db, credential)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
