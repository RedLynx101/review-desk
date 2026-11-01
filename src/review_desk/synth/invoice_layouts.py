"""Invoice layouts. Each draws one document on a FieldCanvas.

`letterhead` and `summary_first` only appear in the held-out split.
"""

from __future__ import annotations

from .canvas import GRAY, INK, LIGHT, PALE, WHITE, FieldCanvas
from .content import Document
from .layout_common import (
    Col,
    ensure_space,
    fit_size,
    item_rows,
    meta_rows,
    notes_block,
    party_block,
    totals_block,
    totals_rows,
    vendor_lines,
    wrap,
)


def _tint(color, amount=0.12):
    return tuple(channel * amount + (1 - amount) for channel in color)


def _continued(fc: FieldCanvas, doc: Document, x, y, font) -> float:
    """Header line of a continuation page. Returns the baseline below it."""
    fc.text(x, y, doc.vendor.name, font, 10, key="vendor_name")
    parts = [(f"{doc.labels['title']} ", None)]
    if doc.number:
        parts.append((doc.number, "document_number"))
    parts.append((" (continued)", None))
    fc.spans(x, y + 13, parts, font, 8.5, color=GRAY)
    return y + 30


def classic(fc: FieldCanvas, doc: Document) -> None:
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    left, right = 48.0, fc.w - 48
    bottom = fc.h - 48

    name_size = fit_size(doc.vendor.name, fonts.bold, 15, 300)
    fc.text(left, 60, doc.vendor.name, fonts.bold, name_size, key="vendor_name")
    y = 75.0
    for line in vendor_lines(doc):
        fc.text(left, y, line, fonts.regular, 8.5, color=GRAY)
        y += 11
    fc.text(right, 64, labels["title"], fonts.bold, 22, align="right", color=accent, key="document_type")
    meta_y = 86.0
    for label, value, key in meta_rows(doc):
        fc.text(right - 92, meta_y, f"{label}:", fonts.bold, 8.5, align="right", color=GRAY)
        fc.text(right, meta_y, value, fonts.regular, 9, align="right", key=key)
        meta_y += 13
    y = max(y, meta_y) + 14

    block_bottom = party_block(fc, left, y, labels["bill_to"], doc.bill_to, fonts, key="bill_to_name")
    if doc.ship_to:
        block_bottom = max(block_bottom, party_block(fc, left + 240, y, labels["ship_to"], doc.ship_to, fonts))
    y = block_bottom + 14

    cols = [
        Col("description", left + 6, "left", right - left - 200, labels["description"]),
        Col("quantity", right - 150, "right", 0, labels["quantity"]),
        Col("unit_price", right - 76, "right", 0, labels["unit_price"]),
        Col("amount", right - 6, "right", 0, labels["amount"]),
    ]

    def header(top):
        fc.rect(left, top, right - left, 19, fill=accent)
        for col in cols:
            fc.text(col.x, top + 13, col.title, fonts.bold, 8.5, align=col.align, color=WHITE)
        return top + 19

    def continuation():
        return _continued(fc, doc, left, 52, fonts.bold)

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=9, row_h=18, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right, rule=LIGHT)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 8, len(rows) * 16 + 50, bottom, continuation)
    y = totals_block(fc, doc, y, right - 86, right - 6, font=fonts.regular, bold=fonts.bold, size=9, row_h=16,
                     strong_size=10.5, rule_color=INK, fill_x0=right - 200, fill_x1=right)
    notes_block(fc, doc, left, y + 18, right - left, fonts.regular, 8)


def banner(fc: FieldCanvas, doc: Document) -> None:
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    left, right = 40.0, fc.w - 40
    bottom = fc.h - 48

    fc.rect(0, 0, fc.w, 84, fill=accent)
    name_size = fit_size(doc.vendor.name, fonts.bold, 19, 370)
    fc.text(left, 38, doc.vendor.name, fonts.bold, name_size, color=WHITE, key="vendor_name")
    fc.text(left, 54, ", ".join(doc.vendor.address), fonts.regular, 8.5, color=WHITE)
    fc.text(left, 66, f"{doc.vendor.phone}   {doc.vendor.email}", fonts.regular, 8.5, color=WHITE)
    fc.text(right, 40, labels["title"], fonts.bold, 17, align="right", color=WHITE, key="document_type")

    # Details run across the page in a strip, label over value.
    rows = meta_rows(doc)
    per_row = 4
    width = (right - left) / per_row
    y = 104.0
    for start in range(0, len(rows), per_row):
        for column, (label, value, key) in enumerate(rows[start:start + per_row]):
            x = left + column * width
            fc.text(x, y, label, fonts.regular, 7.5, color=GRAY)
            fc.text(x, y + 12, value, fonts.bold, 9.5, key=key)
        y += 28
    fc.line(left, y - 4, right, y - 4)
    y += 12

    block_bottom = party_block(fc, left, y, labels["bill_to"], doc.bill_to, fonts, key="bill_to_name")
    if doc.ship_to:
        block_bottom = max(block_bottom, party_block(fc, left + width * 2, y, labels["ship_to"], doc.ship_to, fonts))
    y = block_bottom + 12

    cols = [
        Col("description", left + 6, "left", right - left - 215, labels["description"]),
        Col("quantity", right - 164, "right", 0, labels["quantity"]),
        Col("unit_price", right - 88, "right", 0, labels["unit_price"]),
        Col("amount", right - 6, "right", 0, labels["amount"]),
    ]

    def header(top):
        for col in cols:
            fc.text(col.x, top + 12, col.title, fonts.bold, 8.5, align=col.align, color=accent)
        fc.line(left, top + 17, right, top + 17, color=accent, width=1)
        return top + 17

    def continuation():
        return _continued(fc, doc, left, 52, fonts.bold)

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=9, row_h=17, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right, zebra=PALE)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 10, len(rows) * 17 + 50, bottom, continuation)
    box_left = right - 230
    fc.rect(box_left, y, 230, len(rows) * 17 + 4, fill=PALE)
    y = totals_block(fc, doc, y + 2, box_left + 8, right - 8, font=fonts.regular, bold=fonts.bold, size=9, row_h=17,
                     label_align="left", strong_fill=accent, strong_color=WHITE, fill_x0=box_left, fill_x1=right)
    notes_block(fc, doc, left, y + 18, right - left, fonts.regular, 8)


def sidebar(fc: FieldCanvas, doc: Document) -> None:
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    panel = 172.0
    left, right = panel + 22, fc.w - 34
    bottom = fc.h - 48

    def draw_panel():
        fc.rect(0, 0, panel, fc.h, fill=_tint(accent))
        fc.rect(0, 0, 5, fc.h, fill=accent)
        y = 56.0
        for line in wrap(doc.vendor.name, fonts.bold, 13, panel - 34):
            fc.text(18, y, line, fonts.bold, 13, key="vendor_name")
            y += 16
        y += 2
        for line in vendor_lines(doc):
            fc.text(18, y, line, fonts.regular, 7.5, color=GRAY)
            y += 10
        y += 16
        for label, value, key in meta_rows(doc):
            fc.text(18, y, label.upper(), fonts.regular, 6.5, color=GRAY)
            fc.text(18, y + 12, value, fonts.bold, 9.5, key=key)
            y += 30

    draw_panel()
    fc.text(left, 62, labels["title"], fonts.bold, 24, color=accent, key="document_type")
    y = party_block(fc, left, 88, labels["bill_to"], doc.bill_to, fonts, key="bill_to_name")
    if doc.ship_to:
        y = max(y, party_block(fc, left + 180, 88, labels["ship_to"], doc.ship_to, fonts))
    y += 12

    cols = [
        Col("description", left + 2, "left", right - left - 164, labels["description"]),
        Col("quantity", right - 124, "right", 0, labels["quantity"]),
        Col("unit_price", right - 62, "right", 0, labels["unit_price"]),
        Col("amount", right - 2, "right", 0, labels["amount"]),
    ]

    def header(top):
        fc.line(left, top, right, top, color=INK, width=1)
        for col in cols:
            fc.text(col.x, top + 13, col.title, fonts.bold, 8, align=col.align)
        fc.line(left, top + 18, right, top + 18, color=INK, width=0.5)
        return top + 18

    def continuation():
        draw_panel()
        fc.text(left, 52, f"{labels['title']} (continued)", fonts.bold, 11, color=accent)
        return 66.0

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=8.5, row_h=17, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right, rule=LIGHT)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 10, len(rows) * 15 + 50, bottom, continuation)
    y = totals_block(fc, doc, y, right - 72, right - 2, font=fonts.regular, bold=fonts.bold, size=8.5, row_h=15,
                     strong_size=10, rule_color=INK, fill_x0=right - 190, fill_x1=right)
    notes_block(fc, doc, left, y + 16, right - left, fonts.regular, 7.5)


def ledger(fc: FieldCanvas, doc: Document) -> None:
    """The look of a printout from old accounting software: monospace, no fills, all caps."""
    fonts, labels = doc.fonts, doc.labels
    size = 9.0
    char = size * 0.6
    left = 44.0
    columns = int((fc.w - 2 * left) / char)
    right = left + columns * char
    bottom = fc.h - 46
    line_h = 12.0

    def rule(y, mark="-"):
        fc.text(left, y, mark * columns, fonts.regular, size, color=GRAY)

    name = doc.vendor.name.upper()
    fc.text(left, 52, name, fonts.bold, fit_size(name, fonts.bold, 12, right - left), key="vendor_name")
    y = 65.0
    fc.text(left, y, "  ".join(doc.vendor.address).upper(), fonts.regular, 8)
    y += 11
    fc.text(left, y, f"TEL {doc.vendor.phone}   {doc.vendor.tax_id}", fonts.regular, 8)
    y += 14
    rule(y, "=")
    y += line_h
    fc.text(left, y, labels["title"].upper(), fonts.bold, 11, key="document_type")
    y += line_h + 2

    rows = meta_rows(doc)
    half = (len(rows) + 1) // 2
    second_col = left + char * (columns // 2 + 2)
    for offset, (label, value, key) in enumerate(rows[:half]):
        fc.spans(left, y + offset * line_h, [(f"{label.upper() + ':':<16}", None), (value, key)], fonts.regular, size)
    for offset, (label, value, key) in enumerate(rows[half:]):
        fc.spans(second_col, y + offset * line_h, [(f"{label.upper() + ':':<16}", None), (value, key)], fonts.regular, size)
    y += half * line_h + 4

    fc.spans(left, y, [(f"{labels['bill_to'].upper() + ':':<16}", None), (doc.bill_to.name, "bill_to_name")], fonts.regular, size)
    y += line_h
    fc.text(left + 16 * char, y, ", ".join(doc.bill_to.address), fonts.regular, size)
    y += line_h
    if doc.ship_to:
        fc.spans(left, y, [(f"{labels['ship_to'].upper() + ':':<16}", None), (doc.ship_to.name, None)], fonts.regular, size)
        y += line_h
    y += 2

    cols = [
        Col("quantity", left + 6 * char, "right", 0, labels["quantity"].upper()),
        Col("description", left + 8 * char, "left", char * (columns - 34), labels["description"].upper()),
        Col("unit_price", right - 14 * char, "right", 0, labels["unit_price"].upper()),
        Col("amount", right, "right", 0, labels["amount"].upper()),
    ]

    def header(top):
        rule(top + 2)
        for col in cols:
            fc.text(col.x, top + 13, col.title, fonts.bold, size, align=col.align)
        rule(top + 24)
        return top + 26

    def continuation():
        fc.text(left, 48, f"{name}  (CONTINUED)", fonts.bold, 9.5, key="vendor_name")
        return 56.0

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=size, row_h=line_h, bottom=bottom,
                  continuation=continuation, header=header, x0=left, x1=right)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 2, len(rows) * line_h + 50, bottom, continuation)
    rule(y + 2)
    y += 8
    for row in rows:
        baseline = y + 9
        font = fonts.bold if row.strong else fonts.regular
        fc.text(right - 18 * char, baseline, row.label.upper(), font, size, align="right")
        fc.text(right, baseline, row.value, font, size, align="right", key=row.key)
        y += line_h
    rule(y + 6, "=")
    notes_block(fc, doc, left, y + 20, right - left, fonts.regular, 8, color=INK)


def _box(fc, x, y, width, height, label, value, key, fonts, *, size=9.5):
    fc.rect(x, y, width, height, stroke=GRAY, line_width=0.6)
    fc.text(x + 4, y + 9, label.upper(), fonts.regular, 6, color=GRAY)
    if value:
        fc.text(x + 4, y + height - 6, value, fonts.regular, fit_size(value, fonts.regular, size, width - 8), key=key)


def boxed(fc: FieldCanvas, doc: Document) -> None:
    """A preprinted form: everything sits in a ruled box with a tiny caption."""
    fonts, labels = doc.fonts, doc.labels
    left, right = 36.0, fc.w - 36
    bottom = fc.h - 44
    total_width = right - left
    fc.rect(left, 36, total_width * 0.66, 72, stroke=GRAY, line_width=0.8)
    size = fit_size(doc.vendor.name, fonts.bold, 14, total_width * 0.66 - 12)
    fc.text(left + 6, 56, doc.vendor.name, fonts.bold, size, key="vendor_name")
    y = 69.0
    for line in (", ".join(doc.vendor.address), f"{doc.vendor.phone}   {doc.vendor.tax_id}", doc.vendor.email):
        fc.text(left + 6, y, line, fonts.regular, 7.5, color=GRAY)
        y += 10
    title_x = left + total_width * 0.66
    fc.rect(title_x, 36, total_width * 0.34, 72, fill=PALE, stroke=GRAY, line_width=0.8)
    fc.text(title_x + total_width * 0.17, 79, labels["title"], fonts.bold, 15, align="center", key="document_type")

    rows = meta_rows(doc)
    per_row = 4
    cell = total_width / per_row
    y = 108.0
    for start in range(0, len(rows), per_row):
        for column, (label, value, key) in enumerate(rows[start:start + per_row]):
            _box(fc, left + column * cell, y, cell, 28, label, value, key, fonts)
        y += 28

    half = total_width / 2
    box_h = 56.0
    fc.rect(left, y, half, box_h, stroke=GRAY, line_width=0.6)
    fc.rect(left + half, y, half, box_h, stroke=GRAY, line_width=0.6)
    fc.text(left + 4, y + 9, labels["bill_to"].upper(), fonts.regular, 6, color=GRAY)
    fc.text(left + 4, y + 22, doc.bill_to.name, fonts.bold, 9.5, key="bill_to_name")
    for offset, line in enumerate(doc.bill_to.address):
        fc.text(left + 4, y + 33 + offset * 10, line, fonts.regular, 8)
    if doc.ship_to:
        fc.text(left + half + 4, y + 9, labels["ship_to"].upper(), fonts.regular, 6, color=GRAY)
        fc.text(left + half + 4, y + 22, doc.ship_to.name, fonts.bold, 9.5)
        for offset, line in enumerate(doc.ship_to.address):
            fc.text(left + half + 4, y + 33 + offset * 10, line, fonts.regular, 8)
    y += box_h + 10

    grid = [left, right - 200, right - 140, right - 70, right]
    cols = [
        Col("description", left + 5, "left", grid[1] - left - 10, labels["description"]),
        Col("quantity", grid[2] - 5, "right", 0, labels["quantity"]),
        Col("unit_price", grid[3] - 5, "right", 0, labels["unit_price"]),
        Col("amount", grid[4] - 5, "right", 0, labels["amount"]),
    ]

    def header(top):
        fc.rect(left, top, total_width, 18, fill=PALE, stroke=GRAY, line_width=0.6)
        for col in cols:
            fc.text(col.x, top + 12, col.title, fonts.bold, 8, align=col.align)
        for x in grid[1:-1]:
            fc.line(x, top, x, top + 18, color=GRAY)
        return top + 18

    def continuation():
        return _continued(fc, doc, left, 48, fonts.bold)

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=8.5, row_h=17, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right, rule=GRAY, grid=grid)
    rows = totals_rows(doc)
    y = ensure_space(fc, y, len(rows) * 19 + 50, bottom, continuation)
    label_left = right - 200
    for row in rows:
        fc.rect(label_left, y, 130, 19, stroke=GRAY, line_width=0.6, fill=PALE if row.strong else None)
        fc.rect(right - 70, y, 70, 19, stroke=GRAY, line_width=0.6)
        font = fonts.bold if row.strong else fonts.regular
        fc.text(label_left + 5, y + 13, row.label, font, 8.5)
        fc.text(right - 5, y + 13, row.value, font, fit_size(row.value, font, 8.5, 62), align="right", key=row.key)
        y += 19
    notes_block(fc, doc, left, y + 16, right - left, fonts.regular, 7.5)


def letterhead(fc: FieldCanvas, doc: Document) -> None:
    """Held-out only. A typed letter: the details are in a sentence and tables have no rules."""
    fonts, labels = doc.fonts, doc.labels
    left, right = 66.0, fc.w - 66
    center = fc.w / 2
    bottom = fc.h - 52
    fc.text(center, 62, doc.vendor.name, fonts.bold, fit_size(doc.vendor.name, fonts.bold, 17, right - left), align="center", key="vendor_name")
    fc.text(center, 77, "  |  ".join(doc.vendor.address), fonts.regular, 9, align="center", color=GRAY)
    fc.text(center, 89, f"{doc.vendor.phone}  |  {doc.vendor.web}", fonts.regular, 9, align="center", color=GRAY)
    fc.line(left, 98, right, 98, color=INK, width=0.8)

    y = 124.0
    fc.text(left, y, "To:", fonts.regular, 10.5)
    fc.text(left + 24, y, doc.bill_to.name, fonts.bold, 10.5, key="bill_to_name")
    y += 13
    for line in doc.bill_to.address:
        fc.text(left + 24, y, line, fonts.regular, 10.5)
        y += 13
    y += 12

    title_word = doc.labels["title"].capitalize()
    parts = [(title_word, "document_type")]
    if doc.number:
        parts += [(" no. ", None), (doc.number, "document_number")]
    parts += [(", dated ", None), (doc.date_text(doc.issue_date), "issue_date")]
    if doc.due_date:
        parts += [(", is payable by ", None), (doc.date_text(doc.due_date), "due_date")]
    parts.append((".", None))
    fc.spans(left, y, parts, fonts.regular, 10.5)
    y += 14
    if doc.purchase_order:
        fc.spans(left, y, [("This bill refers to your order ", None), (doc.purchase_order, "purchase_order"), (".", None)], fonts.regular, 10.5)
        y += 14
    y += 12

    cols = [
        Col("description", left, "left", right - left - 170, "Particulars"),
        Col("quantity", right - 128, "right", 0, labels["quantity"]),
        Col("unit_price", right - 64, "right", 0, labels["unit_price"]),
        Col("amount", right, "right", 0, labels["amount"]),
    ]

    def header(top):
        for col in cols:
            fc.text(col.x, top + 10, col.title, fonts.bold, 9.5, align=col.align)
        fc.line(left, top + 15, right, top + 15, color=INK)
        return top + 17

    def continuation():
        return _continued(fc, doc, left, 56, fonts.bold)

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=10, row_h=15.5, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 10, len(rows) * 15 + 56, bottom, continuation)
    fc.line(left, y, right, y, color=INK)
    y = totals_block(fc, doc, y + 4, left, left + 230, font=fonts.regular, bold=fonts.bold, size=10, row_h=15, label_align="left")
    y = notes_block(fc, doc, left, y + 20, right - left, fonts.regular, 9)
    fc.text(left, y + 6, "With thanks for your custom.", fonts.regular, 10)


def summary_first(fc: FieldCanvas, doc: Document) -> None:
    """Held-out only. An app-style bill: the amount sits in a box up top, the arithmetic further down."""
    fonts, labels, accent = doc.fonts, doc.labels, doc.accent
    left, right = 44.0, fc.w - 44
    bottom = fc.h - 48

    fc.text(left, 54, doc.vendor.name, fonts.bold, fit_size(doc.vendor.name, fonts.bold, 13, 290), key="vendor_name")
    y = 68.0
    for line in vendor_lines(doc)[:3]:
        fc.text(left, y, line, fonts.regular, 7.5, color=GRAY)
        y += 10

    box_left = right - 190
    fc.rect(box_left, 38, 190, 74, fill=_tint(accent, 0.1), stroke=accent)
    if doc.deposit is None:
        fc.text(box_left + 10, 53, "Amount due", fonts.regular, 8, color=GRAY)
        amount = doc.money.amount(doc.total)
        fc.text(box_left + 10, 77, amount, fonts.bold, fit_size(amount, fonts.bold, 18, 170), key="total")
    else:
        fc.text(box_left + 10, 53, labels["balance"], fonts.regular, 8, color=GRAY)
        amount = doc.money.amount(doc.balance)
        fc.text(box_left + 10, 77, amount, fonts.bold, fit_size(amount, fonts.bold, 18, 170))
    if doc.due_date:
        fc.spans(box_left + 10, 96, [("by ", None), (doc.date_text(doc.due_date), "due_date")], fonts.regular, 8.5)
    else:
        fc.text(box_left + 10, 96, doc.terms or "", fonts.regular, 8.5)

    y = 132.0
    fc.text(left, y, labels["title"], fonts.bold, 11, color=accent, key="document_type")
    if doc.number:
        fc.text(left + 90, y, doc.number, fonts.regular, 9.5, key="document_number")
    y += 16
    fc.spans(left, y, [(f"{labels['date']} ", None), (doc.date_text(doc.issue_date), "issue_date")], fonts.regular, 8.5, color=INK)
    if doc.purchase_order:
        fc.spans(left + 190, y, [(f"{labels['po']} ", None), (doc.purchase_order, "purchase_order")], fonts.regular, 8.5)
    y += 20
    block_bottom = party_block(fc, left, y, labels["bill_to"], doc.bill_to, fonts, key="bill_to_name", size=8.5)
    if doc.ship_to:
        block_bottom = max(block_bottom, party_block(fc, left + 250, y, labels["ship_to"], doc.ship_to, fonts, size=8.5))
    y = block_bottom + 10

    cols = [
        Col("description", left, "left", right - left - 190, labels["description"]),
        Col("quantity", right - 150, "right", 0, labels["quantity"]),
        Col("unit_price", right - 78, "right", 0, labels["unit_price"]),
        Col("amount", right, "right", 0, labels["amount"]),
    ]

    def header(top):
        for col in cols:
            fc.text(col.x, top + 11, col.title, fonts.regular, 7.5, align=col.align, color=GRAY)
        fc.line(left, top + 16, right, top + 16)
        return top + 16

    def continuation():
        return _continued(fc, doc, left, 52, fonts.bold)

    y = item_rows(fc, doc, y, cols, font=fonts.regular, size=8.5, row_h=18, bottom=bottom, continuation=continuation,
                  header=header, x0=left, x1=right, rule=PALE)
    rows = totals_rows(doc)
    y = ensure_space(fc, y + 8, len(rows) * 15 + 46, bottom, continuation)
    y = totals_block(fc, doc, y, right - 92, right, font=fonts.regular, bold=fonts.bold, size=8.5, row_h=15, rule_color=LIGHT, fill_x0=right - 200, fill_x1=right)
    notes_block(fc, doc, left, y + 14, right - left, fonts.regular, 7.5)


INVOICE_RENDERERS = {
    "classic": classic,
    "banner": banner,
    "sidebar": sidebar,
    "ledger": ledger,
    "boxed": boxed,
    "letterhead": letterhead,
    "summary_first": summary_first,
}
