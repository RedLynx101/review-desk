"""The outside-document scorer, on invented labels. No dataset content lives in this repo."""

import json
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pdfplumber
import pytest
from PIL import Image

from review_desk.evaluation.outside import aggregate, cord_labels, fatura_labels, image_pdf, plain_amount, plain_quantity, score_outside
from review_desk.synth.content import BAD_SUM_KINDS, make_document


def field(value, printed=None, confidence=0.95, needs_review=False):
    return SimpleNamespace(value=value, printed=printed if printed is not None else value, confidence=confidence, needs_review=needs_review)


def result(fields=None, rows=(), status="ok", seconds=12.0):
    return SimpleNamespace(fields=fields or {}, line_items=[SimpleNamespace(cells=cells) for cells in rows], status=status, model_seconds=seconds)


@pytest.mark.parametrize("text, expected", [
    ("41,000", "41000"), ("41.000", "41000"), ("Rp 41.000", "41000"), ("41,000.00", "41000"), ("1.591.600", "1591600"),
    ("-2,000", "2000"), ("12.50", "12.50"), ("none", None), (None, None),
])
def test_plain_amount_reads_either_grouping_mark(text, expected):
    assert plain_amount(text) == expected


@pytest.mark.parametrize("text, expected", [("2", "2"), ("x2", "2"), ("2 pcs", "2"), ("1.00", "1"), ("1,5", "1.5"), ("", None)])
def test_plain_quantity_finds_the_number(text, expected):
    assert plain_quantity(text) == expected


def test_cord_labels_keep_only_what_the_schema_has():
    labels = cord_labels({
        "menu": [{"nm": "Sample Noodles", "cnt": "2x", "price": "30,000"}, {"nm": "Sample Tea", "price": "5,000", "unitprice": "5,000"}, {"cnt": "1"}],
        "sub_total": {"subtotal_price": "35,000", "service_price": "1,000", "tax_price": "3,500"},
        "total": {"total_price": "39,500", "cashprice": "50,000", "changeprice": "10,500"},
    })
    assert {name: label["value"] for name, label in labels["fields"].items()} == {"subtotal": "35000", "tax": "3500", "total": "39500"}
    assert len(labels["items"]) == 2  # A row with no name can't be aligned, so it isn't scored.
    assert labels["items"][0]["quantity"]["value"] == "2"
    assert labels["items"][0]["unit_price"] is None
    assert labels["items"][1]["unit_price"]["value"] == "5000"


def test_cord_labels_accept_a_single_row_and_a_list_shaped_section():
    labels = cord_labels({"menu": {"nm": ["Sample", "Soup"], "price": "9,000"}, "total": [{"total_price": "9,000"}]})
    assert labels["items"][0]["description"]["value"] == "Sample Soup"
    assert labels["fields"]["total"]["value"] == "9000"


LABELS = {
    "doc_id": "doc-1",
    "fields": {"total": {"value": "39500", "printed": "39,500"}, "tax": {"value": "3500", "printed": "3,500"}},
    "items": [
        {"description": {"value": "Sample Noodles", "printed": "Sample Noodles"}, "quantity": {"value": "2", "printed": "2x"}, "unit_price": None, "amount": {"value": "30000", "printed": "30,000"}},
        {"description": {"value": "Sample Tea", "printed": "Sample Tea"}, "quantity": None, "unit_price": None, "amount": {"value": "5000", "printed": "5,000"}},
    ],
}


def test_only_labeled_values_get_scored_and_a_wrong_skip_counts():
    outcome = result(
        fields={"total": field("39500", "39,500"), "tax": field("35.00", "3,500"), "vendor_name": field("Unlabeled Store")},
        rows=[{"description": field("Sample Noodles"), "quantity": field("2", confidence=0.5, needs_review=True), "amount": field("30000", "30,000"), "unit_price": field("15000")}],
    )
    records, document = score_outside(LABELS, outcome, "Sample Noodles 2x 30,000 Sample Tea 5,000 3,500 39,500")
    by_key = {(record["field"], record.get("row")): record for record in records}
    assert ("vendor_name", None) not in by_key and ("unit_price", 0) not in by_key
    assert by_key[("total", None)]["correct"] and not by_key[("tax", None)]["correct"]
    # The digits were read right and the notation wasn't: the column that separates OCR from parsing.
    assert by_key[("tax", None)]["digits_right"] is True
    assert by_key[("description", 1)]["missing_row"] is True and by_key[("description", 1)]["needs_review"] is True
    assert document["rows_labeled"] == 2 and document["rows_missing"] == 1 and document["any_flagged"] is True
    assert not document["all_labeled_right"]


def test_aggregate_counts_and_holds_no_document_text():
    outcome = result(fields={"total": field("39500", "39,500"), "tax": field("3400", "3,400")}, rows=[{"description": field("Sample Noodles"), "amount": field("30000", "30,000"), "quantity": field("2")}, {"description": field("Sample Tea"), "amount": field("5000", "5,000")}])
    records, document = score_outside(LABELS, outcome, "Sample Noodles 2x 30,000 Sample Tea 5,000 3,500 39,500")
    summary = aggregate({"dataset": {"name": "invented"}}, {"model": {"id": "m"}}, records, [document], {"truncated": 1}, 0.9)
    assert summary["all"]["values"] == 7 and summary["all"]["wrong_among_skipped"] == 1 and summary["all"]["skipped"] == 7
    assert summary["by_field"]["tax"]["right"] == 0 and summary["by_field"]["total"]["right"] == 1
    assert summary["documents"]["wrong_though_nothing_flagged"] == 1
    assert summary["model_failures"] == {"truncated": 1}
    written = json.dumps(summary)
    assert "Sample" not in written and "39500" not in written


def test_image_pdf_makes_a_page_with_no_text_layer():
    buffer = BytesIO()
    Image.new("RGB", (400, 600), "white").save(buffer, "PNG")
    with pdfplumber.open(BytesIO(image_pdf(buffer.getvalue(), dpi=200.0))) as pdf:
        assert not pdf.pages[0].extract_text()
        assert float(pdf.pages[0].width) == pytest.approx(400 / 200 * 72, abs=1)
        assert len(pdf.pages[0].images) == 1


def test_image_pdf_refuses_other_formats():
    buffer = BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "BMP")
    with pytest.raises(ValueError):
        image_pdf(buffer.getvalue(), dpi=100.0)


def test_every_bad_sums_document_carries_a_known_mistake_of_each_kind():
    kinds = [make_document("badsums", index, 20261101).anomalies for index in range(12)]
    assert all(kinds)
    seen = {kind for entry in kinds for kind in entry}
    assert seen == set(BAD_SUM_KINDS)


def test_bad_total_is_printed_as_the_truth_and_breaks_the_sum():
    doc = next(d for d in (make_document("badsums", index, 20261101) for index in range(12)) if d.anomalies == ["total_mismatch"])
    expected = doc.subtotal - (doc.discount or Decimal("0")) + (doc.shipping or Decimal("0")) + (doc.tax or Decimal("0"))
    assert doc.total != expected


def test_fatura_labels_take_the_value_out_of_each_box():
    labels = fatura_labels({
        "TABLE": [[{"bbox": [[1, 2], [3, 4]]}]],
        "INVOICE_INFO": [],
        "SELLER_NAME": {"text": "Sample, Example and Placeholder"},
        "BUYER": {"text": "Bill to:Fictitia Sampleton\n1 Sample Road\nTel:+(555)555-0100"},
        "NUMBER": {"text": "INVOICE # INV/12-34/567"},
        "DATE": {"text": "Invoice Date: 14-Apr-2022"},
        "DUE_DATE": {"text": "Due Date : 03/04/2022"},
        "SUB_TOTAL": {"text": "SUB_TOTAL : 885.25  USD"},
        "TAX": {"text": "TAX:VAT (3.22%):  28.51 USD"},
        "TOTAL": {"text": "TOTAL : 1,870.7 USD"},
    })
    values = {name: label["value"] for name, label in labels["fields"].items()}
    assert values == {
        "vendor_name": "Sample, Example and Placeholder", "bill_to_name": "Fictitia Sampleton", "document_number": "INV/12-34/567",
        "issue_date": "2022-04-14", "subtotal": "885.25", "tax": "28.51", "total": "1870.7", "currency": "USD",
    }  # The day-first-or-month-first due date isn't an answer key, so it's left out.
    assert labels["fields"]["tax"]["printed"] == "28.51"
    assert labels["items"] == []


@pytest.mark.parametrize("box, expected", [("INVOICE # INV/12-34/567", "INV/12-34/567"), ("Invoice number 8990-930", "8990-930"), ("INVOICE ID INV95927699", "INV95927699")])
def test_fatura_number_is_the_last_word_in_its_box(box, expected):
    assert fatura_labels({"NUMBER": {"text": box}})["fields"]["document_number"]["value"] == expected


def test_fatura_labels_skip_boxes_that_are_missing_or_empty():
    assert fatura_labels({"SELLER_NAME": {"text": "  "}, "TOTAL": {"text": "TOTAL :"}, "BUYER": []}) == {"fields": {}, "items": []}
