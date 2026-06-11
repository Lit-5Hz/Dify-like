from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
    ExternalMcpServerCreate,
    ExternalMcpOAuthConnectOut,
    ExternalMcpServerOut,
    ExternalMcpServerUpdate,
    ExternalMcpToolOut,
)
from app.services.external_mcp_oauth_service import (
    complete_external_mcp_oauth_callback,
    disconnect_external_mcp_oauth,
    external_mcp_oauth_callback_error_url,
    external_mcp_oauth_callback_success_url,
    start_external_mcp_oauth_flow,
)
from app.services.external_mcp_server_service import (
    create_external_mcp_server,
    delete_external_mcp_server,
    external_mcp_server_to_out,
    get_external_mcp_server,
    list_external_mcp_servers,
    list_external_mcp_tools,
    refresh_external_mcp_server_tool_manifest,
    update_external_mcp_server,
)


router = APIRouter(prefix="/mcp/servers", tags=["external-mcp-servers"])
oauth_router = APIRouter(prefix="/mcp/oauth", tags=["external-mcp-oauth"])


@router.post("", response_model=ExternalMcpServerOut)
def create(
    payload: ExternalMcpServerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        server = create_external_mcp_server(db, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return external_mcp_server_to_out(server)


@router.get("", response_model=list[ExternalMcpServerOut])
def list_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [external_mcp_server_to_out(server) for server in list_external_mcp_servers(db, current_user.id)]


@router.get("/{server_id}", response_model=ExternalMcpServerOut)
def get_one(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    return external_mcp_server_to_out(server)


@router.patch("/{server_id}", response_model=ExternalMcpServerOut)
def update(
    server_id: str,
    payload: ExternalMcpServerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    try:
        server = update_external_mcp_server(db, server, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return external_mcp_server_to_out(server)


@router.delete("/{server_id}")
def delete(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    removed_references = delete_external_mcp_server(db, server)
    return {"ok": True, "removed_workflow_draft_tool_references": removed_references}


@router.post("/{server_id}/oauth/connect", response_model=ExternalMcpOAuthConnectOut)
def connect_oauth(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    try:
        authorization_url = start_external_mcp_oauth_flow(db, server)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"authorization_url": authorization_url}


@router.post("/{server_id}/oauth/disconnect", response_model=ExternalMcpServerOut)
def disconnect_oauth(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    server = disconnect_external_mcp_oauth(db, server)
    return external_mcp_server_to_out(server)


@router.post("/{server_id}/sync", response_model=ExternalMcpServerOut)
async def sync_tools( # Synchronize/refresh the tool list asynchronously
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Find a registered ExternalMcpServer according to server_id, 
    and then visit the remote MCP Server in refresh_external_mcp_server_tool_manifest.
    """
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    try:
        server = await refresh_external_mcp_server_tool_manifest(db, server)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return external_mcp_server_to_out(server)


@router.get("/{server_id}/tools", response_model=list[ExternalMcpToolOut])
def tools(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    return [
        {
            "server_id": server.id,
            "name": str(tool.get("name") or ""),
            "description": str(tool.get("description") or ""),
            "input_schema": tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
        }
        for tool in list_external_mcp_tools(server)
    ]


@oauth_router.get("/callback")
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if error:
        message = error_description or error
        return RedirectResponse(external_mcp_oauth_callback_error_url(message, settings))
    try:
        server = await complete_external_mcp_oauth_callback(db, state, code, settings)
    except ValueError as exc:
        return RedirectResponse(external_mcp_oauth_callback_error_url(str(exc), settings))
    return RedirectResponse(external_mcp_oauth_callback_success_url(server, settings))
