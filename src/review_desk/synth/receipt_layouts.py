"""Receipt layouts. Tall narrow ones return the baseline they ended on so the page can fit them.

`kiosk` only appears in the held-out split.
"""

from __future__ import annotations

from reportlab.pdfbase.pdfmetrics import stringWidth

from .canvas import GRAY, INK, LIGHT, PALE, FieldCanvas
from .content import Document
from .layout_common import fit_size, totals_rows, wrap

#: Page width in points for layouts that print on a roll. None of these break across pages.
ROLL_WIDTHS = {"thermal": 216.0, "pos_wide": 300.0, "kiosk": 240.0}


def _currency_note(fc: FieldCanvas, doc: Document, x, y, font, size, *, align="left") -> float:
    if doc.money.note:
        fc.text(x, y, doc.money.note, font, size, align=align, color=GRAY, key="currency")
        return y + size + 3
    return y


def thermal(fc: FieldCanvas, doc: Document) -> float:
    """A cash register ticket in monospace, with quantity on its own line when it isn't 1."""
    fonts, labels = doc.fonts, doc.labels
    size = 8.5
    line = 10.5
    left, right = 10.0, fc.w - 10
    center = fc.w / 2
    columns = int((right - left) / (size * 0.6))
    y = 22.0

    def rule():
        nonlocal y
        fc.text(left, y, "-" * columns, fonts.regular, size, color=GRAY)
        y += line

    name = doc.vendor.name.upper()
    fc.text(center, y, name, fonts.bold, fit_size(name, fonts.bold, 10, right - left), align="center", key="vendor_name")
    y += line + 1
    for text in (*doc.vendor.address, doc.vendor.phone):
        fc.text(center, y, text, fonts.regular, 7.5, align="center")
        y += 9.5
    y += 3
    rule()
    fc.text(left, y, labels["title"], fonts.bold, size, key="document_type")
    y += line
    fc.spans(left, y, [(f"{labels['date']} ", None), (doc.date_text(doc.issue_date), "issue_date"), (f"  {doc.sale_time}", None)], fonts.regular, size)
    y += line
    if doc.number:
        fc.spans(left, y, [(f"{labels['number']} ", None), (doc.number, "document_number")], fonts.regular, size)
        y += line
    fc.text(left, y, f"Cashier {doc.cashier}", fonts.regular, size)
    y += line
    rule()

    for index, row in enumerate(doc.items):
        fc.text(left, y, row.description, fonts.regular, size, key=f"line_items.{index}.description")
        fc.text(right, y, doc.money.number(row.amount), fonts.regular, size, align="right", key=f"line_items.{index}.amount")
        y += line
        if row.quantity is not None:
            quantity = doc.money.quantity(row.quantity)
            price = doc.money.number(row.unit_price)
            fc.spans(left + 10, y, [(quantity, f"line_items.{index}.quantity"), (" x ", None), (price, f"line_items.{index}.unit_price")], fonts.regular, 7.5)
            y += line - 1
    rule()
    for row in totals_rows(doc):
        font = fonts.bold if row.strong else fonts.regular
        fc.text(left, y, row.label, font, size)
        fc.text(right, y, row.value, font, size, align="right", key=row.key)
        y += line
    if doc.card_last4:
        fc.text(left, y, f"CARD ****{doc.card_last4}", fonts.regular, size)
        y += line
    rule()
    y = _currency_note(fc, doc, center, y, fonts.regular, 7.5, align="center")
    fc.text(center, y + 2, "Thank you", fonts.regular, size, align="center")
    return y + 4


def pos_wide(fc: FieldCanvas, doc: Document) -> float:
    """A point-of-sale slip with a real table and every line priced."""
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    left, right = 16.0, fc.w - 16
    center = fc.w / 2
    y = 28.0
    size = fit_size(doc.vendor.name, fonts.bold, 13, right - left)
    fc.text(center, y, doc.vendor.name, fonts.bold, size, align="center", color=accent, key="vendor_name")
    y += 13
    fc.text(center, y, " / ".join(doc.vendor.address), fonts.regular, 7.5, align="center", color=GRAY)
    y += 10
    fc.text(center, y, f"{doc.vendor.phone}  {doc.vendor.web}", fonts.regular, 7.5, align="center", color=GRAY)
    y += 15
    fc.text(center, y, labels["title"], fonts.bold, 10, align="center", key="document_type")
    y += 15
    if doc.number:
        fc.spans(left, y, [(f"{labels['number']}: ", None), (doc.number, "document_number")], fonts.regular, 8)
    fc.text(right, y, f"{doc.date_text(doc.issue_date)}", fonts.regular, 8, align="right", key="issue_date")
    y += 11
    fc.text(left, y, f"Server {doc.cashier}", fonts.regular, 8, color=GRAY)
    fc.text(right, y, doc.sale_time, fonts.regular, 8, align="right", color=GRAY)
    y += 8

    quantity_x, each_x = left + 18, right - 56
    fc.rect(left, y, right - left, 15, fill=PALE)
    y += 10.5
    fc.text(quantity_x, y, labels["quantity"], fonts.bold, 7.5, align="right")
    fc.text(quantity_x + 8, y, labels["description"], fonts.bold, 7.5)
    fc.text(each_x, y, labels["unit_price"], fonts.bold, 7.5, align="right")
    fc.text(right - 2, y, labels["amount"], fonts.bold, 7.5, align="right")
    y += 14
    for index, row in enumerate(doc.items):
        fc.text(quantity_x, y, doc.money.quantity(row.quantity), fonts.regular, 8.5, align="right", key=f"line_items.{index}.quantity")
        fc.text(quantity_x + 8, y, row.description, fonts.regular, 8.5, key=f"line_items.{index}.description")
        fc.text(each_x, y, doc.money.number(row.unit_price), fonts.regular, 8.5, align="right", key=f"line_items.{index}.unit_price")
        fc.text(right - 2, y, doc.money.number(row.amount), fonts.regular, 8.5, align="right", key=f"line_items.{index}.amount")
        y += 13
    fc.line(left, y - 7, right, y - 7, color=LIGHT)
    y += 6
    for row in totals_rows(doc):
        font = fonts.bold if row.strong else fonts.regular
        size = 10 if row.strong else 8.5
        fc.text(right - 86, y, row.label, font, size, align="right")
        fc.text(right - 2, y, row.value, font, size, align="right", key=row.key)
        y += 13 if not row.strong else 15
    if doc.card_last4:
        fc.text(right - 86, y, "Card", fonts.regular, 8, align="right")
        fc.text(right - 2, y, f"****{doc.card_last4}", fonts.regular, 8, align="right")
        y += 12
    y += 4
    y = _currency_note(fc, doc, center, y, fonts.regular, 7.5, align="center")
    fc.text(center, y + 4, "Keep this slip for returns within 30 days.", fonts.regular, 7.5, align="center", color=GRAY)
    return y + 6


def card_slip(fc: FieldCanvas, doc: Document) -> float:
    """An emailed e-receipt printed to a full page: a centered card, with the amount up top."""
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    width = 370.0
    left = (fc.w - width) / 2
    right = left + width
    pad = 22.0
    fc.rect(left, 58, width, 5, fill=accent)
    y = 92.0
    fc.text(left + pad, y, "Receipt from", fonts.regular, 10, color=GRAY)
    y += 20
    name_size = fit_size(doc.vendor.name, fonts.bold, 15, width - 2 * pad)
    fc.text(left + pad, y, doc.vendor.name, fonts.bold, name_size, key="vendor_name")
    y += 28
    columns = (("Amount Paid", doc.money.amount(doc.total), "total"),
               (labels["date"], doc.date_text(doc.issue_date), "issue_date"),
               ("Payment Method", f"CARD ****{doc.card_last4}", None))
    x = left + pad
    for title, value, key in columns:
        fc.text(x, y, title.upper(), fonts.regular, 7, color=GRAY)
        fc.text(x, y + 14, value, fonts.bold, 9.5, key=key)
        x += (width - 2 * pad) / 3
    y += 40
    fc.text(left + pad, y, labels["title"], fonts.bold, 8.5, key="document_type")
    if doc.number:
        # The # stays out of the field's box: it isn't part of the number.
        number_x = right - pad - stringWidth(doc.number, fonts.regular, 8.5)
        fc.text(number_x, y, "#", fonts.regular, 8.5, align="right", color=GRAY)
        fc.text(number_x, y, doc.number, fonts.regular, 8.5, color=GRAY, key="document_number")
    y += 8
    fc.rect(left + pad, y, width - 2 * pad, 1, fill=LIGHT)
    y += 18
    for index, row in enumerate(doc.items):
        fc.text(left + pad, y, row.description, fonts.regular, 9, key=f"line_items.{index}.description")
        if row.quantity is not None:
            fc.spans(left + pad + 190, y, [("x ", None), (doc.money.quantity(row.quantity), f"line_items.{index}.quantity")], fonts.regular, 9, color=GRAY)
        fc.text(right - pad, y, doc.money.amount(row.amount), fonts.regular, 9, align="right", key=f"line_items.{index}.amount")
        y += 17
    y += 2
    fc.rect(left + pad, y - 10, width - 2 * pad, 1, fill=LIGHT)
    y += 6
    for row in totals_rows(doc):
        font = fonts.bold if row.strong else fonts.regular
        fc.text(right - pad - 110, y, row.label, font, 9, align="right")
        fc.text(right - pad, y, row.value, font, 9, align="right", key=row.key)
        y += 15
    y += 8
    y = _currency_note(fc, doc, left + pad, y, fonts.regular, 7.5)
    for text in wrap(f"Questions? {doc.vendor.email}  {doc.vendor.phone}", fonts.regular, 7.5, width - 2 * pad):
        fc.text(left + pad, y, text, fonts.regular, 7.5, color=GRAY)
        y += 10
    for text in doc.vendor.address:
        fc.text(left + pad, y, text, fonts.regular, 7.5, color=GRAY)
        y += 10
    fc.rect(left, 44, width, y - 44 + 8, stroke=LIGHT, line_width=0.8)
    return y


def kiosk(fc: FieldCanvas, doc: Document) -> float:
    """Held-out only. Order number first, tax split across two codes, amounts tagged by tax code."""
    fonts, labels = doc.fonts, doc.labels
    left, right = 12.0, fc.w - 12
    y = 30.0
    if doc.number:
        fc.spans(left, y, [("ORDER ", None)], fonts.bold, 14)
        fc.text(left + 56, y, doc.number, fonts.bold, fit_size(doc.number, fonts.bold, 14, right - left - 56), key="document_number")
    else:
        fc.text(left, y, "ORDER", fonts.bold, 14)
    y += 14
    fc.text(left, y, labels["title"], fonts.regular, 8, color=GRAY, key="document_type")
    y += 16
    name_size = fit_size(doc.vendor.name, fonts.bold, 9.5, right - left)
    fc.text(left, y, doc.vendor.name, fonts.bold, name_size, key="vendor_name")
    y += 11
    fc.text(left, y, ", ".join(doc.vendor.address), fonts.regular, fit_size(", ".join(doc.vendor.address), fonts.regular, 7, right - left, 5.5), color=GRAY)
    y += 11
    fc.spans(left, y, [(doc.date_text(doc.issue_date), "issue_date"), (f"  {doc.sale_time}  Kiosk {doc.cashier}", None)], fonts.regular, 8)
    y += 8
    fc.line(left, y, right, y, color=INK, width=0.8)
    y += 12
    for index, row in enumerate(doc.items):
        x = left
        if row.quantity is not None:
            x = fc.text(x, y, doc.money.quantity(row.quantity), fonts.regular, 8.5, key=f"line_items.{index}.quantity")
            x = fc.text(x, y, "x ", fonts.regular, 8.5)
        fc.text(x, y, row.description, fonts.regular, 8.5, key=f"line_items.{index}.description")
        fc.text(right - 10, y, doc.money.number(row.amount), fonts.regular, 8.5, align="right", key=f"line_items.{index}.amount")
        fc.text(right, y, "A", fonts.regular, 7, align="right", color=GRAY)
        y += 12
    fc.line(left, y - 6, right, y - 6, color=LIGHT)
    y += 6
    for row in totals_rows(doc):
        font = fonts.bold if row.strong else fonts.regular
        size = 11 if row.strong else 8.5
        fc.text(left, y, row.label, font, size)
        fc.text(right, y, row.value, font, size, align="right", key=row.key)
        y += 15 if row.strong else 12
    if doc.card_last4:
        fc.text(left, y, f"Card ****{doc.card_last4} approved", fonts.regular, 8, color=GRAY)
        y += 11
    y = _currency_note(fc, doc, left, y, fonts.regular, 7.5)
    return y


RECEIPT_RENDERERS = {
    "thermal": thermal,
    "pos_wide": pos_wide,
    "card_slip": card_slip,
    "kiosk": kiosk,
}
