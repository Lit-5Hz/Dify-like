from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlatformSkill, SkillSearchDocument, SkillUsageEvent
from app.services.retrieval_defaults import DEFAULT_BM25_B, DEFAULT_BM25_K1
from app.services.sparse_bm25 import tokenize_for_sparse


TOKENIZER = "jieba_v1"
INDEX_VERSION = "skill_search_v1"
DEFAULT_TOP_K = 5
FIELD_WEIGHTS = {
    "task_patterns": 3.0,
    "name": 2.5,
    "tags": 2.0,
    "description": 1.2,
    "inputs": 1.0,
    "outputs": 1.0,
}


@dataclass
class SkillSearchHit:
    skill: PlatformSkill
    score: float
    explicit: bool
    match_summary: str
    search_document: SkillSearchDocument | None = None


def ensure_visible_skill_search_documents(db: Session, owner_user_id: str) -> list[tuple[PlatformSkill, SkillSearchDocument]]:
    skills = _visible_skill_candidates(db, owner_user_id)
    rows: list[tuple[PlatformSkill, SkillSearchDocument]] = []
    for skill in skills:
        document = ensure_skill_search_document(db, skill)
        rows.append((skill, document))
    return rows


def search_visible_skills(
    db: Session,
    owner_user_id: str,
    query: str,
    explicit_skill_ids: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[SkillSearchHit]:
    explicit_ids = []
    for skill_id in explicit_skill_ids or []:
        if skill_id and skill_id not in explicit_ids:
            explicit_ids.append(skill_id)

    rows = ensure_visible_skill_search_documents(db, owner_user_id)
    if not rows:
        return []

    query_terms = set(tokenize_for_sparse(query))
    field_stats = _field_corpus_stats([document for _, document in rows])
    hits: list[SkillSearchHit] = []

    for skill, document in rows:
        explicit = skill.id in explicit_ids
        score = _score_document(query_terms, document, field_stats) if query_terms else 0.0
        if explicit or score > 0:
            hits.append(
                SkillSearchHit(
                    skill=skill,
                    score=score,
                    explicit=explicit,
                    match_summary=_match_summary(query_terms, document, score, explicit),
                    search_document=document,
                )
            )

    hits.sort(
        key=lambda hit: (
            not hit.explicit,
            -hit.score,
            hit.skill.visibility != "private",
            -(hit.skill.updated_at.timestamp() if hit.skill.updated_at else 0),
        )
    )
    explicit_hits = [hit for hit in hits if hit.explicit]
    non_explicit_hits = [hit for hit in hits if not hit.explicit][: max(top_k - len(explicit_hits), 0)]
    return (explicit_hits + non_explicit_hits)[:top_k]


def upsert_skill_search_document(db: Session, skill: PlatformSkill) -> SkillSearchDocument:
    fields = _skill_index_fields(skill)
    search_text = "\n".join(_stringify_field(value) for value in fields.values())
    search_hash = hashlib.sha256(search_text.encode("utf-8")).hexdigest()
    document = db.scalar(select(SkillSearchDocument).where(SkillSearchDocument.skill_id == skill.id))
    if document and document.search_text_hash == search_hash and document.index_version == INDEX_VERSION:
        document.visibility = skill.visibility
        document.owner_user_id = skill.owner_user_id
        document.publish_status = skill.publish_status
        return document

    field_tokens: dict[str, list[str]] = {}
    field_counts: dict[str, dict[str, int]] = {}
    field_lengths: dict[str, int] = {}
    all_counts: Counter[str] = Counter()
    for field_name, value in fields.items():
        tokens = tokenize_for_sparse(_stringify_field(value))
        counts = Counter(tokens)
        field_tokens[field_name] = tokens
        field_counts[field_name] = dict(counts)
        field_lengths[field_name] = len(tokens)
        all_counts.update(counts)

    if not document:
        document = SkillSearchDocument(skill_id=skill.id)
        db.add(document)
    document.visibility = skill.visibility
    document.owner_user_id = skill.owner_user_id
    document.publish_status = skill.publish_status
    document.tokenizer = TOKENIZER
    document.index_version = INDEX_VERSION
    document.search_text_hash = search_hash
    document.field_tokens_json = field_tokens
    document.field_token_counts_json = field_counts
    document.field_lengths_json = field_lengths
    document.all_token_counts_json = dict(all_counts)
    document.doc_length = sum(field_lengths.values())
    return document


def ensure_skill_search_document(db: Session, skill: PlatformSkill) -> SkillSearchDocument:
    document = db.scalar(select(SkillSearchDocument).where(SkillSearchDocument.skill_id == skill.id))
    if (
        not document
        or document.index_version != INDEX_VERSION
        or document.visibility != skill.visibility
        or document.publish_status != skill.publish_status
    ):
        return upsert_skill_search_document(db, skill)
    return document


def sync_skill_search_document_status(db: Session, skill: PlatformSkill) -> None:
    document = db.scalar(select(SkillSearchDocument).where(SkillSearchDocument.skill_id == skill.id))
    if not document:
        return
    document.visibility = skill.visibility
    document.owner_user_id = skill.owner_user_id
    document.publish_status = skill.publish_status


def record_skill_usage(
    db: Session,
    skill: PlatformSkill,
    owner_user_id: str,
    usage_stage: str,
    assistant_session_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        SkillUsageEvent(
            skill_id=skill.id,
            owner_user_id=owner_user_id,
            assistant_session_id=assistant_session_id or "",
            usage_stage=usage_stage,
            metadata_json=metadata or {},
        )
    )
    skill.usage_count = int(skill.usage_count or 0) + 1
    skill.last_used_at = now


def _visible_skill_candidates(db: Session, owner_user_id: str) -> list[PlatformSkill]:
    return list(
        db.scalars(
            select(PlatformSkill)
            .where(
                PlatformSkill.status == "active",
                (
                    (PlatformSkill.owner_user_id == owner_user_id) & (PlatformSkill.visibility == "private")
                )
                | (
                    (PlatformSkill.visibility == "platform") & (PlatformSkill.publish_status == "published")
                ),
            )
            .order_by(PlatformSkill.visibility.asc(), PlatformSkill.updated_at.desc())
        )
    )


def _skill_index_fields(skill: PlatformSkill) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    try:
        from app.services.platform_skill_service import load_skill_manifest

        loaded = load_skill_manifest(skill)
        manifest = loaded if isinstance(loaded, dict) else {}
    except Exception:
        manifest = {}
    return {
        "name": skill.name,
        "description": skill.description or manifest.get("description", ""),
        "task_patterns": manifest.get("task_patterns", []),
        "tags": manifest.get("tags", []),
        "inputs": manifest.get("inputs", []),
        "outputs": manifest.get("outputs", []),
    }


def _field_corpus_stats(documents: list[SkillSearchDocument]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for field_name in FIELD_WEIGHTS:
        lengths = []
        dfs: dict[str, int] = {}
        for document in documents:
            field_counts = _field_counts(document, field_name)
            length = int(_field_lengths(document).get(field_name, 0) or 0)
            lengths.append(length)
            for token in field_counts:
                dfs[token] = dfs.get(token, 0) + 1
        stats[field_name] = {
            "doc_count": len(documents),
            "avg_length": sum(lengths) / max(len(lengths), 1),
            "dfs": dfs,
        }
    return stats


def _score_document(
    query_terms: set[str],
    document: SkillSearchDocument,
    field_stats: dict[str, dict[str, Any]],
) -> float:
    total = 0.0
    for field_name, weight in FIELD_WEIGHTS.items():
        counts = _field_counts(document, field_name)
        length = int(_field_lengths(document).get(field_name, 0) or 0)
        stats = field_stats[field_name]
        avg_length = float(stats["avg_length"] or 0)
        if not counts or length <= 0 or avg_length <= 0:
            continue
        field_score = 0.0
        for term in query_terms:
            frequency = int(counts.get(term, 0) or 0)
            if frequency <= 0:
                continue
            df = int(stats["dfs"].get(term, 0) or 0)
            if df <= 0:
                continue
            doc_count = int(stats["doc_count"] or 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            denominator = frequency + DEFAULT_BM25_K1 * (1 - DEFAULT_BM25_B + DEFAULT_BM25_B * length / avg_length)
            if denominator > 0:
                field_score += idf * (frequency * (DEFAULT_BM25_K1 + 1) / denominator)
        total += field_score * weight
    return total


def _match_summary(query_terms: set[str], document: SkillSearchDocument, score: float, explicit: bool) -> str:
    if explicit:
        return "explicitly selected"
    matched_fields = []
    for field_name in FIELD_WEIGHTS:
        counts = _field_counts(document, field_name)
        if any(term in counts for term in query_terms):
            matched_fields.append(field_name)
    if not matched_fields:
        return f"bm25={score:.3f}"
    return f"bm25={score:.3f}; matched {', '.join(matched_fields)}"


def _field_counts(document: SkillSearchDocument, field_name: str) -> dict[str, int]:
    payload = document.field_token_counts_json if isinstance(document.field_token_counts_json, dict) else {}
    value = payload.get(field_name, {})
    return {str(key): int(count or 0) for key, count in value.items()} if isinstance(value, dict) else {}


def _field_lengths(document: SkillSearchDocument) -> dict[str, int]:
    payload = document.field_lengths_json if isinstance(document.field_lengths_json, dict) else {}
    return {str(key): int(value or 0) for key, value in payload.items()}


def _stringify_field(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(_stringify_field(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_stringify_field(item)}" for key, item in value.items())
    return str(value or "")
