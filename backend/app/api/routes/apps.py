from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import AppCreate, AppOut, AppUpdate
from app.services.app_service import (
    create_app,
    delete_app,
    get_owned_app,
    list_apps,
    update_app,
)

router = APIRouter(tags=["apps"])


@router.post("/apps", response_model=AppOut)
def create(payload: AppCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return create_app(db, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/apps", response_model=list[AppOut])
def list_owned(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return list_apps(db, current_user.id)


@router.get("/apps/{app_id}", response_model=AppOut)
def get_one(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    app = get_owned_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return app


@router.patch("/apps/{app_id}", response_model=AppOut)
def update(
    app_id: str,
    payload: AppUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    try:
        return update_app(db, app, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/apps/{app_id}")
def delete(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    app = get_owned_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    delete_app(db, app)
    return {"ok": True}

