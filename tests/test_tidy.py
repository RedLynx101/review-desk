from review_desk.schema import Cited, ItemReading, ModelReading
from review_desk.tidy import (
    complete_bill_to,
    complete_names,
    complete_row,
    complete_totals,
    complete_vendor,
    merge_quantity_lines,
    tidy_amount,
    tidy_date,
    tidy_identifier,
    tidy_reading,
)

from helpers import INVOICE_ROWS, document, invoice_reading


def test_identifiers_lose_their_label():
    assert tidy_identifier("Ticket 27826") == "27826"
    assert tidy_identifier("Invoice No.: A1047") == "A1047"
    assert tidy_identifier("Invoice #A1047") == "A1047"
    assert tidy_identifier("#A1047") == "A1047"
    assert tidy_identifier("PO-77821") == "PO-77821"
    assert tidy_identifier("Receipt: R-001") == "R-001"
    assert tidy_identifier(None) is None


def test_identifier_that_only_looks_like_a_label_survives():
    assert tidy_identifier("INV-2026-00412") == "INV-2026-00412"
    assert tidy_identifier("order") == "order"


def test_dates_lose_label_and_time():
    assert tidy_date("Apr 16, 2025  09:11") == "Apr 16, 2025"
    assert tidy_date("Date: 03/05/2026") == "03/05/2026"
    assert tidy_date("Due Date: April 4, 2026") == "April 4, 2026"
    assert tidy_date("04/05/2026 2:15 pm") == "04/05/2026"
    assert tidy_date(None) is None


def test_amounts_lose_their_label():
    assert tidy_amount("Total: $12.00") == "$12.00"
    assert tidy_amount("$12.00") == "$12.00"
    assert tidy_amount("Sales Tax 8.25% 1.65") == "1.65"
    assert tidy_amount(None) is None
    assert tidy_amount("none") == "none"


def _row(description, quantity=None, unit_price=None, amount=None, line=1):
    return ItemReading(description=description, quantity=quantity, unit_price=unit_price, amount=amount, line=line)


def test_quantity_line_folds_into_the_item_above():
    rows = [_row("Painter's tape", amount="24.90"), _row("5 x 4.98", line=2), _row("Drill bit set", amount="29.83", line=3)]
    merged = merge_quantity_lines(rows)
    assert [row.description for row in merged] == ["Painter's tape", "Drill bit set"]
    assert merged[0].quantity == "5" and merged[0].unit_price == "4.98"


def test_a_real_item_that_starts_with_a_number_stays_an_item():
    rows = [_row("Paper", amount="1.00"), _row("4 x 6 photo print", amount="2.00", line=2)]
    assert len(merge_quantity_lines(rows)) == 2


def test_quantity_line_with_nothing_above_stays():
    assert len(merge_quantity_lines([_row("5 x 4.98")])) == 1


def test_empty_rows_are_dropped():
    assert merge_quantity_lines([_row(None), _row("Paper", amount="1.00")])[0].description == "Paper"


ROWS = [
    "Item    Qty    Each    Amount",
    "Terracotta pot    4    9.85    39.40",
    "Neoprene gasket, 4 in.    4    10.00    40.00",
    "Rush fee    57.75",
    "4 Herb bundle    2.53    10.12",
    "Paper clips    x 3    $4.50",
]


def _complete(row):
    return complete_row(document(ROWS), row)


def test_missing_columns_come_back_from_the_row_when_the_product_checks_out():
    row = _complete(_row("Terracotta pot", amount="39.40", line=2))
    assert (row.quantity, row.unit_price, row.amount) == ("4", "9.85", "39.40")


def test_a_digit_inside_the_description_is_not_taken_for_a_quantity():
    row = _complete(_row("Neoprene gasket, 4 in.", amount="40.00", line=3))
    assert (row.quantity, row.unit_price) == ("4", "10.00")


def test_missing_amount_comes_from_the_row_only_if_it_fits():
    row = _complete(_row("Terracotta pot", quantity="4", unit_price="9.85", line=2))
    assert row.amount == "39.40"
    row = _complete(_row("Terracotta pot", quantity="5", unit_price="9.85", line=2))
    assert row.amount is None


def test_the_only_number_on_a_row_is_its_amount():
    assert _complete(_row("Rush fee", line=4)).amount == "57.75"


def test_a_quantity_glued_to_the_description_is_split_off_when_the_product_checks_out():
    row = _complete(_row("4 Herb bundle", unit_price="2.53", amount="10.12", line=5))
    assert row.description == "Herb bundle"


def test_a_number_that_starts_a_description_stays_there_without_proof():
    doc = document(["10 in wrench    19.99"])
    row = complete_row(doc, _row("10 in wrench", amount="19.99", line=1))
    assert row.description == "10 in wrench" and row.quantity is None


def test_a_times_count_after_the_description_is_split_off():
    row = _complete(_row("Paper clips x 3", amount="$4.50", line=6))
    assert row.description == "Paper clips" and row.quantity == "3"


def test_a_times_count_before_the_description_is_split_off():
    row = complete_row(document(["2x Terracotta pot    19.06"]), _row("2x Terracotta pot", amount="19.06", line=1))
    assert row.description == "Terracotta pot" and row.quantity == "2"


def test_quantity_and_price_on_the_line_below_an_item_are_picked_up():
    doc = document(["Sketchbook    4.88", "2 x 2.44", "Wax seal kit    21.11"])
    row = complete_row(doc, _row("Sketchbook", unit_price="4.88", amount="4.88", line=1))
    assert (row.quantity, row.unit_price, row.amount) == ("2", "2.44", "4.88")


def test_a_price_that_only_repeats_the_amount_is_dropped_when_no_quantity_is_printed():
    doc = document(["Wax seal kit    21.11", "Paper clips    3.00"])
    row = complete_row(doc, _row("Wax seal kit", unit_price="21.11", amount="21.11", line=1))
    assert row.unit_price is None and row.amount == "21.11"


def test_a_price_read_where_the_amount_should_be_is_moved_to_the_amount():
    doc = document(["Rye rolls, half doz    5.21", "Oat cookie    1.98"])
    row = complete_row(doc, _row("Rye rolls, half doz", unit_price="5.21", line=1))
    assert row.unit_price is None and row.amount == "5.21"


def test_a_vendor_name_wrapped_onto_a_row_above_is_joined():
    doc = document(["Larkspindle Signal    Invoice", "Repair & Sons Ltd    Date"])
    assert complete_vendor(doc, Cited(text="Repair & Sons Ltd", line=2)).text == "Larkspindle Signal Repair & Sons Ltd"


def test_a_vendor_name_wrapped_onto_a_row_below_is_joined():
    doc = document(["Tumbledown Field    Invoice", "Instruments Co.    Date"])
    assert complete_vendor(doc, Cited(text="Tumbledown Field", line=1)).text == "Tumbledown Field Instruments Co."


def test_a_title_above_the_vendor_is_not_joined_to_it():
    doc = document(["INVOICE", "Marrowfen Paper Goods Ltd", "12 Fable Street"])
    assert complete_vendor(doc, Cited(text="Marrowfen Paper Goods Ltd", line=2)).text == "Marrowfen Paper Goods Ltd"


def test_an_address_below_the_vendor_is_not_joined_to_it():
    doc = document(["Marrowfen Paper Goods Ltd", "12 Fable Street"])
    assert complete_vendor(doc, Cited(text="Marrowfen Paper Goods Ltd", line=1)).text == "Marrowfen Paper Goods Ltd"


def test_a_purchase_order_on_a_receipt_with_no_number_becomes_the_receipt_number():
    reading = ModelReading.model_validate(invoice_reading(
        document_type="receipt", document_number={"text": None, "line": None}, purchase_order={"text": "11-4-89782", "line": 8},
    ))
    tidied = complete_names(document(["Vendor Co."]), reading)
    assert tidied.document_number.text == "11-4-89782" and tidied.purchase_order.text is None


def _reading(**overrides):
    return ModelReading.model_validate(invoice_reading(**overrides))


def test_a_dropped_subtotal_is_recovered_when_the_column_sum_is_printed_under_a_subtotal_label():
    fixed = complete_totals(document(INVOICE_ROWS), _reading(subtotal={"text": None, "line": None}))
    assert fixed.subtotal.text == "$65.50" and fixed.subtotal.line == 10


def test_a_misread_tax_is_replaced_when_the_equation_points_to_a_printed_tax_amount():
    fixed = complete_totals(document(INVOICE_ROWS), _reading(tax=[{"text": "10%", "line": 11}]))
    assert [entry.text for entry in fixed.tax] == ["$6.55"]


def test_totals_stay_alone_when_the_needed_amount_is_not_printed_under_a_label():
    rows = [row.replace("Sales Tax 10%    $6.55", "Sales Tax 10%    $9.99") for row in INVOICE_ROWS]
    reading = _reading(tax=[{"text": "$9.99", "line": 11}])
    assert complete_totals(document(rows), reading).tax[0].text == "$9.99"


def test_a_correct_reading_passes_through_unchanged():
    reading = _reading()
    assert complete_totals(document(INVOICE_ROWS), reading) == reading
    assert complete_bill_to(document(INVOICE_ROWS), reading) == reading


def test_a_name_under_a_bill_to_label_replaces_a_wrong_pick():
    fixed = complete_bill_to(document(INVOICE_ROWS), _reading(bill_to_name={"text": "12 Imaginary Lane", "line": 2}))
    assert fixed.bill_to_name.text == "Mock Harbor Rowing Club" and fixed.bill_to_name.line == 6


def test_a_bill_to_label_in_a_later_column_reads_its_own_column():
    rows = ["Larkspindle Signal    Customer", "Repair & Sons Ltds    Dummy Mae Ipsum"]
    reading = _reading(bill_to_name={"text": "Larkspindle Signal", "line": 1})
    assert complete_bill_to(document(rows), reading).bill_to_name.text == "Dummy Mae Ipsum"


def test_a_bill_to_label_with_the_name_on_the_same_row_reads_that_row():
    rows = ["CUSTOMER:    Sampleton Bake Society", "TERMS:    Net 14"]
    reading = _reading(bill_to_name={"text": "TERMS:", "line": 2})
    assert complete_bill_to(document(rows), reading).bill_to_name.text == "Sampleton Bake Society"


def test_customer_id_is_not_a_bill_to_label():
    rows = ["Customer ID: CUST-14343", "Terms: Net 45"]
    reading = _reading(bill_to_name={"text": "Mock Harbor Rowing Club", "line": 6})
    assert complete_bill_to(document(rows), reading).bill_to_name.text == "Mock Harbor Rowing Club"


def test_receipts_do_not_get_a_bill_to_from_a_customer_copy_line():
    rows = ["Customer", "Mock Harbor Rowing Club"]
    reading = _reading(document_type="receipt", bill_to_name={"text": None, "line": None})
    assert complete_bill_to(document(rows), reading).bill_to_name.text is None


def test_a_row_citing_a_line_that_does_not_exist_is_left_alone():
    row = _row("Terracotta pot", amount="39.40", line=99)
    assert _complete(row) == row


def test_tidy_reading_leaves_the_original_alone():
    reading = ModelReading.model_validate(invoice_reading(document_number={"text": "Invoice No. A1047", "line": 3}))
    tidied = tidy_reading(reading)
    assert tidied.document_number.text == "A1047"
    assert reading.document_number.text == "Invoice No. A1047"
