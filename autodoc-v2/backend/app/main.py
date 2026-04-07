from __future__ import annotations

import logging
import traceback
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.idempotency import idempotency_store
from app.models import AccountingRow, ErrorResponse, ProcessTicketResponse
from app.services.extract_ticket import extract_ticket_from_bytes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("autodoc")

app = FastAPI(
    title="Autodoc v2",
    description="Motor FastAPI para extracción contable (GPT-4o Vision): imágenes y PDF.",
    version="2.0.0",
)


def require_autodoc_secret(x_autodoc_secret: Annotated[str | None, Header(alias="X-Autodoc-Secret")] = None) -> None:
    settings = get_settings()
    expected = (settings.autodoc_secret or "").strip()
    if not expected:
        logger.error("AUTODOC_SECRET no está configurado; rechazando /process-ticket")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor sin AUTODOC_SECRET configurado",
        )
    if not x_autodoc_secret or x_autodoc_secret.strip() != expected:
        logger.warning("Intento de acceso con X-Autodoc-Secret inválido o ausente")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")


def _mime_from_upload(upload: UploadFile) -> str:
    name = (upload.filename or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    ct = (upload.content_type or "").split(";")[0].strip().lower()
    if ct and ct != "application/octet-stream":
        return ct
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "autodoc-v2"}


@app.post(
    "/process-ticket",
    response_model=ProcessTicketResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def process_ticket(
    _: Annotated[None, Depends(require_autodoc_secret)],
    file: Annotated[UploadFile, File(..., description="Imagen o PDF del comprobante")],
    user_notes: Annotated[
        Optional[str],
        Form(description="Notas opcionales del usuario"),
    ] = None,
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
) -> ProcessTicketResponse | JSONResponse:
    notes = (user_notes or "").strip()
    logger.info(
        "process-ticket filename=%s content_type=%s user_notes_len=%s idempotency_key=%s",
        file.filename,
        file.content_type,
        len(notes),
        idempotency_key or "-",
    )

    try:
        file_bytes = await file.read()
    except Exception:
        logger.exception("No se pudo leer el archivo")
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="read_error", detail="No se pudo leer el archivo").model_dump(),
        )

    if not file_bytes:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="validation_error", detail="Archivo vacío").model_dump(),
        )

    cache_key = (
        f"idemp:{idempotency_key.strip()}"
        if idempotency_key and idempotency_key.strip()
        else f"fp:{idempotency_store.fingerprint(file_bytes, notes)}"
    )
    cached = idempotency_store.get(cache_key)
    if cached is not None:
        logger.info("Respuesta idempotente cache_key=%s", cache_key[:48])
        payload = ProcessTicketResponse.model_validate(cached)
        payload.cached = True
        payload.idempotency_key = idempotency_key.strip() if idempotency_key else None
        return payload

    mime = _mime_from_upload(file)
    warnings: list[str] = []
    if not mime.startswith("image/") and mime != "application/pdf":
        warnings.append(f"content-type inusual ({mime}); se intentará según extensión")

    try:
        rows, raw = extract_ticket_from_bytes(file_bytes, mime, file.filename or "ticket", notes)
    except ValueError as e:
        logger.warning("Validación / parsing: %s", e)
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="extraction_error", detail=str(e)).model_dump(),
        )
    except RuntimeError as e:
        logger.error("Configuración: %s", e)
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(error="misconfigured", detail=str(e)).model_dump(),
        )
    except Exception:
        logger.error("Error no controlado en extracción:\n%s", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error="internal_error", detail="Fallo la extracción").model_dump(),
        )

    if not rows:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="no_rows",
                detail="El modelo no devolvió filas; reenviá un archivo más claro",
            ).model_dump(),
        )

    sheets_rows = [r.to_sheets_row() for r in rows]
    body = ProcessTicketResponse(
        idempotency_key=idempotency_key.strip() if idempotency_key else None,
        cached=False,
        rows=rows,
        sheets_rows=sheets_rows,
        model_raw=raw,
        warnings=warnings,
    )
    idempotency_store.set(
        cache_key,
        body.model_dump(mode="json"),
    )
    return body


@app.post("/process-ticket/dry-run-schema", include_in_schema=False)
async def process_ticket_schema_hint(_: Annotated[None, Depends(require_autodoc_secret)]) -> dict[str, list[str]]:
    """Ayuda para integraciones: muestra el orden de columnas para Google Sheets."""
    sample = AccountingRow()
    return {"columns": list(sample.to_sheets_row().keys())}
