from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import traceback
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, status
from starlette.datastructures import Headers
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from openai import BadRequestError

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
from app.services.document_preview import preview_for_curation_ui
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


def _append_pending_manual_curation(
    pending_docs: list[dict],
    *,
    index: int,
    name: str,
    mime: str,
    data: bytes,
    drive_by_index: dict[int, str],
    extraction_note: str,
) -> None:
    """Si falla el modelo, igual cargar el comprobante en la UI con filas vacías (edición manual)."""
    try:
        preview = preview_for_curation_ui(data, mime, name)
        pending_docs.append(
            {
                "index": index,
                "filename": name,
                "mime": mime,
                "preview_mime": "image/jpeg",
                "preview_base64": base64.standard_b64encode(preview).decode("ascii"),
                "original_base64": base64.standard_b64encode(data).decode("ascii"),
                "drive_web_view_link": drive_by_index.get(index, ""),
                "rows": [],
                "extraction_note": (extraction_note or "")[:4000],
            },
        )
        logger.info(
            "Curación manual (fallback) index=%s archivo=%s",
            index,
            name,
        )
    except Exception:
        logger.warning(
            "Sin fallback de curación index=%s archivo=%s",
            index,
            name,
            exc_info=True,
        )


def _is_multipart_file_field_name(key: str) -> bool:
    """n8n / clientes pueden usar ``images`` (Divi) o ``data[]`` / ``data`` (axios u otros)."""
    k = (key or "").strip()
    if k in ("images", "image", "file", "files", "data", "data[]"):
        return True
    if len(k) > 2 and k.startswith("data[") and k.endswith("]"):
        return True
    if len(k) > 2 and k.startswith("files[") and k.endswith("]"):
        return True
    return False


def _upload_files_from_form(form) -> list:
    """Partes de archivo del multipart (cualquier nombre de campo habitual)."""
    out: list = []
    for key, value in form.multi_items():
        if not _is_multipart_file_field_name(key):
            continue
        if isinstance(value, str):
            continue
        if getattr(value, "read", None) is None:
            continue
        out.append(value)
    return out


def _upload_files_from_n8n_wrapped_strings(form) -> list:
    """
    n8n ``helpers.httpRequest`` a veces reenvuelve el multipart: no hay UploadFile, solo
    ``type`` y uno o más ``data[]`` con cadenas (p. ej. base64). Reconstruye un único archivo.
    """
    chunks: list[bytes] = []
    for key, value in form.multi_items():
        if key != "data[]":
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        s = value.strip()
        if s.startswith("data:") and ";base64," in s:
            s = s.split(";base64,", 1)[1]
        raw: bytes
        try:
            raw = base64.b64decode(s, validate=False)
        except (binascii.Error, ValueError):
            raw = s.encode("latin-1")
        if raw:
            chunks.append(raw)

    if not chunks:
        return []

    data = b"".join(chunks)
    if len(data) < 4:
        return []

    t_raw = form.get("type")
    filename = "upload.bin"
    mime = "application/octet-stream"

    if data.startswith(b"%PDF"):
        filename, mime = "upload.pdf", "application/pdf"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        filename, mime = "upload.png", "image/png"
    elif data[:2] == b"\xff\xd8":
        filename, mime = "upload.jpg", "image/jpeg"

    if isinstance(t_raw, str) and "/" in t_raw:
        hint = t_raw.split(";")[0].strip().lower()
        if hint in ("application/pdf", "image/jpeg", "image/jpg", "image/png", "image/webp"):
            mime = hint
            if "pdf" in mime:
                filename = "upload.pdf"
            elif "png" in mime:
                filename = "upload.png"
            elif "jpeg" in mime or "jpg" in mime or "webp" in mime:
                filename = "upload.jpg" if "webp" not in mime else "upload.webp"

    stream = BytesIO(data)
    headers = Headers({"content-type": mime})
    uf = UploadFile(file=stream, filename=filename, headers=headers)
    logger.info(
        "process-batch fallback n8n data[]: partes=%s tamaño=%s nombre=%s",
        len(chunks),
        len(data),
        filename,
    )
    return [uf]


def _curation_public_url(path_with_query: str) -> str:
    base = (get_settings().public_base_url or "").strip().rstrip("/")
    if base:
        return f"{base}{path_with_query}"
    return path_with_query


@app.get("/health")
async def health() -> dict[str, str]:
    # Cloud Run define K_REVISION (nombre de la revisión desplegada).
    rev = (os.environ.get("K_REVISION") or "").strip()
    out: dict[str, str] = {"status": "ok", "service": "autodoc-v2-batch"}
    if rev:
        out["k_revision"] = rev
    return out


@app.get("/curation/{task_id}", response_class=HTMLResponse)
async def curation_page(request: Request, task_id: str, t: str = "", i: int = 0) -> HTMLResponse:
    pending = load_pending(task_id)
    if not pending or not verify_token(pending, t):
        raise HTTPException(status_code=404, detail="Enlace de curación inválido o expirado")

    docs = pending.get("docs")
    if not isinstance(docs, list) or not docs:
        raise HTTPException(
            status_code=404,
            detail=(
                "Sin documentos para curación: la sesión no tiene ítems curables (extracciones fallidas o lote viejo). "
                "Si Cloud Run tiene varias instancias, definí CURATION_PENDING_GCS_BUCKET y volvé a procesar el lote."
            ),
        )
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
    request: Request,
    _: Annotated[None, Depends(require_autodoc_secret)],
) -> ProcessBatchResponse | JSONResponse:
    # Multipart parseado aquí (no solo FastAPI File/Form): n8n arma el body con Buffer y el
    # binding List[UploadFile]=File() a veces dejaba images=[] → 400 "Enviá al menos un archivo".
    form = await request.form()
    user_notes = form.get("user_notes")
    if isinstance(user_notes, UploadFile):
        user_notes = None
    elif user_notes is not None:
        user_notes = str(user_notes)

    batch_id = form.get("batch_id")
    if isinstance(batch_id, UploadFile):
        batch_id = None
    elif batch_id is not None:
        batch_id = str(batch_id)

    drive_links_json = form.get("drive_links_json")
    if isinstance(drive_links_json, UploadFile):
        drive_links_json = None
    elif drive_links_json is not None:
        drive_links_json = str(drive_links_json)

    images = _upload_files_from_form(form)
    if not images:
        images = _upload_files_from_n8n_wrapped_strings(form)

    if not images:
        keys = sorted({k for k, _ in form.multi_items()})
        logger.warning(
            "process-batch sin archivos reconocidos (images/data[]/file…); keys=%s",
            keys,
        )
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
            preview = preview_for_curation_ui(data, mime, name)
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
            _append_pending_manual_curation(
                pending_docs,
                index=index,
                name=name,
                mime=mime,
                data=data,
                drive_by_index=drive_by_index,
                extraction_note=str(e),
            )
        except RuntimeError as e:
            logger.error("Configuración u OpenAI index=%s: %s", index, e)
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(error="misconfigured", detail=str(e)).model_dump(),
            )
        except BadRequestError as e:
            omsg = e.message
            b = e.body
            if isinstance(b, dict):
                err = b.get("error")
                if isinstance(err, dict) and err.get("message"):
                    omsg = str(err["message"])
            logger.warning("OpenAI 400 index=%s file=%s: %s", index, name, omsg)
            results.append(
                TicketProcessResult(
                    filename=name,
                    index=index,
                    success=False,
                    error="openai_error",
                    detail=omsg[:4000],
                ),
            )
            _append_pending_manual_curation(
                pending_docs,
                index=index,
                name=name,
                mime=mime,
                data=data,
                drive_by_index=drive_by_index,
                extraction_note=f"OpenAI: {omsg[:2000]}",
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
            _append_pending_manual_curation(
                pending_docs,
                index=index,
                name=name,
                mime=mime,
                data=data,
                drive_by_index=drive_by_index,
                extraction_note="Fallo interno al extraer; revisá los registros del servicio.",
            )

    if not pending_docs:
        logger.warning(
            "process-batch: ningún documento para curación (extracciones fallidas o datos vacíos). batch_id=%s",
            bid,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": "no_curation_docs",
                "detail": "No se pudo preparar ningún documento para la pantalla de curación. Revisá results[].",
                "batch_id": bid,
                "results": [r.model_dump() for r in results],
            },
        )

    # Crear UNA sesión batch de curación.
    tid = str(uuid.uuid4())
    tok = secrets.token_urlsafe(28)
    save_pending_batch(
        task_id=tid,
        submission_token=tok,
        batch_id=bid,
        user_notes=notes,
        docs=pending_docs,
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
