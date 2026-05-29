from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.db.models import KnowledgeBase
from app.services.retrieval_defaults import (
    JINA_FALLBACK_PROVIDER,
    JINA_RERANK_TIMEOUT,
)


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
            vectors.extend(self._embed(texts[start : start + self._config.batch_size]))
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
            raise ValueError("Knowledge embedding API key is not configured on the backend.")
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


def normalize_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def build_embedding_provider(kb: KnowledgeBase) -> EmbeddingProvider:
    settings = get_settings()
    provider_id = normalize_provider(kb.embedding_provider)
    if provider_id not in {"openai", "openai_compatible", "zhipu", "zhipuai", "dashscope", "qwen"}:
        raise ValueError(f"Unsupported knowledge embedding provider: {kb.embedding_provider}")
    return OpenAICompatibleEmbeddingProvider(
        provider_id=provider_id,
        model_version=kb.embedding_model,
        dimension=kb.embedding_dimension,
        api_key=settings.knowledge_embedding_api_key,
        base_url=_resolve_embedding_base_url(provider_id, kb.embedding_base_url or settings.knowledge_embedding_base_url),
    )


def validate_embedding_dimensions(vectors: list[list[float]], expected_dimension: int) -> None:
    for index, vector in enumerate(vectors):
        actual = len(vector)
        if actual != expected_dimension:
            raise ValueError(
                f"Embedding dimension mismatch at vector {index}: expected {expected_dimension}, got {actual}."
            )


def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    enabled: bool,
    top_n: int,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    warnings: list[str] = []
    if not enabled:
        return _passthrough_rerank(chunks, max(top_n, 0)), JINA_FALLBACK_PROVIDER, warnings

    settings = get_settings()
    if not settings.retrieval_jina_api_key:
        warnings.append("Jina rerank API key is not configured; downgraded to passthrough.")
        return _passthrough_rerank(chunks, max(top_n, 0)), JINA_FALLBACK_PROVIDER, warnings

    try:
        return (
            _jina_rerank(
                query=query,
                chunks=chunks,
                top_n=max(top_n, 0),
                api_key=settings.retrieval_jina_api_key,
                model=settings.retrieval_jina_model,
                base_url=settings.retrieval_jina_base_url,
            ),
            "jina",
            warnings,
        )
    except Exception as exc:
        warnings.append(f"Jina rerank failed or timed out; downgraded to passthrough. Details: {exc}")
        return _passthrough_rerank(chunks, max(top_n, 0)), JINA_FALLBACK_PROVIDER, warnings


def _resolve_embedding_base_url(provider_id: str, base_url: str) -> str:
    explicit = str(base_url or "").strip()
    if explicit:
        return explicit
    if provider_id == "openai":
        return "https://api.openai.com/v1"
    if provider_id in {"dashscope", "qwen"}:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider_id in {"zhipu", "zhipuai"}:
        return "https://open.bigmodel.cn/api/paas/v4"
    raise ValueError("Knowledge embedding base URL is required for openai_compatible providers.")


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
    if not chunks or top_n <= 0:
        return []
    endpoint = (base_url or "https://api.jina.ai/v1").rstrip("/")
    response = httpx.post(
        f"{endpoint}/rerank",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model or "jina-reranker-v2",
            "query": query,
            "documents": [chunk["content"] for chunk in chunks],
            "top_n": top_n,
        },
        timeout=JINA_RERANK_TIMEOUT,
    )
    response.raise_for_status()
    return _chunks_from_rerank_response(chunks, response.json().get("results", []))


def _chunks_from_rerank_response(chunks: list[dict[str, Any]], results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        raise ValueError("Jina returned an invalid response: missing results list.")

    ranked: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", -1))
        if index < 0 or index >= len(chunks):
            continue
        chunk = dict(chunks[index])
        chunk["score"] = float(item.get("relevance_score", item.get("score", chunk.get("score", 0.0))) or 0.0)
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
