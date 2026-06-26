from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import App, KnowledgeBase, PlatformAssistantSession, PlatformSkill, Workflow, WorkflowVersion
from app.schemas import (
    AppCreate,
    AssistantLoadedSkill,
    AssistantWorkflowRecommendation,
    PlatformAssistantApplyRequest,
    PlatformAssistantApplyResponse,
    PlatformAssistantChatRequest,
    PlatformAssistantChatResponse,
    PlatformAssistantMessageOut,
    WorkflowUpdate,
)
from app.services.app_service import create_app, normalize_workflow_spec
from app.services.platform_skill_service import PLATFORM_ASSISTANT_ACTIONS, validate_workflow_draft_structure
from app.services.skill_loader_service import load_skills_progressively
from app.services.workflow_service import _validate_workflow_spec, update_workflow


def chat_with_platform_assistant(
    db: Session,
    owner_user_id: str,
    payload: PlatformAssistantChatRequest,
) -> PlatformAssistantChatResponse:
    session = _get_or_create_session(db, owner_user_id, payload.conversation_id)
    query = payload.message.strip()
    messages = _session_messages(session)
    messages.append({"role": "user", "content": query})

    current_draft = _session_draft(session)
    selected_skills = _load_requested_skills(db, owner_user_id, query, payload.skill_ids, session.id)
    owned_knowledge_bases = _list_owned_knowledge_bases(db, owner_user_id)

    if _is_confirmation(query) and current_draft.get("draft_spec"):
        created = _create_from_session_draft(db, owner_user_id, session, current_draft)
        answer = (
            f"已创建 app「{created.app.name}」，并写入 workflow「{created.workflow.name}」。\n\n"
            "你可以进入工作室继续编辑、发布或运行这个 workflow。"
        )
        messages.append({"role": "assistant", "content": answer})
        session.messages_json = messages
        session.status = "applied"
        session.created_app_id = created.app.id
        session.created_workflow_id = created.workflow.id
        db.commit()
        db.refresh(session)
        return _response(
            session=session,
            answer=answer,
            messages=messages,
            selected_skills=selected_skills,
            recommendations=[],
            draft=current_draft,
            created=created,
            model_status="local_action",
        )

    recommendations = recommend_published_workflows(db, query)
    base_template = (
        _draft_spec_from_draft(current_draft)
        or _select_template_from_skills(selected_skills)
        or _select_template_from_recommendations(db, recommendations)
        or _default_assistant_workflow()
    )
    base_template = validate_workflow_draft_structure(base_template)
    llm_plan = _call_platform_assistant_model(
        query,
        messages,
        recommendations,
        selected_skills,
        current_draft,
        base_template,
        owned_knowledge_bases,
    )
    model_status = str(llm_plan.pop("_status", "fallback"))
    model_message = str(llm_plan.pop("_message", ""))

    draft_spec = _draft_from_model(llm_plan, base_template)
    draft_spec, kb_note = _apply_knowledge_base_request(query, draft_spec, owned_knowledge_bases)
    explanation = explain_workflow_draft(draft_spec)
    fallback_answer = _build_consultant_answer(query, recommendations, selected_skills, explanation)
    answer = str(llm_plan.get("answer") or fallback_answer)
    if kb_note:
        answer = f"{answer}\n\n{kb_note}"
    if "确认" not in answer and "创建" not in answer:
        answer = f"{answer}\n\n如果这个草稿方向正确，可以继续告诉我修改点，或者直接说“确认创建”。"

    draft = {
        "app_name": str(llm_plan.get("app_name") or current_draft.get("app_name") or _suggest_name(query, "Workflow App"))[:120],
        "app_description": str(
            llm_plan.get("app_description")
            or current_draft.get("app_description")
            or "由平台助手根据对话草拟的 workflow app。"
        ),
        "workflow_name": str(
            llm_plan.get("workflow_name") or current_draft.get("workflow_name") or _suggest_name(query, "Assisted Workflow")
        )[:120],
        "workflow_description": str(
            llm_plan.get("workflow_description")
            or current_draft.get("workflow_description")
            or "由平台助手根据对话生成的 workflow draft。"
        ),
        "draft_spec": draft_spec,
        "explanation": explanation,
    }
    messages.append({"role": "assistant", "content": answer})
    session.messages_json = messages
    session.draft_json = draft
    session.status = "drafting"
    session.metadata_json = {
        "model_status": model_status,
        "model_message": model_message,
        "skill_ids": [skill.id for skill, _ in selected_skills],
        "recommendation_count": len(recommendations),
    }
    db.commit()
    db.refresh(session)

    return _response(
        session=session,
        answer=answer,
        messages=messages,
        selected_skills=selected_skills,
        recommendations=recommendations,
        draft=draft,
        model_status=model_status,
        model_message=model_message,
    )


def apply_platform_assistant_plan(
    db: Session,
    owner_user_id: str,
    payload: PlatformAssistantApplyRequest,
) -> PlatformAssistantApplyResponse:
    draft = {
        "app_name": payload.app_name,
        "app_description": payload.app_description,
        "workflow_name": payload.workflow_name,
        "workflow_description": payload.workflow_description,
        "draft_spec": payload.draft_spec,
    }
    return _create_from_session_draft(db, owner_user_id, None, draft)


def update_owned_workflow_draft_from_assistant(
    db: Session,
    owner_user_id: str,
    workflow_id: str,
    draft_spec: dict[str, Any],
) -> Workflow:
    workflow = db.scalar(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(Workflow.id == workflow_id, App.owner_user_id == owner_user_id)
    )
    if not workflow:
        raise ValueError("Workflow not found")
    app = db.get(App, workflow.app_id)
    if not app:
        raise ValueError("App not found")
    normalized = validate_workflow_draft_structure(draft_spec)
    _validate_workflow_spec(db, app, normalized)
    return update_workflow(db, workflow, WorkflowUpdate(draft_spec=normalized))


def recommend_published_workflows(db: Session, query: str, limit: int = 5) -> list[AssistantWorkflowRecommendation]:
    pattern = f"%{query.strip()}%" if query.strip() else "%"
    rows = db.execute(
        select(Workflow, App, WorkflowVersion)
        .join(App, App.id == Workflow.app_id)
        .join(WorkflowVersion, WorkflowVersion.id == Workflow.published_version_id)
        .where(
            Workflow.published_version_id.is_not(None),
            or_(Workflow.name.ilike(pattern), Workflow.description.ilike(pattern), App.name.ilike(pattern)),
        )
        .order_by(Workflow.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        AssistantWorkflowRecommendation(
            workflow_id=workflow.id,
            app_id=app.id,
            app_name=app.name,
            workflow_name=workflow.name,
            description=workflow.description or app.description,
            version_id=version.id,
        )
        for workflow, app, version in rows
    ]


def _list_owned_knowledge_bases(db: Session, owner_user_id: str) -> list[KnowledgeBase]:
    return list(
        db.scalars(
            select(KnowledgeBase)
            .where(KnowledgeBase.owner_user_id == owner_user_id, KnowledgeBase.scope == "creator")
            .order_by(KnowledgeBase.updated_at.desc())
        )
    )


def explain_workflow_draft(draft_spec: dict[str, Any]) -> dict[str, Any]:
    spec = validate_workflow_draft_structure(draft_spec)
    nodes = [node for node in spec.get("nodes", []) if isinstance(node, dict)]
    edges = spec.get("edges", []) if isinstance(spec.get("edges"), list) else []
    node_map = {str(node.get("id")): node for node in nodes}
    node_explanations = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        node_explanations.append(
            {
                "id": node_id,
                "type": node_type,
                "summary": _node_summary(node),
            }
        )

    branch_explanations = []
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        source, target = _edge_pair(edge)
        if source and target:
            outgoing.setdefault(source, []).append(target)
            branch_explanations.append(
                {
                    "from": source,
                    "to": target,
                    "meaning": f"{_node_label(node_map.get(source))} 完成后进入 {_node_label(node_map.get(target))}。",
                }
            )
    decision_branches = [
        {
            "node_id": source,
            "targets": targets,
            "meaning": f"节点 {source} 有 {len(targets)} 个后续分支，需要在运行时根据条件或节点输出选择。",
        }
        for source, targets in outgoing.items()
        if len(targets) > 1
    ]
    return {
        "summary": f"当前草稿包含 {len(nodes)} 个节点、{len(branch_explanations)} 条连线。",
        "nodes": node_explanations,
        "branches": branch_explanations,
        "decision_branches": decision_branches,
    }


def _create_from_session_draft(
    db: Session,
    owner_user_id: str,
    session: PlatformAssistantSession | None,
    draft: dict[str, Any],
) -> PlatformAssistantApplyResponse:
    draft_spec = validate_workflow_draft_structure(draft.get("draft_spec") or {})
    app_name = str(draft.get("app_name") or "Workflow App").strip()[:120]
    workflow_name = str(draft.get("workflow_name") or "Assisted Workflow").strip()[:120]
    validation_app = App(
        owner_user_id=owner_user_id,
        name=app_name,
        description=str(draft.get("app_description") or ""),
        system_prompt="",
        model_provider="deepseek",
        model_name="deepseek-v4-pro",
        model_credential_id="",
        model_base_url="https://api.deepseek.com/v1",
    )
    _validate_workflow_spec(db, validation_app, draft_spec)
    app = create_app(
        db,
        AppCreate(
            name=app_name,
            description=str(draft.get("app_description") or ""),
            system_prompt="You are a workflow app created by the platform assistant.",
            model_provider="deepseek",
            model_name="deepseek-v4-pro",
            model_credential_id="",
            model_base_url="https://api.deepseek.com/v1",
            temperature=70,
            top_p=100,
            max_tokens=1024,
        ),
        owner_user_id,
    )
    workflow = db.scalar(select(Workflow).where(Workflow.app_id == app.id).order_by(Workflow.created_at.asc()))
    if not workflow:
        raise ValueError("Default workflow was not created")
    workflow = update_workflow(
        db,
        workflow,
        WorkflowUpdate(
            name=workflow_name,
            description=str(draft.get("workflow_description") or ""),
            draft_spec=draft_spec,
        ),
    )
    if session:
        session.created_app_id = app.id
        session.created_workflow_id = workflow.id
    return PlatformAssistantApplyResponse(app=app, workflow=workflow)


def _response(
    session: PlatformAssistantSession,
    answer: str,
    messages: list[dict[str, str]],
    selected_skills: list[tuple[PlatformSkill, dict[str, Any]]],
    recommendations: list[AssistantWorkflowRecommendation],
    draft: dict[str, Any],
    created: PlatformAssistantApplyResponse | None = None,
    model_status: str = "fallback",
    model_message: str = "",
) -> PlatformAssistantChatResponse:
    return PlatformAssistantChatResponse(
        conversation_id=session.id,
        answer=answer,
        messages=[PlatformAssistantMessageOut(role=item["role"], content=item["content"]) for item in messages],
        recommendations=recommendations,
        loaded_skills=[
            AssistantLoadedSkill(
                skill_id=skill.id,
                name=str(loaded["manifest"].get("name") or skill.name),
                version=str(loaded["manifest"].get("version") or skill.version),
                visibility=skill.visibility,
                loaded_files=loaded["loaded_files"],
                load_stages=loaded.get("load_stages", []),
                summary=str(loaded["manifest"].get("description") or skill.description),
                score=float(loaded.get("score") or 0.0),
                match_summary=str(loaded.get("match_summary") or ""),
                deferred_references=loaded.get("deferred_references", []),
                loaded_references=list((loaded.get("loaded_references") or {}).keys()),
            )
            for skill, loaded in selected_skills
        ],
        load_stages=[
            {
                "skill_id": skill.id,
                "name": skill.name,
                "visibility": skill.visibility,
                "stages": loaded.get("load_stages", []),
                "score": loaded.get("score", 0.0),
                "match_summary": loaded.get("match_summary", ""),
            }
            for skill, loaded in selected_skills
        ],
        deferred_references=[
            {"skill_id": skill.id, "name": skill.name, "references": loaded.get("deferred_references", [])}
            for skill, loaded in selected_skills
            if loaded.get("deferred_references")
        ],
        loaded_references=[
            {"skill_id": skill.id, "name": skill.name, "references": list((loaded.get("loaded_references") or {}).keys())}
            for skill, loaded in selected_skills
            if loaded.get("loaded_references")
        ],
        suggested_app={
            "name": draft.get("app_name", ""),
            "description": draft.get("app_description", ""),
        },
        suggested_workflow={
            "name": draft.get("workflow_name", ""),
            "description": draft.get("workflow_description", ""),
            "draft_spec": draft.get("draft_spec", {}),
        },
        draft_explanation=draft.get("explanation") or explain_workflow_draft(draft.get("draft_spec") or {}),
        created_app=created.app if created else None,
        created_workflow=created.workflow if created else None,
        allowed_actions=sorted(PLATFORM_ASSISTANT_ACTIONS),
        model_status=model_status,
        model_message=model_message,
    )


def _get_or_create_session(db: Session, owner_user_id: str, conversation_id: str | None) -> PlatformAssistantSession:
    if conversation_id:
        session = db.scalar(
            select(PlatformAssistantSession).where(
                PlatformAssistantSession.id == conversation_id,
                PlatformAssistantSession.owner_user_id == owner_user_id,
            )
        )
        if session:
            return session
    session = PlatformAssistantSession(
        owner_user_id=owner_user_id,
        status="drafting",
        messages_json=[],
        draft_json={},
        metadata_json={},
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _session_messages(session: PlatformAssistantSession) -> list[dict[str, str]]:
    if not isinstance(session.messages_json, list):
        return []
    messages = []
    for item in session.messages_json:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            messages.append({"role": str(item["role"]), "content": str(item.get("content") or "")})
    return messages[-20:]


def _session_draft(session: PlatformAssistantSession) -> dict[str, Any]:
    return deepcopy(session.draft_json) if isinstance(session.draft_json, dict) else {}


def _is_confirmation(query: str) -> bool:
    text = query.strip().lower()
    confirmations = ["确认", "可以创建", "创建吧", "就这样", "没问题", "生成 app", "创建 app", "apply", "confirm"]
    return any(item in text for item in confirmations)


def _draft_spec_from_draft(draft: dict[str, Any]) -> dict[str, Any] | None:
    spec = draft.get("draft_spec")
    return deepcopy(spec) if isinstance(spec, dict) else None


def _draft_from_model(plan: dict[str, Any], base_template: dict[str, Any]) -> dict[str, Any]:
    candidate = plan.get("draft_spec")
    if isinstance(candidate, dict):
        try:
            return validate_workflow_draft_structure(candidate)
        except ValueError:
            pass
    return validate_workflow_draft_structure(base_template)


def _apply_knowledge_base_request(
    query: str,
    draft_spec: dict[str, Any],
    owned_knowledge_bases: list[KnowledgeBase],
) -> tuple[dict[str, Any], str]:
    spec = deepcopy(draft_spec)
    requested = _extract_requested_knowledge_base(query)
    allowed_ids = {kb.id for kb in owned_knowledge_bases}
    matched = _match_knowledge_base(requested, owned_knowledge_bases) if requested else None
    note = ""

    for node in spec.get("nodes", []):
        if not isinstance(node, dict) or str(node.get("type") or "") != "retrieval":
            continue
        raw_ids = node.get("knowledge_base_ids")
        current_ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
        valid_ids = [kb_id for kb_id in current_ids if kb_id in allowed_ids]
        if requested:
            if matched:
                valid_ids = [matched.id]
                note = f"已将检索节点绑定到知识库「{matched.name}」。"
            else:
                candidates = "、".join(f"{kb.name}({kb.id[:8]})" for kb in owned_knowledge_bases[:5]) or "暂无可用知识库"
                note = f"没有找到与你输入的「{requested}」匹配的私有知识库。请使用真实知识库名称或 ID；当前可选：{candidates}。"
        node["knowledge_base_ids"] = valid_ids
        break
    return validate_workflow_draft_structure(spec), note


def _extract_requested_knowledge_base(query: str) -> str:
    text = query.strip()
    markers = ["知识库", "知識庫", "knowledge base", "kb"]
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker.lower())
        if index < 0:
            continue
        value = text[index + len(marker) :].strip(" ：:，,。.;；\"'")
        return value.split()[0].strip(" ：:，,。.;；\"'") if value else ""
    return ""


def _match_knowledge_base(requested: str, owned_knowledge_bases: list[KnowledgeBase]) -> KnowledgeBase | None:
    value = requested.strip().lower()
    if not value:
        return None
    for kb in owned_knowledge_bases:
        if kb.id.lower() == value or kb.id.lower().startswith(value):
            return kb
    for kb in owned_knowledge_bases:
        if kb.name.strip().lower() == value or value in kb.name.strip().lower():
            return kb
    return None


def _load_requested_skills(
    db: Session,
    owner_user_id: str,
    query: str,
    skill_ids: list[str],
    assistant_session_id: str,
) -> list[tuple[PlatformSkill, dict[str, Any]]]:
    results = load_skills_progressively(
        db,
        owner_user_id=owner_user_id,
        query=query,
        explicit_skill_ids=skill_ids,
        assistant_session_id=assistant_session_id,
        load_workflow_template=True,
    )
    return [
        (
            item.skill,
            {
                "manifest": item.manifest,
                "policy": item.policy or {},
                "rules_excerpt": item.rules_excerpt,
                "workflow_template": item.workflow_template,
                "loaded_files": item.loaded_files,
                "load_stages": item.load_stages,
                "score": item.score,
                "match_summary": item.match_summary,
                "deferred_references": item.deferred_references,
                "loaded_references": item.loaded_references,
            },
        )
        for item in results
    ]


def _select_template_from_skills(loaded_skills: list[tuple[PlatformSkill, dict[str, Any]]]) -> dict[str, Any] | None:
    for _, loaded in loaded_skills:
        template = loaded.get("workflow_template")
        if isinstance(template, dict):
            return deepcopy(template)
    return None


def _select_template_from_recommendations(
    db: Session,
    recommendations: list[AssistantWorkflowRecommendation],
) -> dict[str, Any] | None:
    if not recommendations:
        return None
    version = db.get(WorkflowVersion, recommendations[0].version_id)
    if not version:
        return None
    return deepcopy(version.spec_json)


def _default_assistant_workflow() -> dict[str, Any]:
    spec = normalize_workflow_spec(None)
    for node in spec.get("nodes", []):
        if isinstance(node, dict) and str(node.get("type") or "") in {"agent", "react_agent"}:
            node["tools"] = []
    return spec


def _build_consultant_answer(
    query: str,
    recommendations: list[AssistantWorkflowRecommendation],
    selected_skills: list[tuple[PlatformSkill, dict[str, Any]]],
    explanation: dict[str, Any],
) -> str:
    parts = [f"我先根据你的描述整理了一个 workflow app 草稿：{query}"]
    if selected_skills:
        parts.append(f"已参考 {len(selected_skills)} 个你的私有 skill。")
    if recommendations:
        names = "、".join(item.workflow_name for item in recommendations[:3])
        parts.append(f"我找到可以参考的已发布 workflow：{names}。")
    else:
        parts.append("暂时没有匹配到已发布 workflow，所以先从最小可编辑草稿开始。")
    parts.append(f"草稿说明：{explanation.get('summary', '')}")
    for node in explanation.get("nodes", [])[:6]:
        parts.append(f"- {node['id']}: {node['summary']}")
    if explanation.get("branches"):
        parts.append("当前连线含义：")
        for branch in explanation["branches"][:6]:
            parts.append(f"- {branch['from']} -> {branch['to']}: {branch['meaning']}")
    parts.append("你可以继续告诉我需要增删哪些节点、调整哪些分支；如果方向正确，直接说“确认创建”。")
    return "\n".join(parts)


def _call_platform_assistant_model(
    query: str,
    messages: list[dict[str, str]],
    recommendations: list[AssistantWorkflowRecommendation],
    selected_skills: list[tuple[PlatformSkill, dict[str, Any]]],
    current_draft: dict[str, Any],
    base_template: dict[str, Any],
    owned_knowledge_bases: list[KnowledgeBase],
) -> dict[str, Any]:
    settings = get_settings()
    base_url = str(settings.platform_assistant_api_base_url or "").strip().rstrip("/")
    api_key = str(settings.platform_assistant_api_key or "").strip()
    model = str(settings.platform_assistant_model or "").strip()
    if not base_url or not api_key or not model:
        return {
            "_status": "not_configured",
            "_message": "Set PLATFORM_ASSISTANT_API_BASE_URL, PLATFORM_ASSISTANT_API_KEY, and PLATFORM_ASSISTANT_MODEL for model-backed dialogue.",
        }

    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    assistant_messages = [
        {
            "role": "system",
            "content": (
                "You are an independent platform assistant for helping users design workflow apps through dialogue. "
                "Do not claim to run workflow agent nodes, MCP tools, or workflow runtime tools. "
                "You may propose or revise a workflow draft, explain nodes and branches, and ask follow-up questions. "
                "Return JSON only with keys: answer, app_name, app_description, workflow_name, "
                "workflow_description, draft_spec. draft_spec is optional; if included it must be a valid workflow JSON."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "current_user_message": query,
                    "conversation_messages": messages[-12:],
                    "current_draft": current_draft,
                    "base_template": base_template,
                    "published_workflow_recommendations": [item.model_dump() for item in recommendations[:5]],
                    "owned_knowledge_bases": [
                        {"id": kb.id, "name": kb.name, "description": kb.description}
                        for kb in owned_knowledge_bases[:20]
                    ],
                    "loaded_skills": [
                        {
                            "name": skill.name,
                            "version": skill.version,
                            "visibility": skill.visibility,
                            "manifest": loaded.get("manifest", {}),
                            "rules_excerpt": loaded.get("rules_excerpt", ""),
                            "loaded_files": loaded.get("loaded_files", []),
                            "load_stages": loaded.get("load_stages", []),
                            "score": loaded.get("score", 0.0),
                            "match_summary": loaded.get("match_summary", ""),
                            "deferred_references": loaded.get("deferred_references", []),
                            "loaded_references": loaded.get("loaded_references", {}),
                        }
                        for skill, loaded in selected_skills[:5]
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        with httpx.Client(timeout=float(settings.platform_assistant_timeout_seconds or 30)) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": assistant_messages,
                    "temperature": float(settings.platform_assistant_temperature),
                },
            )
            response.raise_for_status()
        content = str(response.json()["choices"][0]["message"]["content"])
        parsed = _parse_model_json(content)
        parsed["_status"] = "model"
        return parsed
    except Exception as exc:
        return {"_status": "error", "_message": str(exc)}


def _parse_model_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"answer": content}
    return parsed if isinstance(parsed, dict) else {"answer": content}


def _node_summary(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or "")
    if node_type == "start":
        return "接收用户输入，作为 workflow 的起点。"
    if node_type == "retrieval":
        ids = node.get("knowledge_base_ids") if isinstance(node.get("knowledge_base_ids"), list) else []
        return f"检索节点，运行时会从 {len(ids)} 个知识库取上下文；没有知识库时可先作为占位。"
    if node_type in {"agent", "react_agent"}:
        tools = node.get("tools") if isinstance(node.get("tools"), list) else []
        return f"Agent 节点，负责根据输入和上下文生成回答；当前启用 {len(tools)} 个 workflow 内工具。"
    if node_type == "end":
        return "结束节点，返回最终输出。"
    return f"{node_type} 节点。"


def _node_label(node: dict[str, Any] | None) -> str:
    if not node:
        return "未知节点"
    return f"{node.get('id')}({node.get('type')})"


def _edge_pair(edge: Any) -> tuple[str, str]:
    if isinstance(edge, list) and len(edge) == 2:
        return str(edge[0]).strip(), str(edge[1]).strip()
    if isinstance(edge, dict):
        return str(edge.get("source") or edge.get("from") or "").strip(), str(edge.get("target") or edge.get("to") or "").strip()
    return "", ""


def _suggest_name(query: str, fallback: str) -> str:
    compact = " ".join(query.strip().split())
    if not compact:
        return fallback
    return compact[:80]
