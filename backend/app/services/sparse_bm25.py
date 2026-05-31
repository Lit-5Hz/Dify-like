from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

import jieba

from app.services.retrieval_defaults import DEFAULT_BM25_B, DEFAULT_BM25_K1

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_CHAR_RE = re.compile(r"[a-z0-9_\u4e00-\u9fff]")
_TOKEN_EDGE_RE = re.compile(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
    "以及",
    "一个",
    "一些",
    "一种",
    "这个",
    "这些",
    "那些",
    "什么",
    "如何",
    "是否",
    "可以",
    "进行",
    "通过",
    "对于",
    "关于",
    "其中",
    "如果",
    "因为",
    "所以",
    "但是",
    "而且",
    "并且",
    "或者",
    "不是",
    "没有",
    "就是",
    "主要",
    "相关",
    "内容",
    "的",
    "了",
    "和",
    "是",
    "在",
    "有",
    "与",
    "及",
    "或",
    "也",
    "都",
    "而",
    "就",
    "并",
    "对",
    "中",
    "为",
    "以",
    "等",
    "其",
    "该",
    "这",
    "那",
    "你",
    "我",
    "他",
    "她",
    "它",
    "们",
}


@dataclass(frozen=True)
class BM25CorpusStats:
    doc_count: int
    avg_doc_length: float
    document_frequencies: dict[str, int]


def tokenize_for_sparse(text: str) -> list[str]:
    value = str(text or "").lower()
    tokens: list[str] = []
    tokens.extend(_normalize_token(token) for token in _ASCII_TOKEN_RE.findall(value))

    if _CJK_RE.search(value):
        for token in jieba.lcut(value, HMM=True):
            normalized = _normalize_token(token)
            if not normalized or _ASCII_TOKEN_RE.fullmatch(normalized):
                continue
            tokens.append(normalized)

    return [token for token in tokens if token]


def build_bm25_sparse_vectors(contents: Sequence[str]) -> list[dict[str, list[int] | list[float]]]:
    tokenized_documents = [tokenize_for_sparse(content) for content in contents]
    stats = build_bm25_stats(tokenized_documents)
    return [bm25_document_sparse_vector(tokens, stats) for tokens in tokenized_documents]


def build_bm25_stats(tokenized_documents: Sequence[Sequence[str]]) -> BM25CorpusStats:
    doc_count = len(tokenized_documents)
    lengths = [len(tokens) for tokens in tokenized_documents]
    avg_doc_length = sum(lengths) / max(doc_count, 1)
    document_frequencies: dict[str, int] = {}
    for tokens in tokenized_documents:
        for token in set(tokens):
            document_frequencies[token] = document_frequencies.get(token, 0) + 1
    return BM25CorpusStats(
        doc_count=doc_count,
        avg_doc_length=avg_doc_length,
        document_frequencies=document_frequencies,
    )


def bm25_document_sparse_vector(
    tokens: Sequence[str],
    stats: BM25CorpusStats,
) -> dict[str, list[int] | list[float]]:
    if not tokens or stats.doc_count <= 0 or stats.avg_doc_length <= 0:
        return {"indices": [], "values": []}

    doc_length = len(tokens)
    weights: dict[str, float] = {}
    for token, frequency in Counter(tokens).items():
        df = stats.document_frequencies.get(token, 0)
        if df <= 0:
            continue
        idf = math.log(1 + (stats.doc_count - df + 0.5) / (df + 0.5))
        denominator = frequency + DEFAULT_BM25_K1 * (
            1 - DEFAULT_BM25_B + DEFAULT_BM25_B * doc_length / stats.avg_doc_length
        )
        if denominator <= 0:
            continue
        weights[token] = idf * (frequency * (DEFAULT_BM25_K1 + 1) / denominator)
    return _sparse_vector_from_weights(weights)


def bm25_query_sparse_vector(text: str) -> dict[str, list[int] | list[float]]:
    tokens = sorted(set(tokenize_for_sparse(text)))
    return _sparse_vector_from_weights({token: 1.0 for token in tokens})


def bm25_scores_for_contents(query: str, contents: Sequence[str]) -> list[float]:
    query_terms = set(tokenize_for_sparse(query))
    if not query_terms or not contents:
        return [0.0 for _ in contents]

    tokenized_documents = [tokenize_for_sparse(content) for content in contents]
    stats = build_bm25_stats(tokenized_documents)
    scores: list[float] = []
    for tokens in tokenized_documents:
        if not tokens or stats.doc_count <= 0 or stats.avg_doc_length <= 0:
            scores.append(0.0)
            continue
        doc_length = len(tokens)
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency <= 0:
                continue
            df = stats.document_frequencies.get(term, 0)
            if df <= 0:
                continue
            idf = math.log(1 + (stats.doc_count - df + 0.5) / (df + 0.5))
            denominator = frequency + DEFAULT_BM25_K1 * (
                1 - DEFAULT_BM25_B + DEFAULT_BM25_B * doc_length / stats.avg_doc_length
            )
            if denominator > 0:
                score += idf * (frequency * (DEFAULT_BM25_K1 + 1) / denominator)
        scores.append(score)
    return scores


def _sparse_vector_from_weights(weights: dict[str, float]) -> dict[str, list[int] | list[float]]:
    merged: dict[int, float] = {}
    for token, value in weights.items():
        if value <= 0:
            continue
        index = _stable_sparse_token_id(token)
        merged[index] = merged.get(index, 0.0) + float(value)
    pairs = sorted(merged.items())
    return {
        "indices": [index for index, _ in pairs],
        "values": [value for _, value in pairs],
    }


def _normalize_token(token: str) -> str:
    normalized = _TOKEN_EDGE_RE.sub("", str(token or "").strip().lower())
    if not normalized or normalized in _STOPWORDS:
        return ""
    if not _TOKEN_CHAR_RE.search(normalized):
        return ""
    return normalized


def _stable_sparse_token_id(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF
