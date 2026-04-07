from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AccountingRow(BaseModel):
    """22 columnas contables (accounting_schema.md) listas para Google Sheets."""

    Clase: str = Field(default="", description="Tipo de comprobante")
    Comprobante: str = Field(default="", description="Número de factura o ticket")
    Fecha: str = Field(default="", description="Fecha de carga o vacío")
    F_Emision: str = Field(default="", alias="F.Emision", description="Fecha de emisión")
    Nombre: str = Field(default="")
    Cuit: str = Field(default="")
    Articulo: str = Field(default="")
    Detalle: str = Field(default="")
    Cuenta: str = Field(default="")
    Precio: str = Field(default="")
    IVA: str = Field(default="")
    Centro_Costo: str = Field(default="", alias="Centro Costo")
    Tipo_Comp: str = Field(default="", alias="Tipo Comp.")
    Afecta_Iva: str = Field(default="", alias="Afecta Iva")
    Percep_1: str = Field(default="", alias="Percep 1")
    Importe_Percep_1: str = Field(default="", alias="Importe Percep 1")
    Percep_2: str = Field(default="", alias="Percep 2")
    Importe_Percep_2: str = Field(default="", alias="Importe Percep 2")
    Percep_3: str = Field(default="", alias="Percep 3")
    Importe_Percep_3: str = Field(default="", alias="Importe Percep 3")
    Iva_Total: str = Field(default="", alias="Iva Total")
    Cantidad: str = Field(default="1")

    model_config = {"populate_by_name": True}

    @field_validator(
        "Clase",
        "Comprobante",
        "Fecha",
        "F_Emision",
        "Nombre",
        "Cuit",
        "Articulo",
        "Detalle",
        "Cuenta",
        "Precio",
        "IVA",
        "Centro_Costo",
        "Tipo_Comp",
        "Afecta_Iva",
        "Percep_1",
        "Importe_Percep_1",
        "Percep_2",
        "Importe_Percep_2",
        "Percep_3",
        "Importe_Percep_3",
        "Iva_Total",
        "Cantidad",
        mode="before",
    )
    @classmethod
    def _coerce_cells(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, bool):
            return "Si" if v else "No"
        if isinstance(v, (int, float)):
            return str(v)
        return str(v).strip()

    @model_validator(mode="after")
    def _cantidad_default(self) -> AccountingRow:
        if not str(self.Cantidad).strip():
            self.Cantidad = "1"
        return self

    def to_sheets_row(self) -> dict[str, str]:
        d = self.model_dump(by_alias=True)
        return {k: (v if v is not None else "") for k, v in d.items()}


class TicketProcessResult(BaseModel):
    """Resultado por cada imagen del lote."""

    filename: str = ""
    index: int = Field(default=0, ge=0)
    success: bool
    rows: List[AccountingRow] = Field(default_factory=list)
    sheets_rows: List[dict[str, str]] = Field(default_factory=list)
    error: Optional[str] = None
    detail: Optional[str] = None


class ProcessBatchResponse(BaseModel):
    batch_id: Optional[str] = None
    ticket_count: int = 0
    user_notes: Optional[str] = None
    results: List[TicketProcessResult] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
