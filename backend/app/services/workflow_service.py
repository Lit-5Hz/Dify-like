from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import App, KnowledgeBase, Workflow, WorkflowVersion
from app.schemas import WorkflowCreate, WorkflowUpdate
from app.services.app_service import (
    _collect_workflow_credential_ids,
    _collect_workflow_knowledge_base_ids,
    normalize_workflow_spec,
)
from app.services.model_credential_service import get_model_credential
from app.services.agent_tool_spec import validate_workflow_agent_tools


def create_workflow(db: Session, app: App, payload: WorkflowCreate) -> Workflow:
    draft_spec = normalize_workflow_spec(payload.draft_spec)
    _validate_workflow_spec(db, app.owner_user_id, app.model_credential_id, draft_spec)
    workflow = Workflow(
        app_id=app.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        draft_spec=draft_spec,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return workflow


def list_app_workflows(db: Session, app: App) -> list[Workflow]:
    return list(db.scalars(select(Workflow).where(Workflow.app_id == app.id).order_by(Workflow.created_at.desc())))


def get_owned_workflow(db: Session, workflow_id: str, owner_user_id: str) -> Workflow | None:
    return db.scalar(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(Workflow.id == workflow_id, App.owner_user_id == owner_user_id)
    )


def get_published_workflow_for_chat(db: Session, workflow_id: str) -> tuple[Workflow, App, WorkflowVersion] | None:
    row = db.execute(
        select(Workflow, App, WorkflowVersion)
        .join(App, App.id == Workflow.app_id)
        .join(WorkflowVersion, WorkflowVersion.id == Workflow.published_version_id)
        .where(Workflow.id == workflow_id)
    ).first()
    if not row:
        return None
    workflow, app, version = row
    return workflow, app, version


def update_workflow(db: Session, workflow: Workflow, payload: WorkflowUpdate) -> Workflow:
    app = db.get(App, workflow.app_id)
    if not app:
        raise ValueError("App not found.")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values and values["name"] is not None:
        workflow.name = str(values["name"]).strip()
    if "description" in values and values["description"] is not None:
        workflow.description = str(values["description"]).strip()
    if "draft_spec" in values and values["draft_spec"] is not None:
        draft_spec = normalize_workflow_spec(values["draft_spec"])
        _validate_workflow_spec(db, app.owner_user_id, app.model_credential_id, draft_spec)
        workflow.draft_spec = draft_spec
    db.commit()
    db.refresh(workflow)
    return workflow


def publish_workflow(db: Session, workflow: Workflow) -> WorkflowVersion:
    app = db.get(App, workflow.app_id)
    if not app:
        raise ValueError("App not found.")
    spec = normalize_workflow_spec(workflow.draft_spec)
    _validate_workflow_spec(db, app.owner_user_id, app.model_credential_id, spec)
    last_version = db.scalar(
        select(func.max(WorkflowVersion.version_number)).where(WorkflowVersion.workflow_id == workflow.id)
    )
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=int(last_version or 0) + 1,
        spec_json=deepcopy(spec),
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    db.commit()
    db.refresh(version)
    db.refresh(workflow)
    return version


def list_workflow_versions(db: Session, workflow: Workflow) -> list[WorkflowVersion]:
    return list(
        db.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.version_number.desc())
        )
    )


def delete_workflow(db: Session, workflow: Workflow) -> None:
    db.delete(workflow)
    db.commit()


def published_workflow_references_knowledge_base(db: Session, kb_id: str) -> bool:
    for version in db.scalars(select(WorkflowVersion)):
        if kb_id in _collect_workflow_knowledge_base_ids(version.spec_json):
            return True
    return False


def remove_knowledge_base_from_workflow_drafts(db: Session, owner_user_id: str, kb_id: str) -> None:
    rows = db.scalars(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(App.owner_user_id == owner_user_id)
    )
    for workflow in rows:
        next_spec, changed = _remove_knowledge_base_id(workflow.draft_spec, kb_id)
        if changed:
            workflow.draft_spec = next_spec


def _validate_workflow_spec(db: Session, owner_user_id: str, app_model_credential_id: str | None, spec: dict[str, Any]) -> None:
    validate_workflow_agent_tools(spec)

    credential_ids = _collect_workflow_credential_ids(spec)
    app_credential_id = str(app_model_credential_id or "").strip()
    if app_credential_id:
        credential_ids.add(app_credential_id)
    for credential_id in credential_ids:
        if not get_model_credential(db, credential_id, owner_user_id):
            raise ValueError(f"Model credential not found: {credential_id}")

    for kb_id in _collect_workflow_knowledge_base_ids(spec):
        exists = db.scalar(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.scope == "creator",
            )
        )
        if not exists:
            raise ValueError(f"Knowledge database not found: {kb_id}")


def _remove_knowledge_base_id(workflow_spec: dict | None, kb_id: str) -> tuple[dict[str, Any], bool]:
    spec = deepcopy(normalize_workflow_spec(workflow_spec))
    changed = False
    nodes = spec.get("nodes")
    if not isinstance(nodes, list):
        return spec, False

    next_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            next_nodes.append(node)
            continue
        next_node = dict(node)
        ids = next_node.get("knowledge_base_ids")
        if isinstance(ids, list):
            next_ids = [str(item) for item in ids if str(item) != kb_id]
            if next_ids != ids:
                next_node["knowledge_base_ids"] = next_ids
                changed = True
        if str(next_node.get("kb_id") or "") == kb_id:
            next_node.pop("kb_id", None)
            changed = True
        next_nodes.append(next_node)

    if changed:
        spec["nodes"] = next_nodes
    return spec, changed
