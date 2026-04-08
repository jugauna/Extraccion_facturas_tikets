from __future__ import annotations

import json
import logging

from app.services.curation_store import read_gold_rows_for_few_shot

logger = logging.getLogger(__name__)


def build_few_shot_addon() -> str:
    examples = read_gold_rows_for_few_shot(3)
    if not examples:
        return ""
    parts = [
        "\n\n---\nEJEMPLOS VALIDADOS POR HUMANO (gold_dataset, últimos hasta 3). "
        "Usalos solo como referencia de formato y estilo de valores; no copies datos de negocio de otros comprobantes.\n"
    ]
    for i, ex in enumerate(examples, 1):
        parts.append(f"Ejemplo {i} (JSON rows):\n```json\n{json.dumps(ex, ensure_ascii=False, indent=2)}\n```\n")
    return "".join(parts)
