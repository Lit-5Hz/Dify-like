from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.runtime.agent_adapters import AgentInvocation, RuntimeEvent, build_agent_adapter
from app.services.agent_tool_spec import resolve_agent_enabled_builtin_tool_names
from app.services.external_mcp_server_service import resolve_agent_mcp_tool_runtime_specs
from app.services.model_credential_service import resolve_model_api_key
from app.services.retrieval_defaults import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    DEFAULT_FUSION_CANDIDATE_TOP_K,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_RRF_K,
    DEFAULT_SPARSE_CANDIDATE_TOP_K,
    DEFAULT_SPARSE_MIN_SCORE,
    DEFAULT_SPARSE_STOPWORDS_ENABLED,
    DEFAULT_SPARSE_TOKENIZER,
    DEFAULT_SPARSE_WEIGHTING,
)
from app.services.retrieval_service import retrieve_chunks
from app.services.run_log_service import add_step


@dataclass
class WorkflowResult:
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


class WorkflowExecutor:
    def __init__(self, db: Session, app: Any, workflow_spec: dict[str, Any], run_id: str):
        self.db = db
        self.app = app
        self.workflow_spec = workflow_spec
        self.run_id = run_id
        self.result = WorkflowResult()

    async def execute(
        self,
        query: str,
        conversation_id: str = "",
        user_id: str = "",
        history_messages: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        context: dict[str, Any] = {
            "query": query,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "history_messages": history_messages or [],
        }

        for node in self._ordered_nodes(self.workflow_spec):
            node_type = self._normalize_type(str(node.get("type", "")))
            if node_type == "start":
                for event in self._execute_start(node, context):
                    yield event
            elif node_type == "retrieval":
                for event in self._execute_retrieval(node, context):
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
        if not enabled:
            result = {
                "chunks": [],
                "metadata": {
                    "contract_version": "retrieval.v1",
                    "knowledge_base_ids": [],
                    "retrieval_mode": "disabled",
                    "retrieval_top_k": int(
                        node.get("retrieval_top_k", DEFAULT_RETRIEVAL_TOP_K) or DEFAULT_RETRIEVAL_TOP_K
                    ),
                    "dense_retrieved": 0,
                    "sparse_retrieved": 0,
                    "sparse_top_k": DEFAULT_SPARSE_CANDIDATE_TOP_K,
                    "sparse_min_score": DEFAULT_SPARSE_MIN_SCORE,
                    "sparse_weighting": DEFAULT_SPARSE_WEIGHTING,
                    "sparse_tokenizer": DEFAULT_SPARSE_TOKENIZER,
                    "sparse_stopwords_enabled": DEFAULT_SPARSE_STOPWORDS_ENABLED,
                    "bm25_k1": DEFAULT_BM25_K1,
                    "bm25_b": DEFAULT_BM25_B,
                    "rrf_k": DEFAULT_RRF_K,
                    "fusion_candidate_top_k": DEFAULT_FUSION_CANDIDATE_TOP_K,
                    "total_retrieved": 0,
                    "total_returned": 0,
                    "rerank_enabled": False,
                    "rerank_top_n": int(
                        node.get("retrieval_top_k", DEFAULT_RETRIEVAL_TOP_K) or DEFAULT_RETRIEVAL_TOP_K
                    ),
                    "rerank_provider": "passthrough",
                    "query_enhancement": {},
                    "warnings": [],
                },
            }
        else:
            result = retrieve_chunks(
                db=self.db,
                owner_user_id=getattr(self.app, "owner_user_id", ""),
                query=context["query"],
                retrieval_node=node,
            )

        chunks = result["chunks"]
        metadata = result["metadata"]
        self.result.retrieved_chunks = chunks
        output = {
            "chunks": chunks,
            "enabled": enabled,
            "metadata": metadata,
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
        for warning in metadata.get("warnings", []):
            event = {
                "type": "workflow_warning",
                "node_id": node.get("id", "retrieval"),
                "message": warning,
            }
            add_step(self.db, self.run_id, "workflow_warning", node.get("id", "retrieval"), node, event)
            yield event

    async def _execute_agent(self, node: dict[str, Any], context: dict[str, Any]) -> AsyncIterator[RuntimeEvent]:
        started = perf_counter()
        adapter_name = node.get("adapter")
        model_config = self._resolve_model_config(node)
        provider = model_config.get("provider") or self.app.model_provider
        adapter = build_agent_adapter(adapter_name, provider)
        provider = str(provider or "").strip()
        model_name = str(model_config.get("model_name") or self.app.model_name or "").strip()
        if not provider or provider.lower() == "mock":
            raise ValueError("AgentScope requires a real model provider. Configure the app or agent node model provider.")
        if not model_name or model_name.lower() == "mock-react":
            raise ValueError("AgentScope requires a real model name. Configure the app or agent node model name.")
        credential_id = str(model_config.get("credential_id") or "").strip()
        if not credential_id:
            raise ValueError("Missing model credential. Choose a credential in the app or agent node config.")
        try:
            api_key = resolve_model_api_key(self.db, credential_id, getattr(self.app, "owner_user_id", ""))
        except Exception as exc:
            raise ValueError(str(exc)) from exc

        enabled_tools = resolve_agent_enabled_builtin_tool_names(node)
        enabled_mcp_tools = resolve_agent_mcp_tool_runtime_specs(
            self.db,
            getattr(self.app, "owner_user_id", ""),
            node,
        )
        invocation = AgentInvocation(
            app_name=self.app.name,
            query=context["query"],
            system_prompt=self.app.system_prompt,
            model_provider=provider,
            model_name=model_name,
            model_config=model_config,
            model_credential_id=credential_id,
            api_key=api_key,
            node_config=node,
            enabled_tools=enabled_tools,
            enabled_mcp_tools=enabled_mcp_tools,
            retrieved_chunks=self.result.retrieved_chunks,
            history_messages=context["history_messages"],
        )

        final_answer = ""
        async for event in adapter.run(invocation):
            if event["type"] == "tool_call":
                self.result.tool_calls.append(event)
                step_input = dict(event.get("input", {}))
                if event.get("server_id"):
                    step_input["_mcp"] = {
                        "server_id": event["server_id"],
                        "server_name": event.get("server_name", ""),
                        "registered_name": event.get("registered_name", ""),
                    }
                add_step(
                    self.db,
                    self.run_id,
                    "tool_call",
                    event["name"],
                    step_input,
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
                "enabled_tools": enabled_tools,
                "enabled_mcp_tools": [
                    {
                        "server_id": tool.get("server_id", ""),
                        "server_name": tool.get("server_name", ""),
                        "name": tool.get("name", ""),
                    }
                    for tool in enabled_mcp_tools
                ],
            },
            {
                "answer": final_answer,
                "tool_calls": self.result.tool_calls,
                "context": invocation.context_metadata,
            },
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
        nodes = {node["id"]: node for node in spec.get("nodes", []) if isinstance(node, dict) and "id" in node}
        if not nodes:
            return [
                {"id": "start", "type": "start"},
                {
                    "id": "retrieval",
                    "type": "retrieval",
                    "enabled": True,
                    "retrieval_top_k": DEFAULT_RETRIEVAL_TOP_K,
                },
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
        if node_type in {"agent", "react_agent"}:
            return "agent"
        return node_type

    def _resolve_model_config(self, node: dict[str, Any]) -> dict[str, Any]:
        node_model = node.get("model") if isinstance(node.get("model"), dict) else {}
        app_model = {
            "provider": getattr(self.app, "model_provider", ""),
            "model_name": getattr(self.app, "model_name", ""),
            "credential_id": getattr(self.app, "model_credential_id", ""),
            "base_url": getattr(self.app, "model_base_url", ""),
            "temperature": getattr(self.app, "temperature", None),
            "top_p": getattr(self.app, "top_p", None),
            "max_tokens": getattr(self.app, "max_tokens", None),
        }
        node_overrides = {
            key: value
            for key, value in node_model.items()
            if key != "api_key" and value not in (None, "")
        }
        return {**app_model, **node_overrides}
