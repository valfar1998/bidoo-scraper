"""Matching semantico comps via embeddings (Gemini) con fallback TF-IDF."""

from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any

import requests

from comps import CompRow, load_comps
from database import connect, ensure_db

EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
_CACHE: dict[str, list[float]] = {}


def semantic_comps_enabled() -> bool:
    return os.getenv("SEMANTIC_COMPS", "true").lower() in ("1", "true", "yes")


def _embed_model() -> str:
    return os.getenv("GEMINI_EMBED_MODEL", "text-embedding-004").strip()


def _tokenize(text: str) -> list[str]:
    return [w for w in re.split(r"\W+", text.lower()) if len(w) >= 3]


def _tfidf_vector(text: str, corpus: list[list[str]]) -> dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    df: dict[str, int] = {}
    for doc in corpus:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    n = len(corpus)
    vec: dict[str, float] = {}
    counts: dict[str, int] = {}
    for term in tokens:
        counts[term] = counts.get(term, 0) + 1
    for term, count in counts.items():
        idf = math.log((n + 1) / (df.get(term, 0) + 1)) + 1
        vec[term] = (count / len(tokens)) * idf
    return vec


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in set(a) | set(b))
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _cosine_dense(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _ensure_cache_table() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comp_embeddings (
                product TEXT PRIMARY KEY,
                embedding_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


def _load_cached_embedding(product: str) -> list[float] | None:
    _ensure_cache_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT embedding_json FROM comp_embeddings WHERE product = ?",
            (product,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["embedding_json"])
    except json.JSONDecodeError:
        return None


def _save_cached_embedding(product: str, vector: list[float]) -> None:
    _ensure_cache_table()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO comp_embeddings(product, embedding_json, updated_at)
            VALUES(?, ?, ?)
            """,
            (product, json.dumps(vector), time.time()),
        )


def embed_text(text: str) -> list[float] | None:
    key = text.strip().lower()[:200]
    if key in _CACHE:
        return _CACHE[key]
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        try:
            response = requests.post(
                EMBED_URL.format(model=_embed_model()),
                params={"key": api_key},
                json={"content": {"parts": [{"text": key}]}},
                timeout=30,
            )
            response.raise_for_status()
            values = response.json()["embedding"]["values"]
            _CACHE[key] = values
            return values
        except Exception:
            pass
    return None


def _embedding_for_product(product: str) -> list[float] | None:
    cached = _load_cached_embedding(product)
    if cached:
        return cached
    vector = embed_text(product)
    if vector:
        _save_cached_embedding(product, vector)
    return vector


def match_comp_semantic(title: str, rows: list[CompRow] | None = None) -> tuple[CompRow | None, float]:
    """Ritorna (comp, score 0-1). Soglia default 0.72."""
    if not semantic_comps_enabled():
        return None, 0.0
    rows = rows if rows is not None else load_comps()
    if not rows:
        return None, 0.0
    threshold = float(os.getenv("SEMANTIC_COMPS_THRESHOLD", "0.72"))
    title_vec = embed_text(title)
    if title_vec:
        best_row: CompRow | None = None
        best_score = 0.0
        for row in rows:
            if not row.product:
                continue
            prod_vec = _embedding_for_product(row.product)
            if not prod_vec:
                continue
            score = _cosine_dense(title_vec, prod_vec)
            if score > best_score:
                best_score = score
                best_row = row
        if best_row and best_score >= threshold:
            return best_row, best_score
    # Fallback TF-IDF locale
    corpus = [_tokenize(row.product) for row in rows if row.product]
    if not corpus:
        return None, 0.0
    query_vec = _tfidf_vector(title, corpus)
    best_row = None
    best_score = 0.0
    for row in rows:
        if not row.product:
            continue
        score = _cosine(query_vec, _tfidf_vector(row.product, corpus))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row and best_score >= max(0.35, threshold - 0.25):
        return best_row, best_score
    return None, 0.0
