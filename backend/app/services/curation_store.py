from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _gcs_client():
    from google.cloud import storage

    return storage.Client()


def _pending_gcs_bucket() -> str:
    return (get_settings().curation_pending_gcs_bucket or "").strip()


def _pending_gcs_blob_path(task_id: str) -> str:
    p = (get_settings().curation_pending_gcs_prefix or "curation_pending").strip().strip("/")
    return f"{p}/{task_id}.json" if p else f"{task_id}.json"


def _gold_dir() -> Path:
    root = Path(get_settings().resolve_data_dir())
    d = root / "gold_dataset"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pending_dir() -> Path:
    root = Path(get_settings().resolve_data_dir())
    d = root / "curation_pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_pending_batch(
    task_id: str,
    submission_token: str,
    batch_id: str,
    user_notes: str,
    docs: list[dict[str, Any]],
) -> None:
    """
    Guarda una sesión de curación (lote) con N documentos.
    Cada doc debe incluir:
      - filename, mime
      - original_base64 (bytes originales)
      - preview_mime, preview_base64 (para UI)
      - rows (lista de dicts para sheets/UI)
    """
    payload = {
        "task_id": task_id,
        "submission_token": submission_token,
        "batch_id": batch_id,
        "user_notes": user_notes,
        "docs": docs,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    bucket_name = _pending_gcs_bucket()
    if bucket_name:
        blob = _gcs_client().bucket(bucket_name).blob(_pending_gcs_blob_path(task_id))
        blob.upload_from_string(raw, content_type="application/json; charset=utf-8")
        logger.info("Curation pending GCS task_id=%s docs=%s bucket=%s", task_id, len(docs), bucket_name)
    else:
        path = _pending_dir() / f"{task_id}.json"
        path.write_text(raw, encoding="utf-8")
        logger.info("Curation pending (local) task_id=%s docs=%s", task_id, len(docs))


def load_pending(task_id: str) -> dict[str, Any] | None:
    bucket_name = _pending_gcs_bucket()
    if bucket_name:
        blob = _gcs_client().bucket(bucket_name).blob(_pending_gcs_blob_path(task_id))
        if not blob.exists():
            return None
        try:
            return json.loads(blob.download_as_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("pending GCS corrupto %s", task_id)
            return None

    path = _pending_dir() / f"{task_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("pending corrupto %s", task_id)
        return None


def delete_pending(task_id: str) -> None:
    bucket_name = _pending_gcs_bucket()
    if bucket_name:
        blob = _gcs_client().bucket(bucket_name).blob(_pending_gcs_blob_path(task_id))
        if blob.exists():
            blob.delete()
        return
    path = _pending_dir() / f"{task_id}.json"
    if path.is_file():
        path.unlink()


def verify_token(pending: dict[str, Any], token: str | None) -> bool:
    if not token:
        return False
    return (pending.get("submission_token") or "") == token


def save_gold_batch(
    task_id: str,
    batch_id: str,
    user_notes: str,
    docs: list[dict[str, Any]],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}_{task_id[:8]}_batch.json"
    path = _gold_dir() / name
    doc = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "batch_id": batch_id,
        "user_notes": user_notes,
        "docs": docs,
        "source": "human_curation",
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Gold dataset guardado %s", path)
    return path


def list_recent_gold_paths(limit: int = 3) -> list[Path]:
    d = _gold_dir()
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def read_gold_rows_for_few_shot(limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in list_recent_gold_paths(limit):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            rows = doc.get("rows")
            if isinstance(rows, list) and rows:
                out.append({"rows": rows})
        except (OSError, json.JSONDecodeError):
            logger.warning("Omitiendo gold corrupto %s", path)
    return out
