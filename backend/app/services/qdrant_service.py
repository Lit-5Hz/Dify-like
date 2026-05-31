from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.db.models import KnowledgeBase, KnowledgeChunk

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


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
        from qdrant_client.http.models import Distance, SparseIndexParams, SparseVectorParams, VectorParams
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    if _collection_exists(client, kb.qdrant_collection):
        return
    try:
        client.create_collection(
            collection_name=kb.qdrant_collection,
            vectors_config={
                DENSE_VECTOR_NAME: VectorParams(size=kb.embedding_dimension, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: SparseVectorParams(index=SparseIndexParams(on_disk=False)),
            },
        )
    except Exception:
        client.create_collection(
            collection_name=kb.qdrant_collection,
            vectors_config=VectorParams(size=kb.embedding_dimension, distance=Distance.COSINE),
        )


def upsert_knowledge_chunks(
    kb: KnowledgeBase,
    chunks: list[KnowledgeChunk],
    vectors: list[list[float]],
    sparse_vectors: list[dict[str, list[int] | list[float]]] | None = None,
) -> None:
    if not chunks:
        return
    if len(chunks) != len(vectors):
        raise ValueError(f"Vector count mismatch: {len(chunks)} chunks, {len(vectors)} vectors.")
    if sparse_vectors is not None and len(chunks) != len(sparse_vectors):
        raise ValueError(f"Sparse vector count mismatch: {len(chunks)} chunks, {len(sparse_vectors)} vectors.")

    try:
        from qdrant_client.http.models import PointStruct, SparseVector
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    named_points = []
    legacy_points = []
    for chunk, vector in zip(chunks, vectors):
        metadata = dict(chunk.metadata_json or {})
        metadata["content"] = chunk.content
        sparse_payload = None
        if sparse_vectors is not None:
            sparse_payload = sparse_vectors[len(named_points)]
        sparse_vector = SparseVector(
            indices=[int(index) for index in (sparse_payload or {}).get("indices", [])],
            values=[float(value) for value in (sparse_payload or {}).get("values", [])],
        )
        named_vector: dict[str, Any] = {DENSE_VECTOR_NAME: vector}
        if sparse_vector.indices:
            named_vector[SPARSE_VECTOR_NAME] = sparse_vector
        named_points.append(
            PointStruct(
                id=chunk.qdrant_point_id or chunk.id,
                vector=named_vector,
                payload=metadata,
            )
        )
        legacy_points.append(
            PointStruct(
                id=chunk.qdrant_point_id or chunk.id,
                vector=vector,
                payload=metadata,
            )
        )
    try:
        client.upsert(collection_name=kb.qdrant_collection, points=named_points, wait=True)
    except Exception:
        client.upsert(collection_name=kb.qdrant_collection, points=legacy_points, wait=True)


def update_knowledge_sparse_vectors(
    kb: KnowledgeBase,
    chunks: list[KnowledgeChunk],
    sparse_vectors: list[dict[str, list[int] | list[float]]],
) -> None:
    if not chunks:
        return
    if len(chunks) != len(sparse_vectors):
        raise ValueError(f"Sparse vector count mismatch: {len(chunks)} chunks, {len(sparse_vectors)} vectors.")
    try:
        from qdrant_client.http.models import PointVectors, SparseVector
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    points = []
    empty_point_ids: list[str] = []
    for chunk, sparse_payload in zip(chunks, sparse_vectors):
        point_id = chunk.qdrant_point_id or chunk.id
        indices = [int(index) for index in sparse_payload.get("indices", [])]
        values = [float(value) for value in sparse_payload.get("values", [])]
        if not indices:
            empty_point_ids.append(point_id)
            continue
        points.append(
            PointVectors(
                id=point_id,
                vector={
                    SPARSE_VECTOR_NAME: SparseVector(indices=indices, values=values),
                },
            )
        )
    if points:
        client.update_vectors(collection_name=kb.qdrant_collection, points=points, wait=True)
    if empty_point_ids:
        client.delete_vectors(
            collection_name=kb.qdrant_collection,
            vectors=[SPARSE_VECTOR_NAME],
            points=empty_point_ids,
            wait=True,
        )


def search_knowledge_chunks(kb: KnowledgeBase, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
    if not query_vector or limit <= 0:
        return []
    client = get_qdrant_client()
    try:
        response = client.query_points(
            collection_name=kb.qdrant_collection,
            query=query_vector,
            using=DENSE_VECTOR_NAME,
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", None) or getattr(response, "result", None) or []
    except Exception:
        response = client.query_points(
            collection_name=kb.qdrant_collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", None) or getattr(response, "result", None) or []

    return [_point_to_hit(point) for point in points]


def search_sparse_knowledge_chunks(
    kb: KnowledgeBase,
    sparse_vector: dict[str, list[int] | list[float]],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or not sparse_vector.get("indices"):
        return []
    try:
        from qdrant_client.http.models import SparseVector
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    response = client.query_points(
        collection_name=kb.qdrant_collection,
        query=SparseVector(
            indices=[int(index) for index in sparse_vector.get("indices", [])],
            values=[float(value) for value in sparse_vector.get("values", [])],
        ),
        using=SPARSE_VECTOR_NAME,
        limit=limit,
        with_payload=True,
    )
    points = getattr(response, "points", None) or getattr(response, "result", None) or []
    return [_point_to_hit(point) for point in points]


def delete_knowledge_points(kb: KnowledgeBase, point_ids: list[str]) -> None:
    if not point_ids:
        return
    try:
        from qdrant_client.http.models import PointIdsList
    except ImportError as exc:
        raise RuntimeError("qdrant-client is not installed. Run `pip install -e .` in backend first.") from exc

    client = get_qdrant_client()
    client.delete(
        collection_name=kb.qdrant_collection,
        points_selector=PointIdsList(points=[point_id for point_id in point_ids if point_id]),
        wait=True,
    )


def delete_collection(kb: KnowledgeBase) -> None:
    client = get_qdrant_client()
    if _collection_exists(client, kb.qdrant_collection):
        client.delete_collection(kb.qdrant_collection)


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
