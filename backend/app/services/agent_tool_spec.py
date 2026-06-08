from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.tools.registry import list_tools


SUPPORTED_AGENT_TOOL_TYPES = {"builtin", "mcp"}


def normalize_agent_tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    tools: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue

        tool_type = str(item.get("type") or "").strip()
        name = str(item.get("name") or "").strip()
        enabled = bool(item.get("enabled", True))
        raw_config = item.get("config")
        config = deepcopy(raw_config) if isinstance(raw_config, dict) else {}
        server_id = str(config.get("server_id") or "").strip()

        key = (tool_type, name, server_id)
        if key in seen:
            continue
        seen.add(key)
        tools.append(
            {
                "type": tool_type,
                "name": name,
                "enabled": enabled,
                "config": config,
            }
        )
    return tools


def validate_workflow_agent_tools(workflow_spec: dict[str, Any]) -> None:
    if "tools" in workflow_spec:
        raise ValueError("Workflow-level tools are not supported. Configure tools on agent nodes with agent.tools.")

    known_builtin_tools = {tool.name for tool in list_tools()}
    for node in _agent_nodes(workflow_spec):
        node_id = str(node.get("id") or "agent")
        if "tool_names" in node:
            raise ValueError(f"Agent node {node_id} uses legacy tool_names. Configure tools with agent.tools.")

        raw_tools = node.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError(f"Agent node {node_id} tools must be a list.")
        for tool in raw_tools:
            if not isinstance(tool, dict):
                raise ValueError(f"Agent node {node_id} tools must be objects.")
            tool_type = str(tool.get("type") or "").strip()
            name = str(tool.get("name") or "").strip()
            if tool_type not in SUPPORTED_AGENT_TOOL_TYPES:
                raise ValueError(f"Unsupported agent tool type: {tool_type or 'empty'}")
            if tool_type == "builtin" and name not in known_builtin_tools:
                raise ValueError(f"Unknown builtin tool: {name or 'empty'}")
            if tool_type == "mcp":
                config = tool.get("config")
                if not isinstance(config, dict):
                    raise ValueError(f"MCP tool config is required on agent node {node_id}.")
                server_id = str(config.get("server_id") or "").strip()
                if not server_id:
                    raise ValueError(f"MCP tool {name or 'empty'} on agent node {node_id} requires config.server_id.")


def resolve_agent_enabled_builtin_tool_names(agent_node: dict[str, Any]) -> list[str]:
    return [
        tool["name"]
        for tool in normalize_agent_tools(agent_node.get("tools", []))
        if tool["type"] == "builtin" and bool(tool.get("enabled", True))
    ]


def resolve_agent_enabled_mcp_tools(agent_node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        tool
        for tool in normalize_agent_tools(agent_node.get("tools", []))
        if tool["type"] == "mcp" and bool(tool.get("enabled", True))
    ]


def _agent_nodes(workflow_spec: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow_spec.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict) and _is_agent_node(node)]


def _is_agent_node(node: dict[str, Any]) -> bool:
    return str(node.get("id") or "") == "agent" or str(node.get("type") or "") in {"agent", "react_agent"}
