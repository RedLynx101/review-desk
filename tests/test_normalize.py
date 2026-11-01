from datetime import date
from decimal import Decimal

import pytest

from review_desk.normalize import format_amount, parse_amount, parse_date, parse_quantity, resolve_currency, squash


@pytest.mark.parametrize("text,expected", [
    ("$1,234.56", "1234.56"),
    ("1,234.56", "1234.56"),
    ("1234.56", "1234.56"),
    ("1.234,56 €", "1234.56"),
    ("€1.234,56", "1234.56"),
    ("£999.00", "999.00"),
    ("CA$12.50", "12.50"),
    ("USD 12.50", "12.50"),
    ("12.50 USD", "12.50"),
    ("1,234,567.89", "1234567.89"),
    ("1.234.567,89", "1234567.89"),
    ("7", "7"),
    ("0.50", "0.50"),
    ("12,5", "12.5"),
])
def test_amounts_parse_in_both_notations(text, expected):
    assert parse_amount(text).value == Decimal(expected)


@pytest.mark.parametrize("text", ["-$20.00", "($20.00)", "20.00-", "−20.00"])
def test_negative_forms_are_recognized(text):
    parsed = parse_amount(text)
    assert parsed.negative and parsed.value == Decimal("20.00")


@pytest.mark.parametrize("text", ["1,234", "1.234"])
def test_three_digits_after_one_separator_is_reported_as_ambiguous(text):
    assert parse_amount(text).ambiguous


@pytest.mark.parametrize("text", [None, "", "abc", "$", "12a.50", "Net 30", "N/A"])
def test_non_amounts_are_refused(text):
    assert parse_amount(text) is None


def test_currency_hint_is_kept():
    assert parse_amount("£12.00").currency_hint == "£"
    assert parse_amount("12.00 EUR").currency_hint == "EUR"


def test_format_amount():
    assert format_amount(Decimal("12.5")) == "12.50"
    assert format_amount(Decimal("12")) == "12.00"
    assert format_amount(Decimal("1234.567")) == "1234.567"


def test_quantity_parses_like_a_number():
    assert parse_quantity("2.5") == Decimal("2.5")
    assert parse_quantity("x") is None


@pytest.mark.parametrize("text,expected", [
    ("March 5, 2026", date(2026, 3, 5)),
    ("Mar 5, 2026", date(2026, 3, 5)),
    ("Mar. 5 2026", date(2026, 3, 5)),
    ("05 Mar 2026", date(2026, 3, 5)),
    ("5 March 2026", date(2026, 3, 5)),
    ("2026-03-05", date(2026, 3, 5)),
    ("05.03.2026", date(2026, 3, 5)),
    ("13/03/2026", date(2026, 3, 13)),
    ("03/13/2026", date(2026, 3, 13)),
    ("3/13/26", date(2026, 3, 13)),
])
def test_dates_parse(text, expected):
    assert parse_date(text).value == expected


def test_numeric_dates_that_could_swap_keep_both_readings():
    parsed = parse_date("05/03/2026")
    assert parsed.style == "ambiguous"
    assert {parsed.value, parsed.alternative} == {date(2026, 5, 3), date(2026, 3, 5)}


def test_same_day_and_month_is_not_ambiguous():
    parsed = parse_date("05/05/2026")
    assert parsed.alternative is None and parsed.value == date(2026, 5, 5)


def test_a_day_above_twelve_settles_the_order():
    assert parse_date("13/03/2026").style == "day_first"
    assert parse_date("03/13/2026").style == "month_first"


@pytest.mark.parametrize("text", [None, "", "soon", "13/13/2026", "February 30, 2026", "2026-02-30", "Net 30"])
def test_non_dates_are_refused(text):
    assert parse_date(text) is None


@pytest.mark.parametrize("text,code,how", [
    ("USD", "USD", "printed_code"),
    ("All amounts in CAD", "CAD", "printed_code"),
    ("£", "GBP", "unique_symbol"),
    ("€", "EUR", "unique_symbol"),
    ("CA$", "CAD", "unique_symbol"),
    ("$", "USD", "dollar_sign_assumed_usd"),
    (None, None, "missing"),
    ("doubloons", None, "unrecognized"),
])
def test_currency_resolution_says_how_it_decided(text, code, how):
    assert resolve_currency(text) == (code, how)


def test_squash_keeps_only_letters_and_digits():
    assert squash("  INV-2026/0012 ") == "inv20260012"
    assert squash(None) == ""
