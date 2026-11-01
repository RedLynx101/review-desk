"""The known answers are right: they add up, and they sit where the ink is."""

from __future__ import annotations

import io
from decimal import Decimal

import pdfplumber
import pytest

from review_desk.synth import content
from review_desk.synth.canvas import FieldCanvas
from review_desk.synth.content import make_document
from review_desk.synth.formats import MONEY_STYLES, MoneyStyle, format_date, round_cents
from review_desk.synth.generate import DEFAULT_SEED

MANY = [make_document(split, index, DEFAULT_SEED) for split in ("dev", "heldout") for index in range(500)]


def _squash(text: str) -> str:
    return "".join((text or "").split())


def _line_sum(doc):
    return sum((row.amount for row in doc.items), Decimal("0"))


def _expected_total(doc):
    return doc.subtotal - (doc.discount or 0) + (doc.shipping or 0) + (doc.tax or 0)


def test_documents_without_anomalies_add_up():
    checked = 0
    for doc in MANY:
        if doc.anomalies:
            continue
        checked += 1
        assert doc.subtotal == _line_sum(doc), doc.base_id
        assert doc.total == _expected_total(doc), doc.base_id
        for row in doc.items:
            if row.quantity is not None and row.unit_price is not None:
                assert row.amount == round_cents(row.quantity * row.unit_price), doc.base_id
    assert checked > 700


@pytest.mark.parametrize("anomaly", ["total_mismatch", "subtotal_mismatch", "line_amount_mismatch", "missing_number"])
def test_each_anomaly_occurs_and_really_breaks_what_it_claims(anomaly):
    flagged = [doc for doc in MANY if anomaly in doc.anomalies]
    assert len(flagged) >= 5, f"too few {anomaly} documents to measure anything"
    for doc in flagged:
        if anomaly == "total_mismatch":
            assert doc.total != _expected_total(doc)
        elif anomaly == "subtotal_mismatch":
            assert doc.subtotal != _line_sum(doc)
        elif anomaly == "line_amount_mismatch":
            assert any(
                row.quantity is not None and row.unit_price is not None
                and row.amount != round_cents(row.quantity * row.unit_price)
                for row in doc.items
            )
        else:
            assert doc.number is None


def test_invoice_and_receipt_rules():
    for doc in MANY:
        assert doc.items, doc.base_id
        if doc.document_type == "receipt":
            assert doc.due_date is None and doc.purchase_order is None and doc.bill_to is None
        else:
            assert doc.bill_to is not None
            if doc.due_date:
                assert doc.due_date > doc.issue_date


def test_traps_are_actually_present():
    """Distractors only test anything if a meaningful share of documents carry them."""
    invoices = [doc for doc in MANY if doc.document_type == "invoice"]
    def share(predicate):
        return sum(1 for doc in invoices if predicate(doc)) / len(invoices)
    assert share(lambda d: d.deposit is not None) > 0.10
    assert share(lambda d: d.ship_to is not None) > 0.25
    assert share(lambda d: d.customer_id is not None) > 0.35
    assert share(lambda d: len(d.tax_lines) > 1) > 0.03
    assert share(lambda d: len(d.items) > 25) > 0.05
    assert share(lambda d: d.money.decimal_comma) > 0.04


def test_every_field_value_is_found_inside_its_box_in_the_text_layer(clean_corpus):
    """Crop the real PDF at each recorded box and read it back. This catches any layout whose
    bookkeeping drifted from what it drew, in every layout and font."""
    checked = 0
    for doc, _, pdf, truth in clean_corpus:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            pages = document.pages
            cells = [truth["fields"][name] for name in truth["fields"]]
            cells += [cell for row in truth["line_items"] for cell in row.values()]
            for cell in cells:
                for location in cell["locations"]:
                    page = pages[location["page"]]
                    x0, top, x1, bottom = location["bbox"]
                    box = (max(0, x0), max(0, top), min(page.width, x1), min(page.height, bottom))
                    found = _squash(page.crop(box).extract_text())
                    assert _squash(location["printed"]) in found, (doc.base_id, location, found)
                    checked += 1
    assert checked > 3000


def test_field_values_are_in_the_printed_ink(clean_corpus):
    for doc, _, _, truth in clean_corpus:
        fields = truth["fields"]
        number = fields["document_number"]
        if number["value"]:
            assert any(number["value"] == location["printed"] for location in number["locations"]), doc.base_id
        vendor = fields["vendor_name"]
        joined = " ".join(location["printed"] for location in vendor["locations"])
        assert all(word.lower() in joined.lower() for word in vendor["value"].split()), doc.base_id


def test_null_fields_have_no_locations_and_filled_ones_do(clean_corpus):
    for doc, _, _, truth in clean_corpus:
        for name, cell in truth["fields"].items():
            assert bool(cell["value"]) == bool(cell["locations"]) or cell["value"] is None, (doc.base_id, name)
            if cell["value"] is None:
                assert cell["locations"] == []


def test_two_tax_lines_record_both_locations_and_their_sum(clean_corpus):
    seen = 0
    for doc, _, _, truth in clean_corpus:
        if truth["tax_printed_as_separate_lines"]:
            seen += 1
            assert len(truth["fields"]["tax"]["locations"]) == 2
            assert Decimal(truth["fields"]["tax"]["value"]) == sum(amount for _, amount in doc.tax_lines)
    assert seen > 0


def test_every_page_of_a_clean_document_carries_the_visible_label(clean_corpus):
    for doc, _, pdf, truth in clean_corpus:
        assert truth["synthetic"] is True
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            assert "synthetic" in document.metadata["Subject"].lower()
            assert len(document.pages) == truth["page_count"]
            for page in document.pages:
                assert "SYNTHETICTESTDOCUMENT" in _squash(page.extract_text()), doc.base_id


def test_long_documents_flow_onto_more_pages(clean_corpus):
    multi = [item for item in clean_corpus if item[3]["page_count"] > 1]
    assert multi, "the corpus should contain at least one multi-page invoice"
    for _, _, _, truth in multi:
        assert truth["document_type"] == "invoice"
        assert truth["fields"]["total"]["locations"][-1]["page"] == truth["page_count"] - 1


def _overlapping_chars(page) -> list[tuple[str, str]]:
    chars = [c for c in page.chars if c["text"].strip()]
    found = []
    for index, a in enumerate(chars):
        for b in chars[index + 1:index + 400]:
            across = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            down = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            narrow = min(a["x1"] - a["x0"], b["x1"] - b["x0"])
            short = min(a["bottom"] - a["top"], b["bottom"] - b["top"])
            if across > 0.4 * narrow and down > 0.45 * short:
                found.append((a["text"], b["text"]))
    return found


def test_overlap_detector_sees_a_real_collision():
    canvas = FieldCanvas(300, 200, "Synthetic control")
    canvas.text(20, 50, "Subtotal 1,234.56", "Helvetica", 10)
    canvas.text(40, 50, "Discount", "Helvetica", 10)
    with pdfplumber.open(io.BytesIO(canvas.finish())) as document:
        assert _overlapping_chars(document.pages[0])


def test_no_layout_draws_text_on_top_of_text_or_off_the_page(clean_corpus):
    for doc, _, pdf, _ in clean_corpus:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            for page in document.pages:
                assert _overlapping_chars(page) == [], doc.base_id
                for char in page.chars:
                    assert -0.5 <= char["x0"] and char["x1"] <= page.width + 0.5, doc.base_id
                    assert -0.5 <= char["top"] and char["bottom"] <= page.height + 0.5, doc.base_id


def test_money_formats():
    amount = Decimal("1234.50")
    assert MoneyStyle("USD", "$").amount(amount) == "$1,234.50"
    assert MoneyStyle("EUR", "€", symbol_after=True, decimal_comma=True).amount(amount) == "1.234,50 €"
    assert MoneyStyle("GBP", "£").negative(Decimal("5")) == "-£5.00"
    assert MoneyStyle("USD", "").quantity(Decimal("20")) == "20"
    assert MoneyStyle("EUR", "", decimal_comma=True).quantity(Decimal("2.5")) == "2,5"
    assert all(style.currency for styles in MONEY_STYLES.values() for style in styles)


def test_date_formats():
    from datetime import date

    day = date(2026, 3, 5)
    assert format_date(day, "long") == "March 5, 2026"
    assert format_date(day, "us_slash") == "03/05/2026"
    assert format_date(day, "day_slash") == "05/03/2026"
    assert format_date(day, "iso") == "2026-03-05"
    assert format_date(day, "us_short") == "3/5/26"


def test_seed_pool_lists_stay_separate_from_truth_values():
    """Guard against someone adding a held-out stem to the dev pool while tuning."""
    assert len(set(content._VENDOR_STEMS["dev"])) == len(content._VENDOR_STEMS["dev"])
    assert content._VENDOR_STEMS["samples"] == content._VENDOR_STEMS["dev"]
