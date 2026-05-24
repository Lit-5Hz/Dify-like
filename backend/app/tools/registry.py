from __future__ import annotations

import ast
import json
import operator
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str


TOOL_DEFINITIONS = [
    ToolDefinition(
        name="calculator",
        label="Calculator",
        description="计算简单算式。",
    ),
    ToolDefinition(
        name="current_time",
        label="Current Time",
        description="返回服务器当前时间。",
    ),
    ToolDefinition(
        name="query_order",
        label="Mock Order Query",
        description="按订单号查询 mock 电商订单状态。",
    ),
    ToolDefinition(
        name="mock_weather",
        label="Mock Weather",
        description="返回城市的 mock 天气。",
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
    trace_sink: Callable[[dict[str, Any]], None] | None = None,
):
    # 函数作用：把本平台里启用的工具名，包装成 AgentScope 可以注册和调用的 Python 函数。
    # builders 中的函数为每个工具提供明确函数签名，这样 AgentScope 能从函数签名和 docstring 推断出更准确的工具 schema。
    from agentscope.tool import Toolkit

    toolkit = Toolkit()
    builders = {
        "calculator": _build_calculator_tool,
        "current_time": _build_current_time_tool,
        "query_order": _build_query_order_tool,
        "mock_weather": _build_mock_weather_tool,
    }

    for tool_name in dict.fromkeys(enabled_tools):
        if tool_name not in _TOOL_NAMES:
            continue
        tool_builder = builders.get(tool_name)
        if tool_builder:
            # register_tool_function() 会把 Python 函数解析成 AgentScope 能理解的工具对象，再存进 toolkit.tools。
            toolkit.register_tool_function(tool_builder(trace_sink))

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


def _build_calculator_tool(trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def calculator(expression: str):
        """计算简单算式。

        Args:
            expression (str): 要计算的算式，例如 "1 + 2 * 3"。
        """
        arguments = {"expression": expression}
        return _run_agentscope_tool("calculator", arguments, trace_sink)

    return calculator


def _build_current_time_tool(trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def current_time():
        """返回服务器当前时间。"""
        return _run_agentscope_tool("current_time", {}, trace_sink)

    return current_time


def _build_query_order_tool(trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def query_order(order_id: str):
        """查询 mock 电商订单状态。

        Args:
            order_id (str): 订单号，例如 "10086"。
        """
        arguments = {"order_id": order_id}
        return _run_agentscope_tool("query_order", arguments, trace_sink)

    return query_order


def _build_mock_weather_tool(trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def mock_weather(city: str = "上海"):
        """查询 mock 天气。

        Args:
            city (str): 城市名称，例如 "上海"。
        """
        arguments = {"city": city}
        return _run_agentscope_tool("mock_weather", arguments, trace_sink)

    return mock_weather


def _run_agentscope_tool(
    tool_name: str,
    arguments: dict[str, Any],
    trace_sink: Callable[[dict[str, Any]], None] | None,
):
    # 这个函数是 AgentScope 工具函数和本项目工具系统之间的桥。
    # AgentScope 调用工具函数 -> 这里调用 run_tool() -> 再把结果包装回 AgentScope 的 ToolResponse。
    from agentscope.message import TextBlock
    from agentscope.tool import ToolResponse

    started = perf_counter()
    output = run_tool(tool_name, arguments)
    event = {
        "type": "tool_call",
        "name": tool_name,
        "input": arguments,
        "output": output,
        "source": "agentscope",
        "latency_ms": int((perf_counter() - started) * 1000),
    }
    if trace_sink:
        # trace_sink 来自 AgentScopeAdapter.run() 里的 tool_events.append。
        # 这样 agent 内部工具调用也能被前端 Logs 看到，而不是只存在于 AgentScope 内部。
        trace_sink(event)
    # 把本项目 dict 格式的工具结果转成 AgentScope 认识的工具响应格式：
    # 先包成 TextBlock，再包成 ToolResponse，返回给 AgentScope agent。
    return ToolResponse(content=[TextBlock(type="text", text=_serialize_tool_output(output))])


def _serialize_tool_output(output: Any) -> str:
    return json.dumps(output, ensure_ascii=False, default=str)
