from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credential_crypto import decrypt_secret, encrypt_secret
from app.db.models import App, ExternalMcpServer, Workflow
from app.mcp.client import call_mcp_tool, initialize_mcp_server, is_session_error, list_mcp_tools
from app.schemas import ExternalMcpServerCreate, ExternalMcpServerUpdate
from app.services.agent_tool_spec import normalize_agent_tools

"""
SUPPORTED_TRANSPORT_TYPES:
    Transport_type describes the "communication mode".
    Now the first edition only supports streamable_http. That is, sending JSON-RPC requests through HTTP POST.
    If it is expanded in the future, there may be:
        Stdio = standard input/output communication through local process.
        Sse = receive streaming messages through Server-Sent Events.
        Websocket = communicate through WebSocket.

SUPPORTED_AUTH_TYPES:
    Auth_type describes the "authentication method".
    Now support:
        None = external MCP Server is called without authentication header.
        Bearer = call external MCP Server with Authorization header.
"""
SUPPORTED_TRANSPORT_TYPES = {"streamable_http"}
SUPPORTED_AUTH_TYPES = {"none", "bearer"}
RESERVED_CUSTOM_HEADER_NAMES = {
    "accept",
    "content-length",
    "content-type",
    "host",
    "mcp-protocol-version",
}
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


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
    custom_headers = normalize_custom_headers(payload.custom_headers, auth_type)

    server = ExternalMcpServer(
        owner_user_id=owner_user_id,
        name=name,
        description=payload.description.strip(),
        transport_type=transport_type,
        server_url=server_url,
        auth_type=auth_type,
        encrypted_auth_secret=_encrypt_optional_secret(payload.auth_secret),
        encrypted_headers_json=_encrypt_headers_json(custom_headers),
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
    if "custom_headers" in values and values["custom_headers"] is not None:
        server.encrypted_headers_json = _encrypt_headers_json(
            normalize_custom_headers(values["custom_headers"], server.auth_type)
        )
        needs_resync = True
    if server.auth_type == "none":
        server.encrypted_auth_secret = ""
    if server.auth_type == "bearer" and not server.encrypted_auth_secret:
        raise ValueError("Bearer auth requires an auth secret.")
    normalize_custom_headers(resolve_external_mcp_custom_headers(server), server.auth_type)
    if needs_resync:
        server.status = "pending_sync"
        server.last_sync_at = None
        server.last_sync_error = ""
        server.tool_manifest_json = {"tools": []}
        server.mcp_session_id = ""
    db.commit()
    db.refresh(server)
    return server


def delete_external_mcp_server(db: Session, server: ExternalMcpServer) -> int:
    removed_references = remove_external_mcp_server_from_workflow_drafts(db, server.owner_user_id, server.id)
    db.delete(server)
    db.commit()
    return removed_references


def remove_external_mcp_server_from_workflow_drafts(db: Session, owner_user_id: str, server_id: str) -> int:
    removed_references = 0
    rows = db.scalars(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(App.owner_user_id == owner_user_id)
    )
    for workflow in rows:
        next_spec, removed = _remove_external_mcp_server_from_spec(workflow.draft_spec, server_id)
        if removed:
            workflow.draft_spec = next_spec
            removed_references += removed
    return removed_references


async def refresh_external_mcp_server_tool_manifest(db: Session, server: ExternalMcpServer) -> ExternalMcpServer:
    """
    Initialize the remote MCP server, refresh tools/list, and persist sync status.
    """
    try:
        auth_secret = resolve_external_mcp_auth_secret(server)
        custom_headers = resolve_external_mcp_custom_headers(server)
        init_response = await initialize_mcp_server(server.server_url, server.auth_type, auth_secret, custom_headers)
        server.mcp_session_id = init_response.session_id
        tools = await list_mcp_tools(
            server.server_url,
            server.auth_type,
            auth_secret,
            custom_headers,
            server.mcp_session_id,
        )
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


def resolve_external_mcp_custom_headers(server: ExternalMcpServer) -> dict[str, str]:
    encrypted = str(server.encrypted_headers_json or "").strip()
    if not encrypted:
        return {}
    try:
        decoded = decrypt_secret(encrypted)
        parsed = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Unable to decrypt stored MCP custom headers.") from exc
    return normalize_custom_headers(parsed, server.auth_type)


async def call_external_mcp_tool_with_session_retry(
    db: Session,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    server = db.get(ExternalMcpServer, server_id)
    if not server:
        raise ValueError(f"External MCP server not found: {server_id}")

    auth_secret = resolve_external_mcp_auth_secret(server)
    custom_headers = resolve_external_mcp_custom_headers(server)
    try:
        return await call_mcp_tool(
            server.server_url,
            server.auth_type,
            tool_name,
            arguments,
            auth_secret,
            custom_headers,
            server.mcp_session_id,
        )
    except ValueError as exc:
        if not is_session_error(exc):
            raise

    init_response = await initialize_mcp_server(server.server_url, server.auth_type, auth_secret, custom_headers)
    server.mcp_session_id = init_response.session_id
    db.commit()
    db.refresh(server)
    return await call_mcp_tool(
        server.server_url,
        server.auth_type,
        tool_name,
        arguments,
        auth_secret,
        custom_headers,
        server.mcp_session_id,
    )


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
                "custom_headers": resolve_external_mcp_custom_headers(server),
                "session_id": server.mcp_session_id,
                "db": db,
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
        "has_custom_headers": bool(server.encrypted_headers_json),
        "custom_header_names": list(resolve_external_mcp_custom_headers(server).keys()),
        "has_mcp_session": bool(server.mcp_session_id),
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


def normalize_custom_headers(value: Any, auth_type: str = "none") -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError("MCP custom headers must be an object.")
    headers: dict[str, str] = {}
    seen: set[str] = set()
    normalized_auth_type = str(auth_type or "none").strip().lower()
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        if not name:
            raise ValueError("MCP custom header name is required.")
        lower_name = name.lower()
        if lower_name in seen:
            raise ValueError(f"Duplicate MCP custom header: {name}")
        if not HEADER_NAME_PATTERN.match(name):
            raise ValueError(f"Invalid MCP custom header name: {name}")
        if lower_name in RESERVED_CUSTOM_HEADER_NAMES:
            raise ValueError(f"MCP custom header is reserved: {name}")
        if normalized_auth_type == "bearer" and lower_name == "authorization":
            raise ValueError("Authorization custom header is not allowed when bearer auth is enabled.")
        if not isinstance(raw_value, str):
            raise ValueError(f"MCP custom header value must be a string: {name}")
        headers[name] = raw_value.strip()
        seen.add(lower_name)
    return headers


def _encrypt_headers_json(headers: dict[str, str]) -> str:
    if not headers:
        return ""
    return encrypt_secret(json.dumps(headers, ensure_ascii=False, sort_keys=True))


def _remove_external_mcp_server_from_spec(workflow_spec: Any, server_id: str) -> tuple[dict[str, Any], int]:
    if not isinstance(workflow_spec, dict):
        return {}, 0
    nodes = workflow_spec.get("nodes")
    if not isinstance(nodes, list):
        return dict(workflow_spec), 0

    removed = 0
    next_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            next_nodes.append(node)
            continue
        tools = node.get("tools")
        if not isinstance(tools, list):
            next_nodes.append(node)
            continue

        next_tools: list[Any] = []
        for tool in tools:
            if _tool_references_external_mcp_server(tool, server_id):
                removed += 1
                continue
            next_tools.append(tool)

        next_node = dict(node)
        next_node["tools"] = next_tools
        next_nodes.append(next_node)

    if not removed:
        return dict(workflow_spec), 0
    next_spec = dict(workflow_spec)
    next_spec["nodes"] = next_nodes
    return next_spec, removed


def _tool_references_external_mcp_server(tool: Any, server_id: str) -> bool:
    if not isinstance(tool, dict):
        return False
    if str(tool.get("type") or "").strip() != "mcp":
        return False
    config = tool.get("config")
    if not isinstance(config, dict):
        return False
    return str(config.get("server_id") or "").strip() == server_id
