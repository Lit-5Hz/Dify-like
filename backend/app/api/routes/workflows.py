from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import WorkflowCreate, WorkflowOut, WorkflowUpdate, WorkflowVersionOut
from app.services.app_service import get_owned_app
from app.services.workflow_service import (
    create_workflow,
    delete_workflow,
    get_owned_workflow,
    list_app_workflows,
    list_workflow_versions,
    publish_workflow,
    update_workflow,
)

router = APIRouter(tags=["workflows"])


@router.post("/apps/{app_id}/workflows", response_model=WorkflowOut)
def create(
    app_id: str,
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    try:
        return create_workflow(db, app, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/apps/{app_id}/workflows", response_model=list[WorkflowOut])
def list_for_app(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    app = get_owned_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return list_app_workflows(db, app)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
def get_one(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.patch("/workflows/{workflow_id}", response_model=WorkflowOut)
def update(
    workflow_id: str,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        return update_workflow(db, workflow, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}")
def delete(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    delete_workflow(db, workflow)
    return {"ok": True}


@router.post("/workflows/{workflow_id}/publish", response_model=WorkflowVersionOut)
def publish(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        return publish_workflow(db, workflow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionOut])
def versions(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return list_workflow_versions(db, workflow)
