"""Turn an approved record into files. Only `Store.export_record` can produce the record."""

from __future__ import annotations

import csv
import io
import json

from .store import ExportRecord

CSV_HEADER_FIELDS = (
    "document_type", "vendor_name", "document_number", "issue_date", "due_date", "purchase_order", "bill_to_name",
    "currency", "subtotal", "discount", "shipping", "tax", "total",
)
CSV_ITEM_FIELDS = ("description", "quantity", "unit_price", "amount")


def _guard_formula(value: str | None) -> str:
    """Spreadsheets run cells that start with these. The text came from an untrusted document."""
    if value is None:
        return ""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_json(record: ExportRecord) -> bytes:
    payload = {
        "fields": record.fields,
        "line_items": record.line_items,
        "provenance": {
            "document_id": record.document_id,
            "filename": record.filename,
            "source_sha256": record.sha256,
            "approved_by": record.approved_by,
            "approved_at": record.approved_at,
            "content_hash": record.content_hash,
            "audit_head": record.audit_head,
        },
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def to_csv(record: ExportRecord) -> bytes:
    """One row per line item, with the document's fields repeated, which is what accounting imports want."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([*CSV_HEADER_FIELDS, *(f"item_{name}" for name in CSV_ITEM_FIELDS), "approved_by", "approved_at", "content_hash"])
    header = [_guard_formula(record.fields.get(name)) for name in CSV_HEADER_FIELDS]
    provenance = [_guard_formula(record.approved_by), record.approved_at, record.content_hash]
    rows = record.line_items or [{}]
    for row in rows:
        writer.writerow([*header, *(_guard_formula(row.get(name)) for name in CSV_ITEM_FIELDS), *provenance])
    return buffer.getvalue().encode("utf-8-sig")
