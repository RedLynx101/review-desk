"""Turn literal printed values into normalized ones, deterministically.

The model only copies text. Dates and amounts get parsed here, where the rules can be tested and where
ambiguity gets reported instead of guessed away. 05/03/2026 has two readings and this says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

_MONTHS = {
    name: index + 1
    for index, names in enumerate((
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
        ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
        ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ))
    for name in names
}
_SYMBOLS = ("CA$", "C$", "US$", "A$", "$", "£", "€", "¥", "₹")
_CODES = ("USD", "CAD", "GBP", "EUR", "AUD", "JPY", "INR", "CHF", "NZD", "MXN", "IDR", "RP")
SYMBOL_TO_CODE = {"CA$": "CAD", "C$": "CAD", "US$": "USD", "A$": "AUD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR"}
ISO_CODES = frozenset(_CODES) - {"RP"}


@dataclass(frozen=True)
class ParsedAmount:
    value: Decimal
    negative: bool = False
    ambiguous: bool = False  # "1.234" could be 1234 or 1.234. Reported, not resolved.
    currency_hint: str | None = None  # Symbol or code found inside the text.


@dataclass(frozen=True)
class ParsedDate:
    value: date
    alternative: date | None = None  # The other reading when day and month could swap.
    style: str = "unambiguous"  # "month_first", "day_first" or "unambiguous"


def parse_amount(text: str | None) -> ParsedAmount | None:
    """Parse a printed amount in US or European notation. Returns None if it isn't one."""
    if not text:
        return None
    body = text.strip()
    negative = False
    if body.startswith("(") and body.endswith(")"):
        negative, body = True, body[1:-1].strip()
    hint = None
    for symbol in _SYMBOLS:
        if symbol in body:
            hint = symbol
            body = body.replace(symbol, " ")
            break
    upper = body.upper()
    for code in _CODES:
        if re.search(rf"\b{code}\b", upper):
            hint = hint or code
            body = re.sub(rf"\b{code}\b", " ", body, flags=re.IGNORECASE)
            break
    body = re.sub(r"\s+", "", body)
    if body.startswith(("-", "−", "–")):
        negative, body = True, body[1:]
    elif body.endswith("-"):
        negative, body = True, body[:-1]
    if not body or not re.fullmatch(r"[\d.,' ]*\d[\d.,']*", body):
        return None
    body = body.replace("'", "")
    return _parse_digits(body, negative, hint)


def _parse_digits(body: str, negative: bool, hint: str | None) -> ParsedAmount | None:
    last_dot, last_comma = body.rfind("."), body.rfind(",")
    ambiguous = False
    if last_dot >= 0 and last_comma >= 0:
        decimal_mark = "." if last_dot > last_comma else ","
    elif last_dot >= 0 or last_comma >= 0:
        mark = "." if last_dot >= 0 else ","
        decimals = len(body) - body.rfind(mark) - 1
        if body.count(mark) > 1:
            decimal_mark = None  # 1,234,567 or 1.234.567 can only be grouping.
        elif decimals == 3:
            ambiguous = True  # 1,234 is likely grouping, though it could be three decimals.
            decimal_mark = None
        else:
            decimal_mark = mark
    else:
        decimal_mark = None

    if decimal_mark:
        grouping = "," if decimal_mark == "." else "."
        whole, fraction = body.rsplit(decimal_mark, 1)
        cleaned = f"{whole.replace(grouping, '')}.{fraction}"
    else:
        cleaned = body.replace(",", "").replace(".", "")
    try:
        return ParsedAmount(Decimal(cleaned), negative, ambiguous, hint)
    except InvalidOperation:
        return None


def format_amount(value: Decimal) -> str:
    """Canonical form used in results: two decimals when a value has at most two."""
    if value == value.quantize(Decimal("0.01")):
        return f"{value:.2f}"
    return format(value.normalize(), "f")


def parse_quantity(text: str | None) -> Decimal | None:
    parsed = parse_amount(text)
    return parsed.value if parsed is not None else None


def _year(text: str) -> int | None:
    number = int(text)
    if len(text) == 4:
        return number
    if len(text) == 2:
        return 2000 + number
    return None


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(text: str | None) -> ParsedDate | None:
    """Parse a printed date. Numeric day/month forms keep both readings when both are valid."""
    if not text:
        return None
    cleaned = text.strip().rstrip(".,")
    cleaned = re.sub(r"\s+", " ", cleaned)

    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", cleaned)
    if match:
        built = _build(int(match[1]), int(match[2]), int(match[3]))
        return ParsedDate(built) if built else None

    match = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})", cleaned)
    if match:
        first, second = int(match[1]), int(match[2])
        year = _year(match[3])
        month_first = _build(year, first, second)
        day_first = _build(year, second, first)
        if month_first and day_first:
            if month_first == day_first:
                return ParsedDate(month_first)
            # Dots are a European convention. Slashes say nothing either way.
            if "." in cleaned:
                return ParsedDate(day_first, None, "day_first")
            return ParsedDate(month_first, day_first, "ambiguous")
        if month_first:
            return ParsedDate(month_first, None, "month_first")
        if day_first:
            return ParsedDate(day_first, None, "day_first")
        return None

    match = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4}|\d{2})", cleaned)
    if match and match[1].lower() in _MONTHS:
        built = _build(_year(match[3]), _MONTHS[match[1].lower()], int(match[2]))
        return ParsedDate(built, None, "month_first") if built else None

    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[ -]([A-Za-z]+)\.?,?[ -](\d{4}|\d{2})", cleaned)
    if match and match[2].lower() in _MONTHS:
        built = _build(_year(match[3]), _MONTHS[match[2].lower()], int(match[1]))
        return ParsedDate(built, None, "day_first") if built else None
    return None


def resolve_currency(text: str | None) -> tuple[str | None, str]:
    """Map a printed currency mark to an ISO code. Returns (code, how it was resolved)."""
    if not text:
        return None, "missing"
    body = text.strip()
    upper = body.upper()
    for code in sorted(ISO_CODES):
        if re.search(rf"\b{code}\b", upper):
            return code, "printed_code"
    for symbol, code in SYMBOL_TO_CODE.items():
        if symbol in body:
            return code, "unique_symbol"
    if "$" in body:
        return "USD", "dollar_sign_assumed_usd"
    return None, "unrecognized"


def squash(text: str | None) -> str:
    """Letters and digits only, lowercased: the form used to compare printed strings."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None
