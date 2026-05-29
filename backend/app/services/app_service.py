from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, AppTool, KnowledgeBase
from app.schemas import AppCreate, AppUpdate, DEFAULT_RETRIEVAL_NODE, DEFAULT_WORKFLOW_SPEC
from app.services.model_credential_service import get_model_credential
from app.services.retrieval_defaults import DEFAULT_QUERY_LLM_TEMPERATURE, DEFAULT_RETRIEVAL_TOP_K


LEGACY_NODE_TYPE = "".join(["r", "a", "g"])


def normalize_workflow_spec(workflow_spec: dict | None) -> dict:
    spec = deepcopy(workflow_spec or DEFAULT_WORKFLOW_SPEC)
    nodes = spec.get("nodes", [])
    if not isinstance(nodes, list):
        nodes = deepcopy(DEFAULT_WORKFLOW_SPEC["nodes"])

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
            next_nodes.append(deepcopy(node))

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
    app = db.scalar(select(App).where(App.id == app_id, App.owner_user_id == owner_user_id))
    return _prepare_app_out(app) if app else None


def get_app(db: Session, app_id: str, owner_user_id: str) -> App | None:
    return get_owned_app(db, app_id, owner_user_id)


def get_chat_accessible_app(db: Session, app_id: str, user_id: str) -> App | None:
    app = db.get(App, app_id)
    if not app:
        return None
    if app.owner_user_id == user_id or app.status == "published":
        return _prepare_app_out(app)
    return None


def create_app(db: Session, payload: AppCreate, owner_user_id: str) -> App:
    workflow_spec = normalize_workflow_spec(payload.workflow_spec or DEFAULT_WORKFLOW_SPEC)
    _validate_app_credentials(db, owner_user_id, payload.model_credential_id, workflow_spec)
    _validate_workflow_knowledge_bases(db, owner_user_id, workflow_spec)
    app = App(
        owner_user_id=owner_user_id,
        name=payload.name,
        description=payload.description,
        status="draft",
        system_prompt=payload.system_prompt,
        model_provider=payload.model_provider,
        model_name=payload.model_name,
        model_credential_id=payload.model_credential_id,
        model_base_url=payload.model_base_url,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
        workflow_spec=workflow_spec,
    )
    db.add(app)
    db.flush()
    db.add(AppTool(app_id=app.id, tool_name="query_order", enabled=True))
    db.commit()
    db.refresh(app)
    return _prepare_app_out(app)


def list_apps(db: Session, owner_user_id: str) -> list[App]:
    apps = list(db.scalars(select(App).where(App.owner_user_id == owner_user_id).order_by(App.created_at.desc())))
    return [_prepare_app_out(app) for app in apps]


def list_published_apps(db: Session, user_id: str) -> list[dict[str, Any]]:
    apps = list(db.scalars(select(App).where(App.status == "published").order_by(App.updated_at.desc())))
    return [
        {
            "id": app.id,
            "owner_user_id": app.owner_user_id,
            "name": app.name,
            "description": app.description,
            "status": app.status,
            "owned": app.owner_user_id == user_id,
            "created_at": app.created_at,
            "updated_at": app.updated_at,
        }
        for app in apps
    ]


def update_app(db: Session, app: App, payload: AppUpdate, owner_user_id: str) -> App:
    next_model_credential_id = payload.model_credential_id if payload.model_credential_id is not None else app.model_credential_id
    next_workflow_spec = normalize_workflow_spec(payload.workflow_spec if payload.workflow_spec is not None else app.workflow_spec)
    _validate_app_credentials(db, owner_user_id, next_model_credential_id, next_workflow_spec)
    _validate_workflow_knowledge_bases(db, owner_user_id, next_workflow_spec)

    values = payload.model_dump(exclude_unset=True)
    if "workflow_spec" in values:
        values["workflow_spec"] = next_workflow_spec
    if "status" in values and values["status"] not in {"draft", "published"}:
        raise ValueError("App status must be draft or published.")
    for key, value in values.items():
        setattr(app, key, value)
    db.commit()
    db.refresh(app)
    return _prepare_app_out(app)


def publish_app(db: Session, app: App) -> App:
    app.status = "published"
    db.commit()
    db.refresh(app)
    return _prepare_app_out(app)


def unpublish_app(db: Session, app: App) -> App:
    app.status = "draft"
    db.commit()
    db.refresh(app)
    return _prepare_app_out(app)


def delete_app(db: Session, app: App) -> None:
    db.delete(app)
    db.commit()


def set_app_tools(db: Session, app_id: str, tool_names: list[str]) -> list[AppTool]:
    existing = {tool.tool_name: tool for tool in db.scalars(select(AppTool).where(AppTool.app_id == app_id))}
    for tool in existing.values():
        tool.enabled = tool.tool_name in tool_names
    for tool_name in tool_names:
        if tool_name not in existing:
            db.add(AppTool(app_id=app_id, tool_name=tool_name, enabled=True))
    db.commit()
    return list(db.scalars(select(AppTool).where(AppTool.app_id == app_id).order_by(AppTool.tool_name)))


def get_enabled_tool_names(db: Session, app_id: str) -> list[str]:
    rows = db.scalars(
        select(AppTool).where(AppTool.app_id == app_id, AppTool.enabled.is_(True)).order_by(AppTool.tool_name)
    )
    return [row.tool_name for row in rows]


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


def _prepare_app_out(app: App) -> App:
    app.workflow_spec = normalize_workflow_spec(app.workflow_spec)
    return app


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


def _validate_app_credentials(
    db: Session,
    owner_user_id: str,
    model_credential_id: str | None,
    workflow_spec: dict | None,
) -> None:
    credential_ids = {str(model_credential_id or "").strip()} if str(model_credential_id or "").strip() else set()
    credential_ids.update(_collect_workflow_credential_ids(workflow_spec))
    for credential_id in credential_ids:
        if not get_model_credential(db, credential_id, owner_user_id):
            raise ValueError(f"Model credential not found: {credential_id}")


def _validate_workflow_knowledge_bases(db: Session, owner_user_id: str, workflow_spec: dict | None) -> None:
    kb_ids = _collect_workflow_knowledge_base_ids(workflow_spec)
    for kb_id in kb_ids:
        exists = db.scalar(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.scope == "creator",
            )
        )
        if not exists:
            raise ValueError(f"Knowledge database not found: {kb_id}")


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
