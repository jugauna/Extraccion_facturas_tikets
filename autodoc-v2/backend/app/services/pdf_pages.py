from __future__ import annotations

import logging
from io import BytesIO

from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)


def pdf_bytes_to_jpeg_pages(pdf_bytes: bytes, dpi: int = 300) -> list[bytes]:
    """Renderiza cada página del PDF a JPEG (RGB) a la DPI indicada (por defecto 300)."""
    pil_images = convert_from_bytes(pdf_bytes, dpi=dpi)
    out: list[bytes] = []
    for im in pil_images:
        rgb = im.convert("RGB")
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=92, optimize=True)
        out.append(buf.getvalue())
    logger.info("PDF renderizado: %s página(s) a JPEG @ %s DPI", len(out), dpi)
    return out
