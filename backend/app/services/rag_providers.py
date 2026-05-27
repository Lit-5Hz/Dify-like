from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.db.models import KnowledgeBase
from app.services.model_credential_service import resolve_model_api_key


@dataclass(frozen=True)
class EmbeddingConfig:
    provider_id: str
    model_version: str
    dimension: int
    batch_size: int = 64


class EmbeddingProvider:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    def get_config(self) -> EmbeddingConfig:
        raise NotImplementedError


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        provider_id: str,
        model_version: str,
        dimension: int,
        api_key: str,
        base_url: str,
        batch_size: int = 64,
    ):
        self._config = EmbeddingConfig(
            provider_id=provider_id,
            model_version=model_version,
            dimension=dimension,
            batch_size=batch_size,
        )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._config.batch_size):
            batch = texts[start : start + self._config.batch_size]
            vectors.extend(self._embed(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self._embed([text])
        return vectors[0] if vectors else []

    def get_config(self) -> EmbeddingConfig:
        return self._config

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise ValueError(f"Missing API key for embedding provider '{self._config.provider_id}'.")
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self._config.model_version, "input": texts},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("Embedding provider returned an invalid response: missing data list.")

        ordered = sorted(data, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors: list[list[float]] = []
        for item in ordered:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("Embedding provider returned an invalid embedding item.")
            vectors.append([float(value) for value in item["embedding"]])
        return vectors


def build_embedding_provider(db: Session, kb: KnowledgeBase) -> EmbeddingProvider:
    provider_id = normalize_provider(kb.embedding_provider)
    api_key = resolve_model_api_key(db, kb.embedding_credential_id, kb.owner_user_id)
    base_url = _resolve_embedding_base_url(provider_id, kb.embedding_base_url)
    if provider_id not in {"openai", "openai_compatible", "zhipu", "zhipuai"}:
        raise ValueError(f"Unsupported embedding provider: {kb.embedding_provider}")
    return OpenAICompatibleEmbeddingProvider(
        provider_id=provider_id,
        model_version=kb.embedding_model,
        dimension=kb.embedding_dimension,
        api_key=api_key,
        base_url=base_url,
    )


def validate_embedding_dimensions(vectors: list[list[float]], expected_dimension: int) -> None:
    for index, vector in enumerate(vectors):
        actual = len(vector)
        if actual != expected_dimension:
            raise ValueError(
                f"Embedding dimension mismatch at vector {index}: expected {expected_dimension}, got {actual}."
            )


def normalize_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def _resolve_embedding_base_url(provider_id: str, base_url: str) -> str:
    explicit = str(base_url or "").strip()
    if explicit:
        return explicit
    if provider_id == "openai":
        return "https://api.openai.com/v1"
    if provider_id in {"zhipu", "zhipuai"}:
        return "https://open.bigmodel.cn/api/paas/v4"
    raise ValueError("Embedding base URL is required for openai_compatible providers.")


def rerank_chunks(
    db: Session,
    owner_user_id: str,
    query: str,
    chunks: list[dict[str, Any]],
    provider: str,
    top_n: int,
    credential_id: str = "",
    model: str = "",
    base_url: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    selected = normalize_provider(provider or "passthrough")
    warnings: list[str] = []
    if selected in {"", "none", "passthrough"}:
        return _passthrough_rerank(chunks, top_n), warnings

    if not credential_id:
        warnings.append(f"Rerank provider '{selected}' has no credential_id; downgraded to passthrough.")
        return _passthrough_rerank(chunks, top_n), warnings

    api_key = resolve_model_api_key(db, credential_id, owner_user_id)
    try:
        if selected == "jina":
            return _jina_rerank(query, chunks, top_n, api_key, model, base_url), warnings
        if selected == "cohere":
            return _cohere_rerank(query, chunks, top_n, api_key, model, base_url), warnings
    except Exception as exc:
        warnings.append(f"Rerank provider '{selected}' failed or timed out; downgraded to passthrough. Details: {exc}")
        return _passthrough_rerank(chunks, top_n), warnings

    warnings.append(f"Unsupported rerank provider '{selected}'; downgraded to passthrough.")
    return _passthrough_rerank(chunks, top_n), warnings


def _passthrough_rerank(chunks: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    normalized = _normalize_chunk_scores(chunks)
    return normalized[: max(top_n, 0)]


def _jina_rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_n: int,
    api_key: str,
    model: str,
    base_url: str,
) -> list[dict[str, Any]]:
    endpoint = (base_url or "https://api.jina.ai/v1").rstrip("/")
    payload = {
        "model": model or "jina-reranker-v2",
        "query": query,
        "documents": [chunk["content"] for chunk in chunks],
        "top_n": top_n,
    }
    response = httpx.post(
        f"{endpoint}/rerank",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=3.0,
    )
    response.raise_for_status()
    return _chunks_from_rerank_response(chunks, response.json().get("results", []), "relevance_score")


def _cohere_rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_n: int,
    api_key: str,
    model: str,
    base_url: str,
) -> list[dict[str, Any]]:
    endpoint = (base_url or "https://api.cohere.com/v2").rstrip("/")
    payload = {
        "model": model or "rerank-multilingual-v3.0",
        "query": query,
        "documents": [chunk["content"] for chunk in chunks],
        "top_n": top_n,
    }
    response = httpx.post(
        f"{endpoint}/rerank",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=3.0,
    )
    response.raise_for_status()
    return _chunks_from_rerank_response(chunks, response.json().get("results", []), "relevance_score")


def _chunks_from_rerank_response(
    chunks: list[dict[str, Any]],
    results: Any,
    score_key: str,
) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        raise ValueError("Rerank provider returned an invalid response: missing results list.")

    ranked: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", -1))
        if index < 0 or index >= len(chunks):
            continue
        chunk = dict(chunks[index])
        chunk["score"] = float(item.get(score_key, item.get("score", chunk.get("score", 0.0))) or 0.0)
        ranked.append(chunk)
    return _normalize_chunk_scores(ranked)


def _normalize_chunk_scores(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not chunks:
        return []
    scores = [float(chunk.get("score", 0.0) or 0.0) for chunk in chunks]
    max_score = max(scores)
    min_score = min(scores)
    normalized: list[dict[str, Any]] = []
    for chunk, score in zip(chunks, scores):
        next_chunk = dict(chunk)
        if 0.0 <= score <= 1.0:
            next_chunk["score"] = score
        elif max_score == min_score:
            next_chunk["score"] = 1.0 if score > 0 else 0.0
        else:
            next_chunk["score"] = (score - min_score) / (max_score - min_score)
        normalized.append(next_chunk)
    normalized.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return normalized
