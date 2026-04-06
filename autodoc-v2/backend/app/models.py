from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AccountingRow(BaseModel):
    """Una fila contable alineada con accounting_schema.md (Google Sheets)."""

    Clase: str = Field(default="", description="Tipo de comprobante (factura, ticket, monotributo, etc.)")
    Comprobante: str = Field(default="", description="Número de factura o ticket")
    Fecha: str = Field(default="", description="Fecha de carga (YYYY-MM-DD si aplica)")
    F_Emision: str = Field(
        default="",
        alias="F.Emision",
        description="Fecha de emisión del comprobante",
    )
    Nombre: str = Field(default="", description="Razón social o nombre del emisor")
    Cuit: str = Field(default="", description="CUIT/CUIL del emisor")
    Articulo: str = Field(default="")
    Detalle: str = Field(default="")
    Cuenta: str = Field(default="")
    Precio: str = Field(default="", description="Importe neto como string para Sheets")
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
        """Claves exactas esperadas por encabezados de Google Sheets."""
        d = self.model_dump(by_alias=True)
        return {k: (v if v is not None else "") for k, v in d.items()}


class ProcessTicketResponse(BaseModel):
    idempotency_key: Optional[str] = None
    cached: bool = False
    rows: List[AccountingRow]
    sheets_rows: List[dict[str, str]] = Field(default_factory=list)
    model_raw: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
