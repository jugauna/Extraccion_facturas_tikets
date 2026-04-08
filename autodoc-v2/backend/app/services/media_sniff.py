from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def trim_leading_pdf(data: bytes, search_limit: int = 65536) -> bytes | None:
    """
    Devuelve bytes a partir de %PDF- si el archivo es PDF con basura/BOM delante.
    Si no hay firma PDF en el tramo buscado, devuelve None.
    """
    if not data or len(data) < 5:
        return None
    if data.startswith(b"%PDF-"):
        return data
    if data.startswith(b"\xef\xbb\xbf%PDF"):
        return data[3:]
    lim = min(len(data), search_limit)
    i = data.find(b"%PDF-", 0, lim)
    if i >= 0:
        logger.info("PDF detectado con %s byte(s) previos al %%PDF-; se usa desde el offset", i)
        return data[i:]
    return None
