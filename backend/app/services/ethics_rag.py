from __future__ import annotations

import logging
import math
import re
from pathlib import Path

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

_ETHICS_PATH = Path(__file__).resolve().parent.parent / "data" / "ethics_manual.txt"
_CHUNK_CHARS = 450
_EMBED_MODEL = "text-embedding-3-small"

_cache_chunks: list[str] | None = None
_cache_vectors: list[list[float]] | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_chunks() -> list[str]:
    global _cache_chunks
    if _cache_chunks is not None:
        return _cache_chunks
    text = ""
    try:
        text = _ETHICS_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Manual de ética no encontrado en %s", _ETHICS_PATH)
    text = text.strip() or "Política de gastos: gastos deben ser razonables y documentados."
    parts = re.split(r"\n\s*\n+", text)
    chunks: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= _CHUNK_CHARS:
            chunks.append(p)
        else:
            for i in range(0, len(p), _CHUNK_CHARS):
                chunks.append(p[i : i + _CHUNK_CHARS])
    _cache_chunks = chunks
    return chunks


def _embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=_EMBED_MODEL, input=texts)
    ordered = sorted(resp.data, key=lambda d: d.index)
    return [d.embedding for d in ordered]


def _ensure_vectors(client: OpenAI) -> tuple[list[str], list[list[float]]]:
    global _cache_vectors
    chunks = _load_chunks()
    if _cache_vectors is not None and len(_cache_vectors) == len(chunks):
        return chunks, _cache_vectors
    logger.info("Embeddings manual ética: %s chunk(s)", len(chunks))
    _cache_vectors = _embed_batch(client, chunks)
    return chunks, _cache_vectors


def analyze_expense_text(detalle: str) -> dict:
    """
    Compara el texto del gasto (p. ej. Detalle) contra el manual vía embeddings.
    Devuelve top coincidencias y si conviene revisión humana.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    text = (detalle or "").strip()
    if not text:
        return {
            "detalle_empty": True,
            "needs_review": False,
            "max_similarity": 0.0,
            "matches": [],
            "threshold": settings.ethics_similarity_review_threshold,
            "note": "Sin texto para comparar",
        }

    client = OpenAI(api_key=settings.openai_api_key)
    chunks, vectors = _ensure_vectors(client)
    qvec = _embed_batch(client, [text])[0]

    scored: list[tuple[float, str]] = []
    for ch, vec in zip(chunks, vectors):
        scored.append((_cosine(qvec, vec), ch))
    scored.sort(key=lambda x: -x[0])
    top = scored[:3]
    max_sim = top[0][0] if top else 0.0

    threshold = settings.ethics_similarity_review_threshold
    needs = max_sim >= threshold

    return {
        "detalle_empty": False,
        "needs_review": needs,
        "max_similarity": round(max_sim, 4),
        "threshold": threshold,
        "matches": [{"similarity": round(s, 4), "snippet": c[:280]} for s, c in top],
        "note": "needs_review indica alta similitud con alguna sección del manual; revisar política.",
    }
