from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import App, PlatformSkill, Run, Workflow
from app.schemas import SkillSynthesizeRequest, SkillValidationOut
from app.services.app_service import normalize_workflow_spec
from app.services.run_log_service import get_run_for_owner, list_run_steps
from app.services.workflow_service import _validate_workflow_spec


PLATFORM_ASSISTANT_ACTIONS = {
    "recommend_published_workflows",
    "load_private_skill_manifest",
    "load_private_skill_rules",
    "load_private_skill_tool_policy",
    "load_private_skill_reference",
    "load_private_skill_workflow_template",
    "load_platform_skill_manifest",
    "load_platform_skill_rules",
    "load_platform_skill_tool_policy",
    "load_platform_skill_reference",
    "load_platform_skill_workflow_template",
    "create_app_with_draft_workflow",
    "update_owned_workflow_draft",
    "get_owned_run_trace",
}

REQUIRED_SKILL_FILES = {"skill.yaml", "rules.md", "tool_policy.yaml"}
OPTIONAL_SKILL_DIRS = {"references", "examples", "evals"}
REFERENCE_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}
MAX_REFERENCE_BYTES = 256 * 1024
MAX_REFERENCES_BYTES = 1024 * 1024
MAX_REFERENCE_INJECTION_CHARS = 12000


def list_skills(db: Session, owner_user_id: str) -> list[PlatformSkill]:
    return list(
        db.scalars(
            select(PlatformSkill)
            .where(
                PlatformSkill.owner_user_id == owner_user_id,
                PlatformSkill.visibility == "private",
                PlatformSkill.status == "active",
            )
            .order_by(PlatformSkill.updated_at.desc())
        )
    )


def list_platform_skills(db: Session) -> list[PlatformSkill]:
    return list(
        db.scalars(
            select(PlatformSkill)
            .where(
                PlatformSkill.visibility == "platform",
                PlatformSkill.publish_status == "published",
                PlatformSkill.status == "active",
            )
            .order_by(PlatformSkill.published_at.desc(), PlatformSkill.updated_at.desc())
        )
    )


def list_visible_skills(db: Session, owner_user_id: str) -> list[PlatformSkill]:
    return list_skills(db, owner_user_id) + list_platform_skills(db)


def get_owned_skill(db: Session, skill_id: str, owner_user_id: str) -> PlatformSkill | None:
    return db.scalar(
        select(PlatformSkill).where(
            PlatformSkill.id == skill_id,
            PlatformSkill.owner_user_id == owner_user_id,
            PlatformSkill.visibility == "private",
            PlatformSkill.status == "active",
        )
    )


def get_visible_skill(db: Session, skill_id: str, owner_user_id: str) -> PlatformSkill | None:
    return db.scalar(
        select(PlatformSkill).where(
            PlatformSkill.id == skill_id,
            PlatformSkill.status == "active",
            (
                (PlatformSkill.owner_user_id == owner_user_id) & (PlatformSkill.visibility == "private")
            )
            | (
                (PlatformSkill.visibility == "platform") & (PlatformSkill.publish_status == "published")
            ),
        )
    )


def get_authored_platform_skill(db: Session, skill_id: str, owner_user_id: str) -> PlatformSkill | None:
    return db.scalar(
        select(PlatformSkill).where(
            PlatformSkill.id == skill_id,
            PlatformSkill.owner_user_id == owner_user_id,
            PlatformSkill.visibility == "platform",
            PlatformSkill.status == "active",
        )
    )


def delete_skill(db: Session, skill: PlatformSkill) -> None:
    try:
        skill_path = _safe_existing_skill_path(skill)
    except ValueError:
        skill_path = None
    db.delete(skill)
    db.commit()
    if skill_path and skill_path.exists():
        shutil.rmtree(skill_path)


def publish_skill(db: Session, skill_id: str, owner_user_id: str) -> PlatformSkill:
    source = get_owned_skill(db, skill_id, owner_user_id)
    if not source:
        raise ValueError("Skill not found")
    validation = validate_skill_by_id(db, source)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))

    source_root = _safe_existing_skill_path(source)
    platform_skill = PlatformSkill(
        owner_user_id=owner_user_id,
        name=source.name,
        description=source.description,
        version=source.version,
        status="active",
        visibility="platform",
        publish_status="published",
        source_skill_id=source.id,
        storage_path="",
        source_app_id=source.source_app_id,
        source_workflow_id=source.source_workflow_id,
        source_run_id=source.source_run_id,
        published_at=datetime.now(timezone.utc),
        revoked_at=None,
        usage_count=0,
        last_used_at=None,
        metadata_json={
            **(source.metadata_json if isinstance(source.metadata_json, dict) else {}),
            "source_visibility": "private",
            "published_snapshot": True,
        },
    )
    db.add(platform_skill)
    db.flush()

    target_root = _platform_skill_storage_path(platform_skill.id)
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, target_root)
    platform_skill.storage_path = str(target_root)
    _stamp_manifest_visibility(target_root, "platform")

    from app.services.platform_skill_search_service import upsert_skill_search_document

    upsert_skill_search_document(db, platform_skill)
    db.commit()
    db.refresh(platform_skill)
    return platform_skill


def revoke_platform_skill(db: Session, skill_id: str, owner_user_id: str) -> PlatformSkill:
    skill = get_authored_platform_skill(db, skill_id, owner_user_id)
    if not skill:
        raise ValueError("Platform skill not found")
    if skill.publish_status == "revoked":
        return skill
    skill.publish_status = "revoked"
    skill.revoked_at = datetime.now(timezone.utc)
    from app.services.platform_skill_search_service import sync_skill_search_document_status

    sync_skill_search_document_status(db, skill)
    db.commit()
    db.refresh(skill)
    return skill


def build_skill_zip(skill: PlatformSkill) -> bytes:
    root = _safe_existing_skill_path(skill)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
            archive.write(file_path, file_path.relative_to(root).as_posix())
    return buffer.getvalue()


def validate_skill_by_id(db: Session, skill: PlatformSkill) -> SkillValidationOut:
    app = db.get(App, skill.source_app_id) if skill.source_app_id else None
    return validate_skill_directory(db, skill.owner_user_id, Path(skill.storage_path), app)


def validate_skill_files(db: Session, owner_user_id: str, files: dict[str, str]) -> SkillValidationOut:
    errors: list[str] = []
    normalized = {str(key).replace("\\", "/").strip("/"): str(value) for key, value in files.items()}
    missing = sorted(file_name for file_name in REQUIRED_SKILL_FILES if file_name not in normalized)
    if missing:
        errors.append(f"Missing required skill files: {', '.join(missing)}")
    errors.extend(_validate_skill_documents(normalized))
    template_text = normalized.get("workflow_template.json")
    if template_text:
        try:
            validate_workflow_draft_structure(json.loads(template_text))
        except Exception as exc:
            errors.append(f"Invalid workflow_template.json: {exc}")
    return SkillValidationOut(ok=not errors, errors=errors)


def validate_skill_directory(
    db: Session,
    owner_user_id: str,
    skill_path: Path,
    app: App | None = None,
) -> SkillValidationOut:
    errors: list[str] = []
    root = _safe_path(skill_path)
    if not root.exists() or not root.is_dir():
        return SkillValidationOut(ok=False, errors=["Skill directory not found"])

    missing = sorted(file_name for file_name in REQUIRED_SKILL_FILES if not (root / file_name).is_file())
    if missing:
        errors.append(f"Missing required skill files: {', '.join(missing)}")

    files = {
        file_path.relative_to(root).as_posix(): file_path.read_text(encoding="utf-8")
        for file_path in root.rglob("*")
        if file_path.is_file()
    }
    errors.extend(_validate_skill_documents(files))

    template_path = root / "workflow_template.json"
    if template_path.exists():
        try:
            template = json.loads(template_path.read_text(encoding="utf-8"))
            normalized = validate_workflow_draft_structure(template)
            if app:
                _validate_workflow_spec(db, app, normalized)
        except Exception as exc:
            errors.append(f"Invalid workflow_template.json: {exc}")

    if app and app.owner_user_id != owner_user_id:
        errors.append("Source app does not belong to the skill owner")
    return SkillValidationOut(ok=not errors, errors=errors)


def load_skill_manifest(skill: PlatformSkill) -> dict[str, Any]:
    return _load_yaml_like(_read_skill_file(skill, "skill.yaml"))


def load_skill_rules(skill: PlatformSkill) -> str:
    return _read_skill_file(skill, "rules.md")


def load_skill_tool_policy(skill: PlatformSkill) -> dict[str, Any]:
    policy = _load_yaml_like(_read_skill_file(skill, "tool_policy.yaml"))
    _validate_tool_policy(policy)
    return policy


def load_skill_reference(skill: PlatformSkill, relative_path: str) -> str:
    normalized = str(relative_path).replace("\\", "/").strip("/")
    if not normalized.startswith("references/"):
        normalized = f"references/{normalized}"
    if Path(normalized).suffix.lower() not in REFERENCE_EXTENSIONS:
        raise ValueError("Unsupported reference file type")
    content = _read_skill_file(skill, normalized)
    return content[:MAX_REFERENCE_INJECTION_CHARS]


def load_skill_workflow_template(skill: PlatformSkill) -> dict[str, Any] | None:
    root = _safe_existing_skill_path(skill)
    path = root / "workflow_template.json"
    if not path.exists():
        return None
    return validate_workflow_draft_structure(json.loads(path.read_text(encoding="utf-8")))


def validate_workflow_draft_structure(draft_spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(draft_spec, dict):
        raise ValueError("Workflow draft must be a JSON object")
    spec = normalize_workflow_spec(draft_spec)
    nodes = spec.get("nodes")
    edges = spec.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Workflow draft must contain nodes")
    if not isinstance(edges, list):
        raise ValueError("Workflow draft edges must be a list")

    node_ids: set[str] = set()
    supported = {"start", "retrieval", "agent", "react_agent", "end"}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"Node at index {index} must be an object")
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if not node_id:
            raise ValueError(f"Node at index {index} is missing id")
        if node_id in node_ids:
            raise ValueError(f"Duplicate node id: {node_id}")
        if node_type not in supported:
            raise ValueError(f"Unsupported node type: {node_type or 'empty'}")
        node_ids.add(node_id)

    for index, edge in enumerate(edges):
        if isinstance(edge, list) and len(edge) == 2:
            source, target = str(edge[0]).strip(), str(edge[1]).strip()
        elif isinstance(edge, dict):
            source = str(edge.get("source") or edge.get("from") or "").strip()
            target = str(edge.get("target") or edge.get("to") or "").strip()
        else:
            raise ValueError(f"Edge at index {index} must be [source, target] or an object")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"Edge at index {index} references missing node")
    return spec


def synthesize_skill(db: Session, owner_user_id: str, payload: SkillSynthesizeRequest) -> PlatformSkill:
    run = get_run_for_owner(db, payload.run_id, owner_user_id)
    if not run:
        raise ValueError("Run not found")
    app = db.get(App, run.app_id)
    workflow = db.get(Workflow, run.workflow_id)
    if not app or not workflow or app.owner_user_id != owner_user_id:
        raise ValueError("Run source is not owned by current user")

    steps = list_run_steps(db, run.id)
    skill = get_owned_skill(db, payload.skill_id, owner_user_id) if payload.skill_id else None
    if skill:
        skill.name = payload.skill_name or skill.name
        skill.description = f"Generated from workflow trace {run.id}"
        skill.source_app_id = app.id
        skill.source_workflow_id = workflow.id
        skill.source_run_id = run.id
    else:
        skill = PlatformSkill(
            owner_user_id=owner_user_id,
            name=payload.skill_name or _safe_skill_name(workflow.name or app.name),
            description=f"Generated from workflow trace {run.id}",
            version="1.0.0",
            status="active",
            visibility="private",
            publish_status="draft",
            source_skill_id="",
            storage_path="",
            source_app_id=app.id,
            source_workflow_id=workflow.id,
            source_run_id=run.id,
            usage_count=0,
            metadata_json={},
        )
        db.add(skill)
        db.flush()
    skill.visibility = "private"
    skill.publish_status = "draft"

    skill.storage_path = str(_skill_storage_path(owner_user_id, skill.id))
    skill.metadata_json = {
        "source": "skill_synthesizer",
        "run_status": run.status,
        "feedback_present": bool(payload.feedback.strip()),
        "step_count": len(steps),
    }

    root = _prepare_skill_dir(owner_user_id, skill.id)
    _write_synthesized_skill(root, skill, app, workflow, run, steps, payload.feedback)
    validation = validate_skill_directory(db, owner_user_id, root, app)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))

    from app.services.platform_skill_search_service import upsert_skill_search_document

    upsert_skill_search_document(db, skill)
    db.commit()
    db.refresh(skill)
    return skill


def _validate_skill_documents(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name in ("skill.yaml", "tool_policy.yaml"):
        if name in files:
            try:
                data = _load_yaml_like(files[name])
                if not isinstance(data, dict):
                    errors.append(f"{name} must be an object")
                elif name == "tool_policy.yaml":
                    _validate_tool_policy(data)
            except Exception as exc:
                errors.append(f"Invalid {name}: {exc}")

    total_reference_bytes = 0
    for path, content in files.items():
        if not path.startswith("references/"):
            continue
        suffix = Path(path).suffix.lower()
        if suffix not in REFERENCE_EXTENSIONS:
            errors.append(f"Unsupported reference file type: {path}")
        size = len(content.encode("utf-8"))
        if size > MAX_REFERENCE_BYTES:
            errors.append(f"Reference file is too large: {path}")
        total_reference_bytes += size
    if total_reference_bytes > MAX_REFERENCES_BYTES:
        errors.append("References directory is too large")
    return errors


def _validate_tool_policy(policy: dict[str, Any]) -> None:
    declared = set()
    for key in ("allow", "allowed_actions", "actions"):
        value = policy.get(key)
        if isinstance(value, list):
            declared.update(str(item).strip() for item in value if str(item).strip())
    invalid = sorted(action for action in declared if action not in PLATFORM_ASSISTANT_ACTIONS)
    if invalid:
        raise ValueError(f"Tool policy declares unsupported platform assistant actions: {', '.join(invalid)}")


def _write_synthesized_skill(
    root: Path,
    skill: PlatformSkill,
    app: App,
    workflow: Workflow,
    run: Run,
    steps: list[Any],
    feedback: str,
) -> None:
    tool_steps = [step for step in steps if step.type == "tool_call"]
    error_steps = [step for step in steps if step.error]
    start_steps = [step for step in steps if step.type == "start"]
    query = ""
    if start_steps:
        query = str(start_steps[0].output_json.get("query") or start_steps[0].input_json.get("query") or "")

    skill_doc = {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "visibility": "private",
        "task_patterns": _task_patterns(query, app.name, workflow.name),
        "inputs": ["user_goal", "workflow_context"],
        "outputs": ["workflow_draft", "implementation_notes", "validation_result"],
        "source": {
            "app_id": app.id,
            "workflow_id": workflow.id,
            "run_id": run.id,
        },
    }
    tool_policy = {
        "allow": [
            "recommend_published_workflows",
            "load_private_skill_manifest",
            "load_private_skill_rules",
            "load_private_skill_tool_policy",
            "load_private_skill_reference",
            "load_private_skill_workflow_template",
            "create_app_with_draft_workflow",
            "update_owned_workflow_draft",
            "get_owned_run_trace",
        ],
        "deny": ["workflow_runtime", "agent_node_tools", "mcp_runtime", "external_network", "credential_write"],
        "constraints": [
            "Only operate on the current user's private skills and owned workflow drafts.",
            "Validate workflow drafts before writing them.",
        ],
    }

    (root / "skill.yaml").write_text(json.dumps(skill_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "tool_policy.yaml").write_text(json.dumps(tool_policy, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "rules.md").write_text(_rules_markdown(query, workflow, tool_steps, error_steps, feedback), encoding="utf-8")
    (root / "workflow_template.json").write_text(
        json.dumps(normalize_workflow_spec(workflow.draft_spec), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "references" / "trace_summary.md").write_text(
        _trace_summary_markdown(run, steps, feedback),
        encoding="utf-8",
    )
    (root / "examples" / "example_run.json").write_text(
        json.dumps(
            {
                "query": query,
                "run_id": run.id,
                "status": run.status,
                "tool_calls": [step.name for step in tool_steps],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "evals" / "smoke.json").write_text(
        json.dumps(
            {
                "name": "schema_smoke",
                "input": query or f"Create a workflow similar to {workflow.name}",
                "expected": {"must_validate_workflow_template": True},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _rules_markdown(query: str, workflow: Workflow, tool_steps: list[Any], error_steps: list[Any], feedback: str) -> str:
    lines = [
        "# Prompt Rules",
        "",
        "## Task Pattern",
        f"- Build or refine a workflow app similar to `{workflow.name}`.",
    ]
    if query:
        lines.append(f"- Original user goal: {query}")
    lines.extend(
        [
            "",
            "## Decision Heuristics",
            "- Start from the workflow template when the user intent matches the task pattern.",
            "- Ask for missing app goal, inputs, outputs, and required integrations before writing a draft.",
            "- Prefer updating workflow structure over changing this skill unless the same pattern repeats with a successful trace.",
            "",
            "## Tool Usage Pattern",
        ]
    )
    if tool_steps:
        lines.extend(f"- `{step.name}` was used after validation of the workflow context." for step in tool_steps)
    else:
        lines.append("- No runtime tool calls were required in the source trace.")
    if error_steps:
        lines.extend(["", "## Failure Notes"])
        lines.extend(f"- `{step.name}` failed: {step.error}" for step in error_steps)
    if feedback.strip():
        lines.extend(["", "## User Feedback", feedback.strip()])
    return "\n".join(lines) + "\n"


def _trace_summary_markdown(run: Run, steps: list[Any], feedback: str) -> str:
    lines = [
        "# Trace Summary",
        "",
        f"- Run: `{run.id}`",
        f"- Status: `{run.status}`",
        f"- Steps: {len(steps)}",
        "",
        "## Steps",
    ]
    lines.extend(f"- {step.type}: {step.name} ({step.latency_ms} ms){' ERROR: ' + step.error if step.error else ''}" for step in steps)
    if feedback.strip():
        lines.extend(["", "## Feedback", feedback.strip()])
    return "\n".join(lines) + "\n"


def _task_patterns(query: str, app_name: str, workflow_name: str) -> list[str]:
    candidates = [query.strip(), app_name.strip(), workflow_name.strip()]
    return [item for item in candidates if item][:5] or ["workflow app creation"]


def _safe_skill_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "Workflow Skill")).strip()
    return cleaned[:120] or "Workflow Skill"


def _prepare_skill_dir(owner_user_id: str, skill_id: str) -> Path:
    root = _skill_storage_path(owner_user_id, skill_id)
    if root.exists():
        shutil.rmtree(root)
    for dirname in ("references", "examples", "evals"):
        (root / dirname).mkdir(parents=True, exist_ok=True)
    return root


def _skill_storage_path(owner_user_id: str, skill_id: str) -> Path:
    root = _safe_path(get_settings().storage_dir / "skills" / owner_user_id)
    path = _safe_path(root / skill_id)
    if not _is_relative_to(path, root):
        raise ValueError("Invalid skill storage path")
    return path


def _platform_skill_storage_path(skill_id: str) -> Path:
    root = _safe_path(get_settings().storage_dir / "skills" / "platform")
    path = _safe_path(root / skill_id)
    if not _is_relative_to(path, root):
        raise ValueError("Invalid platform skill storage path")
    return path


def _safe_existing_skill_path(skill: PlatformSkill) -> Path:
    path = _safe_path(Path(skill.storage_path))
    root = (
        _safe_path(get_settings().storage_dir / "skills" / "platform")
        if skill.visibility == "platform"
        else _safe_path(get_settings().storage_dir / "skills" / skill.owner_user_id)
    )
    if not _is_relative_to(path, root):
        raise ValueError("Skill path escapes owner storage root")
    if not path.exists():
        raise ValueError("Skill directory not found")
    return path


def _read_skill_file(skill: PlatformSkill, relative_path: str) -> str:
    root = _safe_existing_skill_path(skill)
    path = _safe_path(root / relative_path)
    if not _is_relative_to(path, root) or not path.is_file():
        raise ValueError("Skill file not found")
    return path.read_text(encoding="utf-8")


def _safe_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_yaml_like(text: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            result: dict[str, Any] = {}
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip().strip("'\"")
            return result


def _stamp_manifest_visibility(root: Path, visibility: str) -> None:
    path = root / "skill.yaml"
    if not path.exists():
        return
    try:
        data = _load_yaml_like(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    data["visibility"] = visibility
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
