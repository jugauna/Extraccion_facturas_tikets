from __future__ import annotations

import logging
import traceback
import uuid
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas import ErrorResponse, ProcessBatchResponse, TicketProcessResult
from app.services.extract_ticket import extract_ticket_from_bytes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("autodoc.batch")

app = FastAPI(
    title="Autodoc v2 — Multi-ticket",
    description="Procesamiento por lote de imágenes y PDFs (GPT-4o Vision) para Rendiciones.",
    version="2.1.0",
)


def _configure_cors(application: FastAPI) -> None:
    settings = get_settings()
    origins = settings.cors_origin_list()
    if not origins:
        logger.warning(
            "CORS_ORIGINS vacío: se permite cualquier origen (*) sin credenciales en cookie. "
            "Para producción definí CORS_ORIGINS=https://tudominio.com,https://www.tudominio.com"
        )
    use_any = not origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else ["*"],
        allow_credentials=not use_any,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )


_configure_cors(app)


def require_autodoc_secret(
    x_autodoc_secret: Annotated[Optional[str], Header(alias="X-Autodoc-Secret")] = None,
) -> None:
    settings = get_settings()
    expected = (settings.autodoc_secret or "").strip()
    if not expected:
        logger.error("AUTODOC_SECRET no configurado")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor sin AUTODOC_SECRET configurado",
        )
    if not x_autodoc_secret or x_autodoc_secret.strip() != expected:
        logger.warning("Acceso rechazado: X-Autodoc-Secret inválido o ausente")
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
    return {"status": "ok", "service": "autodoc-v2-batch"}


@app.post(
    "/process-batch",
    response_model=ProcessBatchResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def process_batch(
    _: Annotated[None, Depends(require_autodoc_secret)],
    images: Annotated[
        List[UploadFile],
        File(description="Uno o más archivos: imágenes (tickets) y/o PDFs"),
    ],
    user_notes: Annotated[Optional[str], Form(description="Notas opcionales del usuario sobre el lote")] = None,
    batch_id: Annotated[Optional[str], Form()] = None,
) -> ProcessBatchResponse | JSONResponse:
    if not images:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="validation_error", detail="Enviá al menos un archivo").model_dump(),
        )

    bid = (batch_id or "").strip() or str(uuid.uuid4())
    notes = (user_notes or "").strip()
    logger.info("process-batch batch_id=%s files=%s user_notes_len=%s", bid, len(images), len(notes))

    results: list[TicketProcessResult] = []

    for index, upload in enumerate(images):
        name = upload.filename or f"ticket_{index}"
        try:
            data = await upload.read()
        except Exception:
            logger.exception("Lectura fallida index=%s file=%s", index, name)
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=False,
                    error="read_error",
                    detail="No se pudo leer el archivo",
                ),
            )
            continue

        if not data:
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=False,
                    error="empty_file",
                    detail="Archivo vacío",
                ),
            )
            continue

        mime = _mime_from_upload(upload)
        if not mime.startswith("image/") and mime != "application/pdf":
            logger.warning("Tipo inusual index=%s mime=%s", index, mime)

        try:
            rows, _raw = extract_ticket_from_bytes(data, mime, name, notes)
            sheets = [r.to_sheets_row() for r in rows]
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=True,
                    rows=rows,
                    sheets_rows=sheets,
                ),
            )
        except ValueError as e:
            logger.warning("Extracción inválida index=%s: %s", index, e)
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=False,
                    error="extraction_error",
                    detail=str(e),
                ),
            )
        except RuntimeError as e:
            logger.error("Configuración u OpenAI index=%s: %s", index, e)
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(error="misconfigured", detail=str(e)).model_dump(),
            )
        except Exception:
            logger.error("Error interno index=%s:\n%s", index, traceback.format_exc())
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=False,
                    error="internal_error",
                    detail="Fallo la extracción",
                ),
            )

    return ProcessBatchResponse(
        batch_id=bid,
        ticket_count=len(images),
        user_notes=notes or None,
        results=results,
    )
