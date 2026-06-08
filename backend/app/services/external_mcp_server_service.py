from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credential_crypto import decrypt_secret, encrypt_secret
from app.db.models import ExternalMcpServer
from app.mcp.client import initialize_mcp_server, list_mcp_tools
from app.schemas import ExternalMcpServerCreate, ExternalMcpServerUpdate
from app.services.agent_tool_spec import normalize_agent_tools


SUPPORTED_TRANSPORT_TYPES = {"streamable_http"}
SUPPORTED_AUTH_TYPES = {"none", "bearer"}


def list_external_mcp_servers(db: Session, owner_user_id: str) -> list[ExternalMcpServer]:
    return list(
        db.scalars(
            select(ExternalMcpServer)
            .where(ExternalMcpServer.owner_user_id == owner_user_id)
            .order_by(ExternalMcpServer.created_at.desc())
        )
    )


def get_external_mcp_server(db: Session, server_id: str, owner_user_id: str) -> ExternalMcpServer | None:
    if not server_id:
        return None
    return db.scalar(
        select(ExternalMcpServer).where(
            ExternalMcpServer.id == server_id,
            ExternalMcpServer.owner_user_id == owner_user_id,
        )
    )


def create_external_mcp_server(
    db: Session,
    payload: ExternalMcpServerCreate,
    owner_user_id: str,
) -> ExternalMcpServer:
    transport_type = _normalize_transport_type(payload.transport_type)
    auth_type = _normalize_auth_type(payload.auth_type)
    name = payload.name.strip()
    server_url = payload.server_url.strip()
    if not name:
        raise ValueError("MCP server name is required.")
    if not server_url:
        raise ValueError("MCP server URL is required.")
    if auth_type == "bearer" and not payload.auth_secret.strip():
        raise ValueError("Bearer auth requires an auth secret.")

    server = ExternalMcpServer(
        owner_user_id=owner_user_id,
        name=name,
        description=payload.description.strip(),
        transport_type=transport_type,
        server_url=server_url,
        auth_type=auth_type,
        encrypted_auth_secret=_encrypt_optional_secret(payload.auth_secret),
        status="pending_sync",
        tool_manifest_json={"tools": []},
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def update_external_mcp_server(
    db: Session,
    server: ExternalMcpServer,
    payload: ExternalMcpServerUpdate,
) -> ExternalMcpServer:
    values = payload.model_dump(exclude_unset=True)
    needs_resync = False
    if "name" in values and values["name"] is not None:
        name = str(values["name"]).strip()
        if not name:
            raise ValueError("MCP server name is required.")
        server.name = name
    if "description" in values and values["description"] is not None:
        server.description = str(values["description"]).strip()
    if "transport_type" in values and values["transport_type"] is not None:
        server.transport_type = _normalize_transport_type(values["transport_type"])
        needs_resync = True
    if "server_url" in values and values["server_url"] is not None:
        server_url = str(values["server_url"]).strip()
        if not server_url:
            raise ValueError("MCP server URL is required.")
        server.server_url = server_url
        needs_resync = True
    if "auth_type" in values and values["auth_type"] is not None:
        server.auth_type = _normalize_auth_type(values["auth_type"])
        needs_resync = True
    if "auth_secret" in values and values["auth_secret"] is not None:
        server.encrypted_auth_secret = _encrypt_optional_secret(values["auth_secret"])
        needs_resync = True
    if server.auth_type == "none":
        server.encrypted_auth_secret = ""
    if server.auth_type == "bearer" and not server.encrypted_auth_secret:
        raise ValueError("Bearer auth requires an auth secret.")
    if needs_resync:
        server.status = "pending_sync"
        server.last_sync_at = None
        server.last_sync_error = ""
        server.tool_manifest_json = {"tools": []}
    db.commit()
    db.refresh(server)
    return server


def delete_external_mcp_server(db: Session, server: ExternalMcpServer) -> None:
    db.delete(server)
    db.commit()


def sync_external_mcp_server(db: Session, server: ExternalMcpServer) -> ExternalMcpServer:
    try:
        auth_secret = resolve_external_mcp_auth_secret(server)
        initialize_mcp_server(server.server_url, server.auth_type, auth_secret)
        tools = list_mcp_tools(server.server_url, server.auth_type, auth_secret)
        server.tool_manifest_json = {"tools": tools}
        server.status = "active"
        server.last_sync_error = ""
        server.last_sync_at = datetime.now(timezone.utc)
    except Exception as exc:
        server.status = "error"
        server.last_sync_error = str(exc)
        server.last_sync_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(server)
        raise ValueError(str(exc)) from exc
    db.commit()
    db.refresh(server)
    return server


def list_external_mcp_tools(server: ExternalMcpServer) -> list[dict[str, Any]]:
    manifest = server.tool_manifest_json if isinstance(server.tool_manifest_json, dict) else {}
    tools = manifest.get("tools", [])
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, dict) and str(tool.get("name") or "").strip()]


def get_external_mcp_tool(server: ExternalMcpServer, tool_name: str) -> dict[str, Any] | None:
    for tool in list_external_mcp_tools(server):
        if str(tool.get("name") or "").strip() == tool_name:
            return tool
    return None


def resolve_external_mcp_auth_secret(server: ExternalMcpServer) -> str:
    if not server.encrypted_auth_secret:
        return ""
    return decrypt_secret(server.encrypted_auth_secret)


def resolve_agent_mcp_tool_runtime_specs(
    db: Session,
    owner_user_id: str,
    agent_node: dict[str, Any],
) -> list[dict[str, Any]]:
    runtime_specs: list[dict[str, Any]] = []
    for tool in normalize_agent_tools(agent_node.get("tools", [])):
        if tool["type"] != "mcp" or not bool(tool.get("enabled", True)):
            continue
        config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        server_id = str(config.get("server_id") or "").strip()
        if not server_id:
            raise ValueError(f"MCP tool {tool['name']} requires config.server_id.")
        server = get_external_mcp_server(db, server_id, owner_user_id)
        if not server:
            raise ValueError(f"External MCP server not found: {server_id}")
        manifest_tool = get_external_mcp_tool(server, tool["name"])
        if not manifest_tool:
            raise ValueError(f"MCP tool not found on external server {server.name}: {tool['name']}")
        runtime_specs.append(
            {
                "type": "mcp",
                "server_id": server.id,
                "server_name": server.name,
                "server_url": server.server_url,
                "auth_type": server.auth_type,
                "auth_secret": resolve_external_mcp_auth_secret(server),
                "name": str(manifest_tool.get("name") or tool["name"]),
                "description": str(manifest_tool.get("description") or ""),
                "input_schema": manifest_tool.get("input_schema")
                if isinstance(manifest_tool.get("input_schema"), dict)
                else {"type": "object", "properties": {}},
            }
        )
    return runtime_specs


def external_mcp_server_to_out(server: ExternalMcpServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "owner_user_id": server.owner_user_id,
        "name": server.name,
        "description": server.description,
        "transport_type": server.transport_type,
        "server_url": server.server_url,
        "auth_type": server.auth_type,
        "has_auth_secret": bool(server.encrypted_auth_secret),
        "status": server.status,
        "last_sync_at": server.last_sync_at,
        "last_sync_error": server.last_sync_error,
        "tool_manifest_json": server.tool_manifest_json,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
    }


def _normalize_transport_type(value: str) -> str:
    transport_type = str(value or "streamable_http").strip().lower()
    if transport_type not in SUPPORTED_TRANSPORT_TYPES:
        raise ValueError(f"Unsupported MCP transport type: {transport_type or 'empty'}")
    return transport_type


def _normalize_auth_type(value: str) -> str:
    auth_type = str(value or "none").strip().lower()
    if auth_type not in SUPPORTED_AUTH_TYPES:
        raise ValueError(f"Unsupported MCP auth type: {auth_type or 'empty'}")
    return auth_type


def _encrypt_optional_secret(value: str | None) -> str:
    secret = str(value or "").strip()
    if not secret:
        return ""
    return encrypt_secret(secret)
