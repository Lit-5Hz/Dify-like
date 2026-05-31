from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_DATASET_CACHE_DIR = PROJECT_ROOT / "testretrieval" / "datasets"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "testretrieval" / "outputs"


def _load_root_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_root_env()
sys.path.insert(0, str(BACKEND_DIR))

try:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
    from app.db.session import SessionLocal, init_db
    from app.schemas import KnowledgeBaseCreate
    from app.services.knowledge_database_service import (
        _current_embedding_snapshot,
        _store_chunks,
        create_knowledge_base,
        delete_knowledge_base,
    )
    from app.services.qdrant_service import ensure_collection, upsert_knowledge_chunks
    from app.services.retrieval_providers import build_embedding_provider, validate_embedding_dimensions
    from app.services.retrieval_service import retrieve_chunks
    from app.services.sparse_bm25 import build_bm25_sparse_vectors
except Exception as exc:  # pragma: no cover - this is an operator-facing script.
    raise SystemExit(
        "Failed to import backend modules. Run this script from the project environment "
        "after installing backend dependencies, for example: `cd backend && python -m pip install -e .`.\n"
        f"Details: {exc}"
    ) from exc


DATASET_REPOS = {
    "T2Retrieval": "C-MTEB/T2Retrieval",
    "MMarcoRetrieval": "C-MTEB/MMarcoRetrieval",
    "DuRetrieval": "C-MTEB/DuRetrieval",
}


@dataclass(frozen=True)
class CorpusDoc:
    doc_id: str
    text: str


@dataclass(frozen=True)
class EvalQuery:
    query_id: str
    text: str
    relevance: dict[str, float]


@dataclass(frozen=True)
class DatasetBundle:
    name: str
    repo: str
    queries: list[EvalQuery]
    documents: list[CorpusDoc]
    signature: str


class EvalConfigError(RuntimeError):
    pass


def main() -> None:
    args = parse_args()
    ks = parse_ks(args.ks)
    dataset_names = [normalize_dataset_name(item) for item in args.datasets]
    output_dir = resolve_project_path(args.output_dir)
    cache_dir = resolve_project_path(args.dataset_cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    validate_runtime_config()
    init_db()

    all_query_results: list[dict[str, Any]] = []
    dataset_metrics: dict[str, dict[str, float]] = {}

    with SessionLocal() as db:
        for dataset_name in dataset_names:
            repo = DATASET_REPOS[dataset_name]
            print(f"\n=== Loading {repo} ===", flush=True)
            bundle = load_eval_dataset(
                dataset_name=dataset_name,
                repo=repo,
                cache_dir=cache_dir,
                max_queries=args.max_queries,
                negative_docs=args.negative_docs,
                seed=args.seed,
            )
            print(
                f"Prepared {len(bundle.queries)} queries and {len(bundle.documents)} corpus documents.",
                flush=True,
            )

            kb = build_or_reuse_eval_kb(
                db=db,
                owner_user_id=args.owner_user_id,
                bundle=bundle,
                reset_index=args.reset_index,
                upsert_batch_size=args.upsert_batch_size,
            )

            query_results = run_retrieval_eval(
                db=db,
                owner_user_id=args.owner_user_id,
                kb=kb,
                bundle=bundle,
                top_k=args.top_k,
                ks=ks,
                rerank=args.rerank,
                retrieval_mode=args.retrieval_mode,
            )
            metrics = aggregate_metrics(query_results, ks)
            dataset_metrics[dataset_name] = metrics
            all_query_results.extend(query_results)
            print_metrics(dataset_name, metrics, ks)

    overall_metrics = aggregate_metrics(all_query_results, ks)
    print_metrics("overall", overall_metrics, ks)
    write_outputs(
        output_dir=output_dir,
        args=args,
        ks=ks,
        dataset_metrics=dataset_metrics,
        overall_metrics=overall_metrics,
        query_results=all_query_results,
    )
    print(f"\nWrote outputs to {output_dir}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal C-MTEB offline retrieval evaluation.")
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=["T2Retrieval", "MMarcoRetrieval", "DuRetrieval"],
        help="Dataset names: T2Retrieval MMarcoRetrieval DuRetrieval.",
    )
    parser.add_argument("--max-queries", type=int, default=20)
    parser.add_argument("--negative-docs", type=int, default=200)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--ks", default="1,3,5,10")
    parser.add_argument("--retrieval-mode", choices=["dense", "sparse", "hybrid"], default="hybrid")
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--reset-index", action="store_true")
    parser.add_argument("--owner-user-id", default="c-mteb-eval")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--dataset-cache-dir", default=str(DEFAULT_DATASET_CACHE_DIR))
    parser.add_argument("--upsert-batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=13)
    return parser.parse_args()


def parse_ks(value: str) -> list[int]:
    ks: list[int] = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        k = int(item)
        if k <= 0:
            raise ValueError("--ks values must be positive integers.")
        if k not in ks:
            ks.append(k)
    if not ks:
        raise ValueError("--ks must contain at least one positive integer.")
    return sorted(ks)


def normalize_dataset_name(value: str) -> str:
    compact = str(value or "").strip().split("/")[-1]
    for dataset_name in DATASET_REPOS:
        if compact.lower() == dataset_name.lower():
            return dataset_name
    valid = ", ".join(DATASET_REPOS)
    raise ValueError(f"Unsupported dataset {value!r}. Valid options: {valid}.")


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def validate_runtime_config() -> None:
    try:
        snapshot = _current_embedding_snapshot(require_api_key=True)
    except Exception as exc:
        raise EvalConfigError(
            "Knowledge embedding config is incomplete. Set KNOWLEDGE_EMBEDDING_PROVIDER, "
            "KNOWLEDGE_EMBEDDING_MODEL, KNOWLEDGE_EMBEDDING_DIMENSION, "
            "KNOWLEDGE_EMBEDDING_API_KEY, and optionally KNOWLEDGE_EMBEDDING_BASE_URL in .env."
        ) from exc
    print(
        "Embedding config: "
        f"{snapshot['provider']}/{snapshot['model']} dim={snapshot['dimension']} base_url={snapshot['base_url']}",
        flush=True,
    )


def load_eval_dataset(
    dataset_name: str,
    repo: str,
    cache_dir: Path,
    max_queries: int,
    negative_docs: int,
    seed: int,
) -> DatasetBundle:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise EvalConfigError(
            "The `datasets` package is required for C-MTEB loading. Install it in your backend "
            "environment, for example: `python -m pip install datasets`."
        ) from exc

    dataset = load_default_dataset(load_dataset, repo, cache_dir)
    qrel_dataset = load_default_dataset(load_dataset, f"{repo}-qrels", cache_dir)
    corpus_rows = preferred_split(dataset, "corpus")
    query_rows = preferred_split(dataset, "queries")
    qrel_rows = preferred_split(qrel_dataset, None)

    corpus = normalize_corpus(corpus_rows)
    queries = normalize_queries(query_rows)
    qrels = normalize_qrels(qrel_rows)
    selected_queries, selected_doc_ids = select_eval_subset(
        corpus=corpus,
        queries=queries,
        qrels=qrels,
        max_queries=max_queries,
        negative_docs=negative_docs,
        seed=seed,
    )
    documents = [corpus[doc_id] for doc_id in selected_doc_ids]
    signature = dataset_signature(dataset_name, selected_queries, selected_doc_ids)
    return DatasetBundle(
        name=dataset_name,
        repo=repo,
        queries=selected_queries,
        documents=documents,
        signature=signature,
    )


def load_default_dataset(load_dataset: Any, repo: str, cache_dir: Path) -> Any:
    return load_dataset(repo, cache_dir=str(cache_dir))


def preferred_split(data: Any, config: str | None) -> Any:
    if not isinstance(data, dict):
        return data
    preferred = []
    if config:
        preferred.append(config)
    preferred.extend(["test", "dev", "validation", "train", "corpus", "queries"])
    for split_name in preferred:
        if split_name in data:
            return data[split_name]
    if data:
        first_key = next(iter(data.keys()))
        return data[first_key]
    raise ValueError("Loaded an empty HuggingFace dataset.")


def normalize_corpus(rows: Any) -> dict[str, CorpusDoc]:
    corpus: dict[str, CorpusDoc] = {}
    for row in rows:
        doc_id = field_as_str(row, ["_id", "id", "doc_id", "docid", "corpus_id", "corpus-id"])
        text = corpus_text(row)
        if doc_id and text:
            corpus[doc_id] = CorpusDoc(doc_id=doc_id, text=text)
    if not corpus:
        raise ValueError("Could not normalize any corpus rows. Check the C-MTEB corpus schema.")
    return corpus


def normalize_queries(rows: Any) -> dict[str, str]:
    queries: dict[str, str] = {}
    for row in rows:
        query_id = field_as_str(row, ["_id", "id", "query_id", "query-id", "qid"])
        text = field_as_str(row, ["text", "query", "question", "content", "sentence"])
        if query_id and text:
            queries[query_id] = text
    if not queries:
        raise ValueError("Could not normalize any query rows. Check the C-MTEB query schema.")
    return queries


def normalize_qrels(rows: Any) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    for row in rows:
        query_id = field_as_str(row, ["query-id", "query_id", "qid", "query"])
        doc_id = field_as_str(row, ["corpus-id", "corpus_id", "doc_id", "docid", "pid", "document"])
        score = field_as_float(row, ["score", "relevance", "label"], default=1.0)
        if query_id and doc_id and score > 0:
            qrels.setdefault(query_id, {})[doc_id] = score
    if not qrels:
        raise ValueError("Could not normalize any qrels rows. Check the C-MTEB qrels schema.")
    return qrels


def corpus_text(row: dict[str, Any]) -> str:
    title = field_as_str(row, ["title", "name"])
    text = field_as_str(row, ["text", "contents", "content", "passage", "document"])
    if title and text and title not in text:
        return f"{title}\n{text}".strip()
    return (text or title).strip()


def field_as_str(row: dict[str, Any], names: list[str]) -> str:
    for name in names:
        if name not in row:
            continue
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            value = " ".join(str(item) for item in value if item is not None)
        text = str(value).strip()
        if text:
            return text
    return ""


def field_as_float(row: dict[str, Any], names: list[str], default: float) -> float:
    for name in names:
        if name not in row:
            continue
        try:
            return float(row.get(name))
        except (TypeError, ValueError):
            continue
    return default


def select_eval_subset(
    corpus: dict[str, CorpusDoc],
    queries: dict[str, str],
    qrels: dict[str, dict[str, float]],
    max_queries: int,
    negative_docs: int,
    seed: int,
) -> tuple[list[EvalQuery], list[str]]:
    if max_queries <= 0:
        raise ValueError("--max-queries must be positive for the minimal evaluation flow.")
    rng = random.Random(seed)
    selected_queries: list[EvalQuery] = []
    positive_doc_ids: list[str] = []
    positive_seen: set[str] = set()

    for query_id in sorted(qrels):
        if query_id not in queries:
            continue
        relevance = {doc_id: score for doc_id, score in qrels[query_id].items() if doc_id in corpus and score > 0}
        if not relevance:
            continue
        selected_queries.append(EvalQuery(query_id=query_id, text=queries[query_id], relevance=relevance))
        for doc_id in relevance:
            if doc_id not in positive_seen:
                positive_seen.add(doc_id)
                positive_doc_ids.append(doc_id)
        if len(selected_queries) >= max_queries:
            break

    if not selected_queries:
        raise ValueError("No usable queries were found after intersecting queries, corpus, and qrels.")

    negative_pool = [doc_id for doc_id in corpus if doc_id not in positive_seen]
    rng.shuffle(negative_pool)
    selected_doc_ids = [*positive_doc_ids, *negative_pool[: max(negative_docs, 0)]]
    return selected_queries, selected_doc_ids


def dataset_signature(dataset_name: str, queries: list[EvalQuery], doc_ids: list[str]) -> str:
    payload = {
        "dataset": dataset_name,
        "query_ids": [query.query_id for query in queries],
        "doc_ids": doc_ids,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_or_reuse_eval_kb(
    db: Session,
    owner_user_id: str,
    bundle: DatasetBundle,
    reset_index: bool,
    upsert_batch_size: int,
) -> KnowledgeBase:
    name = f"C-MTEB::{bundle.name}::minimal"
    existing = find_eval_kb(db, owner_user_id, name)
    if existing and reset_index:
        print(f"Resetting existing knowledge base {name}.", flush=True)
        delete_knowledge_base(db, existing)
        existing = None

    if existing:
        metadata = dict((existing.config_json or {}).get("c_mteb_eval") or {})
        if metadata.get("signature") == bundle.signature and eval_kb_is_ready(db, existing, len(bundle.documents)):
            print(f"Reusing existing knowledge base {name}.", flush=True)
            return existing
        raise EvalConfigError(
            f"Knowledge base {name} already exists, but it is incomplete or differs from this run. "
            "Re-run with --reset-index to rebuild it."
        )

    print(f"Building knowledge base {name}. This may call the configured embedding API.", flush=True)
    kb = create_knowledge_base(
        db,
        KnowledgeBaseCreate(
            name=name,
            description=f"Minimal C-MTEB evaluation index for {bundle.repo}",
        ),
        owner_user_id,
    )
    kb.config_json = {
        **dict(kb.config_json or {}),
        "c_mteb_eval": {
            "dataset": bundle.name,
            "repo": bundle.repo,
            "signature": bundle.signature,
            "document_count": len(bundle.documents),
            "query_count": len(bundle.queries),
        },
    }
    db.commit()
    db.refresh(kb)
    index_documents(db, kb, bundle.documents, upsert_batch_size)
    return kb


def find_eval_kb(db: Session, owner_user_id: str, name: str) -> KnowledgeBase | None:
    return db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.owner_user_id == owner_user_id,
            KnowledgeBase.scope == "creator",
            KnowledgeBase.name == name,
        )
    )


def eval_kb_is_ready(db: Session, kb: KnowledgeBase, expected_documents: int) -> bool:
    documents = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id)))
    if not kb.locked or len(documents) != expected_documents:
        return False
    return all(document.status == "ready" for document in documents)


def index_documents(db: Session, kb: KnowledgeBase, documents: list[CorpusDoc], upsert_batch_size: int) -> None:
    provider = build_embedding_provider(kb)
    vector_chunks: list[KnowledgeChunk] = []
    for index, doc in enumerate(documents, start=1):
        document = KnowledgeDocument(
            knowledge_base_id=kb.id,
            filename=safe_filename(doc.doc_id),
            file_path=f"c-mteb://{kb.name}/{doc.doc_id}",
            mime_type="text/plain",
            status="chunking",
            metadata_json={"c_mteb_doc_id": doc.doc_id},
        )
        db.add(document)
        db.flush()
        chunks = _store_chunks(
            db=db,
            kb=kb,
            document=document,
            elements=[
                {
                    "text": doc.text,
                    "element_type": "NarrativeText",
                    "chunk_type": "text",
                    "section": None,
                    "page_num": None,
                    "metadata": {"c_mteb_doc_id": doc.doc_id},
                }
            ],
        )
        for chunk in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)):
            metadata = dict(chunk.metadata_json or {})
            metadata["source_file"] = doc.doc_id
            metadata["c_mteb_doc_id"] = doc.doc_id
            chunk.metadata_json = metadata
        document.status = "embedding"
        vector_chunks.extend(chunks)
        if index % 25 == 0 or index == len(documents):
            print(f"  stored chunks for {index}/{len(documents)} documents", flush=True)

    db.commit()
    if not vector_chunks:
        raise ValueError("No chunks were created for the selected C-MTEB documents.")

    print(f"Embedding and upserting {len(vector_chunks)} chunks.", flush=True)
    vectors = provider.embed_documents([chunk.content for chunk in vector_chunks])
    validate_embedding_dimensions(vectors, kb.embedding_dimension)
    sparse_vectors = build_bm25_sparse_vectors([chunk.content for chunk in vector_chunks])
    for chunk in vector_chunks:
        chunk.qdrant_point_id = chunk.id
    db.flush()
    ensure_collection(kb)
    upsert_chunks_in_batches(kb, vector_chunks, vectors, sparse_vectors, max(upsert_batch_size, 1))
    for document in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id)):
        document.status = "ready"
        document.error = ""
    kb.locked = True
    db.commit()
    db.refresh(kb)


def upsert_chunks_in_batches(
    kb: KnowledgeBase,
    chunks: list[KnowledgeChunk],
    vectors: list[list[float]],
    sparse_vectors: list[dict[str, list[int] | list[float]]],
    batch_size: int,
) -> None:
    total = len(chunks)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch_chunks = chunks[start:end]
        batch_vectors = vectors[start:end]
        batch_sparse_vectors = sparse_vectors[start:end]
        upsert_knowledge_chunks(
            kb,
            batch_chunks,
            batch_vectors,
            batch_sparse_vectors,
        )
        print(f"  upserted chunks {end}/{total}", flush=True)


def safe_filename(value: str) -> str:
    compact = str(value or "doc").replace("\\", "_").replace("/", "_").strip()
    return compact[:255] or "doc"


def run_retrieval_eval(
    db: Session,
    owner_user_id: str,
    kb: KnowledgeBase,
    bundle: DatasetBundle,
    top_k: int,
    ks: list[int],
    rerank: bool,
    retrieval_mode: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    retrieval_node = {
        "enabled": True,
        "knowledge_base_ids": [kb.id],
        "retrieval_top_k": max(top_k, 0),
        "retrieval_strategy": retrieval_mode,
        "rerank_enabled": rerank,
        "query_enhancement_enabled": False,
    }
    for index, query in enumerate(bundle.queries, start=1):
        started = time.perf_counter()
        retrieval = retrieve_chunks(
            db=db,
            owner_user_id=owner_user_id,
            query=query.text,
            retrieval_node=retrieval_node,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        chunks = retrieval.get("chunks", [])
        retrieved_doc_ids = dedupe_doc_ids([str(chunk.get("source_file") or "") for chunk in chunks])
        metrics = query_metrics(retrieved_doc_ids, query.relevance, ks)
        row = {
            "dataset": bundle.name,
            "query_id": query.query_id,
            "query_text": query.text,
            "expected_doc_ids": list(query.relevance.keys()),
            "retrieved_doc_ids": retrieved_doc_ids,
            "retrieved_chunks": [
                {
                    "rank": rank,
                    "doc_id": chunk.get("source_file"),
                    "chunk_id": chunk.get("chunk_id"),
                    "score": chunk.get("score"),
                    "retrieval_source": chunk.get("retrieval_source"),
                    "content": str(chunk.get("content") or "")[:500],
                }
                for rank, chunk in enumerate(chunks, start=1)
            ],
            "metrics": metrics,
            "retrieval_metadata": retrieval.get("metadata", {}),
            "latency_ms": latency_ms,
        }
        results.append(row)
        print(f"  evaluated {index}/{len(bundle.queries)} queries", flush=True)
    return results


def dedupe_doc_ids(doc_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for doc_id in doc_ids:
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        deduped.append(doc_id)
    return deduped


def query_metrics(retrieved_doc_ids: list[str], relevance: dict[str, float], ks: list[int]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    relevant_ids = {doc_id for doc_id, score in relevance.items() if score > 0}
    for k in ks:
        top_docs = retrieved_doc_ids[:k]
        hits = [doc_id for doc_id in top_docs if doc_id in relevant_ids]
        metrics[f"Recall@{k}"] = len(set(hits)) / max(len(relevant_ids), 1)
        metrics[f"Precision@{k}"] = len(hits) / k
        metrics[f"MRR@{k}"] = reciprocal_rank(top_docs, relevant_ids)
        metrics[f"nDCG@{k}"] = ndcg(top_docs, relevance, k)
    return metrics


def reciprocal_rank(top_docs: list[str], relevant_ids: set[str]) -> float:
    for rank, doc_id in enumerate(top_docs, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg(top_docs: list[str], relevance: dict[str, float], k: int) -> float:
    dcg = 0.0
    for rank, doc_id in enumerate(top_docs[:k], start=1):
        rel = float(relevance.get(doc_id, 0.0) or 0.0)
        if rel <= 0:
            continue
        dcg += ((2.0**rel) - 1.0) / math.log2(rank + 1)
    ideal_rels = sorted((score for score in relevance.values() if score > 0), reverse=True)[:k]
    idcg = sum(((2.0**rel) - 1.0) / math.log2(rank + 1) for rank, rel in enumerate(ideal_rels, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def aggregate_metrics(query_results: list[dict[str, Any]], ks: list[int]) -> dict[str, float]:
    keys = [f"{metric}@{k}" for metric in ["Recall", "Precision", "MRR", "nDCG"] for k in ks]
    if not query_results:
        return {key: 0.0 for key in keys}
    aggregated: dict[str, float] = {}
    for key in keys:
        aggregated[key] = sum(float(row["metrics"].get(key, 0.0)) for row in query_results) / len(query_results)
    aggregated["query_count"] = float(len(query_results))
    aggregated["latency_ms_avg"] = sum(float(row["latency_ms"]) for row in query_results) / len(query_results)
    aggregated["latency_ms_p95"] = percentile([float(row["latency_ms"]) for row in query_results], 0.95)
    return aggregated


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(math.ceil(len(ordered) * p) - 1, 0), len(ordered) - 1)
    return ordered[index]


def print_metrics(label: str, metrics: dict[str, float], ks: list[int]) -> None:
    print(f"\n--- {label} ---")
    print(f"queries={int(metrics.get('query_count', 0))}")
    for metric_name in ["Recall", "Precision", "MRR", "nDCG"]:
        values = " ".join(f"{metric_name}@{k}={metrics.get(f'{metric_name}@{k}', 0.0):.4f}" for k in ks)
        print(values)
    print(
        f"latency_ms_avg={metrics.get('latency_ms_avg', 0.0):.1f} "
        f"latency_ms_p95={metrics.get('latency_ms_p95', 0.0):.1f}"
    )


def write_outputs(
    output_dir: Path,
    args: argparse.Namespace,
    ks: list[int],
    dataset_metrics: dict[str, dict[str, float]],
    overall_metrics: dict[str, float],
    query_results: list[dict[str, Any]],
) -> None:
    max_k = max(ks)
    metrics_payload = {
        "config": {
            "datasets": args.datasets,
            "max_queries": args.max_queries,
            "negative_docs": args.negative_docs,
            "top_k": args.top_k,
            "ks": ks,
            "rerank": args.rerank,
            "owner_user_id": args.owner_user_id,
        },
        "datasets": dataset_metrics,
        "overall": overall_metrics,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "per_query_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in query_results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output_dir / "failed_cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in query_results:
            if float(row["metrics"].get(f"Recall@{max_k}", 0.0)) <= 0.0:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except EvalConfigError as exc:
        raise SystemExit(str(exc)) from exc
