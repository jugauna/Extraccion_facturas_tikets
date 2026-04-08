from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    autodoc_secret: str = ""
    openai_api_key: str = ""
    openai_model_vision: str = "gpt-4o"
    cors_origins: str = ""
    # Base pública del servicio (sin barra final) para enlaces de curación en correos, ej. https://xxx.run.app
    public_base_url: str = ""
    # Rutas de datos bajo cwd (Cloud Run: /app/data). Con varias instancias, el disco no se comparte:
    # definí CURATION_PENDING_GCS_BUCKET para guardar sesiones de curación en GCS.
    data_dir: str = "data"
    curation_pending_gcs_bucket: str = ""
    curation_pending_gcs_prefix: str = "curation_pending"
    # Si similitud embedding Detalle ↔ manual ≥ umbral, marcar revisión ética
    ethics_similarity_review_threshold: float = 0.78
    # Webhook de n8n para persistencia post-curación (Sheets/Drive/email). Ej: https://.../webhook/autodoc-curation-submit
    persist_webhook_url: str = ""
    # Secreto opcional para el webhook de persistencia (header X-Autodoc-Secret)
    persist_webhook_secret: str = ""

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]

    def resolve_data_dir(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
