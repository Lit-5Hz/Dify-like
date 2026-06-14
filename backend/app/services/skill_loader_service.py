from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import PlatformSkill
from app.services.platform_skill_search_service import (
    DEFAULT_TOP_K,
    SkillSearchHit,
    record_skill_usage,
    search_visible_skills,
)
from app.services.platform_skill_service import (
    load_skill_manifest,
    load_skill_reference,
    load_skill_rules,
    load_skill_tool_policy,
    load_skill_workflow_template,
)


@dataclass
class LoadedSkill:
    skill: PlatformSkill
    manifest: dict[str, Any]
    loaded_files: list[str]
    load_stages: list[str]
    score: float
    explicit: bool
    match_summary: str
    policy: dict[str, Any] | None = None
    rules_excerpt: str = ""
    workflow_template: dict[str, Any] | None = None
    loaded_references: dict[str, str] = field(default_factory=dict)
    deferred_references: list[str] = field(default_factory=list)


def load_skills_progressively(
    db: Session,
    owner_user_id: str,
    query: str,
    explicit_skill_ids: list[str] | None = None,
    assistant_session_id: str = "",
    load_workflow_template: bool = True,
    reference_requests: dict[str, list[str]] | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[LoadedSkill]:
    hits = search_visible_skills(
        db,
        owner_user_id=owner_user_id,
        query=query,
        explicit_skill_ids=explicit_skill_ids or [],
        top_k=top_k,
    )
    loaded: list[LoadedSkill] = []
    for index, hit in enumerate(hits):
        manifest = _safe_manifest(hit.skill)
        item = LoadedSkill(
            skill=hit.skill,
            manifest=manifest,
            loaded_files=["skill.yaml"],
            load_stages=["manifest"],
            score=hit.score,
            explicit=hit.explicit,
            match_summary=hit.match_summary,
            deferred_references=_manifest_reference_names(manifest),
        )
        record_skill_usage(
            db,
            hit.skill,
            owner_user_id,
            "manifest",
            assistant_session_id,
            {"score": hit.score, "explicit": hit.explicit, "rank": index + 1},
        )

        _load_strategy_stage(db, owner_user_id, assistant_session_id, hit, item)
        if load_workflow_template and index == 0:
            template = load_skill_workflow_template(hit.skill)
            if template:
                item.workflow_template = template
                item.loaded_files.append("workflow_template.json")
                item.load_stages.append("template")
                record_skill_usage(
                    db,
                    hit.skill,
                    owner_user_id,
                    "template",
                    assistant_session_id,
                    {"score": hit.score, "rank": index + 1},
                )
        _load_requested_references(db, owner_user_id, assistant_session_id, hit, item, reference_requests or {})
        loaded.append(item)
    return loaded


def _load_strategy_stage(
    db: Session,
    owner_user_id: str,
    assistant_session_id: str,
    hit: SkillSearchHit,
    item: LoadedSkill,
) -> None:
    item.policy = load_skill_tool_policy(hit.skill)
    rules = load_skill_rules(hit.skill)
    item.rules_excerpt = rules[:2000]
    item.loaded_files.extend(["tool_policy.yaml", "rules.md"])
    item.load_stages.append("rules")
    record_skill_usage(
        db,
        hit.skill,
        owner_user_id,
        "rules",
        assistant_session_id,
        {"score": hit.score, "explicit": hit.explicit},
    )


def _load_requested_references(
    db: Session,
    owner_user_id: str,
    assistant_session_id: str,
    hit: SkillSearchHit,
    item: LoadedSkill,
    reference_requests: dict[str, list[str]],
) -> None:
    requests = reference_requests.get(hit.skill.id, []) + reference_requests.get(hit.skill.name, [])
    unique_requests: list[str] = []
    for request in requests:
        if request and request not in unique_requests:
            unique_requests.append(request)
    for relative_path in unique_requests[:5]:
        content = load_skill_reference(hit.skill, relative_path)
        item.loaded_references[relative_path] = content
        item.loaded_files.append(relative_path if relative_path.startswith("references/") else f"references/{relative_path}")
    if unique_requests:
        item.load_stages.append("references")
        record_skill_usage(
            db,
            hit.skill,
            owner_user_id,
            "references",
            assistant_session_id,
            {"references": unique_requests[:5]},
        )


def _safe_manifest(skill: PlatformSkill) -> dict[str, Any]:
    manifest = load_skill_manifest(skill)
    return manifest if isinstance(manifest, dict) else {}


def _manifest_reference_names(manifest: dict[str, Any]) -> list[str]:
    references = manifest.get("references", [])
    if not isinstance(references, list):
        return []
    names = []
    for item in references:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and item.get("path"):
            names.append(str(item["path"]).strip())
    return names[:20]
