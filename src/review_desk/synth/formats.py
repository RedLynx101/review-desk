"""How amounts, quantities and dates get printed. Truth values stay normalized."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def round_cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _group(amount: Decimal, thousands: str, decimal_mark: str) -> str:
    whole, cents = f"{abs(amount):.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", thousands)
    return f"{grouped}{decimal_mark}{cents}"


@dataclass(frozen=True)
class MoneyStyle:
    """One way a document prints money. `currency` is the ISO 4217 code used in the truth."""

    currency: str
    symbol: str
    symbol_after: bool = False
    decimal_comma: bool = False
    note: str | None = None  # Printed line that names the currency, such as "All amounts in CAD".

    def number(self, amount: Decimal) -> str:
        if self.decimal_comma:
            return _group(amount, ".", ",")
        return _group(amount, ",", ".")

    def amount(self, value: Decimal) -> str:
        body = self.number(value)
        if not self.symbol:
            return body
        return f"{body} {self.symbol}" if self.symbol_after else f"{self.symbol}{body}"

    def negative(self, value: Decimal) -> str:
        return f"-{self.amount(value)}"

    def quantity(self, value: Decimal) -> str:
        text = format(value.normalize(), "f")
        return text.replace(".", ",") if self.decimal_comma else text


#: Money styles per locale. The first entry of each list is the most common.
MONEY_STYLES: dict[str, list[MoneyStyle]] = {
    "us": [
        MoneyStyle("USD", "$"),
        MoneyStyle("USD", "$"),
        MoneyStyle("USD", "", note="All amounts in USD"),
    ],
    "ca": [
        MoneyStyle("CAD", "CA$"),
        MoneyStyle("CAD", "$", note="All amounts in CAD"),
    ],
    "gb": [
        MoneyStyle("GBP", "£"),
        MoneyStyle("GBP", "", note="Amounts shown in GBP"),
    ],
    "eu": [
        MoneyStyle("EUR", "€", symbol_after=True, decimal_comma=True),
        MoneyStyle("EUR", "€", decimal_comma=True),
        MoneyStyle("EUR", "€"),
    ],
}

#: Date formats allowed per locale. Day-first numeric dates only appear outside the US, where a
#: reader has the same trouble with 05/03 as the software does.
DATE_FORMATS: dict[str, list[str]] = {
    "us": ["long", "abbr", "us_slash", "us_slash", "us_short", "iso"],
    "ca": ["long", "abbr", "iso", "iso"],
    "gb": ["day_abbr", "day_long", "day_slash", "day_slash"],
    "eu": ["day_abbr", "day_dot", "day_dot", "iso"],
}


def format_date(value: date, style: str) -> str:
    month = _MONTHS[value.month - 1]
    formats = {
        "long": f"{month} {value.day}, {value.year}",
        "abbr": f"{month[:3]} {value.day}, {value.year}",
        "us_slash": f"{value.month:02d}/{value.day:02d}/{value.year}",
        "us_short": f"{value.month}/{value.day}/{value.year % 100:02d}",
        "iso": value.isoformat(),
        "day_abbr": f"{value.day:02d} {month[:3]} {value.year}",
        "day_long": f"{value.day} {month} {value.year}",
        "day_slash": f"{value.day:02d}/{value.month:02d}/{value.year}",
        "day_dot": f"{value.day:02d}.{value.month:02d}.{value.year}",
    }
    return formats[style]
