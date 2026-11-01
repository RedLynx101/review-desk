"""Compare one extraction with its truth file, field by field and cell by cell."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from ..normalize import squash
from ..schema import ExtractionResult, FieldResult, HEADER_FIELDS, ITEM_FIELDS

AMOUNT_FIELDS = {"subtotal", "discount", "shipping", "tax", "total", "unit_price", "amount"}
EXACT_ID_FIELDS = {"document_number", "purchase_order"}
ROW_MATCH_FLOOR = 0.6


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def values_match(name: str, truth: str | None, predicted: str | None, *, implied: str | None = None) -> bool:
    """Whether a value is right. A null truth needs a null answer, except implied quantities."""
    if truth is None:
        if predicted is None:
            return True
        if implied is not None:
            return _decimal(predicted) == _decimal(implied)
        return False
    if predicted is None:
        return False
    if name in AMOUNT_FIELDS or name == "quantity":
        left, right = _decimal(truth), _decimal(predicted)
        return left is not None and left == right
    if name in EXACT_ID_FIELDS:
        return "".join(truth.split()).strip("#:").upper() == "".join(predicted.split()).strip("#:").upper()
    if name in ("issue_date", "due_date", "currency", "document_type"):
        return truth.strip().lower() == predicted.strip().lower()
    return squash(truth) == squash(predicted)


def location_outcome(truth_cell: dict, field: FieldResult | None) -> str:
    """"hit" if a predicted box lands on a truth box. "none" if there was nothing to compare."""
    if field is None or not field.sources or not truth_cell["locations"]:
        return "none"
    for box in field.sources:
        center_x, center_y = (box.x0 + box.x1) / 2, (box.top + box.bottom) / 2
        for location in truth_cell["locations"]:
            x0, top, x1, bottom = location["bbox"]
            if location["page"] == box.page and x0 - 3 <= center_x <= x1 + 3 and top - 3 <= center_y <= bottom + 3:
                return "hit"
    return "miss"


def _signal_names(field: FieldResult | None) -> list[str]:
    if field is None:
        return []
    return [f"{signal.name}.{signal.outcome}" for signal in field.signals if signal.outcome != "not_applicable"]


def _readable(cell: dict, page_text: str | None) -> bool | None:
    """Could anyone read this value from the extracted words? None for values not printed."""
    if page_text is None or not cell["locations"]:
        return None
    return all(squash(location["printed"]) in page_text for location in cell["locations"][:1])


def _record(truth: dict, kind: str, name: str, row: int | None, cell: dict, field: FieldResult | None, *, page_text: str | None = None, **extra) -> dict:
    predicted = field.value if field else None
    correct = values_match(name, cell["value"], predicted, implied=cell.get("implied"))
    return {
        "doc_id": truth["doc_id"],
        "base_id": truth["base_id"],
        "condition": truth["condition"],
        "severity": (truth.get("degradation") or {}).get("severity"),
        "layout": truth["layout"],
        "document_type": truth["document_type"],
        "anomalies": truth["anomalies"],
        "kind": kind,
        "field": name,
        "row": row,
        "truth": cell["value"],
        "predicted": predicted,
        "printed": field.printed if field else None,
        "correct": correct,
        "confidence": field.confidence if field else 0.0,
        "needs_review": field.needs_review if field else True,
        "location": location_outcome(cell, field),
        "readable": _readable(cell, page_text),
        "signals": _signal_names(field),
        **extra,
    }


def align_rows(truth_rows: list[dict], predicted_rows: list) -> list[tuple[int, int | None]]:
    """Pair truth rows with predicted rows, each used once, best pairs first.

    A long invoice can repeat a description ("Data logger (lot 2)", "Data logger (lot 3)") and OCR can
    lose the part that tells them apart, so description alone would score a right row as wrong.
    A matching amount settles it, and table order breaks any tie that's left.
    """
    candidates = []
    for t_index, row in enumerate(truth_rows):
        target = squash(row["description"]["value"])
        truth_amount = _decimal(row["amount"]["value"])
        for p_index, predicted in enumerate(predicted_rows):
            cell = predicted.cells.get("description")
            ratio = SequenceMatcher(None, target, squash(cell.value if cell else ""), autojunk=False).ratio()
            amount_cell = predicted.cells.get("amount")
            same_amount = amount_cell is not None and truth_amount is not None and _decimal(amount_cell.value) == truth_amount
            if ratio < ROW_MATCH_FLOOR and not (same_amount and ratio >= 0.3):
                continue
            distance = abs(t_index - p_index) / max(len(truth_rows), 1)
            candidates.append((ratio + (1.0 if same_amount else 0.0) - 0.05 * distance, t_index, p_index))
    candidates.sort(key=lambda item: -item[0])
    paired: dict[int, int] = {}
    used: set[int] = set()
    for _, t_index, p_index in candidates:
        if t_index not in paired and p_index not in used:
            paired[t_index] = p_index
            used.add(p_index)
    return [(t_index, paired.get(t_index)) for t_index in range(len(truth_rows))]


def score_document(truth: dict, result: ExtractionResult, *, words: str | None = None) -> tuple[list[dict], dict]:
    """Return one record per field and cell, and a summary record for the document.

    `words` is the text the pipeline extracted from the page, if known. It separates values that were
    lost in OCR from values the model got wrong while having the right text in front of it.
    """
    page_text = squash(words) if words is not None else None
    records = []
    for name in HEADER_FIELDS:
        records.append(_record(truth, "field", name, None, truth["fields"][name], result.fields.get(name), page_text=page_text))

    pairs = align_rows(truth["line_items"], result.line_items)
    matched_predicted = set()
    for t_index, p_index in pairs:
        predicted = result.line_items[p_index] if p_index is not None else None
        if predicted is not None:
            matched_predicted.add(p_index)
        for name in ITEM_FIELDS:
            cell = truth["line_items"][t_index][name]
            field = predicted.cells.get(name) if predicted else None
            records.append(_record(truth, "cell", name, t_index, cell, field, page_text=page_text, missing_row=predicted is None))
    extra_rows = 0
    for p_index, predicted in enumerate(result.line_items):
        if p_index in matched_predicted:
            continue
        extra_rows += 1
        for name in ITEM_FIELDS:
            field = predicted.cells.get(name)
            empty = {"value": None, "locations": []}
            records.append(_record(truth, "cell", name, None, empty, field, extra_row=True))

    wrong = [record for record in records if not record["correct"]]
    # A silently dropped row has no confidence of its own. Only the sum check can catch it.
    flagged = any(field.needs_review for field in result.fields.values()) or any(
        cell.needs_review for row in result.line_items for cell in row.cells.values()
    )
    document = {
        "doc_id": truth["doc_id"],
        "base_id": truth["base_id"],
        "condition": truth["condition"],
        "severity": (truth.get("degradation") or {}).get("severity"),
        "layout": truth["layout"],
        "document_type": truth["document_type"],
        "anomalies": truth["anomalies"],
        "status": result.status,
        "failure": result.failure,
        "rows_truth": len(truth["line_items"]),
        "rows_predicted": len(result.line_items),
        "rows_missing": sum(1 for _, p_index in pairs if p_index is None),
        "rows_extra": extra_rows,
        "wrong_values": len(wrong),
        "all_correct": not wrong and result.status == "ok",
        "any_flagged": flagged or result.status != "ok",
        "checks": {check.name: check.outcome for check in result.checks},
        "model_seconds": result.model_seconds,
    }
    return records, document
