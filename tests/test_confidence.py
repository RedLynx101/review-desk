"""Confidence has to track checkable facts, in both directions."""

from review_desk.confidence import DEFAULT_THRESHOLD, assess, score_signals
from review_desk.schema import ModelReading, Signal
from review_desk.tidy import tidy_reading

from helpers import INVOICE_ROWS, document, invoice_reading


def _assess(rows=INVOICE_ROWS, doc=None, **overrides):
    reading = ModelReading.model_validate(invoice_reading(**overrides))
    return assess(doc or document(rows), tidy_reading(reading))


def _outcome(field, name):
    return next((signal.outcome for signal in field.signals if signal.name == name), None)


def test_a_correct_reading_scores_high_with_every_check_passing():
    fields, items, checks = _assess()
    assert {check.name: check.outcome for check in checks} == {"items_sum": "pass", "totals_sum": "pass"}
    for name in ("total", "subtotal", "tax", "document_number", "issue_date", "due_date", "purchase_order", "bill_to_name", "vendor_name"):
        assert not fields[name].needs_review, (name, fields[name].confidence, fields[name].signals)
    assert fields["total"].value == "72.05" and fields["tax"].value == "6.55"
    assert fields["issue_date"].value == "2026-03-05"
    for row in items:
        assert not row.cells["amount"].needs_review


def test_a_value_not_on_the_page_goes_to_review():
    fields, _, _ = _assess(document_number={"text": "ZZ-99999", "line": 3})
    assert _outcome(fields["document_number"], "source") == "fail"
    assert fields["document_number"].needs_review
    assert fields["document_number"].sources == []


def test_an_invented_total_fails_both_source_and_arithmetic():
    fields, _, checks = _assess(total={"text": "$999.99", "line": 12})
    assert _outcome(fields["total"], "source") == "fail"
    assert _outcome(fields["total"], "arithmetic") == "fail"
    assert fields["total"].confidence < 0.05
    assert next(c for c in checks if c.name == "totals_sum").outcome == "fail"


def test_picking_the_balance_due_instead_of_the_total_gets_caught():
    """A printed number under the wrong label: the source check alone would pass this."""
    fields, _, _ = _assess(total={"text": "$52.05", "line": 14})
    assert _outcome(fields["total"], "source") == "pass"
    assert _outcome(fields["total"], "arithmetic") == "fail"
    assert fields["total"].needs_review


def test_a_document_whose_printed_math_is_wrong_goes_to_review():
    rows = [row.replace("Total    $72.05", "Total    $72.95") for row in INVOICE_ROWS]
    fields, _, checks = _assess(rows=rows, total={"text": "$72.95", "line": 12})
    assert next(c for c in checks if c.name == "totals_sum").outcome == "fail"
    assert fields["total"].needs_review
    # The column still sums to the subtotal, so the suspicion belongs with the total, not the subtotal.
    assert not fields["subtotal"].needs_review


def test_reading_no_line_items_at_all_is_flagged():
    fields, items, checks = _assess(line_items=[])
    assert items == []
    assert next(c for c in checks if c.name == "items_sum").outcome == "fail"
    assert fields["subtotal"].needs_review


def test_a_dropped_line_item_fails_the_column_sum():
    fields, items, checks = _assess(line_items=[
        {"description": "Neoprene gasket, 4 in.", "quantity": "4", "unit_price": "10.00", "amount": "40.00", "line": 8},
    ])
    assert next(c for c in checks if c.name == "items_sum").outcome == "fail"
    assert fields["subtotal"].needs_review
    assert items[0].cells["amount"].needs_review
    assert items[0].cells["description"].needs_review


def test_a_line_whose_quantity_times_price_disagrees_is_flagged():
    _, items, _ = _assess(line_items=[
        {"description": "Neoprene gasket, 4 in.", "quantity": "4", "unit_price": "11.00", "amount": "40.00", "line": 8},
        {"description": "Rush cutting fee", "quantity": "1", "unit_price": "25.50", "amount": "25.50", "line": 9},
    ])
    assert _outcome(items[0].cells["amount"], "arithmetic") == "fail"
    assert items[0].cells["unit_price"].needs_review


def test_a_null_for_a_field_with_a_label_on_the_page_is_suspicious():
    fields, _, _ = _assess(due_date={"text": None, "line": None})
    assert _outcome(fields["due_date"], "absence") == "fail"
    assert fields["due_date"].needs_review


def test_a_null_for_a_field_with_no_label_is_fine():
    fields, _, _ = _assess()
    assert _outcome(fields["discount"], "absence") == "pass"
    assert not fields["discount"].needs_review
    assert fields["discount"].value is None


def test_a_missed_tax_line_is_noticed():
    fields, _, _ = _assess(tax=[])
    assert _outcome(fields["tax"], "absence") == "fail"
    assert fields["tax"].needs_review


def test_cited_line_that_does_not_hold_counts_against_the_value():
    right, _, _ = _assess()
    wrong_line, _, _ = _assess(document_number={"text": "A1047", "line": 9})
    assert _outcome(wrong_line["document_number"], "source") == "weak"
    assert wrong_line["document_number"].confidence < right["document_number"].confidence


def test_low_ocr_confidence_lowers_the_score():
    sure = document(INVOICE_ROWS, source="ocr", confidence=0.96)
    unsure = document(INVOICE_ROWS, source="ocr", confidence=0.55)
    high, _, _ = _assess(doc=sure)
    low, _, _ = _assess(doc=unsure)
    assert _outcome(low["document_number"], "reading") == "fail"
    assert low["document_number"].confidence < high["document_number"].confidence
    assert low["document_number"].needs_review


def test_arithmetic_rescues_amounts_read_with_middling_ocr_confidence():
    """Numbers that add up are strong evidence, even when OCR wasn't sure of the glyphs."""
    unsure = document(INVOICE_ROWS, source="ocr", confidence=0.8)
    fields, _, _ = _assess(doc=unsure)
    assert _outcome(fields["total"], "reading") == "weak"
    assert not fields["total"].needs_review


def test_ambiguous_date_without_evidence_is_weak():
    rows = [row.replace("March 5, 2026", "05/03/2026").replace("Due Date: April 4, 2026", "Due Date: 06/04/2026") for row in INVOICE_ROWS]
    fields, _, _ = _assess(rows=rows, issue_date={"text": "05/03/2026", "line": 3}, due_date={"text": "06/04/2026", "line": 4}, currency={"text": None, "line": None})
    assert _outcome(fields["issue_date"], "format") in ("weak", "pass")


def test_a_day_above_twelve_settles_an_ambiguous_date_on_the_same_document():
    rows = [row.replace("March 5, 2026", "05/03/2026").replace("Due Date: April 4, 2026", "Due Date: 25/03/2026") for row in INVOICE_ROWS]
    fields, _, _ = _assess(rows=rows, issue_date={"text": "05/03/2026", "line": 3}, due_date={"text": "25/03/2026", "line": 4})
    assert fields["issue_date"].value == "2026-03-05"
    assert _outcome(fields["issue_date"], "format") == "pass"


def test_currency_hint_settles_day_first_when_nothing_else_does():
    rows = [row.replace("March 5, 2026", "05/03/2026").replace("Due Date: April 4, 2026", "Due Date: 06/04/2026").replace("$", "£") for row in INVOICE_ROWS]
    fields, _, _ = _assess(
        rows=rows, issue_date={"text": "05/03/2026", "line": 3}, due_date={"text": "06/04/2026", "line": 4},
        currency={"text": "£", "line": 12}, subtotal={"text": "£65.50", "line": 10},
        tax=[{"text": "£6.55", "line": 11}], total={"text": "£72.05", "line": 12},
    )
    assert fields["issue_date"].value == "2026-03-05"
    assert _outcome(fields["issue_date"], "format") == "weak"
    assert fields["currency"].value == "GBP"


def test_due_before_issue_is_flagged():
    rows = [row.replace("April 4, 2026", "January 4, 2026") for row in INVOICE_ROWS]
    fields, _, _ = _assess(rows=rows, due_date={"text": "January 4, 2026", "line": 4})
    assert _outcome(fields["due_date"], "date_order") == "fail"


def test_split_tax_sums_and_keeps_both_sources():
    rows = [row.replace("Sales Tax 10%    $6.55", "State Tax    $4.00") for row in INVOICE_ROWS]
    rows.insert(11, "County Tax    $2.55")
    fields, _, _ = _assess(
        rows=rows, tax=[{"text": "$4.00", "line": 11}, {"text": "$2.55", "line": 12}], total={"text": "$72.05", "line": 13},
    )
    assert fields["tax"].value == "6.55"
    assert len(fields["tax"].sources) == 2
    assert _outcome(fields["tax"], "arithmetic") == "pass"


def test_vendor_read_as_the_customer_is_flagged():
    fields, _, _ = _assess(vendor_name={"text": "Mock Harbor Rowing Club", "line": 6})
    assert fields["vendor_name"].needs_review


def test_a_row_without_an_amount_is_flagged():
    _, items, _ = _assess(line_items=[
        {"description": "Neoprene gasket, 4 in.", "quantity": "4", "unit_price": "10.00", "amount": None, "line": 8},
    ])
    assert items[0].cells["amount"].needs_review


def test_a_null_quantity_on_a_row_that_prints_one_is_flagged():
    _, items, _ = _assess(line_items=[
        {"description": "Neoprene gasket, 4 in.", "quantity": None, "unit_price": "10.00", "amount": "40.00", "line": 8},
    ])
    assert _outcome(items[0].cells["quantity"], "absence") == "fail"


def test_score_is_logistic_of_base_plus_effects_and_never_uses_a_model_opinion():
    signals = [Signal(name="source", outcome="pass", detail="x"), Signal(name="label", outcome="fail", detail="y")]
    score, updated = score_signals("amount", signals, {"base.amount": 2.0, "source.pass": 1.0, "label.fail": -3.0})
    assert score == 0.5
    assert [signal.effect for signal in updated] == [1.0, -3.0]


def test_not_applicable_signals_have_no_effect():
    signals = [Signal(name="source", outcome="not_applicable", detail="x")]
    assert score_signals("amount", signals, {"base.amount": 0.0, "source.not_applicable": 9.0})[0] == 0.5


def test_calibration_file_overrides_weights_and_threshold(tmp_path):
    from review_desk.confidence import DEFAULT_WEIGHTS, load_calibration

    assert load_calibration(tmp_path / "missing.json")[0] == DEFAULT_WEIGHTS
    path = tmp_path / "calibration.json"
    path.write_text('{"weights": {"source.pass": 9.0}, "threshold": 0.77}', encoding="utf-8")
    weights, threshold = load_calibration(path)
    assert weights["source.pass"] == 9.0 and threshold == 0.77
    assert weights["label.fail"] == DEFAULT_WEIGHTS["label.fail"]


def test_fitting_learns_from_outcomes_and_keeps_unseen_signals_at_their_prior():
    from review_desk.evaluation.fit import choose_threshold, fit_weights

    records = []
    for _ in range(200):
        records.append({"field": "total", "signals": ["source.pass", "arithmetic.pass"], "correct": True})
        records.append({"field": "total", "signals": ["source.pass", "arithmetic.fail"], "correct": False})
    prior = {"base.amount": 0.0, "source.pass": 0.0, "arithmetic.pass": 0.0, "arithmetic.fail": 0.0, "label.fail": -3.0}
    fitted = fit_weights(records, prior=prior)
    assert fitted["arithmetic.pass"] > 1.0 > -1.0 > fitted["arithmetic.fail"]
    assert fitted["label.fail"] == -3.0

    scored = [{"confidence": 0.99, "correct": True}] * 90 + [{"confidence": 0.8, "correct": True}] * 9 + [{"confidence": 0.8, "correct": False}]
    chosen = choose_threshold(scored, target_error=0.0, candidates=[0.5, 0.8, 0.9, 0.95])
    assert chosen["threshold"] == 0.9 and chosen["wrong"] == 0 and chosen["skipped"] == 90


def test_a_label_fragment_read_as_a_name_is_flagged():
    fields, _, _ = _assess(bill_to_name={"text": "ID:", "line": 3})
    assert _outcome(fields["bill_to_name"], "format") == "fail"
    assert fields["bill_to_name"].needs_review


def test_a_clean_page_can_say_a_field_is_absent_but_a_bad_scan_cannot():
    rows = [row for row in INVOICE_ROWS if "Bill To" not in row]
    clear, _, _ = _assess(rows=rows, doc=document(rows, source="ocr", confidence=0.95), bill_to_name={"text": None, "line": None})
    assert _outcome(clear["bill_to_name"], "absence") == "pass"
    murky, _, _ = _assess(rows=rows, doc=document(rows, source="ocr", confidence=0.5), bill_to_name={"text": None, "line": None})
    assert _outcome(murky["bill_to_name"], "absence") == "weak"
    assert murky["bill_to_name"].confidence < clear["bill_to_name"].confidence


def test_a_column_that_adds_up_does_not_vouch_for_a_description():
    _, items, _ = _assess()
    assert _outcome(items[0].cells["description"], "column") is None
    assert _outcome(items[0].cells["amount"], "column") == "pass"


def test_ocr_doubt_counts_for_more_on_a_date_than_on_an_amount_that_adds_up():
    unsure = document(INVOICE_ROWS, source="ocr", confidence=0.8)
    fields, _, _ = _assess(doc=unsure)
    assert fields["issue_date"].needs_review
    assert fields["document_number"].needs_review
    assert not fields["total"].needs_review


def test_class_specific_weight_wins_over_the_shared_one():
    signals = [Signal(name="reading", outcome="weak", detail="x")]
    weights = {"base.date": 0.0, "base.amount": 0.0, "reading.weak": -1.0, "reading.weak@date": 0.0}
    assert score_signals("date", signals, weights)[0] == 0.5
    assert score_signals("amount", signals, weights)[0] < 0.5


def test_review_flag_follows_the_threshold():
    fields, _, _ = _assess(document_number={"text": "ZZ-99999", "line": 3})
    assert fields["document_number"].confidence < DEFAULT_THRESHOLD
    reading = ModelReading.model_validate(invoice_reading())
    relaxed, _, _ = assess(document(INVOICE_ROWS), reading, threshold=0.0)
    assert not any(field.needs_review for field in relaxed.values())
