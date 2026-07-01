from __future__ import annotations

import ast
import json
import operator
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.tools.mcp_runtime_adapter import register_mcp_tools


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str


TOOL_DEFINITIONS = [
    ToolDefinition(
        name="calculator",
        label="Calculator",
        description="Calculate simple arithmetic expressions.",
    ),
    ToolDefinition(
        name="current_time",
        label="Current Time",
        description="Return the current server time.",
    ),
    ToolDefinition(
        name="query_order",
        label="Mock Order Query",
        description="Look up the status of a mock ecommerce order.",
    ),
    ToolDefinition(
        name="mock_weather",
        label="Mock Weather",
        description="Return mock weather for a city.",
    ),
]

_TOOL_NAMES = {tool.name for tool in TOOL_DEFINITIONS}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def list_tools() -> list[ToolDefinition]:
    return TOOL_DEFINITIONS


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "calculator":
        expression = str(arguments.get("expression", "")).strip()
        if not expression:
            return {"error": "Expression is required."}
        try:
            return {"result": _safe_eval_expression(expression)}
        except Exception as exc:
            return {"error": str(exc)}

    if name == "current_time":
        return {"result": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}

    if name == "query_order":
        order_id = str(arguments.get("order_id", "")).strip()
        orders = {
            "10086": "订单 10086 已发货，当前在上海转运中心，预计明天送达。",
            "10010": "订单 10010 待出库，预计今晚发货。",
            "12345": "订单 12345 已签收，签收人为本人。",
        }
        return {"order_id": order_id, "status": orders.get(order_id, "未找到该订单。")}

    if name == "mock_weather":
        city = str(arguments.get("city", "上海")).strip() or "上海"
        return {"city": city, "weather": f"{city} 今天多云，气温 22-27 摄氏度。"}

    return {"error": f"Unknown tool: {name}"}


def build_agentscope_toolkit(
    enabled_tools: Iterable[str],
    mcp_tools: Iterable[dict[str, Any]] | None = None,
):
    from agentscope.tool import Toolkit

    toolkit = Toolkit()
    builders = {
        "calculator": _build_calculator_tool,
        "current_time": _build_current_time_tool,
        "query_order": _build_query_order_tool,
        "mock_weather": _build_mock_weather_tool,
    }
    used_names: set[str] = set()

    for tool_name in dict.fromkeys(enabled_tools):
        if tool_name not in _TOOL_NAMES:
            continue
        tool_builder = builders.get(tool_name)
        if not tool_builder:
            continue
        toolkit.register_tool_function(tool_builder())
        used_names.add(tool_name)

    register_mcp_tools(toolkit, mcp_tools or [], used_names)
    return toolkit


def _safe_eval_expression(expression: str) -> int | float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Only simple arithmetic expressions are allowed.") from exc

    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](evaluate(node.operand))
        raise ValueError("Only simple arithmetic expressions are allowed.")

    return evaluate(tree.body)


def _build_calculator_tool():
    def calculator(expression: str):
        arguments = {"expression": expression}
        return _run_agentscope_tool("calculator", arguments)

    return calculator


def _build_current_time_tool():
    def current_time():
        return _run_agentscope_tool("current_time", {})

    return current_time


def _build_query_order_tool():
    def query_order(order_id: str):
        arguments = {"order_id": order_id}
        return _run_agentscope_tool("query_order", arguments)

    return query_order


def _build_mock_weather_tool():
    def mock_weather(city: str = "上海"):
        arguments = {"city": city}
        return _run_agentscope_tool("mock_weather", arguments)

    return mock_weather


def _run_agentscope_tool(
    tool_name: str,
    arguments: dict[str, Any],
):
    from agentscope.message import TextBlock
    from agentscope.tool import ToolResponse

    output = run_tool(tool_name, arguments)
    return ToolResponse(content=[TextBlock(type="text", text=_serialize_tool_output(output))])


def _serialize_tool_output(output: Any) -> str:
    return json.dumps(output, ensure_ascii=False, default=str)
