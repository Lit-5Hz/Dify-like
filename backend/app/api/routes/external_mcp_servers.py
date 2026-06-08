from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
    ExternalMcpServerCreate,
    ExternalMcpServerOut,
    ExternalMcpServerUpdate,
    ExternalMcpToolOut,
)
from app.services.external_mcp_server_service import (
    create_external_mcp_server,
    delete_external_mcp_server,
    external_mcp_server_to_out,
    get_external_mcp_server,
    list_external_mcp_servers,
    list_external_mcp_tools,
    sync_external_mcp_server,
    update_external_mcp_server,
)


router = APIRouter(prefix="/mcp/servers", tags=["external-mcp-servers"])


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
    delete_external_mcp_server(db, server)
    return {"ok": True}


@router.post("/{server_id}/sync", response_model=ExternalMcpServerOut)
def sync(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    server = get_external_mcp_server(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=404, detail="External MCP server not found")
    try:
        server = sync_external_mcp_server(db, server)
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
