from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


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


def save_pending(
    task_id: str,
    submission_token: str,
    batch_id: str,
    filename: str,
    drive_link: str,
    preview_jpeg: bytes,
    rows: list[dict[str, str]],
) -> None:
    payload = {
        "task_id": task_id,
        "submission_token": submission_token,
        "batch_id": batch_id,
        "filename": filename,
        "drive_web_view_link": drive_link,
        "preview_mime": "image/jpeg",
        "preview_base64": base64.standard_b64encode(preview_jpeg).decode("ascii"),
        "rows": rows,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _pending_dir() / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Curation pending guardado task_id=%s", task_id)


def load_pending(task_id: str) -> dict[str, Any] | None:
    path = _pending_dir() / f"{task_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("pending corrupto %s", task_id)
        return None


def delete_pending(task_id: str) -> None:
    path = _pending_dir() / f"{task_id}.json"
    if path.is_file():
        path.unlink()


def verify_token(pending: dict[str, Any], token: str | None) -> bool:
    if not token:
        return False
    return (pending.get("submission_token") or "") == token


def save_gold_example(
    task_id: str,
    batch_id: str,
    filename: str,
    rows: list[dict[str, str]],
    drive_link: str,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}_{task_id[:8]}.json"
    path = _gold_dir() / name
    doc = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "batch_id": batch_id,
        "filename": filename,
        "drive_web_view_link": drive_link,
        "rows": rows,
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
