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


def looks_like_real_file_bytes(data: bytes) -> bool:
    """True si los primeros bytes coinciden con PDF, JPEG, PNG, GIF, WebP o contenedor HEIC/AVIF (ftyp)."""
    if not data or len(data) < 3:
        return False
    if trim_leading_pdf(data) is not None:
        return True
    if data[:3] == b"\xff\xd8\xff" or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return True
    return False


def early_reject_wrong_payload(data: bytes) -> str | None:
    """
    Rechazo antes de extracción: típico error n8n (ID numérico o texto en vez del binario del archivo).
    Si devuelve str, no intentar OpenAI ni curación con ese cuerpo.
    """
    if not data:
        return "Archivo vacío."
    if looks_like_real_file_bytes(data):
        return None
    n = len(data)
    if n <= 2048:
        try:
            t = data.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError:
            t = ""
        if t and len(t) == n and n <= 512 and t.isdigit():
            return (
                "Se recibió solo un número en texto (no el archivo binario). "
                "En n8n: usá getBinaryDataBuffer / item.binary.image en el multipart, "
                f"no un campo del JSON. Valor recibido: «{t[:48]}»."
            )
        if t and len(t) == n and n <= 256:
            if all(32 <= ord(c) <= 126 or c in "\r\n\t" for c in t) and not t.startswith(
                ("{", "[", "<", "%"),
            ):
                dr = sum(c.isdigit() for c in t) / len(t)
                if dr >= 0.85:
                    return (
                        "Cuerpo solo texto/dígitos, sin firma de imagen ni PDF. "
                        + binary_format_hint(data)
                    )
    if n < 24:
        return "Demasiado corto para ser un comprobante. " + binary_format_hint(data)
    return None


def binary_format_hint(data: bytes) -> str:
    """Pista corta para mensajes de error (no incluye datos sensibles)."""
    if not data:
        return "contenido vacío."
    n = len(data)
    # Caso típico n8n: se manda un ID o número como "archivo" en vez del binario de la foto.
    if n <= 512:
        try:
            text = data.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError:
            text = ""
        if text and len(text) == n:
            if text.isdigit():
                return (
                    "el cuerpo es solo dígitos en texto ASCII (ej. «"
                    + text[:24]
                    + ("…»" if len(text) > 24 else "»")
                    + "), no bytes de imagen/PDF. En n8n el multipart debe llevar el binario del archivo "
                    "(p. ej. Buffer desde getBinaryDataBuffer / item.binary.image), no un campo numérico del JSON."
                )
            if text.isascii() and 1 <= len(text) <= 128:
                printable = sum(32 <= ord(c) <= 126 or c in "\r\n\t" for c in text)
                if printable == len(text) and not text.startswith(("{", "[", "<", "%PDF")):
                    # Texto corto sin firma de archivo
                    digit_ratio = sum(c.isdigit() for c in text) / len(text)
                    if digit_ratio >= 0.85 or (len(text) <= 20 and "%" not in text):
                        return (
                            "el cuerpo parece texto ASCII (no binario de imagen). "
                            "Revisá que el nodo Code / HTTP envíe el archivo como binario, no el contenido de un campo de texto."
                        )
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
