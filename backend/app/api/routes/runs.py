from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import App, User, Workflow
from app.db.session import get_db
from app.schemas import RunOut, RunStepOut
from app.services.run_log_service import get_run_for_user, list_run_steps, list_runs

router = APIRouter(tags=["runs"])


@router.get("/workflows/{workflow_id}/runs", response_model=list[RunOut])
def list_workflow_runs(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = db.scalar(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(Workflow.id == workflow_id, (App.owner_user_id == current_user.id) | (Workflow.published_version_id.is_not(None)))
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return list_runs(db, workflow_id, current_user.id)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepOut])
def get_run_steps(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not get_run_for_user(db, run_id, current_user.id):
        raise HTTPException(status_code=404, detail="Run not found")
    return list_run_steps(db, run_id)
