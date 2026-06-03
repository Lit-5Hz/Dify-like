from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, Workflow
from app.schemas import AppCreate, AppUpdate, DEFAULT_RETRIEVAL_NODE, DEFAULT_WORKFLOW_SPEC
from app.services.model_credential_service import get_model_credential
from app.services.retrieval_defaults import DEFAULT_QUERY_LLM_TEMPERATURE, DEFAULT_RETRIEVAL_TOP_K
from app.services.agent_tool_spec import normalize_agent_tools


LEGACY_NODE_TYPE = "".join(["r", "a", "g"])


def normalize_workflow_spec(workflow_spec: dict | None) -> dict:
    is_default_spec = workflow_spec is None or workflow_spec is DEFAULT_WORKFLOW_SPEC
    spec = deepcopy(DEFAULT_WORKFLOW_SPEC if workflow_spec is None else workflow_spec)
    if not isinstance(spec, dict):
        spec = {}
    nodes = spec.get("nodes", [])
    if not isinstance(nodes, list):
        nodes = _default_workflow_nodes(include_default_agent_tools=is_default_spec)

    next_nodes: list[dict[str, Any]] = []
    has_retrieval_node = False
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "").strip()
        node_id = str(node.get("id") or "").strip()
        if node_id in {"retrieval", LEGACY_NODE_TYPE} or node_type in {"retrieval", LEGACY_NODE_TYPE}:
            has_retrieval_node = True
            next_nodes.append(_normalize_retrieval_node(node))
        else:
            next_nodes.append(_normalize_agent_node(node) if _is_agent_node(node) else deepcopy(node))

    if not any(_node_matches(item, "start") for item in next_nodes):
        next_nodes.insert(0, {"id": "start", "type": "start"})
    if not has_retrieval_node:
        insert_at = 1 if next_nodes else 0
        next_nodes.insert(insert_at, deepcopy(DEFAULT_RETRIEVAL_NODE))
        has_retrieval_node = True
    if not any(_node_matches(item, "agent") or _node_matches(item, "react_agent") for item in next_nodes):
        next_nodes.append({"id": "agent", "type": "react_agent", "model": {}})
    if not any(_node_matches(item, "end") for item in next_nodes):
        next_nodes.append({"id": "end", "type": "end"})

    spec["nodes"] = next_nodes
    spec["edges"] = _normalize_retrieval_edges(spec.get("edges", [])) if has_retrieval_node else spec.get("edges", [])
    return spec


def get_retrieval_node(workflow_spec: dict | None) -> dict[str, Any]:
    for node in normalize_workflow_spec(workflow_spec).get("nodes", []):
        if isinstance(node, dict) and str(node.get("type") or "") == "retrieval":
            return node
    return deepcopy(DEFAULT_RETRIEVAL_NODE)


def get_owned_app(db: Session, app_id: str, owner_user_id: str) -> App | None:
    return db.scalar(select(App).where(App.id == app_id, App.owner_user_id == owner_user_id))


def get_app(db: Session, app_id: str, owner_user_id: str) -> App | None:
    return get_owned_app(db, app_id, owner_user_id)


def create_app(db: Session, payload: AppCreate, owner_user_id: str) -> App:
    _validate_app_model_credential(db, owner_user_id, payload.model_credential_id)
    app = App(
        owner_user_id=owner_user_id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model_provider=payload.model_provider,
        model_name=payload.model_name,
        model_credential_id=payload.model_credential_id,
        model_base_url=payload.model_base_url,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
    )
    db.add(app)
    db.flush()
    db.add(
        Workflow(
            app_id=app.id,
            name="Default workflow",
            description="",
            draft_spec=normalize_workflow_spec(DEFAULT_WORKFLOW_SPEC),
        )
    )
    db.commit()
    db.refresh(app)
    return app


def list_apps(db: Session, owner_user_id: str) -> list[App]:
    return list(db.scalars(select(App).where(App.owner_user_id == owner_user_id).order_by(App.created_at.desc())))


def update_app(db: Session, app: App, payload: AppUpdate, owner_user_id: str) -> App:
    next_model_credential_id = payload.model_credential_id if payload.model_credential_id is not None else app.model_credential_id
    _validate_app_model_credential(db, owner_user_id, next_model_credential_id)

    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(app, key, value)
    db.commit()
    db.refresh(app)
    return app


def delete_app(db: Session, app: App) -> None:
    db.delete(app)
    db.commit()


def _default_workflow_nodes(include_default_agent_tools: bool) -> list[dict[str, Any]]:
    nodes = deepcopy(DEFAULT_WORKFLOW_SPEC["nodes"])
    if include_default_agent_tools:
        return nodes
    for node in nodes:
        if isinstance(node, dict) and _is_agent_node(node):
            node["tools"] = []
    return nodes


def _normalize_agent_node(node: dict[str, Any]) -> dict[str, Any]:
    next_node = deepcopy(node)
    raw_tools = next_node.get("tools")
    if isinstance(raw_tools, list) and all(isinstance(tool, dict) for tool in raw_tools):
        next_node["tools"] = normalize_agent_tools(next_node.get("tools", []))
    return next_node


def _normalize_retrieval_node(node: dict[str, Any]) -> dict[str, Any]:
    next_node = {**deepcopy(DEFAULT_RETRIEVAL_NODE), **deepcopy(node)}
    legacy_kb_id = str(next_node.get("kb_id") or "").strip()
    knowledge_base_ids = _normalize_id_list(next_node.get("knowledge_base_ids"))
    if legacy_kb_id and legacy_kb_id not in knowledge_base_ids:
        knowledge_base_ids.append(legacy_kb_id)
    strategy = str(next_node.get("query_enhancement_strategy") or "rewrite").strip().lower()
    return {
        "id": "retrieval",
        "type": "retrieval",
        "enabled": bool(next_node.get("enabled", True)),
        "knowledge_base_ids": knowledge_base_ids,
        "retrieval_top_k": max(_to_int(next_node.get("retrieval_top_k"), DEFAULT_RETRIEVAL_TOP_K), 0),
        "rerank_enabled": bool(next_node.get("rerank_enabled", False)),
        "query_enhancement_enabled": bool(next_node.get("query_enhancement_enabled", strategy != "none")),
        "query_enhancement_strategy": strategy if strategy in {"rewrite", "hyde", "multi_query"} else "rewrite",
        "query_llm_provider": str(next_node.get("query_llm_provider") or ""),
        "query_llm_model": str(next_node.get("query_llm_model") or ""),
        "query_llm_credential_id": str(next_node.get("query_llm_credential_id") or ""),
        "query_llm_base_url": str(next_node.get("query_llm_base_url") or ""),
        "query_llm_temperature": _to_float(
            next_node.get("query_llm_temperature"),
            DEFAULT_QUERY_LLM_TEMPERATURE,
        ),
    }


def _normalize_retrieval_edges(edges: list | object) -> list:
    if not isinstance(edges, list):
        return [["start", "retrieval"], ["retrieval", "agent"], ["agent", "end"]]

    next_edges = []
    for edge in edges:
        if isinstance(edge, list) and len(edge) == 2:
            source, target = _normalize_edge_node(edge[0]), _normalize_edge_node(edge[1])
        elif isinstance(edge, dict):
            source = _normalize_edge_node(edge.get("from", edge.get("source")))
            target = _normalize_edge_node(edge.get("to", edge.get("target")))
        else:
            continue
        if source and target:
            next_edges.append([source, target])
    return next_edges or [["start", "retrieval"], ["retrieval", "agent"], ["agent", "end"]]


def _normalize_edge_node(value: Any) -> str:
    node_id = str(value or "").strip()
    return "retrieval" if node_id == LEGACY_NODE_TYPE else node_id


def _node_matches(node: dict[str, Any], value: str) -> bool:
    return str(node.get("id") or "") == value or str(node.get("type") or "") == value


def _is_agent_node(node: dict[str, Any]) -> bool:
    return str(node.get("id") or "") == "agent" or str(node.get("type") or "") in {"agent", "react_agent"}


def _collect_workflow_credential_ids(workflow_spec: dict | None) -> set[str]:
    if not isinstance(workflow_spec, dict):
        return set()

    credential_ids: set[str] = set()
    nodes = workflow_spec.get("nodes", [])
    if not isinstance(nodes, list):
        return credential_ids

    for node in nodes:
        if not isinstance(node, dict):
            continue
        model = node.get("model")
        if not isinstance(model, dict):
            model = {}
        credential_id = str(model.get("credential_id") or "").strip()
        if credential_id:
            credential_ids.add(credential_id)
        query_llm_credential_id = str(node.get("query_llm_credential_id") or "").strip()
        if query_llm_credential_id:
            credential_ids.add(query_llm_credential_id)
    return credential_ids


def _collect_workflow_knowledge_base_ids(workflow_spec: dict | None) -> set[str]:
    ids: set[str] = set()
    for node in normalize_workflow_spec(workflow_spec).get("nodes", []):
        if not isinstance(node, dict) or str(node.get("type") or "") != "retrieval":
            continue
        ids.update(_normalize_id_list(node.get("knowledge_base_ids")))
    return ids


def _validate_app_model_credential(db: Session, owner_user_id: str, model_credential_id: str | None) -> None:
    credential_id = str(model_credential_id or "").strip()
    if credential_id and not get_model_credential(db, credential_id, owner_user_id):
        raise ValueError(f"Model credential not found: {credential_id}")


def _normalize_id_list(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    elif isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    else:
        candidates = []
    ids: list[str] = []
    for item in candidates:
        if item and item not in ids:
            ids.append(item)
    return ids


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
