from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

from app.services.media_sniff import binary_format_hint, friendly_decoder_error, trim_leading_pdf
from app.services.pdf_pages import pdf_bytes_to_jpeg_pages

logger = logging.getLogger(__name__)


def _register_heif_opener() -> None:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        logger.info("Soporte HEIC/HEIF activo (pillow-heif)")
    except Exception as e:
        logger.warning("pillow-heif no disponible: HEIC de iPhone puede fallar (%s)", e)


_register_heif_opener()


def _looks_like_isobmff_heic(b: bytes) -> bool:
    """ISO BMFF con marca típica de HEIC/HEIF (fotos iPhone)."""
    if len(b) < 12 or b[4:8] != b"ftyp":
        return False
    brand = b[8:12]
    if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
        return True
    return b"heic" in b[:64] or b"mif1" in b[:64]


def _decode_image_open_error_message(file_bytes: bytes, filename: str, err: Exception) -> str:
    fn = (filename or "").lower()
    hint = binary_format_hint(file_bytes)
    tail = friendly_decoder_error(err)
    if _looks_like_isobmff_heic(file_bytes) or fn.endswith((".heic", ".heif")):
        return (
            "Archivo HEIC/HEIF (típico de iPhone). Con pillow-heif debería abrirse; "
            "si no, en Cámara usá «Más compatible» o exportá JPG. "
            f"{tail} ({hint})"
        )
    return (
        "No se pudo decodificar como imagen. "
        "Si subís PDF desde n8n, el binario debe ser el PDF real (no JSON ni HTML). "
        f"{tail} Pista: {hint}"
    )

MAX_EDGE = 1400
JPEG_QUALITY = 82
# Vision API solo acepta png / jpeg / gif / webp; re-encodamos a PNG (máxima compatibilidad).
MAX_VISION_EDGE = 2048


def _is_pdf(mime: str, filename: str) -> bool:
    m = (mime or "").split(";")[0].strip().lower()
    return m == "application/pdf" or (filename or "").lower().endswith(".pdf")


def bytes_for_openai_vision(file_bytes: bytes, mime: str, filename: str) -> tuple[bytes, str]:
    """
    Devuelve PNG y ``image/png`` re-encodados para la API Vision.
    Corrige uploads con tipo MIME incorrecto y binarios que no coinciden con el formato declarado.
    """
    if not file_bytes:
        raise ValueError("Archivo vacío")
    # PDF por firma al inicio, por MIME/nombre, o con bytes basura delante de %PDF-.
    # No usar solo _is_pdf_magic: n8n a veces manda PDF como upload.bin + image/jpeg.
    pdf_blob = trim_leading_pdf(file_bytes)
    if pdf_blob is None and _is_pdf(mime, filename):
        pdf_blob = file_bytes

    if pdf_blob is not None and len(pdf_blob) >= 5 and pdf_blob.startswith(b"%PDF-"):
        pages = pdf_bytes_to_jpeg_pages(pdf_blob, dpi=200)
        if not pages:
            raise ValueError("PDF sin páginas renderizables")
        im = Image.open(BytesIO(pages[0]))
    else:
        try:
            im = Image.open(BytesIO(file_bytes))
            im.load()
        except Exception as e:
            raise ValueError(_decode_image_open_error_message(file_bytes, filename, e)) from e
    im = im.convert("RGB")
    im.thumbnail((MAX_VISION_EDGE, MAX_VISION_EDGE), Image.Resampling.LANCZOS)
    buf = BytesIO()
    # PNG evita rechazos puntuales de la API con ciertos JPEG (p. ej. perfiles/progresivos raros).
    im.save(buf, format="PNG", optimize=True, compress_level=6)
    out = buf.getvalue()
    logger.info(
        "Vision payload PNG size=%s orig_mime=%s filename=%s",
        len(out),
        (mime or "").split(";")[0].strip().lower() or "?",
        (filename or "")[:120] or "?",
    )
    return out, "image/png"


def make_preview_jpeg_bytes(file_bytes: bytes, mime: str, filename: str) -> bytes:
    """Primera página / imagen reducida a JPEG para la UI de curación."""
    pdf_blob = trim_leading_pdf(file_bytes)
    if pdf_blob is None and _is_pdf(mime, filename):
        pdf_blob = file_bytes

    if pdf_blob is not None and len(pdf_blob) >= 5 and pdf_blob.startswith(b"%PDF-"):
        pages = pdf_bytes_to_jpeg_pages(pdf_blob, dpi=150)
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


def preview_bytes_or_placeholder(file_bytes: bytes, mime: str, filename: str) -> bytes:
    """Vista previa JPEG para pending: nunca devuelve buffer vacío."""
    raw = preview_for_curation_ui(file_bytes, mime, filename)
    if len(raw) < 32:
        return placeholder_preview_jpeg_bytes()
    return raw
