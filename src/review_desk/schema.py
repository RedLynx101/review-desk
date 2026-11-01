"""The two schemas: what the model must return, and what the pipeline hands to everything else.

`ModelReading` is deliberately literal. The model copies printed text and cites a line number, and
normalization and arithmetic happen in Python. A small model is bad at reformatting dates and adding
numbers, and a literal copy can be checked against the page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cited(_Strict):
    """One value copied from the document, and the number of the line it's printed on."""

    text: str | None
    line: int | None


class ItemReading(_Strict):
    description: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None
    line: int | None


class ModelReading(_Strict):
    """Exactly what the model is allowed to return. A strict JSON schema enforces this shape."""

    document_type: Literal["invoice", "receipt"]
    vendor_name: Cited
    document_number: Cited
    issue_date: Cited
    due_date: Cited
    purchase_order: Cited
    bill_to_name: Cited
    currency: Cited
    subtotal: Cited
    discount: Cited
    shipping: Cited
    tax: list[Cited]
    total: Cited
    line_items: list[ItemReading]


HEADER_FIELDS = (
    "document_type", "vendor_name", "document_number", "issue_date", "due_date", "purchase_order",
    "bill_to_name", "currency", "subtotal", "discount", "shipping", "tax", "total",
)
ITEM_FIELDS = ("description", "quantity", "unit_price", "amount")


def _inline(schema: dict, definitions: dict) -> dict:
    """Resolve `$ref`s and drop titles. Grammar-based local servers handle flat schemas best."""
    if "$ref" in schema:
        return _inline(definitions[schema["$ref"].rsplit("/", 1)[-1]], definitions)
    out = {}
    for key, value in schema.items():
        if key in ("title", "description", "$defs", "default"):
            continue
        if isinstance(value, dict):
            if key == "properties":
                out[key] = {name: _inline(sub, definitions) for name, sub in value.items()}
            else:
                out[key] = _inline(value, definitions)
        elif isinstance(value, list):
            out[key] = [_inline(item, definitions) if isinstance(item, dict) else item for item in value]
        else:
            out[key] = value
    return out


def model_json_schema() -> dict:
    """The JSON schema sent with every model request."""
    raw = ModelReading.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))


class Signal(_Strict):
    """One checkable piece of evidence behind a confidence score, readable by a person."""

    name: str
    outcome: Literal["pass", "weak", "fail", "not_applicable"]
    detail: str
    effect: float = 0.0


class SourceBox(_Strict):
    page: int
    x0: float
    top: float
    x1: float
    bottom: float


class FieldResult(_Strict):
    name: str
    value: str | None  # Normalized: ISO dates, plain decimals, trimmed text.
    printed: str | None  # What the model copied from the page.
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool
    signals: list[Signal]
    sources: list[SourceBox]


class LineItemResult(_Strict):
    index: int
    cells: dict[str, FieldResult]


class DocumentCheck(_Strict):
    """A whole-document check, such as whether the line items add up to the subtotal."""

    name: str
    outcome: Literal["pass", "fail", "not_applicable"]
    detail: str


class PageInfo(_Strict):
    number: int
    width: float
    height: float
    source: Literal["text_layer", "ocr"]
    word_count: int
    mean_word_confidence: float | None
    error: str | None = None
    text_layer_agreement: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(_Strict):
    status: Literal["ok", "failed"]
    failure: str | None = None
    pages: list[PageInfo]
    fields: dict[str, FieldResult]
    line_items: list[LineItemResult]
    checks: list[DocumentCheck]
    active_content: list[str]
    model: str
    model_seconds: float | None = None
    review_threshold: float
