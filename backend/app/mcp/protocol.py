from __future__ import annotations

from typing import Any


MCP_PROTOCOL_VERSION = "2025-06-18"

JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

MCP_AUTH_ERROR = -32001
MCP_WORKFLOW_NOT_PUBLISHED = -32002
MCP_TOOL_EXECUTION_ERROR = -32003


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


def parse_jsonrpc_request(payload: Any) -> tuple[Any, str, dict[str, Any]]:
    """
    JSON-RPC: Function call protocol expressed by JSON
    RPC = Remote Procedure Call
    """
    if not isinstance(payload, dict):
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "Invalid JSON-RPC request body.")
    if payload.get("jsonrpc") != "2.0":
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "JSON-RPC version must be 2.0.")

    method = str(payload.get("method") or "").strip()
    if not method:
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "JSON-RPC method is required.")

    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "JSON-RPC params must be an object.")

    return payload.get("id"), method, params


def build_jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def build_jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def extract_jsonrpc_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid JSON-RPC response body.")
    if payload.get("jsonrpc") != "2.0":
        raise ValueError("Invalid JSON-RPC response version.")
    if "error" in payload and payload["error"]:
        error = payload["error"]
        if isinstance(error, dict):
            message = str(error.get("message") or "JSON-RPC error")
        else:
            message = str(error)
        raise ValueError(message)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("JSON-RPC result must be an object.")
    return result

