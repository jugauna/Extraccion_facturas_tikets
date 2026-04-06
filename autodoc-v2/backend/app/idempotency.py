from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Optional, Tuple

from app.config import get_settings


class IdempotencyStore:
    """Cache en memoria (por proceso) para deduplicar solicitudes repetidas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Tuple[float, Any]] = {}

    def _prune_locked(self) -> None:
        settings = get_settings()
        now = time.time()
        ttl = float(settings.idempotency_ttl_seconds)
        max_entries = settings.idempotency_max_entries
        expired = [k for k, (ts, _) in self._data.items() if now - ts > ttl]
        for k in expired:
            del self._data[k]
        if len(self._data) > max_entries:
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][0])[: len(self._data) - max_entries]:
                del self._data[k]

    @staticmethod
    def fingerprint(image_bytes: bytes, voice_transcript: str) -> str:
        h = hashlib.sha256()
        h.update(image_bytes)
        h.update(b"\x00")
        h.update(voice_transcript.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            self._prune_locked()
            item = self._data.get(key)
            if not item:
                return None
            ts, payload = item
            if time.time() - ts > get_settings().idempotency_ttl_seconds:
                del self._data[key]
                return None
            return payload

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._prune_locked()
            self._data[key] = (time.time(), value)


idempotency_store = IdempotencyStore()
