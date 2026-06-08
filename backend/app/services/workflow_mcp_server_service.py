from __future__ import annotations

import re
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credential_crypto import decrypt_secret, encrypt_secret
from app.db.models import App, Workflow, WorkflowMcpServer
from app.schemas import WorkflowMcpServerUpsert


SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$")


def get_workflow_mcp_server(db: Session, workflow: Workflow) -> WorkflowMcpServer | None:
    return db.scalar(select(WorkflowMcpServer).where(WorkflowMcpServer.workflow_id == workflow.id))


def upsert_workflow_mcp_server(
    db: Session,
    workflow: Workflow,
    payload: WorkflowMcpServerUpsert,
) -> tuple[WorkflowMcpServer, str | None]:
    server_name = payload.server_name.strip()
    server_slug = normalize_server_slug(payload.server_slug)
    description = payload.description.strip()
    if not server_name:
        raise ValueError("MCP server name is required.")
    if not server_slug:
        raise ValueError("MCP server slug is required.")
    if not is_valid_server_slug(server_slug):
        raise ValueError("MCP server slug must contain lowercase letters, numbers, and hyphens only.")
    _assert_server_slug_available(db, server_slug, workflow.id)

    server = get_workflow_mcp_server(db, workflow)
    token: str | None = None
    if not server:
        token = generate_mcp_token()
        server = WorkflowMcpServer(
            workflow_id=workflow.id,
            enabled=payload.enabled,
            server_name=server_name,
            server_slug=server_slug,
            description=description,
            auth_type="bearer",
            encrypted_token=encrypt_secret(token),
        )
        db.add(server)
    else:
        server.enabled = payload.enabled
        server.server_name = server_name
        server.server_slug = server_slug
        server.description = description
        server.auth_type = "bearer"
    db.commit()
    db.refresh(server)
    return server, token


def rotate_workflow_mcp_server_token(db: Session, workflow: Workflow) -> tuple[WorkflowMcpServer, str]:
    server = get_workflow_mcp_server(db, workflow)
    if not server:
        raise ValueError("MCP server is not configured.")
    token = generate_mcp_token()
    server.encrypted_token = encrypt_secret(token)
    server.auth_type = "bearer"
    db.commit()
    db.refresh(server)
    return server, token


def get_public_workflow_mcp_server_by_slug(
    db: Session,
    server_slug: str,
) -> tuple[WorkflowMcpServer, Workflow, App] | None:
    row = db.execute(
        select(WorkflowMcpServer, Workflow, App)
        .join(Workflow, Workflow.id == WorkflowMcpServer.workflow_id)
        .join(App, App.id == Workflow.app_id)
        .where(WorkflowMcpServer.server_slug == server_slug)
    ).first()
    if not row:
        return None
    server, workflow, app = row
    return server, workflow, app


def verify_workflow_mcp_server_token(server: WorkflowMcpServer, authorization: str | None) -> bool:
    if str(server.auth_type or "").lower() != "bearer":
        return False
    token = _parse_bearer_token(authorization)
    if not token:
        return False
    try:
        expected = decrypt_secret(server.encrypted_token)
    except ValueError:
        return False
    return secrets.compare_digest(token, expected)


def generate_mcp_token() -> str:
    return f"mcp_{secrets.token_urlsafe(32)}"


def normalize_server_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def is_valid_server_slug(slug: str) -> bool:
    return bool(SLUG_PATTERN.match(slug))


def workflow_mcp_server_to_out(server: WorkflowMcpServer, token: str | None = None) -> dict[str, Any]:
    return {
        "id": server.id,
        "workflow_id": server.workflow_id,
        "enabled": server.enabled,
        "server_name": server.server_name,
        "server_slug": server.server_slug,
        "description": server.description,
        "auth_type": server.auth_type,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
        "token": token,
    }


def _assert_server_slug_available(db: Session, server_slug: str, workflow_id: str) -> None:
    existing = db.scalar(
        select(WorkflowMcpServer).where(
            WorkflowMcpServer.server_slug == server_slug,
            WorkflowMcpServer.workflow_id != workflow_id,
        )
    )
    if existing:
        raise ValueError("MCP server slug is already in use.")


def _parse_bearer_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()
