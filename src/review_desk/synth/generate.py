"""Render documents in every condition and write each PDF beside its answers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np

from .canvas import FieldCanvas, image_pages_to_pdf
from .content import Document, make_document
from .degrade import CONDITIONS, PageTransform, degrade_page, draw_params, encode_jpeg, rasterize
from .invoice_layouts import INVOICE_RENDERERS
from .receipt_layouts import RECEIPT_RENDERERS, ROLL_WIDTHS

GENERATOR_VERSION = 1
DEFAULT_SEED = 20261101
NOTICE = "SYNTHETIC. Invented test document with known answers. Not a real invoice or receipt."
ROLL_FOOT_MARGIN = 42.0


@dataclass
class Rendered:
    pdf: bytes
    locations: dict[str, list[dict]]
    page_sizes: list[list[float]]


def render_clean(doc: Document) -> Rendered:
    """Draw the digital original, whose text layer is intact."""
    title = f"Synthetic {doc.document_type} {doc.base_id}"
    if doc.document_type == "invoice":
        fc = FieldCanvas(*doc.page_size, title)
        INVOICE_RENDERERS[doc.layout](fc, doc)
    elif doc.layout in ROLL_WIDTHS:
        # Roll paper is as long as it needs to be: draw once to measure, then again to fit.
        width = ROLL_WIDTHS[doc.layout]
        probe = FieldCanvas(width, 3000.0, title)
        end = RECEIPT_RENDERERS[doc.layout](probe, doc)
        fc = FieldCanvas(width, end + ROLL_FOOT_MARGIN, title)
        RECEIPT_RENDERERS[doc.layout](fc, doc)
    else:
        fc = FieldCanvas(*doc.page_size, title)
        RECEIPT_RENDERERS[doc.layout](fc, doc)
    pdf = fc.finish()
    return Rendered(pdf, fc.locations, [[fc.w, fc.h]] * (fc.page + 1))


def render_scan(doc: Document, clean: Rendered, condition: str, rng: np.random.Generator) -> tuple[Rendered, dict]:
    """Rasterize the clean pages, wreck them per the drawn settings, and move the field boxes."""
    params = draw_params(condition, rng, narrow=doc.layout in ROLL_WIDTHS)
    pages, transforms = [], []
    for image in rasterize(clean.pdf, params["dpi"]):
        result, transform = degrade_page(image, params, rng)
        pages.append((encode_jpeg(result, params["jpeg_quality"]), transform.width, transform.height))
        transforms.append(transform)
    pdf = image_pages_to_pdf(pages, f"Synthetic {doc.document_type} {doc.base_id} ({condition})")
    return Rendered(pdf, _move_locations(clean.locations, transforms), [[t.width, t.height] for t in transforms]), params


def _move_locations(locations: dict[str, list[dict]], transforms: list[PageTransform]) -> dict[str, list[dict]]:
    moved: dict[str, list[dict]] = {}
    for key, entries in locations.items():
        moved[key] = [
            {**entry, "bbox": transforms[entry["page"]].bbox(entry["bbox"])}
            for entry in entries
        ]
    return moved


def _text(value: Decimal | str | None) -> str | None:
    if value is None:
        return None
    return format(value, "f") if isinstance(value, Decimal) else value


def _field(value, locations: dict, key: str) -> dict:
    entries = locations.get(key, [])
    if value is not None and not entries:
        raise ValueError(f"field {key} has a value but was never drawn")
    return {
        "value": _text(value),
        "locations": [{"page": e["page"], "bbox": e["bbox"], "printed": e["printed"]} for e in entries],
    }


def build_truth(doc: Document, rendered: Rendered, condition: str, degradation: dict | None) -> dict:
    """The known answers for one rendering. Values are normalized; `printed` is the literal ink."""
    loc = rendered.locations
    fields = {
        "document_type": _field(doc.document_type, loc, "document_type"),
        "vendor_name": _field(doc.vendor.name, loc, "vendor_name"),
        "document_number": _field(doc.number, loc, "document_number"),
        "issue_date": _field(doc.issue_date.isoformat(), loc, "issue_date"),
        "due_date": _field(doc.due_date.isoformat() if doc.due_date else None, loc, "due_date"),
        "purchase_order": _field(doc.purchase_order, loc, "purchase_order"),
        "bill_to_name": _field(doc.bill_to.name if doc.bill_to else None, loc, "bill_to_name"),
        "subtotal": _field(doc.subtotal, loc, "subtotal"),
        "discount": _field(doc.discount, loc, "discount"),
        "shipping": _field(doc.shipping, loc, "shipping"),
        "tax": _field(doc.tax, loc, "tax"),
        "total": _field(doc.total, loc, "total"),
    }
    # A symbol is the only currency evidence on most documents, and it lives inside the amounts.
    currency_locations = loc.get("currency") or loc["total"]
    fields["currency"] = {
        "value": doc.money.currency,
        "locations": [{"page": e["page"], "bbox": e["bbox"], "printed": e["printed"]} for e in currency_locations],
        "evidence": "printed_note" if loc.get("currency") else "symbol_in_amounts",
    }

    items = []
    for index, row in enumerate(doc.items):
        entry = {
            name: _field(value, loc, f"line_items.{index}.{name}")
            for name, value in (
                ("description", row.description),
                ("quantity", row.quantity),
                ("unit_price", row.unit_price),
                ("amount", row.amount),
            )
        }
        if row.quantity is None:
            entry["quantity"]["implied"] = "1"
        items.append(entry)

    return {
        "synthetic": True,
        "notice": NOTICE,
        "generator_version": GENERATOR_VERSION,
        "doc_id": f"{doc.base_id}.{condition}",
        "base_id": doc.base_id,
        "split": doc.split,
        "index": doc.index,
        "seed": str(doc.seed),
        "document_type": doc.document_type,
        "layout": doc.layout,
        "condition": condition,
        "locale": doc.locale,
        "page_count": len(rendered.page_sizes),
        "page_sizes": rendered.page_sizes,
        "anomalies": doc.anomalies,
        "degradation": degradation,
        "tax_printed_as_separate_lines": len(doc.tax_lines) > 1,
        "distractors_present": {
            "customer_id": doc.customer_id is not None,
            "order_date": doc.order_date is not None,
            "ship_to": doc.ship_to is not None,
            "deposit_and_balance": doc.deposit is not None,
            "remit_to": doc.remit_to is not None,
            "bank_note": doc.bank_note is not None,
            "cash_and_change": doc.tendered is not None,
        },
        "fields": fields,
        "line_items": items,
    }


def _scan_rng(doc: Document, condition: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{doc.seed}:{condition}:{GENERATOR_VERSION}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def render_all(doc: Document, conditions=CONDITIONS) -> dict[str, tuple[bytes, dict]]:
    """Render one document in the requested conditions. Returns {condition: (pdf, truth)}."""
    clean = render_clean(doc)
    out: dict[str, tuple[bytes, dict]] = {}
    for condition in conditions:
        if condition == "clean":
            out[condition] = (clean.pdf, build_truth(doc, clean, condition, None))
        else:
            rendered, params = render_scan(doc, clean, condition, _scan_rng(doc, condition))
            out[condition] = (rendered.pdf, build_truth(doc, rendered, condition, params))
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate_split(
    split: str,
    count: int,
    out_dir: Path,
    *,
    master_seed: int = DEFAULT_SEED,
    conditions=CONDITIONS,
    start: int = 0,
) -> dict:
    """Write PDFs and truth files for a split, and return (and save) the manifest."""
    target = Path(out_dir) / split
    target.mkdir(parents=True, exist_ok=True)
    documents = []
    for index in range(start, start + count):
        doc = make_document(split, index, master_seed)
        for condition, (pdf, truth) in render_all(doc, conditions).items():
            stem = f"{doc.base_id}.{condition}"
            truth_bytes = (json.dumps(truth, indent=1, sort_keys=True) + "\n").encode()
            (target / f"{stem}.pdf").write_bytes(pdf)
            (target / f"{stem}.truth.json").write_bytes(truth_bytes)
            documents.append({
                "doc_id": stem,
                "pdf": f"{stem}.pdf",
                "pdf_sha256": _sha256(pdf),
                "pdf_bytes": len(pdf),
                "truth_sha256": _sha256(truth_bytes),
                "document_type": doc.document_type,
                "layout": doc.layout,
                "condition": condition,
                "severity": truth["degradation"]["severity"] if truth["degradation"] else None,
                "page_count": truth["page_count"],
                "line_items": len(doc.items),
                "anomalies": doc.anomalies,
            })
    manifest = {
        "synthetic": True,
        "notice": NOTICE,
        "generator_version": GENERATOR_VERSION,
        "master_seed": master_seed,
        "split": split,
        "base_documents": count,
        "start_index": start,
        "conditions": list(conditions),
        "renderings": len(documents),
        "reproducibility": (
            "Truth hashes depend only on the seed. PDF hashes also depend on the installed "
            "PDFium, Pillow and ReportLab builds, so compare them on the same platform and pins."
        ),
        "documents": documents,
    }
    (target / "manifest.json").write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))
    return manifest
