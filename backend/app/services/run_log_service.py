from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, Run, RunStep


def create_run(db: Session, app_id: str, conversation_id: str, input_message_id: str | None = None) -> Run:
    run = Run(app_id=app_id, conversation_id=conversation_id, input_message_id=input_message_id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def add_step(
    db: Session,
    run_id: str,
    step_type: str,
    name: str,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    latency_ms: int = 0,
    error: str = "",
) -> RunStep:
    step = RunStep(
        run_id=run_id,
        type=step_type,
        name=name,
        input_json=input_json,
        output_json=output_json,
        latency_ms=latency_ms,
        error=error,
        ended_at=datetime.now(timezone.utc),
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def finish_run(
    db: Session,
    run: Run,
    started_at: float,
    status: str = "success",
    output_message_id: str | None = None,
    error: str = "",
) -> Run:
    run.status = status
    run.output_message_id = output_message_id
    run.error = error
    run.latency_ms = int((perf_counter() - started_at) * 1000)
    db.commit()
    db.refresh(run)
    return run


def list_runs(db: Session, app_id: str) -> list[Run]:
    return list(db.scalars(select(Run).where(Run.app_id == app_id).order_by(Run.created_at.desc())))


def get_run_for_owner(db: Session, run_id: str, owner_user_id: str) -> Run | None:
    return db.scalar(
        select(Run)
        .join(App, App.id == Run.app_id)
        .where(Run.id == run_id, App.owner_user_id == owner_user_id)
    )


def list_run_steps(db: Session, run_id: str) -> list[RunStep]:
    return list(db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.started_at.asc())))
