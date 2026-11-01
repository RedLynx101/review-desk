"""Earned confidence: a score built from checks anyone can read and rerun.

A model's opinion of itself isn't used anywhere in here. Each field collects signals: is the value
printed where the model said, how sure was OCR about those words, does a matching label sit beside it,
is the format valid, do the numbers add up. A signal has an effect in log-odds, and the score is the
logistic of the base plus the effects, so each score can be explained line by line in the review screen.

The weights in `DEFAULT_WEIGHTS` are starting values chosen by hand. `review_desk.evaluation fit` can
refit them against scored documents with known answers.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from .locate import Match, boxes, choose, find_amount, find_date, find_text, label_context
from .normalize import (
    ParsedAmount,
    ParsedDate,
    format_amount,
    normalize_text,
    parse_amount,
    parse_date,
    parse_quantity,
    resolve_currency,
    squash,
)
from .pagetext import DocumentText
from .schema import Cited, DocumentCheck, FieldResult, ItemReading, LineItemResult, ModelReading, Signal

DEFAULT_THRESHOLD = 0.90
TOLERANCE = Decimal("0.02")
#: Below this mean OCR confidence, a page's silence about a field means little.
POOR_READING = 0.80
CALIBRATION_FILE = Path(__file__).with_name("calibration.json")

#: (signal name, outcome) to log-odds effect. "base" is the starting log-odds for a field class.
DEFAULT_WEIGHTS: dict[str, float] = {
    "base.amount": 0.5, "base.date": 2.0, "base.identifier": 1.8, "base.name": 1.8,
    "base.currency": 2.5, "base.document_type": 3.0, "base.item_text": 2.0, "base.item_number": 0.5,
    "source.pass": 0.8, "source.weak": -1.2, "source.fail": -5.0,
    "reading.pass": 0.5, "reading.weak": -1.2, "reading.fail": -3.0,
    "reading.weak@date": -3.0, "reading.weak@identifier": -3.0, "reading.weak@name": -2.5, "reading.weak@item_text": -2.0,
    "reading.fail@date": -5.0, "reading.fail@identifier": -5.0, "reading.fail@name": -4.0, "reading.fail@item_text": -3.0,
    "label.pass": 0.8, "label.weak": -0.4, "label.fail": -3.0,
    "format.pass": 0.2, "format.weak": -1.5, "format.fail": -5.0,
    "arithmetic.pass": 2.5, "arithmetic.weak": -1.5, "arithmetic.fail": -3.0,
    "column.pass": 2.0, "column.fail": -6.0,
    "absence.pass": 1.0, "absence.weak": -0.5, "absence.fail": -3.0,
    "absence.pass@item_number": 2.5,
    "position.pass": 0.5, "position.weak": -0.5, "position.fail": -2.0,
    "date_order.pass": 0.5, "date_order.weak": -1.5, "date_order.fail": -3.0,
    "evidence.pass": 0.5, "evidence.weak": -1.0, "evidence.fail": -3.0,
}

def load_calibration(path: Path | None = None) -> tuple[dict[str, float], float]:
    """Weights and threshold in force: the fitted file if there is one, else the hand-set defaults."""
    target = path or CALIBRATION_FILE
    if not target.is_file():
        return DEFAULT_WEIGHTS, DEFAULT_THRESHOLD
    payload = json.loads(target.read_text(encoding="utf-8"))
    return {**DEFAULT_WEIGHTS, **payload["weights"]}, float(payload["threshold"])


FIELD_CLASS = {
    "document_type": "document_type", "vendor_name": "name", "bill_to_name": "name",
    "document_number": "identifier", "purchase_order": "identifier", "issue_date": "date", "due_date": "date",
    "currency": "currency", "subtotal": "amount", "discount": "amount", "shipping": "amount", "tax": "amount", "total": "amount",
    "description": "item_text", "quantity": "item_number", "unit_price": "item_number", "amount": "item_number",
}

_MONEY_LIKE = re.compile(r"\d[.,]\d{2}(?:\s|$|[^\d])")

#: Label vocabulary per field: words that support a reading and words that argue against it.
_LABELS: dict[str, tuple[re.Pattern, re.Pattern | None]] = {
    "total": (
        re.compile(r"\btotal\b|\bamount (?:due|paid)\b|\bgrand\b|\bsum\b|\bbalance due\b|\bto pay\b"),
        re.compile(r"sub-?\s?total|before tax|\bnet\b|outstanding|remaining|deposit|paid to date|less payment|\bchange\b|\bcash\b|tender"),
    ),
    "subtotal": (
        re.compile(r"sub-?\s?total|before tax|\bnet\b|\bgoods\b|\bmerchandise\b"),
        re.compile(r"grand|\btax\b|\bvat\b|amount due"),
    ),
    "tax": (re.compile(r"\btax\b|\bvat\b|\bgst\b|\bhst\b|\bpst\b|mwst|\btva\b|\bbtw\b|\biva\b"), None),
    "discount": (re.compile(r"discount|coupon|promo|rebate|\bless\b"), None),
    "shipping": (re.compile(r"ship|freight|delivery|carriage|postage|handling"), None),
    "issue_date": (
        re.compile(r"\bdate\b|\bdated\b|\bissued?\b|\bsold\b|\bpaid\b|\bon\b"),
        re.compile(r"\bdue\b|pay by|payable|order date|ship"),
    ),
    "due_date": (re.compile(r"\bdue\b|pay by|payable|\bby\b"), re.compile(r"order date|issued|invoice date|ship")),
    "document_number": (
        re.compile(r"invoice|\binv\b|\bbill\b|receipt|\bref|trans|ticket|order|\bno\b|#|number"),
        re.compile(r"customer|account|acct|\bp\.?\s?o\b|purchase|your ref|tax id|\bein\b|\bvat\b"),
    ),
    "purchase_order": (
        re.compile(r"\bp\.?\s?o\b|purchase|your ref|your order|customer po"),
        re.compile(r"invoice|customer id|account|acct|^order\b"),
    ),
    "bill_to_name": (
        re.compile(r"bill|invoice to|sold to|customer|client|\bto:"),
        re.compile(r"ship|deliver|job site|remit|pay to"),
    ),
    "vendor_name": (
        re.compile(r"receipt from|^from\b|\bfrom:|^$"),
        re.compile(r"bill|invoice to|sold to|customer|client|ship|deliver|job site|remit|pay to|\bto:"),
    ),
}

#: If one of these appears in the document, a null answer for the field is suspicious.
_ABSENCE_HINTS: dict[str, re.Pattern] = {
    "purchase_order": re.compile(r"\bp\.?\s?o\.?\s*(?:#|no|num|:)|purchase order|your ref|your order|customer po"),
    "due_date": re.compile(r"due date|payment due|pay by|payable by|\bdue\s*:"),
    "bill_to_name": re.compile(r"bill(?:ed)? to|invoice to|sold to|\bcustomer\s*:|\bclient\s*:|\bto:"),
    "document_number": re.compile(r"invoice\s*(?:no|#|num)|inv\.?\s*no|bill no|reference|receipt\s*(?:#|no)|\btrans\b|\bticket\b|\border\b"),
}
_AMOUNT_ABSENCE_HINTS: dict[str, re.Pattern] = {
    "discount": re.compile(r"discount|coupon|promo|rebate"),
    "shipping": re.compile(r"\bshipping\b|\bfreight\b|\bdelivery\b|\bpostage\b"),
    "tax": re.compile(r"\btax\b|\bvat\b|\bgst\b|\bhst\b|\bpst\b|mwst|\btva\b|\bbtw\b|\biva\b"),
    "subtotal": re.compile(r"sub-?\s?total|before tax|\bnet\b"),
}
_LEGAL_SUFFIX = re.compile(r"\b(?:llc|inc|ltd|co|corp|gmbh|s\.?a\.?r\.?l|b\.?v|plc|lp|llp|limited|company)\b\.?", re.IGNORECASE)


def _signal(name: str, outcome: str, detail: str) -> Signal:
    return Signal(name=name, outcome=outcome, detail=detail)


def score_signals(field_class: str, signals: list[Signal], weights: dict[str, float] = DEFAULT_WEIGHTS) -> tuple[float, list[Signal]]:
    """Logistic of the base plus each signal's effect. Returns the score and signals with effects."""
    total = weights.get(f"base.{field_class}", 2.0)
    updated = []
    for signal in signals:
        key = f"{signal.name}.{signal.outcome}"
        effect = 0.0 if signal.outcome == "not_applicable" else weights.get(f"{key}@{field_class}", weights.get(key, 0.0))
        total += effect
        updated.append(signal.model_copy(update={"effect": round(effect, 3)}))
    return round(1.0 / (1.0 + math.exp(-total)), 4), updated


def _pick(document: DocumentText, name: str, matches: list[Match], cited: int | None) -> tuple[Match | None, bool]:
    """The cited match if the citation holds. Otherwise prefer one whose label fits this field.

    Subtotal and total are often the same number. Without this, a bad citation would point the
    highlight for the total at the subtotal's line.
    """
    match, held = choose(matches, cited)
    if held or match is None or name not in _LABELS:
        return match, held
    support, against = _LABELS[name]
    for candidate in matches:
        left, above = label_context(document, candidate)
        if against is not None and (against.search(left) or (not left and against.search(above))):
            continue
        if support.search(left) or (not left and support.search(above)):
            return candidate, False
    return match, False


def _source_signal(match: Match | None, cited_held: bool, cited: int | None) -> Signal:
    if match is None:
        return _signal("source", "fail", "The value wasn't found anywhere on the page.")
    if not match.exact:
        return _signal("source", "weak", f"Near match only ({match.similarity:.0%}) on line {match.line.number}: \"{match.text}\".")
    if cited_held:
        return _signal("source", "pass", f"Printed on line {match.line.number}, the line the model cited.")
    return _signal("source", "weak", f"Printed on line {match.line.number}, but the model cited line {cited}.")


def _reading_signal(match: Match | None, document: DocumentText, *, strict: bool = False) -> Signal:
    if match is None:
        return _signal("reading", "not_applicable", "No matched words to rate.")
    page = document.pages[match.line.page]
    if page.source == "text_layer":
        if not page.visible_words:
            return _signal("reading", "pass", "Taken from the PDF's text layer. OCR wasn't available to compare it with the page.")
        return _visible_check(match, page, strict=strict)
    confidence = match.min_confidence
    if confidence >= 0.90:
        return _signal("reading", "pass", f"OCR confidence {confidence:.0%} on these words.")
    if confidence >= 0.70:
        return _signal("reading", "weak", f"OCR confidence {confidence:.0%} on these words.")
    return _signal("reading", "fail", f"OCR confidence {confidence:.0%} on these words.")


def _reading_order(words: list) -> list:
    """Sort words into rows, then left to right. OCR boxes on one row differ by a point or two."""
    rows: list[list] = []
    for word in sorted(words, key=lambda w: w.center_y):
        if rows and abs(word.center_y - rows[-1][-1].center_y) <= 0.6 * max(word.height, rows[-1][-1].height):
            rows[-1].append(word)
        else:
            rows.append([word])
    return [word for row in rows for word in sorted(row, key=lambda w: w.x0)]


#: Characters OCR commonly swaps, mapped to one form before comparing.
_LOOKALIKES = str.maketrans({"o": "0", "q": "0", "i": "1", "l": "1", "s": "5", "b": "8", "z": "2"})


def _visible_check(match: Match, page, *, strict: bool = False) -> Signal:
    """Does the page image show what the text layer claims, at that spot?

    `strict` is for identifiers, where one changed character is a different document.
    """
    claimed = squash(match.text)
    x0 = min(word.x0 for word in match.words) - 4
    x1 = max(word.x1 for word in match.words) + 4
    top = min(word.top for word in match.words) - 4
    bottom = max(word.bottom for word in match.words) + 4
    nearby = [
        word for word in page.visible_words
        if x0 <= (word.x0 + word.x1) / 2 <= x1 and top <= (word.top + word.bottom) / 2 <= bottom
    ]
    seen = squash(" ".join(word.text for word in _reading_order(nearby)))
    if claimed and claimed in seen:
        return _signal("reading", "pass", "The text layer matches what the page shows here.")
    if not seen:
        return _signal("reading", "weak", "OCR couldn't confirm this text on the page image.")
    # OCR swaps O for 0 and l for 1 all the time, so those don't count as a difference.
    claimed_plain = claimed.translate(_LOOKALIKES)
    seen_plain = seen.translate(_LOOKALIKES)
    if claimed_plain in seen_plain:
        return _signal("reading", "pass", "The text layer matches what the page shows here, allowing for look-alike characters.")
    if claimed.isdigit():
        # 900.00 and 500.00 are 80% alike. For a number, near enough isn't enough, unless OCR
        # read no digits at all here, which says more about OCR than about the document.
        if not any(ch.isdigit() for ch in seen_plain):
            return _signal("reading", "weak", f"OCR couldn't read this number on the page image. It read \"{' '.join(w.text for w in nearby)[:40]}\".")
    else:
        similarity = SequenceMatcher(None, claimed_plain, seen_plain, autojunk=False).ratio()
        if similarity >= (0.9 if strict else 0.8):
            return _signal("reading", "pass", f"The text layer closely matches what the page shows here ({similarity:.0%}).")
    return _signal("reading", "fail", f"The text layer says \"{match.text}\", but the page shows \"{' '.join(w.text for w in nearby)[:60]}\".")


def _label_signal(document: DocumentText, name: str, match: Match | None) -> Signal:
    if match is None or name not in _LABELS:
        return _signal("label", "not_applicable", "No label check for this field.")
    support, against = _LABELS[name]
    left, above = label_context(document, match)
    if against is not None and against.search(left):
        return _signal("label", "fail", f"The nearby label \"{left}\" points to something else.")
    if support.search(left):
        detail = f"Labeled \"{left}\"." if left else "Stands alone, with no label pointing to another role."
        return _signal("label", "pass", detail)
    if against is not None and against.search(above):
        return _signal("label", "fail", f"The label above, \"{above}\", points to something else.")
    if support.search(above):
        return _signal("label", "pass", f"Labeled \"{above}\" just above.")
    return _signal("label", "weak", "No matching label beside this value.")


def _reading_quality(document: DocumentText) -> float | None:
    """Mean OCR confidence across scanned pages. None if nothing was scanned."""
    words = [word for page in document.pages if page.source == "ocr" for word in page.words]
    if not words:
        return None
    return sum(word.confidence for word in words) / len(words)


def _name_format(value: str | None) -> Signal:
    if value is None:
        return _signal("format", "not_applicable", "No name.")
    letters = sum(ch.isalpha() for ch in value)
    if letters < 4 or value.rstrip().endswith(":") or re.fullmatch(r"(?i)[\w.]*\s*(?:id|no|po|ref|terms|date)\.?:?", value.strip()):
        return _signal("format", "fail", f"\"{value}\" doesn't look like a name.")
    return _signal("format", "pass", "Looks like a name.")


def _absence_signal(document: DocumentText, name: str, used_lines: set[int]) -> Signal:
    """Null answers can be checked too: is there a label on the page that suggests a value exists?"""
    signal = _label_absence(document, name, used_lines)
    quality = _reading_quality(document)
    if signal.outcome == "pass" and quality is not None and quality < POOR_READING:
        return _signal("absence", "weak", f"No label for this field was read, but this scan is hard to read (OCR confidence {quality:.0%}), so that proves little.")
    return signal


def _label_absence(document: DocumentText, name: str, used_lines: set[int]) -> Signal:
    if name in _ABSENCE_HINTS:
        for line in document.lines:
            if _ABSENCE_HINTS[name].search(line.text.lower()):
                return _signal("absence", "fail", f"Line {line.number} looks like this field: \"{line.text[:60]}\".")
        return _signal("absence", "pass", "No label for this field on the page.")
    if name in _AMOUNT_ABSENCE_HINTS:
        for line in document.lines:
            if line.number in used_lines:
                continue
            lowered = line.text.lower()
            if _AMOUNT_ABSENCE_HINTS[name].search(lowered) and _MONEY_LIKE.search(line.text):
                return _signal("absence", "fail", f"Line {line.number} looks like this field: \"{line.text[:60]}\".")
        return _signal("absence", "pass", "No amount labeled like this on the page.")
    return _signal("absence", "fail", "This field should always be present.")


@dataclass
class AmountReading:
    name: str
    printed: str | None
    parsed: ParsedAmount | None
    match: Match | None
    cited_held: bool
    cited: int | None


def _read_amount(document: DocumentText, name: str, cited: Cited) -> AmountReading:
    parsed = parse_amount(cited.text)
    if parsed is None:
        return AmountReading(name, cited.text, None, None, False, cited.line)
    match, held = _pick(document, name, find_amount(document, parsed.value), cited.line)
    return AmountReading(name, cited.text, parsed, match, held, cited.line)


@dataclass
class TotalsCheck:
    items_sum: DocumentCheck
    totals_sum: DocumentCheck


def _sum_items(items: list[Decimal]) -> Decimal:
    return sum(items, Decimal("0"))


def check_totals(
    item_amounts: list[Decimal | None],
    subtotal: Decimal | None,
    discount: Decimal | None,
    shipping: Decimal | None,
    tax: Decimal | None,
    total: Decimal | None,
) -> TotalsCheck:
    """The two equations an invoice has to satisfy."""
    known = [amount for amount in item_amounts if amount is not None]
    if not item_amounts:
        items = DocumentCheck(name="items_sum", outcome="fail", detail="No line items were read.")
    elif len(known) != len(item_amounts):
        items = DocumentCheck(name="items_sum", outcome="not_applicable", detail="Some line amounts are missing, so the column can't be summed.")
    else:
        line_sum = _sum_items(known)
        target = subtotal if subtotal is not None else None
        if target is None:
            # No printed subtotal: the column should reach the total after the other charges.
            target = None if total is None else total - (tax or 0) - (shipping or 0) + (discount or 0)
        if target is None:
            items = DocumentCheck(name="items_sum", outcome="not_applicable", detail="There's no subtotal or total to compare with.")
        elif abs(line_sum - target) <= TOLERANCE:
            items = DocumentCheck(name="items_sum", outcome="pass", detail=f"Line items sum to {line_sum}, matching {target}.")
        else:
            items = DocumentCheck(name="items_sum", outcome="fail", detail=f"Line items sum to {line_sum}, but the document says {target}.")

    if subtotal is None or total is None:
        totals = DocumentCheck(name="totals_sum", outcome="not_applicable", detail="Subtotal or total is missing.")
    else:
        expected = subtotal - (discount or 0) + (shipping or 0) + (tax or 0)
        if abs(expected - total) <= TOLERANCE:
            totals = DocumentCheck(name="totals_sum", outcome="pass", detail=f"Subtotal, adjustments and tax add up to the total, {total}.")
        else:
            totals = DocumentCheck(name="totals_sum", outcome="fail", detail=f"Subtotal, adjustments and tax come to {expected}, but the total says {total}.")
    return TotalsCheck(items, totals)


def _arith_from(check: DocumentCheck, name: str) -> Signal:
    if check.outcome == "not_applicable":
        return _signal(name, "not_applicable", check.detail)
    return _signal(name, check.outcome, check.detail)


def _amount_field(document, reading: AmountReading, arithmetic: list[Signal], used_lines, weights, threshold) -> FieldResult:
    name = reading.name
    if reading.printed is None:
        signals = [_absence_signal(document, name, used_lines)] + arithmetic
        return _result(name, None, None, "amount", signals, [], weights, threshold)
    if reading.parsed is None:
        signals = [_signal("format", "fail", f"\"{reading.printed}\" isn't an amount.")]
        return _result(name, None, reading.printed, "amount", signals, [], weights, threshold)
    format_signal = (
        _signal("format", "weak", f"\"{reading.printed}\" could be read two ways.") if reading.parsed.ambiguous
        else _signal("format", "pass", "Parsed as an amount.")
    )
    signals = [
        _source_signal(reading.match, reading.cited_held, reading.cited),
        _reading_signal(reading.match, document),
        _label_signal(document, name, reading.match),
        format_signal,
        *arithmetic,
    ]
    sources = boxes(reading.match) if reading.match else []
    return _result(name, format_amount(reading.parsed.value), reading.printed, "amount", signals, sources, weights, threshold)


def _result(name, value, printed, field_class, signals, sources, weights, threshold) -> FieldResult:
    score, updated = score_signals(field_class, signals, weights)
    return FieldResult(
        name=name, value=value, printed=printed, confidence=score, needs_review=score < threshold,
        signals=updated, sources=sources,
    )


def _text_field(document, name, cited: Cited, extra, field_class, weights, threshold, *, used_lines) -> FieldResult:
    value = normalize_text(cited.text)
    if value is None:
        return _result(name, None, None, field_class, [_absence_signal(document, name, used_lines)] + extra, [], weights, threshold)
    match, held = _pick(document, name, find_text(document, value), cited.line)
    signals = [
        _source_signal(match, held, cited.line),
        _reading_signal(match, document, strict=field_class == "identifier"),
        _label_signal(document, name, match),
        *extra,
    ]
    return _result(name, value, cited.text, field_class, signals, boxes(match) if match else [], weights, threshold)


def _date_context(parsed_dates: list[ParsedDate | None], currency_code: str | None) -> tuple[str | None, str]:
    """Which way round are this document's numeric dates?

    A date with a part above 12 settles it. Otherwise currency is a hint and only a hint.
    """
    styles = {parsed.style for parsed in parsed_dates if parsed and parsed.style in ("month_first", "day_first")}
    if len(styles) == 1:
        return styles.pop(), "another date on this document settles it"
    if currency_code in ("GBP", "EUR", "AUD", "NZD"):
        return "day_first", f"assumed from the currency, {currency_code}"
    if currency_code == "USD":
        return "month_first", "assumed from the currency, USD"
    return None, "nothing on this document settles it"


def _date_field(document, name, cited: Cited, order, order_reason, weights, threshold, *, extra=None) -> tuple[FieldResult, date | None]:
    extra = extra or []
    if cited.text is None:
        signals = [_absence_signal(document, name, set())] + extra
        return _result(name, None, None, "date", signals, [], weights, threshold), None
    parsed = parse_date(cited.text)
    if parsed is None:
        return _result(name, None, cited.text, "date", [_signal("format", "fail", f"\"{cited.text}\" isn't a date.")], [], weights, threshold), None
    value = parsed.value
    format_signal = _signal("format", "pass", "Parsed as a date.")
    if parsed.style == "ambiguous":
        if order == "day_first":
            value = parsed.alternative
        reason = f"Day and month could swap. Read as {value.isoformat()}: {order_reason}."
        settled = "another date" in order_reason
        format_signal = _signal("format", "pass" if settled else "weak", reason)
    match, held = _pick(document, name, find_date(document, value), cited.line)
    signals = [
        _source_signal(match, held, cited.line),
        _reading_signal(match, document),
        _label_signal(document, name, match),
        format_signal,
        *extra,
    ]
    return _result(name, value.isoformat(), cited.text, "date", signals, boxes(match) if match else [], weights, threshold), value


def _document_type_field(document, reading: ModelReading, weights, threshold) -> FieldResult:
    # The top of the first page is where a document says what it is. Fine print further down
    # ("this invoice is not a receipt") says nothing.
    first_page = document.pages[0]
    text = " ".join(
        line.text.lower() for line in document.lines
        if line.page == 0 and line.words[0].top < first_page.height * 0.6
    )
    says_invoice = bool(re.search(r"\binvoice\b", text))
    says_receipt = bool(re.search(r"\breceipt\b|\bcashier\b", text))
    claim = reading.document_type
    if (claim == "invoice" and says_invoice and not says_receipt) or (claim == "receipt" and says_receipt and not says_invoice):
        signal = _signal("evidence", "pass", f"The page says {claim}.")
    elif (claim == "invoice" and says_receipt and not says_invoice) or (claim == "receipt" and says_invoice and not says_receipt):
        signal = _signal("evidence", "fail", f"The page says the opposite of {claim}.")
    else:
        signal = _signal("evidence", "weak", "The page doesn't settle this by name.")
    match = None
    for candidate in find_text(document, claim, fuzzy_floor=1.1):
        # "Invoice To" names a party, not the document. A highlight there would mislead.
        if not re.search(rf"{claim}\s+to\b", candidate.line.text, re.IGNORECASE):
            match = candidate
            break
    return _result("document_type", claim, claim, "document_type", [signal], boxes(match) if match else [], weights, threshold)


def _currency_field(document, reading: ModelReading, total: AmountReading, weights, threshold) -> FieldResult:
    printed = reading.currency.text
    text = " ".join(line.text for line in document.lines)
    code, how = resolve_currency(printed)
    if code is None and total.parsed is not None and total.parsed.currency_hint:
        code, how = resolve_currency(total.parsed.currency_hint)
        printed = total.parsed.currency_hint
    if code is None:
        return _result("currency", None, printed, "currency", [_signal("evidence", "fail", "No currency could be established.")], [], weights, threshold)
    if how == "printed_code":
        signal = _signal("evidence", "pass", f"The code {code} is printed.") if code in text else _signal("evidence", "weak", f"The code {code} isn't printed on the page.")
    elif how == "unique_symbol":
        signal = _signal("evidence", "pass", f"The symbol {printed} means {code}.")
    else:
        other_codes = [c for c in ("CAD", "AUD", "NZD", "MXN") if re.search(rf"\b{c}\b|{c[0]}A?\$", text)]
        if other_codes:
            signal = _signal("evidence", "fail", f"A $ sign read as USD, but {other_codes[0]} also appears.")
        else:
            signal = _signal("evidence", "pass", "A $ sign, with no other dollar currency mentioned, read as USD.")
    match = None
    matches = find_text(document, code, fuzzy_floor=1.1) if how == "printed_code" else []
    if matches:
        match = matches[0]
    sources = boxes(match) if match else (boxes(total.match) if total.match else [])
    return _result("currency", code, printed, "currency", [signal], sources, weights, threshold)


def _vendor_position(document: DocumentText, match: Match | None, value: str | None) -> Signal:
    if match is None or value is None:
        return _signal("position", "not_applicable", "No matched position.")
    first_page = [line for line in document.lines if line.page == 0]
    in_header = match.line.page == 0 and match.line.number <= max(6, len(first_page) // 3)
    has_suffix = bool(_LEGAL_SUFFIX.search(value))
    if in_header and has_suffix:
        return _signal("position", "pass", "Near the top of the page, with a company suffix.")
    if in_header or has_suffix:
        return _signal("position", "weak", "Only partly looks like the issuer: check top-of-page position and company suffix.")
    return _signal("position", "fail", "Not near the top, and no company suffix.")


def _line_math(quantity: Decimal | None, unit_price: Decimal | None, amount: Decimal | None) -> Signal:
    if quantity is None or unit_price is None or amount is None:
        return _signal("arithmetic", "not_applicable", "Quantity or price isn't printed on this line.")
    expected = quantity * unit_price
    if abs(expected - amount) <= Decimal("0.011"):
        return _signal("arithmetic", "pass", f"{quantity} x {unit_price} = {amount}.")
    return _signal("arithmetic", "fail", f"{quantity} x {unit_price} = {expected}, but the line says {amount}.")


def _row_has_other_numbers(document, row: ItemReading, amount: Decimal | None) -> bool:
    """Does this row's line print a number the row doesn't account for, such as a quantity?"""
    line = document.line(row.line)
    if line is None:
        return False
    description = squash(row.description)
    for word in line.words:
        token = word.text.strip(" ,:;$")
        if not re.fullmatch(r"\d[\d.,]*", token) or squash(token) in description:
            continue
        parsed = parse_amount(token)
        if parsed is not None and parsed.value != amount:
            return True
    return False


def _cell_match(document, value: Decimal, row: ItemReading, taken: set, *, side: str) -> tuple[Match | None, bool]:
    """Find a number for one cell of a row, among words no other cell has claimed.

    On a row like "1  Rush fee  57.75  57.75" the price and the amount are the same number, and the
    digit in "Gasket, 4 in." isn't a quantity. Amounts read from the right, quantities from the left.
    A row can run over two lines when its "2 x 3.50" sits below it, so the next line still counts.
    """
    candidates = [match for match in find_amount(document, value) if not (set(match.words) & taken)]
    if not candidates:
        return None, False
    if row.line is None:
        return candidates[0], False
    candidates.sort(key=lambda match: abs(match.line.number - row.line))
    closest = abs(candidates[0].line.number - row.line)
    same_line = [match for match in candidates if abs(match.line.number - row.line) == closest]
    chosen = same_line[-1] if side == "right" else same_line[0]
    return chosen, closest <= 1


def _item_number_cell(document, name, text, parsed_value, row, amount, arithmetic, weights, threshold, *, taken: set, side: str) -> FieldResult:
    line_number = row.line
    if text is None:
        if name == "amount":
            return _result(name, None, None, "item_number", [_signal("absence", "fail", "This row has no amount.")], [], weights, threshold)
        if _row_has_other_numbers(document, row, amount):
            return _result(name, None, None, "item_number", [_signal("absence", "fail", f"Line {line_number} prints a number this row doesn't use.")], [], weights, threshold)
        line = document.line(line_number)
        if line is not None and document.pages[line.page].source == "text_layer":
            # A text layer lists everything printed on the row, so a blank really is a blank.
            return _result(name, None, None, "item_number", [_signal("absence", "pass", "Not printed on this line.")], [], weights, threshold)
        # OCR can drop a number that's right there, especially beside table rules.
        return _result(name, None, None, "item_number", [_signal("absence", "weak", "OCR found nothing here, but it can miss numbers on a table row.")], [], weights, threshold)
    if parsed_value is None:
        return _result(name, None, text, "item_number", [_signal("format", "fail", f"\"{text}\" isn't a number.")], [], weights, threshold)
    match, held = _cell_match(document, parsed_value, row, taken, side=side)
    if match is not None:
        taken.update(match.words)
    signals = [_source_signal(match, held, line_number), _reading_signal(match, document), _signal("format", "pass", "Parsed as a number."), *arithmetic]
    return _result(name, format_amount(parsed_value) if name != "quantity" else format(parsed_value.normalize(), "f"), text, "item_number", signals, boxes(match) if match else [], weights, threshold)


def assess(
    document: DocumentText,
    reading: ModelReading,
    *,
    weights: dict[str, float] = DEFAULT_WEIGHTS,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[dict[str, FieldResult], list[LineItemResult], list[DocumentCheck]]:
    """Check every value the model read against the page and score it."""
    subtotal = _read_amount(document, "subtotal", reading.subtotal)
    discount = _read_amount(document, "discount", reading.discount)
    shipping = _read_amount(document, "shipping", reading.shipping)
    total = _read_amount(document, "total", reading.total)
    tax_readings = [_read_amount(document, "tax", cited) for cited in reading.tax if cited.text is not None]
    tax_value = sum((r.parsed.value for r in tax_readings if r.parsed), Decimal("0")) if tax_readings else None
    if tax_readings and all(r.parsed is None for r in tax_readings):
        tax_value = None

    item_values = []
    for row in reading.line_items:
        parsed = parse_amount(row.amount)
        item_values.append(parsed.value if parsed else None)

    checks = check_totals(
        item_values,
        subtotal.parsed.value if subtotal.parsed else None,
        discount.parsed.value if discount.parsed else None,
        shipping.parsed.value if shipping.parsed else None,
        tax_value,
        total.parsed.value if total.parsed else None,
    )
    totals_signal = _arith_from(checks.totals_sum, "arithmetic")
    if totals_signal.outcome == "not_applicable":
        # No equation to check is itself a finding: nothing has verified these amounts.
        totals_signal = _signal("arithmetic", "weak", f"Couldn't verify: {checks.totals_sum.detail}")
    items_signal = _arith_from(checks.items_sum, "column")

    used_lines = {row.line for row in reading.line_items if row.line}
    fields: dict[str, FieldResult] = {}
    subtotal_evidence = [items_signal] if items_signal.outcome == "pass" and totals_signal.outcome == "fail" else [items_signal, totals_signal]
    fields["subtotal"] = _amount_field(document, subtotal, subtotal_evidence, used_lines, weights, threshold)
    total_evidence = [totals_signal] if subtotal.parsed else [totals_signal, items_signal]
    fields["total"] = _amount_field(document, total, total_evidence, used_lines, weights, threshold)
    fields["discount"] = _amount_field(document, discount, [totals_signal], used_lines, weights, threshold)
    fields["shipping"] = _amount_field(document, shipping, [totals_signal], used_lines, weights, threshold)
    fields["tax"] = _tax_field(document, tax_readings, tax_value, totals_signal, used_lines, weights, threshold)

    code_field = _currency_field(document, reading, total, weights, threshold)
    fields["currency"] = code_field
    fields["document_type"] = _document_type_field(document, reading, weights, threshold)

    # Dates need each other: one date with a day above 12 settles the order of the other.
    parsed_issue = parse_date(reading.issue_date.text)
    parsed_due = parse_date(reading.due_date.text)
    order, order_reason = _date_context([parsed_issue, parsed_due], code_field.value)
    fields["issue_date"], issue = _date_field(document, "issue_date", reading.issue_date, order, order_reason, weights, threshold)
    fields["due_date"], due = _date_field(document, "due_date", reading.due_date, order, order_reason, weights, threshold)
    if issue and due:
        relation = (
            _signal("date_order", "pass", "The due date falls on or after the issue date.") if due >= issue
            else _signal("date_order", "fail", "The due date falls before the issue date.")
        )
        for name in ("issue_date", "due_date"):
            existing = fields[name]
            score, updated = score_signals("date", existing.signals + [relation], weights)
            fields[name] = existing.model_copy(update={"signals": updated, "confidence": score, "needs_review": score < threshold})

    fields["document_number"] = _text_field(document, "document_number", reading.document_number, [_identifier_format(reading.document_number.text)], "identifier", weights, threshold, used_lines=used_lines)
    fields["purchase_order"] = _text_field(document, "purchase_order", reading.purchase_order, [_identifier_format(reading.purchase_order.text)], "identifier", weights, threshold, used_lines=used_lines)
    fields["bill_to_name"] = _text_field(document, "bill_to_name", reading.bill_to_name, [_name_format(reading.bill_to_name.text)], "name", weights, threshold, used_lines=used_lines)
    vendor_value = normalize_text(reading.vendor_name.text)
    vendor_matches = find_text(document, vendor_value) if vendor_value else []
    vendor_match, _ = choose(vendor_matches, reading.vendor_name.line)
    vendor_extra = [_vendor_position(document, vendor_match, vendor_value), _name_format(vendor_value)]
    if vendor_value and squash(vendor_value) == squash(reading.bill_to_name.text):
        vendor_extra.append(_signal("evidence", "fail", "The same name was read as both vendor and customer."))
    fields["vendor_name"] = _text_field(document, "vendor_name", reading.vendor_name, vendor_extra, "name", weights, threshold, used_lines=used_lines)

    items = _line_items(document, reading.line_items, checks.items_sum, weights, threshold)
    return fields, items, [checks.items_sum, checks.totals_sum]


def _identifier_format(text: str | None) -> Signal:
    if text is None:
        return _signal("format", "not_applicable", "No identifier.")
    cleaned = text.strip()
    if not re.search(r"\d", cleaned):
        return _signal("format", "weak", "No digits in this identifier.")
    if len(cleaned) < 3 or len(cleaned) > 40:
        return _signal("format", "weak", "Unusual length for an identifier.")
    return _signal("format", "pass", "Looks like an identifier.")


def _tax_field(document, readings: list[AmountReading], value, totals_signal, used_lines, weights, threshold) -> FieldResult:
    if not readings:
        return _result("tax", None, None, "amount", [_absence_signal(document, "tax", used_lines), totals_signal], [], weights, threshold)
    printed = " + ".join(r.printed or "" for r in readings)
    if value is None:
        return _result("tax", None, printed, "amount", [_signal("format", "fail", f"\"{printed}\" isn't an amount.")], [], weights, threshold)
    # The weakest line decides: a tax split in two is only as sure as its worse half.
    worst_source = next((s for s in (_source_signal(r.match, r.cited_held, r.cited) for r in readings) if s.outcome == "fail"), None)
    source = worst_source or next((s for s in (_source_signal(r.match, r.cited_held, r.cited) for r in readings) if s.outcome == "weak"), None) or _source_signal(readings[0].match, readings[0].cited_held, readings[0].cited)
    rated = [_reading_signal(r.match, document) for r in readings]
    reading_signal = next((s for s in rated if s.outcome == "fail"), None) or next((s for s in rated if s.outcome == "weak"), None) or rated[0]
    labels = [_label_signal(document, "tax", r.match) for r in readings]
    label_signal = next((s for s in labels if s.outcome == "fail"), None) or next((s for s in labels if s.outcome == "weak"), None) or labels[0]
    signals = [source, reading_signal, label_signal, _signal("format", "pass", f"{len(readings)} tax amount(s) parsed."), totals_signal]
    sources = [box for r in readings if r.match for box in boxes(r.match)]
    return _result("tax", format_amount(value), printed, "amount", signals, sources, weights, threshold)


def _line_items(document, rows: list[ItemReading], items_check: DocumentCheck, weights, threshold) -> list[LineItemResult]:
    results = []
    for index, row in enumerate(rows):
        quantity = parse_quantity(row.quantity)
        price_parsed = parse_amount(row.unit_price)
        price = price_parsed.value if price_parsed else None
        amount_parsed = parse_amount(row.amount)
        amount = amount_parsed.value if amount_parsed else None
        math_signal = _line_math(quantity, price, amount)
        sum_signal = _arith_from(items_check, "column")
        cells: dict[str, FieldResult] = {}
        taken: set = set()
        description = normalize_text(row.description)
        if description is None:
            cells["description"] = _result("description", None, None, "item_text", [_signal("source", "fail", "A line item with no description.")], [], weights, threshold)
        else:
            match, held = choose(find_text(document, description), row.line)
            if match is not None:
                taken.update(match.words)
            signals = [_source_signal(match, held, row.line), _reading_signal(match, document)]
            if sum_signal.outcome == "fail":
                signals.append(sum_signal)
            cells["description"] = _result("description", description, row.description, "item_text", signals, boxes(match) if match else [], weights, threshold)
        cells["amount"] = _item_number_cell(document, "amount", row.amount, amount, row, amount, [math_signal, sum_signal], weights, threshold, taken=taken, side="right")
        cells["unit_price"] = _item_number_cell(document, "unit_price", row.unit_price, price, row, amount, [math_signal], weights, threshold, taken=taken, side="right")
        cells["quantity"] = _item_number_cell(document, "quantity", row.quantity, quantity, row, amount, [math_signal], weights, threshold, taken=taken, side="left")
        results.append(LineItemResult(index=index, cells=cells))
    return results
