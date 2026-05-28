from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeBase, KnowledgeChunk
from app.services.knowledge_database_service import _assert_embedding_config_not_drifted
from app.services.model_credential_service import resolve_model_api_key
from app.services.qdrant_service import ensure_collection, search_knowledge_chunks, search_sparse_knowledge_chunks
from app.services.retrieval_defaults import (
    DEFAULT_DENSE_TOP_K,
    DEFAULT_QUERY_LLM_MAX_TOKENS,
    DEFAULT_QUERY_LLM_TEMPERATURE,
    DEFAULT_QUERY_LLM_TIMEOUT,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_RETRIEVAL_STRATEGY,
    DEFAULT_RRF_K,
    DEFAULT_SPARSE_TOP_K,
    QUERY_ENHANCEMENT_STRATEGIES,
)
from app.services.retrieval_providers import (
    build_embedding_provider,
    normalize_provider,
    rerank_chunks,
    validate_embedding_dimensions,
)


CONTRACT_VERSION = "retrieval.v1"


class QueryEnhancementConfigError(ValueError):
    pass


def get_capabilities() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "retrieval_strategy": DEFAULT_RETRIEVAL_STRATEGY,
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "sparse_top_k": DEFAULT_SPARSE_TOP_K,
        "rrf_k": DEFAULT_RRF_K,
        "rerank_provider": "jina",
        "rerank_fallback": "passthrough",
        "query_enhancement_strategies": QUERY_ENHANCEMENT_STRATEGIES,
        "query_llm_temperature": DEFAULT_QUERY_LLM_TEMPERATURE,
    }


def retrieve_chunks(
    db: Session,
    owner_user_id: str,
    query: str,
    retrieval_node: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(retrieval_node.get("enabled", True))
    query_plan = _build_query_plan(db, owner_user_id, query, retrieval_node)
    metadata = _default_metadata(query, retrieval_node, query_plan)
    if not enabled:
        return {"chunks": [], "metadata": metadata}

    top_k = max(_to_int(retrieval_node.get("retrieval_top_k"), DEFAULT_DENSE_TOP_K), 0)
    kb_ids = _normalize_kb_ids(retrieval_node.get("knowledge_base_ids"))
    metadata["knowledge_base_ids"] = kb_ids
    metadata["retrieval_top_k"] = top_k
    if not kb_ids:
        metadata["retrieval_mode"] = "hybrid+passthrough"
        metadata["warnings"].append("No knowledge database is selected for this retrieval node.")
        return {"chunks": [], "metadata": metadata}

    knowledge_bases = _load_knowledge_bases(db, owner_user_id, kb_ids)
    found_ids = {kb.id for kb in knowledge_bases}
    missing_ids = [kb_id for kb_id in kb_ids if kb_id not in found_ids]
    if missing_ids:
        metadata["warnings"].append(f"Some selected knowledge databases are missing or not owned by this app creator: {missing_ids}.")
    if not knowledge_bases:
        metadata["retrieval_mode"] = "hybrid+passthrough"
        return {"chunks": [], "metadata": metadata}

    dense_candidates: list[dict[str, Any]] = []
    sparse_candidates: list[dict[str, Any]] = []
    for kb in knowledge_bases:
        _assert_embedding_config_not_drifted(kb)
        ensure_collection(kb)
        embedding_provider = build_embedding_provider(kb)
        dense_candidates.extend(
            _search_dense_candidates(
                kb=kb,
                embedding_provider=embedding_provider,
                query_variants=query_plan["query_variants"],
                top_k=top_k,
            )
        )
        sparse_candidates.extend(
            _search_sparse_candidates(
                db=db,
                kb=kb,
                query=query_plan["standard_query"],
                limit=DEFAULT_SPARSE_TOP_K,
            )
        )

    fused = _rrf_fuse(
        [_dedupe_ranked_chunks(dense_candidates, top_k), _dedupe_ranked_chunks(sparse_candidates, DEFAULT_SPARSE_TOP_K)],
        top_k,
        DEFAULT_RRF_K,
    )
    expanded = _expand_parent_chunks(db, knowledge_bases, fused)

    rerank_enabled = bool(retrieval_node.get("rerank_enabled", False))
    rerank_top_n = DEFAULT_RERANK_TOP_N if rerank_enabled else top_k
    ranked, rerank_provider, rerank_warnings = rerank_chunks(
        query=query_plan["standard_query"],
        chunks=expanded,
        enabled=rerank_enabled,
        top_n=rerank_top_n,
    )
    metadata.update(
        {
            "retrieval_mode": "hybrid+jina_rerank" if rerank_provider == "jina" else "hybrid+passthrough",
            "dense_retrieved": len(dense_candidates),
            "sparse_retrieved": len(sparse_candidates),
            "total_retrieved": len(expanded),
            "total_returned": len(ranked),
            "rerank_enabled": rerank_enabled,
            "rerank_provider": rerank_provider,
            "warnings": [*metadata.get("warnings", []), *rerank_warnings],
        }
    )
    return {"chunks": ranked, "metadata": metadata}


def _normalize_kb_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = []
    normalized: list[str] = []
    for item in candidates:
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _load_knowledge_bases(db: Session, owner_user_id: str, kb_ids: list[str]) -> list[KnowledgeBase]:
    if not kb_ids:
        return []
    rows = list(
        db.scalars(
            select(KnowledgeBase).where(
                KnowledgeBase.id.in_(kb_ids),
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.scope == "creator",
            )
        )
    )
    by_id = {row.id: row for row in rows}
    return [by_id[kb_id] for kb_id in kb_ids if kb_id in by_id]


def _build_query_plan(
    db: Session,
    owner_user_id: str,
    query: str,
    retrieval_node: dict[str, Any],
) -> dict[str, Any]:
    original_query = str(query or "").strip()
    enabled = bool(retrieval_node.get("query_enhancement_enabled", False))
    strategy = _normalize_query_enhancement_strategy(retrieval_node) if enabled else "none"
    standard_query = _compact_query(original_query)
    variants = [standard_query] if standard_query else []
    generated: list[dict[str, str]] = []
    warnings: list[str] = []
    llm_metadata: dict[str, Any] = {}

    if strategy != "none" and standard_query:
        try:
            llm_payload = _generate_query_enhancement_with_llm(
                db=db,
                owner_user_id=owner_user_id,
                retrieval_node=retrieval_node,
                strategy=strategy,
                query=standard_query,
            )
            llm_metadata = llm_payload.get("metadata", {})
            if strategy == "rewrite":
                rewrites = _normalize_generated_query_list(llm_payload.get("queries"), standard_query)
                if rewrites:
                    standard_query = rewrites[0]
                    variants = [standard_query, *rewrites[1:]]
                    generated.extend({"type": "rewrite", "query": item} for item in rewrites)
            elif strategy == "hyde":
                hypothetical_document = str(llm_payload.get("hypothetical_document") or "").strip()
                if hypothetical_document:
                    variants.append(hypothetical_document)
                    generated.append({"type": "hyde", "query": hypothetical_document})
            elif strategy == "multi_query":
                query_variants = _normalize_generated_query_list(llm_payload.get("queries"), standard_query)
                for item in query_variants:
                    if item != standard_query:
                        variants.append(item)
                        generated.append({"type": "multi_query", "query": item})
        except QueryEnhancementConfigError:
            raise
        except Exception as exc:
            warnings.append(f"Query Enhancement LLM failed; downgraded to local query expansion. Details: {exc}")
            fallback_variants, fallback_generated = _fallback_query_enhancement(strategy, standard_query)
            variants.extend(fallback_variants)
            generated.extend(fallback_generated)

    deduped_variants = []
    for variant in variants:
        if variant and variant not in deduped_variants:
            deduped_variants.append(variant)

    return {
        "strategy": strategy,
        "original_query": original_query,
        "standard_query": standard_query,
        "query_variants": deduped_variants,
        "generated_queries": generated,
        "warnings": warnings,
        "llm": llm_metadata,
        "applied": strategy != "none" and bool(standard_query),
        "intent_matched": bool(standard_query and standard_query != original_query),
    }


def _normalize_query_enhancement_strategy(retrieval_node: dict[str, Any]) -> str:
    strategy = str(retrieval_node.get("query_enhancement_strategy") or "rewrite").strip().lower()
    return strategy if strategy in QUERY_ENHANCEMENT_STRATEGIES else "rewrite"


def _generate_query_enhancement_with_llm(
    db: Session,
    owner_user_id: str,
    retrieval_node: dict[str, Any],
    strategy: str,
    query: str,
) -> dict[str, Any]:
    provider = normalize_provider(str(retrieval_node.get("query_llm_provider") or ""))
    model = str(retrieval_node.get("query_llm_model") or "").strip()
    credential_id = str(retrieval_node.get("query_llm_credential_id") or "").strip()
    base_url = str(retrieval_node.get("query_llm_base_url") or "").strip()

    if not provider or not model or not credential_id:
        raise QueryEnhancementConfigError(
            "Query Enhancement uses a separate LLM. Configure query_llm_provider, query_llm_model, "
            "and query_llm_credential_id on the retrieval node; Agent node model settings are not reused."
        )

    try:
        api_key = resolve_model_api_key(db, credential_id, owner_user_id)
        endpoint = _resolve_query_llm_base_url(provider, base_url).rstrip("/")
    except ValueError as exc:
        raise QueryEnhancementConfigError(str(exc)) from exc

    response = httpx.post(
        f"{endpoint}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You generate retrieval queries. Return only JSON. Do not answer the user directly.",
                },
                {"role": "user", "content": _query_enhancement_prompt(strategy, query)},
            ],
            "temperature": _to_float(
                retrieval_node.get("query_llm_temperature"),
                DEFAULT_QUERY_LLM_TEMPERATURE,
            ),
            "max_tokens": max(
                _to_int(retrieval_node.get("query_llm_max_tokens"), DEFAULT_QUERY_LLM_MAX_TOKENS),
                64,
            ),
        },
        timeout=DEFAULT_QUERY_LLM_TIMEOUT,
    )
    response.raise_for_status()
    parsed = _parse_json_object(_extract_chat_completion_text(response.json()))
    parsed["metadata"] = {
        "provider": provider,
        "model": model,
        "credential_id": credential_id,
        "base_url": endpoint,
        "temperature": _to_float(retrieval_node.get("query_llm_temperature"), DEFAULT_QUERY_LLM_TEMPERATURE),
    }
    return parsed


def _query_enhancement_prompt(strategy: str, query: str) -> str:
    if strategy == "hyde":
        return (
            "Generate a concise hypothetical document that would answer the retrieval query. "
            "Return JSON with key hypothetical_document.\n"
            f"Query: {query}"
        )
    if strategy == "multi_query":
        return (
            "Generate 2 to 4 semantically different retrieval query variants for the same intent. "
            "Return JSON with key queries as an array of strings.\n"
            f"Query: {query}"
        )
    return (
        "Rewrite the retrieval query into 1 to 3 clearer search queries with synonyms when useful. "
        "Return JSON with key queries as an array of strings.\n"
        f"Query: {query}"
    )


def _resolve_query_llm_base_url(provider: str, base_url: str) -> str:
    explicit = str(base_url or "").strip()
    if explicit:
        return explicit
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider in {"dashscope", "qwen"}:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider in {"openai_compatible", "vllm"}:
        raise ValueError("query_llm_base_url is required for openai_compatible or vllm Query Enhancement providers.")
    raise ValueError(f"Unsupported Query Enhancement LLM provider: {provider}")


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Query Enhancement LLM returned an invalid response: missing choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()
    return str(choices[0].get("text") or "").strip() if isinstance(choices[0], dict) else ""


def _parse_json_object(text: str) -> dict[str, Any]:
    compact = str(text or "").strip()
    if compact.startswith("```"):
        compact = re.sub(r"^```(?:json)?", "", compact, flags=re.IGNORECASE).strip()
        compact = re.sub(r"```$", "", compact).strip()
    try:
        parsed = json.loads(compact)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", compact, flags=re.DOTALL)
        if not match:
            raise ValueError("Query Enhancement LLM did not return JSON.")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Query Enhancement LLM JSON must be an object.")
    return parsed


def _normalize_generated_query_list(value: Any, fallback: str) -> list[str]:
    if isinstance(value, str):
        items = [line.strip(" -\t") for line in value.splitlines()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = []
    deduped: list[str] = []
    for item in items:
        compact = _compact_query(item)
        if compact and compact not in deduped:
            deduped.append(compact)
    return deduped or ([fallback] if fallback else [])


def _fallback_query_enhancement(strategy: str, standard_query: str) -> tuple[list[str], list[dict[str, str]]]:
    variants: list[str] = []
    generated: list[dict[str, str]] = []
    if strategy == "rewrite":
        rewrite = _rewrite_query(standard_query)
        if rewrite and rewrite != standard_query:
            variants.append(rewrite)
            generated.append({"type": "local_rewrite", "query": rewrite})
    elif strategy == "hyde":
        hyde_query = f"Hypothetical document about '{standard_query}' covering facts, conditions, steps, and answers."
        variants.append(hyde_query)
        generated.append({"type": "local_hyde", "query": hyde_query})
    elif strategy == "multi_query":
        keyword_query = " ".join(_tokenize_for_sparse(standard_query)[:12])
        if keyword_query and keyword_query != standard_query:
            variants.append(keyword_query)
            generated.append({"type": "local_keyword", "query": keyword_query})
        focus_query = f"{standard_query} key facts conditions steps answer"
        variants.append(focus_query)
        generated.append({"type": "local_focus", "query": focus_query})
    return variants, generated


def _default_metadata(query: str, retrieval_node: dict[str, Any], query_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "knowledge_base_ids": _normalize_kb_ids(retrieval_node.get("knowledge_base_ids")),
        "retrieval_mode": "disabled",
        "retrieval_top_k": max(_to_int(retrieval_node.get("retrieval_top_k"), DEFAULT_DENSE_TOP_K), 0),
        "dense_retrieved": 0,
        "sparse_retrieved": 0,
        "sparse_top_k": DEFAULT_SPARSE_TOP_K,
        "rrf_k": DEFAULT_RRF_K,
        "total_retrieved": 0,
        "total_returned": 0,
        "rerank_enabled": bool(retrieval_node.get("rerank_enabled", False)),
        "rerank_provider": "passthrough",
        "original_query": query_plan["original_query"] or query,
        "standard_query": query_plan["standard_query"],
        "query_variants": query_plan["query_variants"],
        "query_enhancement": {
            "enabled": bool(retrieval_node.get("query_enhancement_enabled", False)),
            "strategy": query_plan["strategy"],
            "applied": query_plan["applied"],
            "generated_queries": query_plan["generated_queries"],
            "variant_count": len(query_plan["query_variants"]),
            "llm": query_plan.get("llm", {}),
        },
        "intent_matched": query_plan["intent_matched"],
        "warnings": list(query_plan.get("warnings", [])),
    }


def _search_dense_candidates(
    kb: KnowledgeBase,
    embedding_provider: Any,
    query_variants: list[str],
    top_k: int,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        return []

    ranked_lists: list[list[dict[str, Any]]] = []
    for query_index, query in enumerate(query_variants):
        query_vector = embedding_provider.embed_query(query)
        validate_embedding_dimensions([query_vector], kb.embedding_dimension)
        raw_hits = search_knowledge_chunks(kb, query_vector, top_k)
        ranked_lists.append([_hit_to_chunk(hit, kb, "dense", query_index) for hit in raw_hits])

    if not ranked_lists:
        return []
    if len(ranked_lists) == 1:
        return _dedupe_ranked_chunks(ranked_lists[0], top_k)
    return _rrf_fuse(ranked_lists, top_k, DEFAULT_RRF_K)


def _search_sparse_candidates(db: Session, kb: KnowledgeBase, query: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    query_terms = _tokenize_for_sparse(query)
    if not query_terms:
        return []
    sparse_vector = _sparse_vectorize(query)
    try:
        raw_hits = search_sparse_knowledge_chunks(kb, sparse_vector, limit)
        if raw_hits:
            return [_hit_to_chunk(hit, kb, "sparse_qdrant", 0) for hit in raw_hits]
    except Exception:
        pass

    chunks = [
        chunk
        for chunk in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id == kb.id))
        if _is_retrievable_chunk(chunk)
    ]
    if not chunks:
        return []

    scores = _lexical_scores(query_terms, chunks)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [_chunk_to_candidate(chunk, score, "sparse_local", kb) for chunk, score in ranked[:limit] if score > 0]


def _is_retrievable_chunk(chunk: KnowledgeChunk) -> bool:
    metadata = dict(chunk.metadata_json or {})
    return metadata.get("chunk_type") != "parent" and bool(chunk.qdrant_point_id or chunk.content)


def _lexical_scores(query_terms: list[str], chunks: list[KnowledgeChunk]) -> dict[KnowledgeChunk, float]:
    tokenized_docs = {chunk: _tokenize_for_sparse(chunk.content) for chunk in chunks}
    lengths = {chunk: len(tokens) for chunk, tokens in tokenized_docs.items()}
    avg_length = sum(lengths.values()) / max(len(lengths), 1)
    doc_count = len(chunks)
    document_frequencies: dict[str, int] = {}
    for tokens in tokenized_docs.values():
        for token in set(tokens):
            document_frequencies[token] = document_frequencies.get(token, 0) + 1

    k1 = 1.5
    b = 0.75
    scores: dict[KnowledgeChunk, float] = {}
    for chunk, tokens in tokenized_docs.items():
        if not tokens:
            scores[chunk] = 0.0
            continue
        score = 0.0
        for term in query_terms:
            frequency = tokens.count(term)
            if not frequency:
                continue
            df = document_frequencies.get(term, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * lengths[chunk] / max(avg_length, 1))
            score += idf * (frequency * (k1 + 1) / denominator)
        scores[chunk] = score
    return scores


def _tokenize_for_sparse(text: str) -> list[str]:
    value = str(text or "").lower()
    word_tokens = re.findall(r"[a-z0-9_]+", value)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]", value)
    return [*word_tokens, *cjk_tokens]


def _sparse_vectorize(text: str) -> dict[str, list[int] | list[float]]:
    tokens = _tokenize_for_sparse(text)
    if not tokens:
        return {"indices": [], "values": []}
    counts = Counter(tokens)
    pairs = sorted(
        (_stable_sparse_token_id(token), 1.0 + math.log(float(count))) for token, count in counts.items()
    )
    merged: dict[int, float] = {}
    for index, value in pairs:
        merged[index] = merged.get(index, 0.0) + value
    return {
        "indices": list(merged.keys()),
        "values": list(merged.values()),
    }


def _stable_sparse_token_id(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


def _chunk_to_candidate(chunk: KnowledgeChunk, score: float, source: str, kb: KnowledgeBase) -> dict[str, Any]:
    metadata = dict(chunk.metadata_json or {})
    return {
        "content": chunk.content,
        "score": float(score or 0.0),
        "source_file": metadata.get("source_file", ""),
        "page_num": metadata.get("page_num"),
        "chunk_type": metadata.get("chunk_type", "text"),
        "chunk_id": metadata.get("chunk_id") or chunk.id,
        "parent_id": metadata.get("parent_id"),
        "section": metadata.get("section"),
        "kb_id": metadata.get("kb_id") or chunk.knowledge_base_id,
        "scope": metadata.get("scope", kb.scope),
        "retrieval_source": source,
        "scores": {source: float(score or 0.0)},
    }


def _hit_to_chunk(
    hit: dict[str, Any],
    kb: KnowledgeBase,
    source: str = "dense",
    query_index: int = 0,
) -> dict[str, Any]:
    payload = hit.get("payload") or {}
    score = float(hit.get("score", 0.0) or 0.0)
    return {
        "content": payload.get("content", ""),
        "score": score,
        "source_file": payload.get("source_file", ""),
        "page_num": payload.get("page_num"),
        "chunk_type": payload.get("chunk_type", "text"),
        "chunk_id": payload.get("chunk_id") or hit.get("id", ""),
        "parent_id": payload.get("parent_id"),
        "section": payload.get("section"),
        "kb_id": payload.get("kb_id") or kb.id,
        "scope": payload.get("scope") or kb.scope,
        "retrieval_source": source,
        "query_index": query_index,
        "scores": {source: score},
    }


def _dedupe_ranked_chunks(chunks: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        key = str(chunk.get("chunk_id") or chunk.get("content") or "")
        if not key:
            continue
        current = deduped.get(key)
        if not current or float(chunk.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
            deduped[key] = chunk
    ranked = sorted(deduped.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return ranked[: max(limit, 0)]


def _rrf_fuse(ranked_lists: list[list[dict[str, Any]]], limit: int, rrf_k: int) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for ranked_list in ranked_lists:
        for rank, chunk in enumerate(ranked_list, start=1):
            key = str(chunk.get("chunk_id") or chunk.get("content") or "")
            if not key:
                continue
            score = 1.0 / (rrf_k + rank)
            current = fused.get(key)
            if not current:
                current = dict(chunk)
                current["score"] = 0.0
                current["scores"] = dict(chunk.get("scores") or {})
                fused[key] = current
            current["score"] = float(current.get("score", 0.0) or 0.0) + score
            source = str(chunk.get("retrieval_source") or "unknown")
            source_scores = dict(current.get("scores") or {})
            source_scores[source] = max(
                float(source_scores.get(source, 0.0) or 0.0),
                float(chunk.get("score", 0.0) or 0.0),
            )
            current["scores"] = source_scores
    ranked = sorted(fused.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return ranked[: max(limit, 0)]


def _expand_parent_chunks(
    db: Session,
    knowledge_bases: list[KnowledgeBase],
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not chunks:
        return chunks
    kb_by_id = {kb.id: kb for kb in knowledge_bases}
    expanded: dict[str, dict[str, Any]] = {}
    for kb_id, kb_chunks in _group_chunks_by_kb(chunks).items():
        kb = kb_by_id.get(kb_id)
        if not kb or not kb.enable_parent_child:
            for chunk in kb_chunks:
                expanded[str(chunk.get("chunk_id") or len(expanded))] = chunk
            continue
        parent_ids = {str(chunk.get("parent_id") or "") for chunk in kb_chunks if chunk.get("parent_id")}
        parents = {
            parent.id: parent
            for parent in db.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id == kb.id, KnowledgeChunk.id.in_(parent_ids))
            )
        }
        for chunk in kb_chunks:
            parent_id = str(chunk.get("parent_id") or "")
            parent = parents.get(parent_id)
            if not parent:
                expanded[str(chunk.get("chunk_id") or len(expanded))] = chunk
                continue
            parent_metadata = dict(parent.metadata_json or {})
            key = parent.id
            current = expanded.get(key)
            child_id = str(chunk.get("chunk_id") or "")
            if current:
                current["score"] = max(float(current.get("score", 0.0) or 0.0), float(chunk.get("score", 0.0) or 0.0))
                current.setdefault("matched_child_chunk_ids", [])
                if child_id and child_id not in current["matched_child_chunk_ids"]:
                    current["matched_child_chunk_ids"].append(child_id)
                continue
            expanded[key] = {
                **chunk,
                "content": parent.content,
                "chunk_id": parent.id,
                "chunk_type": "parent",
                "parent_id": parent.id,
                "matched_chunk_id": child_id,
                "matched_child_chunk_ids": [child_id] if child_id else [],
                "matched_content": chunk.get("content", ""),
                "source_file": parent_metadata.get("source_file", chunk.get("source_file", "")),
                "page_num": parent_metadata.get("page_num", chunk.get("page_num")),
                "section": parent_metadata.get("section", chunk.get("section")),
                "kb_id": parent_metadata.get("kb_id") or kb.id,
                "scope": parent_metadata.get("scope") or kb.scope,
            }
    return sorted(expanded.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)


def _group_chunks_by_kb(chunks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        grouped.setdefault(str(chunk.get("kb_id") or ""), []).append(chunk)
    return grouped


def _compact_query(query: str) -> str:
    return " ".join(str(query or "").split())


def _rewrite_query(query: str) -> str:
    compacted = _compact_query(query)
    return re.sub(r"[?？!！。]+$", "", compacted).strip()


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
