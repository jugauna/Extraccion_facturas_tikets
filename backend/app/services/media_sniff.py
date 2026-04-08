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
    # BOM UTF-8 + PDF
    if data.startswith(b"\xef\xbb\xbf%PDF"):
        return data[3:]
    lim = min(len(data), search_limit)
    i = data.find(b"%PDF-", 0, lim)
    if i >= 0:
        logger.info("PDF detectado con %s byte(s) previos al %%PDF-; se usa desde el offset", i)
        return data[i:]
    return None


def binary_format_hint(data: bytes) -> str:
    """Pista corta para mensajes de error (no incluye datos sensibles)."""
    if not data:
        return "contenido vacío."
    n = len(data)
    if n < 32:
        return f"muy pocos bytes ({n}); posible carga incompleta desde n8n."
    if data[:2] == b"PK":
        return "parece un ZIP (Excel/Word/Office); subí PDF o imagen del comprobante."
    head = data[: min(400, n)]
    try:
        t = head.decode("utf-8")
        s = t.lstrip()
        if s.startswith("{") or s.startswith("["):
            return "parece JSON de texto, no un archivo binario de imagen/PDF; revisá el nodo binario en n8n."
        if s.startswith("<!") or s.startswith("<?xml") or s.startswith("<html"):
            return "parece HTML/XML, no imagen; revisá la URL o el body que envía n8n."
    except UnicodeDecodeError:
        pass
    hx = data[:8].hex()
    return f"firma hexadecimal inicial: {hx} (no coincide con JPG/PNG/PDF/WEBP típicos)."


def friendly_decoder_error(exc: Exception) -> str:
    s = str(exc)
    if "cannot identify image file" in s:
        return (
            "El decodificador de imágenes no reconoce el formato (no es un JPG/PNG/GIF/WebP/HEIF válido)."
        )
    return s[:400]
