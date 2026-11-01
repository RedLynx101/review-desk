"""Deterministic cleanup of what the model copied, before anything gets scored.

Small models make the same few slips: copying the label along with the value, keeping the time on a
date, or reading the "2 x 3.50" line under a receipt item as an item of its own. These are safe to
fix in code, because every result is still checked against the page afterwards.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .locate import _longest_prefix_run
from .normalize import parse_amount, parse_quantity, squash
from .pagetext import Line
from .schema import Cited, ItemReading, ModelReading

# A label has to end at a space or punctuation, so the "PO" in "PO-77821" and the "INV" in
# "INV-2026-00412" stay put: there, those letters are part of the identifier.
_ID_LABEL = re.compile(
    r"^\s*(?:(?:(?:tax\s+)?invoice|inv|bill|receipt|ticket|trans(?:action)?|order|ref(?:erence)?|p\.?\s?o\.?|purchase\s+order|your\s+ref|customer\s+po)(?=[\s:#.]))?"
    r"\s*(?:(?:no|num|number)\.?(?=[\s:#])|#)?\s*[:#.]*\s*",
    re.IGNORECASE,
)
_DATE_LABEL = re.compile(r"^\s*(?:(?:invoice|billing|sale|issue|due|sold|order)\s+)?(?:date|issued|dated|sold|due|pay\s+by|payment\s+due)\s*[:.]?\s*", re.IGNORECASE)
_TIME_SUFFIX = re.compile(r"[\s,]+\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?\s*$", re.IGNORECASE)
_QTY_PRICE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*[xX×@*]\s*\$?\s*(\d[\d,.]*)\s*$")
_MONEY_TOKEN = re.compile(r"[-(]?(?:[A-Za-z]{0,3}\$|[£€])?\s?\d[\d.,']*\d(?:\s?[€£])?\)?|\d")


def tidy_identifier(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = _ID_LABEL.sub("", text.strip(), count=1).strip(" :#.,")
    # If the label pattern swallowed everything, the text was the identifier after all.
    return stripped or text.strip()


def tidy_date(text: str | None) -> str | None:
    if text is None:
        return None
    return _TIME_SUFFIX.sub("", _DATE_LABEL.sub("", text.strip(), count=1)).strip(" :,") or None


def tidy_amount(text: str | None) -> str | None:
    """Keep the amount if the model copied its label too, as in "Total: $12.00"."""
    if text is None or parse_amount(text) is not None:
        return text
    tokens = _MONEY_TOKEN.findall(text)
    for token in reversed(tokens):
        if parse_amount(token) is not None:
            return token.strip()
    return text


def _is_blank(row: ItemReading) -> bool:
    return not any((row.description, row.quantity, row.unit_price, row.amount))


def merge_quantity_lines(rows: list[ItemReading]) -> list[ItemReading]:
    """Fold a "2 x 3.50" pseudo-item into the item before it, and drop empty rows."""
    merged: list[ItemReading] = []
    for row in rows:
        if _is_blank(row):
            continue
        match = _QTY_PRICE.match(row.description or "")
        if match and merged and row.amount in (None, ""):
            previous = merged[-1]
            if previous.quantity is None and previous.unit_price is None:
                merged[-1] = previous.model_copy(update={"quantity": match[1], "unit_price": match[2]})
                continue
        merged.append(row)
    return merged


def tidy_row(row: ItemReading) -> ItemReading:
    quantity = row.quantity
    if quantity is not None and parse_quantity(quantity) is None:
        token = re.search(r"\d+(?:[.,]\d+)?", quantity)
        quantity = token[0] if token else quantity
    return row.model_copy(update={
        "quantity": quantity,
        "unit_price": tidy_amount(row.unit_price),
        "amount": tidy_amount(row.amount),
    })


_TRAILING_COUNT = re.compile(r"^(.*\S)\s+[xX\u00d7]\s*(\d+(?:[.,]\d+)?)\s*$")
_LEADING_TIMES = re.compile(r"^(\d+(?:[.,]\d+)?)\s*[xX\u00d7]\s+(\D.*)$")
_LEADING_NUMBER = re.compile(r"^(\d+(?:[.,]\d+)?)\s+([A-Za-z].*)$")
_LINE_TOLERANCE = Decimal("0.011")


def _numbers_on_line(line: Line, description: str | None) -> list[tuple[str, Decimal]]:
    """The numbers printed on a row, in reading order, leaving out the description's own words."""
    _, description_words = _longest_prefix_run(line, squash(description))
    numbers = []
    for word in line.words:
        if word in description_words:
            continue
        text = word.text.strip(" ,:;()")
        parsed = parse_amount(text)
        if parsed is not None:
            numbers.append((text, parsed.value))
    return numbers


def _product_matches(quantity, price, amount) -> bool:
    return abs(quantity * price - amount) <= _LINE_TOLERANCE


def complete_row(document, row: ItemReading) -> ItemReading:
    """Recover what a small model left out of a row, from the line it cited.

    Models drop a column or glue the quantity onto the description. The page still has it, and
    "4 x 9.85 = 39.40" on the row is strong evidence. Anything taken from here is still checked
    by the same source and arithmetic signals as a value the model copied itself.
    """
    update: dict = {}
    description = row.description or ""
    quantity, price, amount = row.quantity, row.unit_price, row.amount

    trailing = _TRAILING_COUNT.match(description)
    if trailing and quantity is None:
        description, quantity = trailing[1], trailing[2]
    glued = _LEADING_TIMES.match(description)
    if glued and quantity is None:
        quantity, description = glued[1], glued[2]

    line = document.line(row.line)
    if line is not None:
        bare = _LEADING_NUMBER.match(description)
        if bare and quantity is None:
            numbers = _numbers_on_line(line, bare[2])
            if len(numbers) >= 3 and _product_matches(numbers[-3][1], numbers[-2][1], numbers[-1][1]):
                description = bare[2]

        numbers = _numbers_on_line(line, description)
        if quantity is None and price is None and len(numbers) >= 3 and _product_matches(numbers[-3][1], numbers[-2][1], numbers[-1][1]):
            quantity, price = numbers[-3][0], numbers[-2][0]
            amount = amount or numbers[-1][0]
        elif quantity is None and price is None and len(numbers) == 2 and numbers[0][1] == numbers[0][1].to_integral_value():
            quantity = numbers[0][0]
            amount = amount or numbers[1][0]
        below = document.line(row.line + 1)
        if below is not None and below.page == line.page:
            times = _QTY_PRICE.match(below.text)
            if times and (quantity is None or price is None or price == row.amount):
                quantity, price = times[1], times[2]
        parsed_price, parsed_amount = parse_amount(price), parse_amount(amount)
        if quantity is None and parsed_price and parsed_amount and parsed_price.value == parsed_amount.value:
            # With no quantity printed, a "unit price" equal to the amount is the amount read twice.
            price = None
        if amount is None and numbers:
            parsed_quantity, parsed_price = parse_quantity(quantity), parse_amount(price)
            last_text, last_value = numbers[-1]
            if parsed_quantity is not None and parsed_price is not None:
                if _product_matches(parsed_quantity, parsed_price.value, last_value):
                    amount = last_text
            elif len(numbers) == 1:
                amount = last_text

    if description != (row.description or ""):
        update["description"] = description
    if quantity != row.quantity:
        update["quantity"] = quantity
    if price != row.unit_price:
        update["unit_price"] = price
    if amount != row.amount:
        update["amount"] = amount
    return row.model_copy(update=update) if update else row


def complete_rows(document, reading: ModelReading) -> ModelReading:
    return reading.model_copy(update={"line_items": [complete_row(document, row) for row in reading.line_items]})


_LEGAL_ENDING = re.compile(r"\b(?:llc|inc|ltd|co|corp|gmbh|s\.?a\.?r\.?l|b\.?v|plc|llp|limited|company)\.?$", re.IGNORECASE)
_NOT_A_NAME = re.compile(r"\d|invoice|receipt|statement|\bbill\b|\bfrom\b|\bto\b|customer|page|:$|\bdate\b|\bterms\b", re.IGNORECASE)
#: What follows "Customer" when it isn't a label for a name: "Customer ID", "Customer PO", "Customer Copy".
_LABEL_CONTINUES = re.compile(r"^(?:id|no|number|num|#|po|p\.o\.?|ref|reference|copy|account|acct|code|type|service)\b", re.IGNORECASE)


def _same_column_words(line: Line, x0: float) -> list:
    """Words on a row from the one starting at `x0` onward, up to the next wide gap."""
    words = [word for word in line.words if word.x0 >= x0 - 3]
    if not words or abs(words[0].x0 - x0) > 3:
        return []
    run = [words[0]]
    for word in words[1:]:
        if word.x0 - run[-1].x1 > max(run[-1].height * 1.5, 8):
            break
        run.append(word)
    return run


def complete_vendor(document, cited: Cited) -> Cited:
    """A long vendor name wraps, and a small model tends to copy one row of it.

    Only joins a row that starts at the same left edge, sits right beside the cited one and reads
    like part of a name. The wrapped name must also end in a company suffix, or nothing changes.
    """
    line = document.line(cited.line)
    if line is None or not cited.text:
        return cited
    target = squash(cited.text)
    _, run = _longest_prefix_run(line, target)
    if not run:
        return cited
    x0 = run[0].x0

    def beside(other: Line | None) -> list:
        if other is None or other.page != line.page or not other.words:
            return []
        words = _same_column_words(other, x0)
        if not words or abs(words[0].top - run[0].top) > run[0].height * 2.2:
            return []
        text = " ".join(word.text for word in words)
        return [] if _NOT_A_NAME.search(text) else words

    if not _LEGAL_ENDING.search(cited.text.strip()):
        below = beside(document.line(cited.line + 1))
        if below and _LEGAL_ENDING.search(" ".join(word.text for word in below)):
            return cited.model_copy(update={"text": f"{cited.text.strip()} {' '.join(word.text for word in below)}"})
        return cited
    above = beside(document.line(cited.line - 1))
    if above:
        return cited.model_copy(update={"text": f"{' '.join(word.text for word in above)} {cited.text.strip()}", "line": cited.line - 1})
    return cited


_TAX_LABEL = re.compile(r"\b(?:tax|vat|gst|hst|pst|mwst|tva|btw|iva)\b", re.IGNORECASE)
_SUBTOTAL_LABEL = re.compile(r"sub-?\s?total|before tax|\bnet\b", re.IGNORECASE)
_BILL_TO_LABEL = re.compile(r"\b(?:bill(?:ed)?\s+to|invoice\s+to|sold\s+to|customer|client)\b:?\s*$", re.IGNORECASE)
_AMOUNT_TOLERANCE = Decimal("0.02")


def _amount_on_labeled_line(document, label: re.Pattern, target: Decimal, *, skip_lines: set[int]) -> Cited | None:
    """Find `target` printed on a line that carries this label. Returns it as if the model had read it."""
    for line in document.lines:
        if line.number in skip_lines or not label.search(line.text):
            continue
        for word in reversed(line.words):
            text = word.text.strip(" ,:;()")
            parsed = parse_amount(text)
            if parsed is not None and abs(parsed.value - target) <= _AMOUNT_TOLERANCE:
                return Cited(text=text, line=line.number)
    return None


def complete_totals(document, reading: ModelReading) -> ModelReading:
    """Recover a subtotal or tax the model dropped or misread, if the document's own math confirms it.

    The equation does the checking. A value only gets filled in if it's printed under a matching label
    and makes the sum come out exactly, so this can't invent a passing result from thin air.
    """
    update: dict = {}
    item_lines = {row.line for row in reading.line_items if row.line}
    amounts = [parse_amount(row.amount) for row in reading.line_items]
    subtotal = parse_amount(reading.subtotal.text)
    if subtotal is None and reading.line_items and all(amounts):
        column = sum((amount.value for amount in amounts), Decimal("0"))
        found = _amount_on_labeled_line(document, _SUBTOTAL_LABEL, column, skip_lines=item_lines)
        if found:
            update["subtotal"] = found
            subtotal = parse_amount(found.text)
    total = parse_amount(reading.total.text)
    if subtotal is not None and total is not None:
        discount = parse_amount(reading.discount.text)
        shipping = parse_amount(reading.shipping.text)
        tax_values = [parse_amount(entry.text) for entry in reading.tax if entry.text is not None]
        tax_sum = sum((value.value for value in tax_values if value), Decimal("0"))
        before_tax = subtotal.value - (discount.value if discount else 0) + (shipping.value if shipping else 0)
        needed = total.value - before_tax
        if needed > 0 and abs(needed - tax_sum) > _AMOUNT_TOLERANCE:
            found = _amount_on_labeled_line(document, _TAX_LABEL, needed, skip_lines=item_lines)
            if found:
                update["tax"] = [found]
    return reading.model_copy(update=update) if update else reading


def complete_bill_to(document, reading: ModelReading) -> ModelReading:
    """A bill-to label with a name right under it beats whatever the model picked from elsewhere.

    Multi-column layouts run other columns' text into the same rows, and small models lose track of
    which name belongs to which label. This only applies when a label stands alone in its column.
    """
    if reading.document_type != "invoice":
        return reading
    for line in document.lines:
        for index, word in enumerate(line.words):
            start = word.x0
            if _BILL_TO_LABEL.search(word.text):
                last = index
            elif index + 1 < len(line.words) and _BILL_TO_LABEL.search(f"{word.text} {line.words[index + 1].text}"):
                last = index + 1
            else:
                continue
            following = line.words[last + 1] if last + 1 < len(line.words) else None
            if following is not None and _LABEL_CONTINUES.match(following.text):
                continue
            if following is not None and following.x0 - line.words[last].x1 <= max(line.words[last].height * 5, 60):
                # "Customer: Mock Harbor Rowing Club" on one row, however it's spaced.
                name_words = _same_column_words(line, following.x0)
                name_line = line
            else:
                below = document.line(line.number + 1)
                if below is None or below.page != line.page:
                    continue
                name_words = _same_column_words(below, start)
                name_line = below
            name = " ".join(w.text for w in name_words)
            if not name_words or _NOT_A_NAME.search(name):
                continue
            if squash(name) == squash(reading.bill_to_name.text):
                return reading
            return reading.model_copy(update={"bill_to_name": Cited(text=name, line=name_line.number)})
    return reading


def complete_names(document, reading: ModelReading) -> ModelReading:
    update = {"vendor_name": complete_vendor(document, reading.vendor_name)}
    # On a receipt, "Order 1234" is the receipt's own number, not a purchase order.
    if reading.document_type == "receipt" and reading.document_number.text is None and reading.purchase_order.text is not None:
        update["document_number"] = reading.purchase_order
        update["purchase_order"] = Cited(text=None, line=None)
    return reading.model_copy(update=update)


def tidy_reading(reading: ModelReading) -> ModelReading:
    """Return a cleaned copy. The saved raw answer stays untouched."""

    def cited(value: Cited, function) -> Cited:
        return value.model_copy(update={"text": function(value.text)})

    return reading.model_copy(update={
        "document_number": cited(reading.document_number, tidy_identifier),
        "purchase_order": cited(reading.purchase_order, tidy_identifier),
        "issue_date": cited(reading.issue_date, tidy_date),
        "due_date": cited(reading.due_date, tidy_date),
        "subtotal": cited(reading.subtotal, tidy_amount),
        "discount": cited(reading.discount, tidy_amount),
        "shipping": cited(reading.shipping, tidy_amount),
        "total": cited(reading.total, tidy_amount),
        "tax": [cited(entry, tidy_amount) for entry in reading.tax],
        "line_items": [tidy_row(row) for row in merge_quantity_lines(reading.line_items)],
    })
