from __future__ import annotations

import secrets
from typing import Any

import httpx

from app.mcp.protocol import MCP_PROTOCOL_VERSION, extract_jsonrpc_result


DEFAULT_MCP_TIMEOUT = 15.0


def build_jsonrpc_id() -> str:
    return secrets.token_hex(8)


async def initialize_mcp_server(server_url: str, auth_type: str, auth_secret: str = "") -> dict[str, Any]:
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
    )


async def list_mcp_tools(server_url: str, auth_type: str, auth_secret: str = "") -> list[dict[str, Any]]:
    """
    Ask the remote MCP Server:
        What tools do you have?
        What is the name of each tool?
        What is the description?
        What is input schema?
    """
    result = await _post_jsonrpc(
        server_url,
        auth_type,
        auth_secret,
        {
            "jsonrpc": "2.0",
            "id": build_jsonrpc_id(),
            "method": "tools/list",
            "params": {},
        },
    )
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
) -> dict[str, Any]:
    return await _post_jsonrpc(
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
    )


async def _post_jsonrpc(
    server_url: str,
    auth_type: str,
    auth_secret: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Unified "send JSON-RPC request" function on MCP Client side."""
    headers = _build_auth_headers(auth_type, auth_secret)
    try:
        # Send asynchronous HTTP requests
        async with httpx.AsyncClient(timeout=DEFAULT_MCP_TIMEOUT) as client:
            response = await client.post(server_url, json=payload, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/event-stream" in content_type:
                raise ValueError("MCP SSE responses are not supported yet.")
            body = response.json()
    except httpx.HTTPStatusError as exc:
        response_text = exc.response.text if exc.response is not None else ""
        raise ValueError(f"MCP request failed with HTTP {exc.response.status_code}: {response_text}") from exc
    except Exception as exc:
        raise ValueError(f"MCP request failed: {exc}") from exc
    return extract_jsonrpc_result(body)


def _build_auth_headers(auth_type: str, auth_secret: str) -> dict[str, str]:
    normalized = str(auth_type or "none").strip().lower()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    if normalized == "none":
        return headers
    if normalized == "bearer":
        token = str(auth_secret or "").strip()
        if not token:
            raise ValueError("Missing bearer token for external MCP server.")
        return {**headers, "Authorization": f"Bearer {token}"}
    raise ValueError(f"Unsupported MCP auth type: {auth_type}")


def _normalize_input_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"type": "object", "properties": {}}
