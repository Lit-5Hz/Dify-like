from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.mcp.server_runtime import handle_workflow_mcp_request
from app.schemas import WorkflowMcpServerOut, WorkflowMcpServerProvisionOut, WorkflowMcpServerUpsert
from app.services.workflow_mcp_server_service import (
    get_workflow_mcp_server,
    rotate_workflow_mcp_server_token,
    upsert_workflow_mcp_server,
    workflow_mcp_server_to_out,
)
from app.services.workflow_service import get_owned_workflow


api_router = APIRouter(tags=["workflow-mcp-server"])
public_router = APIRouter(tags=["mcp"])


@api_router.get("/workflows/{workflow_id}/mcp-server", response_model=WorkflowMcpServerOut | None)
def get_config(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return get_workflow_mcp_server(db, workflow)


@api_router.put("/workflows/{workflow_id}/mcp-server", response_model=WorkflowMcpServerProvisionOut)
def upsert_config(
    workflow_id: str,
    payload: WorkflowMcpServerUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        server, token = upsert_workflow_mcp_server(db, workflow, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return workflow_mcp_server_to_out(server, token)


@api_router.post("/workflows/{workflow_id}/mcp-server/rotate-token", response_model=WorkflowMcpServerProvisionOut)
def rotate_token(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workflow = get_owned_workflow(db, workflow_id, current_user.id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        server, token = rotate_workflow_mcp_server_token(db, workflow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return workflow_mcp_server_to_out(server, token)


@public_router.post("/mcp/{server_slug}")
async def mcp_endpoint(
    server_slug: str,
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    status_code, body = await handle_workflow_mcp_request(
        db,
        server_slug,
        authorization,
        payload,
        request.headers,
    )
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=body.get("detail", "MCP request failed"))
    return body
