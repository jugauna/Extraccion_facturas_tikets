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


def placeholder_preview_jpeg_bytes() -> bytes:
    """Imagen mínima para la UI si no se puede renderizar el comprobante."""
    im = Image.new("RGB", (320, 120), (241, 245, 249))
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=75, optimize=True)
    return buf.getvalue()


def preview_for_curation_ui(file_bytes: bytes, mime: str, filename: str) -> bytes:
    """
    JPEG para la pantalla de curación: preview normal, o imagen sin PDF/poppler,
    o placeholder (nunca debe fallar: la sesión pending siempre tiene vista).
    """
    try:
        return make_preview_jpeg_bytes(file_bytes, mime, filename)
    except Exception:
        logger.warning("Preview PDF/completo falló (%s); se prueba solo imagen o placeholder", filename)

    m = (mime or "").split(";")[0].strip().lower()
    if m.startswith("image/"):
        try:
            im = Image.open(BytesIO(file_bytes))
            im = im.convert("RGB")
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue()
        except Exception:
            logger.warning("Preview como imagen falló (%s); se usa placeholder", filename)

    return placeholder_preview_jpeg_bytes()
