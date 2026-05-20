from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.runtime.agent_adapters import AgentInvocation, RuntimeEvent, build_agent_adapter
from app.services.rag_service import retrieve_chunks
from app.services.model_credential_service import resolve_model_api_key
from app.services.run_log_service import add_step
from app.tools.registry import run_tool


@dataclass
class WorkflowResult:
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


class WorkflowExecutor:
    def __init__(self, db: Session, app: Any, run_id: str):
        self.db = db
        self.app = app
        self.run_id = run_id
        self.result = WorkflowResult()

    async def execute(self, query: str, enabled_tools: list[str]) -> AsyncIterator[RuntimeEvent]:
        context: dict[str, Any] = {
            "query": query,
            "enabled_tools": enabled_tools,
        }

        for node in self._ordered_nodes(self.app.workflow_spec):
            node_type = self._normalize_type(node.get("type", ""))
            if node_type == "start":
                for event in self._execute_start(node, context):
                    yield event
            elif node_type == "retrieval":
                for event in self._execute_retrieval(node, context):
                    yield event
            elif node_type == "tool":
                for event in self._execute_tool(node, context):
                    yield event
            elif node_type == "agent":
                async for event in self._execute_agent(node, context):
                    yield event
            elif node_type == "end":
                for event in self._execute_end(node, context):
                    yield event
            else:
                event = {
                    "type": "workflow_warning",
                    "node_id": node.get("id"),
                    "message": f"Unsupported node type: {node_type}",
                }
                add_step(self.db, self.run_id, "workflow_warning", node.get("id", "unknown"), node, event)
                yield event

    def _execute_start(self, node: dict[str, Any], context: dict[str, Any]):
        output = {"query": context["query"]}
        add_step(self.db, self.run_id, "start", node.get("id", "start"), node, output)
        yield {"type": "workflow_node", "node_id": node.get("id"), "node_type": "start", "output": output}

    def _execute_retrieval(self, node: dict[str, Any], context: dict[str, Any]):
        started = perf_counter()
        enabled = bool(node.get("enabled", True))
        chunks = retrieve_chunks(self.db, self.app.id, context["query"], limit=int(node.get("top_k", 3))) if enabled else []
        self.result.retrieved_chunks = chunks
        output = {
            "chunks": chunks,
            "enabled": enabled,
        }
        add_step(
            self.db,
            self.run_id,
            "retrieval",
            node.get("id", "retrieval"),
            {"query": context["query"], "node": node},
            output,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        yield {"type": "retrieval", **output}

    def _execute_tool(self, node: dict[str, Any], context: dict[str, Any]):
        started = perf_counter()
        tool_name = node.get("tool_name") or node.get("name")
        tool_input = node.get("input", {})
        output = run_tool(tool_name, tool_input)
        event = {
            "type": "tool_call",
            "name": tool_name,
            "input": tool_input,
            "output": output,
            "node_id": node.get("id"),
            "source": "workflow",
        }
        self.result.tool_calls.append(event)
        add_step(
            self.db,
            self.run_id,
            "tool_call",
            tool_name or node.get("id", "tool"),
            {"node": node, "context": {"query": context["query"]}},
            output,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        yield event

    async def _execute_agent(self, node: dict[str, Any], context: dict[str, Any]) -> AsyncIterator[RuntimeEvent]:
        # 记录 agent 节点开始时间，后面写 run step 时用来计算整体耗时
        started = perf_counter()
        # 读取节点级 adapter 配置；如果节点没有显式声明，就由模型 provider 决定走 mock 还是 AgentScope
        adapter_name = node.get("adapter")
        # 合并 App 级模型配置和节点级 model 覆盖配置。节点里的 model 优先级更高，方便后面做多模型 workflow。
        model_config = self._resolve_model_config(node)
        # 决定使用哪个 agent adapter：mock provider 继续走 MockAgentAdapter，真实 provider 默认走 AgentScopeAdapter。
        provider = model_config.get("provider") or self.app.model_provider
        adapter = build_agent_adapter(adapter_name, provider)
        # 构造 AgentInvocation, agent runtime 的输入包。
        # 作用：WorkflowExecutor 把 workflow 前面准备好的 query、工具列表、检索片段、模型配置打包，然后交给 agent adapter。
        credential_id = str(model_config.get("credential_id") or "").strip()
        api_key = ""
        if adapter.name == "agentscope":
            if not credential_id:
                raise ValueError("Missing model credential. Choose a credential in the app or agent node config.")
            try:
                api_key = resolve_model_api_key(self.db, credential_id, getattr(self.app, "owner_user_id", ""))
            except Exception as exc:
                raise ValueError(str(exc)) from exc
        invocation = AgentInvocation(
            app_name=self.app.name,
            query=context["query"],
            system_prompt=self.app.system_prompt,
            model_provider=provider,
            model_name=model_config.get("model_name") or self.app.model_name,
            model_config=model_config,
            model_credential_id=credential_id,
            api_key=api_key,
            node_config=node,
            enabled_tools=context["enabled_tools"],
            retrieved_chunks=self.result.retrieved_chunks,  # retrieval 节点检索到的片段在这里传给 agent
        )

        final_answer = ""
        # 跑 adapter，并处理 adapter 持续吐出的统一事件。
        # 这里不关心底层是 mock 还是 AgentScope，只处理 tool_call / final / adapter_error 等 RuntimeEvent。
        async for event in adapter.run(invocation):
            if event["type"] == "tool_call":
                self.result.tool_calls.append(event)
                add_step(
                    self.db,
                    self.run_id,
                    "tool_call",
                    event["name"],
                    event.get("input", {}),
                    event.get("output", {}),
                )
            elif event["type"] == "final":
                final_answer = str(event.get("content", ""))
                self.result.answer = final_answer
            elif event["type"] == "adapter_error":
                add_step(
                    self.db,
                    self.run_id,
                    "error",
                    "agent_adapter",
                    {"node": node},
                    event,
                    error=str(event.get("message", "")),
                )
                yield event
                return
            yield event

        # 最后，写一个 agent step，表示这个 agent 节点整体跑完了。
        # input 里保留 adapter 和模型配置摘要，方便在 Logs 里回看当时实际用了哪个模型入口。
        add_step(
            self.db,
            self.run_id,
            "agent",
            node.get("id", "agent"),
            {
                "adapter": adapter.name,
                "model_provider": invocation.model_provider,
                "model_name": invocation.model_name,
                "model_credential_id": invocation.model_credential_id,
                "model_base_url": model_config.get("base_url", ""),
            },
            {"answer": final_answer, "tool_calls": self.result.tool_calls},
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def _execute_end(self, node: dict[str, Any], context: dict[str, Any]):
        output = {
            "answer": self.result.answer,
            "tool_calls": self.result.tool_calls,
            "retrieved_chunks": self.result.retrieved_chunks,
        }
        add_step(self.db, self.run_id, "end", node.get("id", "end"), {"query": context["query"]}, output)
        yield {"type": "workflow_node", "node_id": node.get("id"), "node_type": "end", "output": output}

    def _ordered_nodes(self, workflow_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        spec = workflow_spec or {}
        nodes = {node["id"]: node for node in spec.get("nodes", []) if "id" in node}
        if not nodes:
            return [
                {"id": "start", "type": "start"},
                {"id": "retrieval", "type": "retrieval", "enabled": True, "top_k": 3},
                {"id": "agent", "type": "react_agent", "model": {}},
                {"id": "end", "type": "end"},
            ]

        edges = spec.get("edges", [])
        next_by_source: dict[str, str] = {}
        for edge in edges:
            if isinstance(edge, list) and len(edge) == 2:
                next_by_source[edge[0]] = edge[1]
            elif isinstance(edge, dict) and edge.get("from") and edge.get("to"):
                next_by_source[edge["from"]] = edge["to"]

        current = "start" if "start" in nodes else next(iter(nodes))
        ordered = []
        seen = set()
        while current in nodes and current not in seen:
            seen.add(current)
            ordered.append(nodes[current])
            current = next_by_source.get(current, "")

        return ordered

    def _normalize_type(self, node_type: str) -> str:
        # 它不是在“完整标准化所有节点类型”，而是在处理当前唯一的同义词。
        # 其他类型要么已经是标准名，要么本来就该被当成未知节点。
        if node_type in {"agent", "react_agent"}:
            return "agent"
        return node_type

    def _resolve_model_config(self, node: dict[str, Any]) -> dict[str, Any]:
        # node.model 是节点级覆盖项；app_model 是应用级默认项。
        # 例如整个 App 默认用 DeepSeek，但某个 agent 节点可以临时覆盖成 Qwen。
        node_model = node.get("model") if isinstance(node.get("model"), dict) else {}
        app_model = {
            "provider": getattr(self.app, "model_provider", "mock"),
            "model_name": getattr(self.app, "model_name", "mock-react"),
            "credential_id": getattr(self.app, "model_credential_id", ""),
            "base_url": getattr(self.app, "model_base_url", ""),
            "temperature": getattr(self.app, "temperature", None),
            "top_p": getattr(self.app, "top_p", None),
            "max_tokens": getattr(self.app, "max_tokens", None),
        }
        # 只让 node 里“真正填写了值”的字段覆盖 App 级默认值，避免空字符串把默认配置冲掉。
        node_overrides = {
            key: value
            for key, value in node_model.items()
            if key != "api_key" and value not in (None, "")
        }
        merged = {**app_model, **node_overrides}
        return merged
