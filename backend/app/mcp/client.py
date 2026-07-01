from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.mcp.protocol import MCP_PROTOCOL_VERSION, extract_jsonrpc_result


DEFAULT_MCP_TIMEOUT = 15.0
MCP_SESSION_ID_HEADER = "Mcp-Session-Id"


@dataclass(frozen=True)
class McpJsonRpcResponse:
    result: dict[str, Any]
    session_id: str = ""


def build_jsonrpc_id() -> str:
    return secrets.token_hex(8)


async def initialize_mcp_server(
    server_url: str,
    auth_type: str,
    auth_secret: str = "",
    custom_headers: dict[str, str] | None = None,
) -> McpJsonRpcResponse:
    return await _post_jsonrpc(
        server_url,
        auth_type,
        auth_secret,
        {
            "jsonrpc": "2.0",
            "id": build_jsonrpc_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {
                    "name": "dify-like-backend",
                    "version": "0.1.0",
                },
            },
        },
        custom_headers=custom_headers,
    )


async def list_mcp_tools(
    server_url: str,
    auth_type: str,
    auth_secret: str = "",
    custom_headers: dict[str, str] | None = None,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """
    Ask the remote MCP Server:
        What tools do you have?
        What is the name of each tool?
        What is the description?
        What is input schema?
    """
    response = await _post_jsonrpc(
        server_url,
        auth_type,
        auth_secret,
        {
            "jsonrpc": "2.0",
            "id": build_jsonrpc_id(),
            "method": "tools/list",
            "params": {},
        },
        custom_headers=custom_headers,
        session_id=session_id,
    )
    result = response.result
    tools = result.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("MCP tools/list result must include a tools array.")

    normalized: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "input_schema": _normalize_input_schema(item.get("inputSchema", item.get("input_schema"))),
            }
        )
    return normalized


async def call_mcp_tool(
    server_url: str,
    auth_type: str,
    tool_name: str,
    arguments: dict[str, Any],
    auth_secret: str = "",
    custom_headers: dict[str, str] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    response = await _post_jsonrpc(
        server_url,
        auth_type,
        auth_secret,
        {
            "jsonrpc": "2.0",
            "id": build_jsonrpc_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        },
        custom_headers=custom_headers,
        session_id=session_id,
        timeout_seconds=get_settings().mcp_tool_timeout_seconds,
    )
    return response.result


async def _post_jsonrpc(
    server_url: str,
    auth_type: str,
    auth_secret: str,
    payload: dict[str, Any],
    custom_headers: dict[str, str] | None = None,
    session_id: str = "",
    timeout_seconds: float = DEFAULT_MCP_TIMEOUT,
) -> McpJsonRpcResponse:
    """Unified "send JSON-RPC request" function on MCP Client side."""
    headers = _build_auth_headers(auth_type, auth_secret, custom_headers, session_id)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(server_url, json=payload, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/event-stream" in content_type:
                body = _parse_sse_jsonrpc_response(response.text, payload.get("id"))
            else:
                body = response.json()
    except httpx.HTTPStatusError as exc:
        response_text = exc.response.text if exc.response is not None else ""
        raise ValueError(f"MCP request failed with HTTP {exc.response.status_code}: {response_text}") from exc
    except httpx.TimeoutException as exc:
        raise ValueError(
            f"MCP request timed out after {timeout_seconds:g}s ({type(exc).__name__})."
        ) from exc
    except Exception as exc:
        detail = str(exc).strip()
        message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        raise ValueError(f"MCP request failed: {message}") from exc
    return McpJsonRpcResponse(
        result=extract_jsonrpc_result(body),
        session_id=_extract_session_id(response.headers),
    )


def _build_auth_headers(
    auth_type: str,
    auth_secret: str,
    custom_headers: dict[str, str] | None = None,
    session_id: str = "",
) -> dict[str, str]:
    normalized = str(auth_type or "none").strip().lower()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    if normalized == "none":
        pass
    elif normalized == "bearer":
        token = str(auth_secret or "").strip()
        if not token:
            raise ValueError("Missing bearer token for external MCP server.")
        headers["Authorization"] = f"Bearer {token}"
    else:
        raise ValueError(f"Unsupported MCP auth type: {auth_type}")
    if session_id:
        headers[MCP_SESSION_ID_HEADER] = session_id
    if custom_headers:
        headers.update(custom_headers)
    return headers


def _parse_sse_jsonrpc_response(text: str, request_id: Any) -> dict[str, Any]:
    for event_data in _iter_sse_data_blocks(text):
        if not event_data or event_data == "[DONE]":
            continue
        try:
            payload = json.loads(event_data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("id") == request_id and ("result" in payload or "error" in payload):
            return payload
    raise ValueError("MCP SSE response did not include a matching JSON-RPC response.")


def _iter_sse_data_blocks(text: str):
    data_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        yield "\n".join(data_lines)


def _extract_session_id(headers: httpx.Headers) -> str:
    return str(headers.get(MCP_SESSION_ID_HEADER) or "").strip()


def is_session_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "mcp-session-id" in message
        or "session id" in message
        or "invalid session" in message
        or "session not found" in message
        or "missing session" in message
        or "session expired" in message
        or "session terminated" in message
    )


def is_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "http 401" in message
        or "http 403" in message
        or "unauthorized" in message
        or "forbidden" in message
        or "invalid token" in message
        or "expired token" in message
        or "invalid_token" in message
    )


def _normalize_input_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"type": "object", "properties": {}}
