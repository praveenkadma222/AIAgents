from __future__ import annotations

import math
import os
from typing import Any

from .analyzer import document_hash
from .database import CodeGraphDB

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_models: dict[str, Any] = {}


def _model(model_id: str) -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError(
            "Semantic search requires fastembed. Install project dependencies with "
            "'python -m pip install -r requirements.txt'."
        ) from exc
    if model_id not in _models:
        _models[model_id] = TextEmbedding(model_name=model_id)
    return _models[model_id]


def search(db: CodeGraphDB, query: str, limit: int = 10) -> list[dict[str, Any]]:
    if not query.strip():
        raise ValueError("query must not be empty.")
    model_id = os.environ.get("CODEGRAPH_EMBEDDING_MODEL", DEFAULT_MODEL)
    model = _model(model_id)
    symbols = [symbol for symbol in db.symbols() if symbol["kind"] != "module"]
    cache = db.embedding_cache(model_id)
    missing: list[tuple[str, str, str]] = []
    for symbol in symbols:
        text = "\n".join(part for part in (
            f"{symbol['kind']} {symbol['qualified_name']}", symbol["signature"],
            symbol["docstring"], symbol["source"],
        ) if part)
        content_hash = document_hash(text)
        cached = cache.get(symbol["id"])
        if not cached or cached[0] != content_hash:
            missing.append((symbol["id"], content_hash, text))
    if missing:
        vectors = model.embed([item[2] for item in missing])
        db.save_embeddings(
            model_id,
            [(item[0], item[1], vector.tolist() if hasattr(vector, "tolist") else list(vector))
             for item, vector in zip(missing, vectors)],
        )
        cache = db.embedding_cache(model_id)
    query_vector = next(iter(model.query_embed(query)), None)
    if query_vector is None:
        raise RuntimeError("The local embedding model returned no query vector.")
    query_values = query_vector.tolist() if hasattr(query_vector, "tolist") else list(query_vector)
    query_norm = math.sqrt(sum(value * value for value in query_values))
    if query_norm == 0:
        raise RuntimeError("The local embedding model returned an empty query vector.")
    results: list[dict[str, Any]] = []
    by_id = {symbol["id"]: symbol for symbol in symbols}
    for symbol_id, (content_hash, vector) in cache.items():
        symbol = by_id.get(symbol_id)
        if not symbol or len(vector) != len(query_values):
            continue
        norm = math.sqrt(sum(value * value for value in vector))
        score = sum(left * right for left, right in zip(query_values, vector)) / (query_norm * norm) if norm else 0
        results.append({
            "score": round(score, 4),
            "symbol": {key: symbol[key] for key in (
                "id", "name", "qualified_name", "kind", "file_path", "line",
                "signature", "docstring", "source", "language",
            )},
        })
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]
