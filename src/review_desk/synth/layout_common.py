"""Drawing pieces the invoice and receipt layouts share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from reportlab.pdfbase.pdfmetrics import stringWidth

from .canvas import GRAY, INK, FieldCanvas
from .content import Document, LineItem, Party


@dataclass(frozen=True)
class Col:
    """One item-table column. `x` is the left edge if left-aligned, else the right edge."""

    field: str
    x: float
    align: str = "right"
    width: float = 0.0
    title: str = ""


@dataclass(frozen=True)
class TotalRow:
    label: str
    value: str
    key: str | None
    strong: bool = False


def fit_size(value: str, font: str, size: float, max_width: float, minimum: float = 7.0) -> float:
    """Shrink a font size until the string fits, the way a real template would."""
    while size > minimum and stringWidth(value, font, size) > max_width:
        size -= 0.25
    return size


def wrap(value: str, font: str, size: float, width: float) -> list[str]:
    if width <= 0 or stringWidth(value, font, size) <= width:
        return [value]
    lines, current = [], ""
    for word in value.split():
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def meta_rows(doc: Document) -> list[tuple[str, str, str | None]]:
    """Label, value and field key for the header details. Key None marks a distractor."""
    labels = doc.labels
    rows: list[tuple[str, str, str | None]] = []
    if doc.number:
        rows.append((labels["number"], doc.number, "document_number"))
    rows.append((labels["date"], doc.date_text(doc.issue_date), "issue_date"))
    if doc.due_date:
        rows.append((labels["due"], doc.date_text(doc.due_date), "due_date"))
    if doc.purchase_order:
        rows.append((labels["po"], doc.purchase_order, "purchase_order"))
    if doc.customer_id:
        rows.append(("Customer ID", doc.customer_id, None))
    if doc.order_date:
        rows.append(("Order Date", doc.date_text(doc.order_date), None))
    if doc.terms:
        rows.append(("Terms", doc.terms, None))
    return rows


def totals_rows(doc: Document) -> list[TotalRow]:
    labels, money = doc.labels, doc.money
    rows = [TotalRow(labels["subtotal"], money.amount(doc.subtotal), "subtotal")]
    if doc.discount is not None:
        rows.append(TotalRow(labels["discount"], money.negative(doc.discount), "discount"))
    if doc.shipping is not None:
        rows.append(TotalRow(labels["shipping"], money.amount(doc.shipping), "shipping"))
    for label, amount in doc.tax_lines:
        rows.append(TotalRow(label, money.amount(amount), "tax"))
    rows.append(TotalRow(labels["total"], money.amount(doc.total), "total", strong=True))
    if doc.deposit is not None:
        rows.append(TotalRow(labels["deposit"], money.negative(doc.deposit), None))
        rows.append(TotalRow(labels["balance"], money.amount(doc.balance), None, strong=True))
    if doc.tendered is not None:
        rows.append(TotalRow("Cash", money.amount(doc.tendered), None))
        rows.append(TotalRow("Change", money.amount(doc.change), None))
    return rows


def cell_text(doc: Document, row: LineItem, field: str) -> str:
    if field == "description":
        return row.description
    if field == "quantity":
        return doc.money.quantity(row.quantity) if row.quantity is not None else ""
    if field == "unit_price":
        return doc.money.number(row.unit_price) if row.unit_price is not None else ""
    return doc.money.number(row.amount)


def vendor_lines(doc: Document) -> list[str]:
    vendor = doc.vendor
    lines = list(vendor.address)
    if vendor.phone:
        lines.append(vendor.phone)
    if vendor.email:
        lines.append(vendor.email)
    if vendor.tax_id:
        lines.append(vendor.tax_id)
    return lines


def party_block(fc: FieldCanvas, x, y, label, party: Party, fonts, *, key=None, size=9.0, label_color=GRAY) -> float:
    """Draw a titled name and address block and return the baseline below it."""
    fc.text(x, y, label, fonts.bold, size - 1.5, color=label_color)
    y += size + 3
    fc.text(x, y, party.name, fonts.bold, size + 0.5, key=key)
    y += size + 3
    for line in party.address:
        fc.text(x, y, line, fonts.regular, size)
        y += size + 2.5
    return y


def notes_block(fc: FieldCanvas, doc: Document, x, y, width, font, size, *, color=GRAY) -> float:
    """Currency note, remit-to line and bank details. Returns the baseline below them."""
    if doc.money.note:
        fc.text(x, y, doc.money.note, font, size, color=color, key="currency")
        y += size + 3
    lines = []
    if doc.remit_to:
        lines.append(f"Remit payment to: {doc.remit_to}")
    if doc.bank_note:
        lines.append(doc.bank_note)
    for text in lines:
        for line in wrap(text, font, size, width):
            fc.text(x, y, line, font, size, color=color)
            y += size + 3
    return y


def ensure_space(fc: FieldCanvas, y, needed, bottom, continuation: Callable[[], float]) -> float:
    if y + needed > bottom:
        fc.next_page()
        return continuation()
    return y


def item_rows(
    fc: FieldCanvas,
    doc: Document,
    y: float,
    cols: list[Col],
    *,
    font: str,
    size: float,
    row_h: float,
    bottom: float,
    continuation: Callable[[], float],
    header: Callable[[float], float],
    x0: float,
    x1: float,
    zebra=None,
    rule=None,
    grid: list[float] | None = None,
    color=INK,
) -> float:
    """Draw the item rows, flowing to a new page when needed. `y` is the top of the first row."""
    y = header(y)
    description = next(col for col in cols if col.field == "description")
    for index, row in enumerate(doc.items):
        lines = wrap(row.description, font, size, description.width)
        height = row_h + (len(lines) - 1) * (size + 2)
        if y + height > bottom:
            fc.next_page()
            y = header(continuation())
        if zebra and index % 2 == 0:
            fc.rect(x0, y, x1 - x0, height, fill=zebra)
        baseline = y + row_h / 2 + size * 0.35
        for col in cols:
            key = f"line_items.{index}.{col.field}"
            if col.field == "description":
                for number, line in enumerate(lines):
                    fc.text(col.x, baseline + number * (size + 2), line, font, size, color=color, key=key)
                continue
            value = cell_text(doc, row, col.field)
            if value:
                fc.text(col.x, baseline, value, font, size, align=col.align, color=color, key=key)
        if rule:
            fc.line(x0, y + height, x1, y + height, color=rule)
        if grid:
            for x in grid:
                fc.line(x, y, x, y + height, color=rule or GRAY)
        y += height
    return y


def totals_block(
    fc: FieldCanvas,
    doc: Document,
    y: float,
    label_x: float,
    value_x: float,
    *,
    font: str,
    bold: str,
    size: float,
    row_h: float,
    label_align: str = "right",
    strong_size: float | None = None,
    strong_fill=None,
    strong_color=INK,
    fill_x0: float | None = None,
    fill_x1: float | None = None,
    rule_color=None,
    color=INK,
) -> float:
    """Draw the totals as label and value rows. `y` is the top of the first row."""
    strong_size = strong_size or size
    for row in totals_rows(doc):
        use_font = bold if row.strong else font
        use_size = strong_size if row.strong else size
        use_color = strong_color if row.strong else color
        if row.strong and strong_fill:
            fc.rect(fill_x0, y, fill_x1 - fill_x0, row_h, fill=strong_fill)
        elif row.strong and rule_color:
            fc.line(fill_x0 if fill_x0 is not None else label_x, y, fill_x1 if fill_x1 is not None else value_x, y, color=rule_color)
        baseline = y + row_h / 2 + use_size * 0.35
        fc.text(label_x, baseline, row.label, use_font, use_size, align=label_align, color=use_color)
        fc.text(value_x, baseline, row.value, use_font, use_size, align="right", color=use_color, key=row.key)
        y += row_h
    return y
