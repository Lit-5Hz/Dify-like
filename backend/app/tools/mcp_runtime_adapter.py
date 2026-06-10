from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from time import perf_counter
from typing import Any

from app.mcp.client import call_mcp_tool


def register_mcp_tools(
    toolkit: Any,
    mcp_tools: Iterable[dict[str, Any]],
    used_names: set[str],
    trace_sink: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    for spec in mcp_tools:
        if not isinstance(spec, dict):
            continue
        original_name = str(spec.get("name") or "").strip()
        server_id = str(spec.get("server_id") or "").strip()
        if not original_name or not server_id:
            continue
        registered_name = _pick_registered_tool_name(original_name, server_id, used_names)
        used_names.add(registered_name)

        def _build_tool(_spec=spec):
            """
            What AgentScope needs is a callable function.
            However, the remote MCP tool is not a local function, it is just an HTTP JSON-RPC tool.
            So a function is dynamically created here.
            """
            async def _tool(**kwargs):
                from agentscope.message import TextBlock
                from agentscope.tool import ToolResponse

                started = perf_counter()
                try:
                    output = await call_mcp_tool( # Actually call the remote MCP tool
                        str(_spec.get("server_url") or ""),
                        str(_spec.get("auth_type") or "none"),
                        str(_spec.get("name") or ""),
                        kwargs,
                        str(_spec.get("auth_secret") or ""),
                    )
                except Exception as exc:
                    output = {"error": str(exc)}
                event = { # Record trace
                    "type": "tool_call",
                    "name": str(_spec.get("name") or registered_name),
                    "registered_name": registered_name,
                    "server_id": str(_spec.get("server_id") or ""),
                    "server_name": str(_spec.get("server_name") or ""),
                    "input": kwargs,
                    "output": output,
                    "source": "mcp",
                    "latency_ms": int((perf_counter() - started) * 1000),
                }
                if trace_sink:
                    trace_sink(event)
                # AgentScope doesn't directly recognize Python dict, so it should be packaged as the ToolResponse it knows.
                return ToolResponse(content=[TextBlock(type="text", text=_serialize_tool_output(output))])

            _tool.__name__ = registered_name
            return _tool

        description = _tool_description(spec)
        toolkit.register_tool_function(
            _build_tool(),
            func_name=registered_name,
            func_description=description,
            json_schema={
                "type": "function",
                "function": {
                    "name": registered_name,
                    "description": description,
                    "parameters": _normalize_parameters_schema(spec.get("input_schema")),
                },
            },
        )


def _pick_registered_tool_name(original_name: str, server_id: str, used_names: set[str]) -> str:
    if original_name not in used_names:
        return original_name
    base = _safe_tool_name(original_name)
    suffix = _safe_tool_name(server_id)[:8] or "mcp"
    candidate = f"{base}_{suffix}"
    if candidate not in used_names:
        return candidate
    index = 2
    while f"{candidate}_{index}" in used_names:
        index += 1
    return f"{candidate}_{index}"


def _tool_description(spec: dict[str, Any]) -> str:
    description = str(spec.get("description") or "").strip()
    server_name = str(spec.get("server_name") or "").strip()
    if description:
        return description
    if server_name:
        return f"MCP tool from {server_name}."
    return "MCP tool."


def _normalize_parameters_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"type": "object", "properties": {}}


def _safe_tool_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return text or "mcp_tool"


def _serialize_tool_output(output: Any) -> str:
    if isinstance(output, dict):
        content = output.get("content")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text_parts.append(str(item["text"]))
            if text_parts:
                return "\n".join(text_parts)
        structured = output.get("structuredContent")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, default=str)
    return json.dumps(output, ensure_ascii=False, default=str)
