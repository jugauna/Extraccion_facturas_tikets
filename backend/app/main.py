from __future__ import annotations

import base64
import json
import logging
import secrets
import traceback
import urllib.request
import uuid
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.schemas import (
    AccountingRow,
    CurationSubmitRequest,
    CurationTaskRef,
    ErrorResponse,
    EthicsRagRequest,
    EthicsRagResponse,
    ProcessBatchResponse,
    SHEETS_COLUMNS_ORDER,
    TicketProcessResult,
)
from app.services.curation_store import (
    delete_pending,
    load_pending,
    save_gold_batch,
    save_pending_batch,
    verify_token,
)
from app.services.document_preview import make_preview_jpeg_bytes
from app.services.ethics_rag import analyze_expense_text
from app.services.extract_ticket import extract_ticket_from_bytes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("autodoc.batch")

app = FastAPI(
    title="Autodoc v2 — Multi-ticket",
    description="Procesamiento por lote de imágenes y PDFs (GPT-4o Vision) para Rendiciones.",
    version="2.2.0",
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

CURATION_FIELD_LABELS: dict[str, str] = {
    "Clase": "Clase de comprobante",
    "Comprobante": "Número de comprobante",
    "Fecha": "Fecha (carga)",
    "F.Emision": "Fecha de emisión",
    "Nombre": "Razón social / nombre",
    "Cuit": "CUIT",
    "Articulo": "Artículo",
    "Detalle": "Detalle",
    "Cuenta": "Cuenta",
    "Precio": "Precio / neto",
    "IVA": "IVA",
    "Centro Costo": "Centro de costo",
    "Tipo Comp.": "Tipo comprobante",
    "Afecta Iva": "Afecta IVA",
    "Percep 1": "Percepción 1",
    "Importe Percep 1": "Importe percep. 1",
    "Percep 2": "Percepción 2",
    "Importe Percep 2": "Importe percep. 2",
    "Percep 3": "Percepción 3",
    "Importe Percep 3": "Importe percep. 3",
    "Iva Total": "IVA total",
    "Cantidad": "Cantidad",
}


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


def _parse_drive_links(raw: Optional[str]) -> dict[int, str]:
    if not raw or not str(raw).strip():
        return {}
    try:
        lst = json.loads(raw)
        if isinstance(lst, list):
            return {i: str(v or "") for i, v in enumerate(lst)}
    except json.JSONDecodeError:
        logger.warning("drive_links_json no es JSON válido")
    return {}


def _curation_public_url(path_with_query: str) -> str:
    base = (get_settings().public_base_url or "").strip().rstrip("/")
    if base:
        return f"{base}{path_with_query}"
    return path_with_query


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "autodoc-v2-batch"}


@app.get("/curation/{task_id}", response_class=HTMLResponse)
async def curation_page(request: Request, task_id: str, t: str = "", i: int = 0) -> HTMLResponse:
    pending = load_pending(task_id)
    if not pending or not verify_token(pending, t):
        raise HTTPException(status_code=404, detail="Enlace de curación inválido o expirado")

    docs = pending.get("docs")
    if not isinstance(docs, list) or not docs:
        raise HTTPException(status_code=404, detail="Sin documentos para curación")
    idx = max(0, min(int(i or 0), len(docs) - 1))

    return templates.TemplateResponse(
        "curation.html",
        {
            "request": request,
            "task_id": task_id,
            "token": t,
            "batch_id": pending.get("batch_id", ""),
            "user_notes": pending.get("user_notes", ""),
            "docs": docs,
            "doc_index": idx,
            "columns": list(SHEETS_COLUMNS_ORDER),
            "labels": {k: CURATION_FIELD_LABELS.get(k, k) for k in SHEETS_COLUMNS_ORDER},
        },
    )


@app.post("/curation/submit")
async def curation_submit(body: CurationSubmitRequest) -> dict:
    pending = load_pending(body.task_id)
    if not pending or not verify_token(pending, body.token):
        raise HTTPException(status_code=404, detail="Tarea inválida o token incorrecto")

    if not body.docs:
        raise HTTPException(status_code=400, detail="Enviá al menos un documento")

    src_docs = pending.get("docs") if isinstance(pending.get("docs"), list) else []
    if len(src_docs) != len(body.docs):
        raise HTTPException(status_code=400, detail="Cantidad de documentos no coincide con la sesión")

    gold_docs: list[dict] = []
    try:
        for idx, (src, corrected) in enumerate(zip(src_docs, body.docs, strict=True)):
            rows_raw = corrected.get("rows")
            if not isinstance(rows_raw, list) or not rows_raw:
                rows_raw = [{}]
            validated: list[AccountingRow] = []
            for r in rows_raw:
                if not isinstance(r, dict):
                    raise ValueError(f"docs[{idx}].rows[*] debe ser objeto")
                validated.append(AccountingRow.model_validate(r))
            rows_out = [r.to_sheets_row() for r in validated]

            gold_docs.append(
                {
                    "index": idx,
                    "filename": str(src.get("filename", "")),
                    "mime": str(src.get("mime", "")),
                    "original_base64": str(src.get("original_base64", "")),
                    "rows": rows_out,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Validación: {e}") from e

    gold_path = save_gold_batch(
        task_id=body.task_id,
        batch_id=str(pending.get("batch_id", "")),
        user_notes=str(pending.get("user_notes", "")),
        docs=gold_docs,
    )
    delete_pending(body.task_id)

    # Notificar a n8n (persistencia final) si está configurado.
    settings = get_settings()
    if settings.persist_webhook_url.strip():
        # También enviamos filas "planas" para facilitar el append en Sheets.
        flat_rows: list[dict] = []
        for d in gold_docs:
            for row in d.get("rows") or []:
                if isinstance(row, dict):
                    flat_rows.append(
                        {
                            **row,
                            "Batch_Id": str(pending.get("batch_id", "")),
                            "Source_File": d.get("filename", ""),
                            "Doc_Index": d.get("index", 0),
                        }
                    )

        payload = {
            "task_id": body.task_id,
            "batch_id": str(pending.get("batch_id", "")),
            "user_notes": str(pending.get("user_notes", "")),
            "gold_path": str(gold_path.name),
            "docs": gold_docs,
            "flat_rows": flat_rows,
        }
        try:
            req = urllib.request.Request(
                settings.persist_webhook_url.strip(),
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Autodoc-Secret": settings.persist_webhook_secret.strip(),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                _ = resp.read()
        except Exception as e:
            # No bloquea el éxito de curación: el usuario ya confirmó.
            logger.warning("No se pudo notificar persistencia a n8n: %s", e)

    return {
        "ok": True,
        "message": "Confirmado. Se guardó gold_dataset y se notificó la persistencia (si está configurada).",
    }


@app.post(
    "/ethics-rag",
    response_model=EthicsRagResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def ethics_rag(
    _: Annotated[None, Depends(require_autodoc_secret)],
    body: EthicsRagRequest,
) -> EthicsRagResponse:
    try:
        data = analyze_expense_text(body.detalle)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return EthicsRagResponse(**data)


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
    # Form antes que File (requisito FastAPI / Starlette con multipart; si no, 422 al mezclar con n8n).
    user_notes: Annotated[Optional[str], Form(description="Notas opcionales del usuario sobre el lote")] = None,
    batch_id: Annotated[Optional[str], Form()] = None,
    drive_links_json: Annotated[
        Optional[str],
        Form(description="JSON array de enlaces Drive, mismo orden que images"),
    ] = None,
    images: Annotated[
        List[UploadFile],
        File(description="Uno o más archivos: imágenes (tickets) y/o PDFs"),
    ] = [],
) -> ProcessBatchResponse | JSONResponse:
    if not images:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="validation_error", detail="Enviá al menos un archivo").model_dump(),
        )

    bid = (batch_id or "").strip() or str(uuid.uuid4())
    notes = (user_notes or "").strip()
    drive_by_index = _parse_drive_links(drive_links_json)
    logger.info("process-batch batch_id=%s files=%s user_notes_len=%s", bid, len(images), len(notes))

    results: list[TicketProcessResult] = []
    pending_docs: list[dict] = []

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
            try:
                preview = make_preview_jpeg_bytes(data, mime, name)
                pending_docs.append(
                    {
                        "index": index,
                        "filename": name,
                        "mime": mime,
                        "preview_mime": "image/jpeg",
                        "preview_base64": base64.standard_b64encode(preview).decode("ascii"),
                        "original_base64": base64.standard_b64encode(data).decode("ascii"),
                        "drive_web_view_link": drive_by_index.get(index, ""),
                        "rows": sheets,
                    }
                )
            except Exception as cur_exc:
                logger.warning("Preview/pending no generado para %s: %s", name, cur_exc)

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

    # Crear UNA sesión batch de curación.
    tid = str(uuid.uuid4())
    tok = secrets.token_urlsafe(28)
    save_pending_batch(
        task_id=tid,
        submission_token=tok,
        batch_id=bid,
        user_notes=notes,
        docs=pending_docs if pending_docs else [],
    )
    path_q = f"/curation/{tid}?t={tok}&i=0"

    return ProcessBatchResponse(
        batch_id=bid,
        ticket_count=len(images),
        user_notes=notes or None,
        results=results,
        task_id=tid,
        curation_url=_curation_public_url(path_q),
    )
