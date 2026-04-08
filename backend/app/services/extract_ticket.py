from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

from app.schemas import AccountingRow
from app.services.extraction import extract_accounting_rows
from app.services.pdf_pages import pdf_bytes_to_jpeg_pages

logger = logging.getLogger(__name__)


def _is_pdf(mime: str, filename: str) -> bool:
    m = (mime or "").split(";")[0].strip().lower()
    fn = (filename or "").lower()
    return m == "application/pdf" or fn.endswith(".pdf")


def extract_ticket_from_bytes(
    data: bytes,
    mime: str,
    filename: str,
    user_notes: str,
) -> Tuple[List[AccountingRow], str]:
    """
    Extrae filas contables de una imagen o de un PDF (una llamada Vision por página; se concatenan filas).
    """
    if _is_pdf(mime, filename):
        pages = pdf_bytes_to_jpeg_pages(data, dpi=300)
        if not pages:
            raise ValueError("No se pudo renderizar ninguna página del PDF")
        stem = Path(filename or "document").stem or "document"
        all_rows: List[AccountingRow] = []
        last_raw = ""
        errors: list[str] = []
        any_page_ok = False
        for i, jpeg in enumerate(pages):
            page_filename = f"{stem}_p{i + 1}.jpg"
            try:
                rows, last_raw = extract_accounting_rows(
                    jpeg, "image/jpeg", user_notes, page_filename
                )
                any_page_ok = True
                logger.info(
                    "PDF página %s: %s fila(s) extraída(s)",
                    i + 1,
                    len(rows),
                )
                all_rows.extend(rows)
            except RuntimeError:
                raise
            except Exception as e:
                msg = str(e)[:500]
                logger.warning("PDF página %s falló (%s): %s", i + 1, page_filename, msg)
                errors.append(f"pág.{i + 1}: {msg}")
        if not any_page_ok:
            joined = "; ".join(errors[:5])
            if len(errors) > 5:
                joined += f" … (+{len(errors) - 5})"
            raise ValueError(joined or "Todas las páginas del PDF fallaron al extraer")
        if errors:
            logger.warning(
                "PDF %s: extracción parcial (%s páginas OK, %s con error)",
                filename,
                len(pages) - len(errors),
                len(errors),
            )
        return all_rows, last_raw

    if not (mime or "").lower().startswith("image/"):
        logger.warning("Tipo MIME %s no es image/*; se intenta extracción como imagen", mime)

    return extract_accounting_rows(data, mime, user_notes, filename)
