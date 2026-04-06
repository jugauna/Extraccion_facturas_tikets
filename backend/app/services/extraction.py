from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, List, Tuple

from openai import OpenAI

from app.config import get_settings
from app.schemas import AccountingRow

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "system_gpt4o_vision.txt"


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.exception("No se pudo leer system prompt en %s: %s", _PROMPT_PATH, e)
        raise


def _vision_messages(image_bytes: bytes, mime: str, voice_transcript: str) -> list[dict[str, Any]]:
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    user_text = (
        "Analizá el comprobante en la imagen y emití ÚNICAMENTE el JSON solicitado en el system prompt. "
        "No incluyas markdown ni texto fuera del JSON."
    )
    if voice_transcript.strip():
        user_text += (
            "\n\nContexto adicional (transcripción de voz del usuario; puede aclarar ítems o montos; "
            "si contradice la imagen, priorizá la imagen salvo que la voz corrija un error evidente): "
            f"\n{voice_transcript.strip()}"
        )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ],
        }
    ]


def extract_accounting_rows(image_bytes: bytes, mime: str, voice_transcript: str) -> Tuple[List[AccountingRow], str]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    system_prompt = _load_system_prompt()
    client = OpenAI(api_key=settings.openai_api_key)
    messages = [
        {"role": "system", "content": system_prompt},
        *_vision_messages(image_bytes, mime, voice_transcript),
    ]

    logger.info(
        "OpenAI vision model=%s bytes=%s mime=%s transcript_len=%s",
        settings.openai_model_vision,
        len(image_bytes),
        mime,
        len(voice_transcript or ""),
    )

    try:
        completion = client.chat.completions.create(
            model=settings.openai_model_vision,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4096,
        )
    except Exception:
        logger.exception("Fallo la llamada a OpenAI")
        raise

    raw = completion.choices[0].message.content or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("OpenAI no devolvió JSON válido")
        raise ValueError("La respuesta del modelo no es JSON válido") from None

    rows_raw = payload.get("rows")
    if not isinstance(rows_raw, list):
        raise ValueError('El JSON debe contener una clave "rows" con lista de objetos')

    rows: List[AccountingRow] = []
    for i, item in enumerate(rows_raw):
        if not isinstance(item, dict):
            raise ValueError(f"rows[{i}] debe ser un objeto")
        rows.append(AccountingRow.model_validate(item))

    return rows, raw
