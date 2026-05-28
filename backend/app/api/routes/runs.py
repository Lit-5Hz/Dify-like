from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import RunOut, RunStepOut
from app.services.app_service import get_owned_app
from app.services.run_log_service import get_run_for_owner, list_run_steps, list_runs

router = APIRouter(tags=["runs"])


@router.get("/apps/{app_id}/runs", response_model=list[RunOut])
def list_app_runs(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not get_owned_app(db, app_id, current_user.id):
        raise HTTPException(status_code=404, detail="App not found")
    return list_runs(db, app_id)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepOut])
def get_run_steps(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not get_run_for_owner(db, run_id, current_user.id):
        raise HTTPException(status_code=404, detail="Run not found")
    return list_run_steps(db, run_id)
