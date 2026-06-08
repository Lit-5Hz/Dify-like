from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.mcp.protocol import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    MCP_PROTOCOL_VERSION,
    MCP_TOOL_EXECUTION_ERROR,
    MCP_WORKFLOW_NOT_PUBLISHED,
    JsonRpcError,
    build_jsonrpc_error,
    build_jsonrpc_result,
    parse_jsonrpc_request,
)
from app.services.chat_service import chat_once
from app.services.workflow_mcp_server_service import (
    get_public_workflow_mcp_server_by_slug,
    verify_workflow_mcp_server_token,
)


RUN_WORKFLOW_TOOL_NAME = "run_workflow"


async def handle_workflow_mcp_request(
    db: Session,
    server_slug: str,
    authorization: str | None,
    payload: Any,
) -> tuple[int, dict[str, Any]]:
    request_id = payload.get("id") if isinstance(payload, dict) else None
    try:
        request_id, method, params = parse_jsonrpc_request(payload)
    except JsonRpcError as exc:
        return 200, build_jsonrpc_error(request_id, exc.code, exc.message, exc.data)

    resolved = get_public_workflow_mcp_server_by_slug(db, server_slug)
    if not resolved:
        return 404, {"detail": "MCP server not found"}
    workflow_server, workflow, app = resolved
    if not workflow_server.enabled:
        return 404, {"detail": "MCP server not found"}
    if not verify_workflow_mcp_server_token(workflow_server, authorization):
        return 401, {"detail": "Invalid MCP bearer token"}

    try:
        if method == "initialize":
            return 200, build_jsonrpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": workflow_server.server_name,
                        "version": "0.1.0",
                    },
                },
            )

        if method == "tools/list":
            return 200, build_jsonrpc_result(
                request_id,
                {
                    "tools": [
                        {
                            "name": RUN_WORKFLOW_TOOL_NAME,
                            "description": workflow_server.description
                            or f"Run the published workflow '{workflow.name}'.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                    "conversation_id": {"type": "string"},
                                },
                                "required": ["query"],
                            },
                        }
                    ]
                },
            )

        if method == "tools/call":
            return 200, await _handle_tools_call(db, request_id, workflow_server.server_slug, workflow, app, params)

        return 200, build_jsonrpc_error(request_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
    except JsonRpcError as exc:
        return 200, build_jsonrpc_error(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:
        return 200, build_jsonrpc_error(request_id, JSONRPC_INTERNAL_ERROR, str(exc))


async def _handle_tools_call(
    db: Session,
    request_id: Any,
    server_slug: str,
    workflow: Any,
    app: Any,
    params: dict[str, Any],
) -> dict[str, Any]:
    tool_name = str(params.get("name") or "").strip()
    arguments = params.get("arguments", {})
    if tool_name != RUN_WORKFLOW_TOOL_NAME:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unsupported tool name: {tool_name or 'empty'}")
    if not isinstance(arguments, dict):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call arguments must be an object.")

    query = str(arguments.get("query") or "").strip()
    if not query:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call requires a non-empty query.")

    published_version = workflow.published_version
    if not published_version:
        raise JsonRpcError(MCP_WORKFLOW_NOT_PUBLISHED, "Workflow is not published.")

    conversation_id = str(arguments.get("conversation_id") or "").strip() or None
    mcp_user_id = f"mcp:{server_slug}"
    try:
        result = await chat_once(
            db,
            app,
            workflow,
            published_version,
            query,
            mcp_user_id,
            conversation_id,
        )
    except ValueError as exc:
        raise JsonRpcError(MCP_TOOL_EXECUTION_ERROR, str(exc)) from exc

    structured = {
        "answer": result["answer"],
        "conversation_id": result["conversation_id"],
        "run_id": result["run_id"],
        "workflow_id": workflow.id,
        "workflow_version_id": published_version.id,
        "tool_calls": result["tool_calls"],
        "retrieved_chunks": result["retrieved_chunks"],
    }
    return build_jsonrpc_result(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": structured["answer"],
                }
            ],
            "structuredContent": structured,
            "isError": False,
        },
    )
