from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.db.models import KnowledgeBase, KnowledgeChunk


def get_qdrant_client():
    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    settings = get_settings()
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=30.0,
    )


def ensure_collection(kb: KnowledgeBase) -> None:
    try:
        from qdrant_client.http.models import Distance, VectorParams
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    if _collection_exists(client, kb.qdrant_collection):
        return
    client.create_collection(
        collection_name=kb.qdrant_collection,
        vectors_config=VectorParams(size=kb.embedding_dimension, distance=Distance.COSINE),
    )


def upsert_knowledge_chunks(kb: KnowledgeBase, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None:
    if not chunks:
        return
    if len(chunks) != len(vectors):
        raise ValueError(f"Vector count mismatch: {len(chunks)} chunks, {len(vectors)} vectors.")

    try:
        from qdrant_client.http.models import PointStruct
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    points = []
    for chunk, vector in zip(chunks, vectors):
        metadata = dict(chunk.metadata_json or {})
        metadata["content"] = chunk.content
        points.append(
            PointStruct(
                id=chunk.qdrant_point_id or chunk.id,
                vector=vector,
                payload=metadata,
            )
        )
    client.upsert(collection_name=kb.qdrant_collection, points=points, wait=True)


def search_knowledge_chunks(kb: KnowledgeBase, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
    if not query_vector or limit <= 0:
        return []
    client = get_qdrant_client()
    try:
        response = client.query_points(
            collection_name=kb.qdrant_collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", None) or getattr(response, "result", None) or []
    except AttributeError:
        points = client.search(
            collection_name=kb.qdrant_collection,
            query_vector=query_vector,
            limit=limit,
            with_payload=True,
        )

    return [_point_to_hit(point) for point in points]


def _collection_exists(client: Any, collection_name: str) -> bool:
    collection_exists = getattr(client, "collection_exists", None)
    if callable(collection_exists):
        return bool(collection_exists(collection_name))
    try:
        client.get_collection(collection_name)
        return True
    except Exception:
        return False


def _point_to_hit(point: Any) -> dict[str, Any]:
    payload = getattr(point, "payload", None) or {}
    return {
        "id": str(getattr(point, "id", "")),
        "score": float(getattr(point, "score", 0.0) or 0.0),
        "payload": payload,
    }
