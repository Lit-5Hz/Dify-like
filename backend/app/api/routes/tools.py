from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import AppTool
from app.db.models import User
from app.db.session import get_db
from app.schemas import AppToolOut, AppToolUpdate, ToolOut
from app.services.app_service import get_owned_app, set_app_tools
from app.tools.registry import list_tools

router = APIRouter(tags=["tools"])


@router.get("/tools", response_model=list[ToolOut])
def list_builtin_tools():
    return list_tools()


@router.get("/apps/{app_id}/tools", response_model=list[AppToolOut])
def list_app_tools(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not get_owned_app(db, app_id, current_user.id):
        raise HTTPException(status_code=404, detail="App not found")
    return list(db.scalars(select(AppTool).where(AppTool.app_id == app_id).order_by(AppTool.tool_name)))


@router.put("/apps/{app_id}/tools", response_model=list[AppToolOut])
def update_app_tools(
    app_id: str,
    payload: AppToolUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not get_owned_app(db, app_id, current_user.id):
        raise HTTPException(status_code=404, detail="App not found")
    return set_app_tools(db, app_id, payload.tool_names)
