from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

from app.services.pdf_pages import pdf_bytes_to_jpeg_pages

logger = logging.getLogger(__name__)

MAX_EDGE = 1400
JPEG_QUALITY = 82


def _is_pdf(mime: str, filename: str) -> bool:
    m = (mime or "").split(";")[0].strip().lower()
    return m == "application/pdf" or (filename or "").lower().endswith(".pdf")


def make_preview_jpeg_bytes(file_bytes: bytes, mime: str, filename: str) -> bytes:
    """Primera página / imagen reducida a JPEG para la UI de curación."""
    if _is_pdf(mime, filename):
        pages = pdf_bytes_to_jpeg_pages(file_bytes, dpi=150)
        if not pages:
            raise ValueError("PDF sin páginas renderizables")
        raw = pages[0]
    else:
        raw = file_bytes

    im = Image.open(BytesIO(raw))
    im = im.convert("RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    out = buf.getvalue()
    logger.info("Preview JPEG %s bytes (orig mime=%s)", len(out), mime)
    return out
