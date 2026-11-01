from datetime import date
from decimal import Decimal

from review_desk.locate import boxes, choose, find_amount, find_date, find_text, label_context

from helpers import document


def test_amount_matches_by_meaning_not_by_characters():
    doc = document(["Total    $1,234.50", "Other    1.234,50 €", "Plain    1234.50", "Not this    1234.51"])
    lines = sorted(match.line.number for match in find_amount(doc, Decimal("1234.50")))
    assert lines == [1, 2, 3]


def test_amount_split_across_two_words_is_found():
    doc = document(["Total    1,234 .56"])
    assert find_amount(doc, Decimal("1234.56"))[0].text == "1,234 .56"


def test_amount_box_covers_the_number_when_the_symbol_is_a_separate_word():
    assert find_amount(document(["Total    $ 12.00"]), Decimal("12.00"))[0].text == "12.00"


def test_date_found_in_any_printed_form():
    doc = document(["Issued March 5, 2026", "Other 2026-03-05", "Third 05/03/2026"])
    lines = sorted({match.line.number for match in find_date(doc, date(2026, 3, 5))})
    assert lines == [1, 2, 3]


def test_text_found_exactly_and_marked_exact():
    doc = document(["Bill To", "Mock Harbor Rowing Club", "Mock Harbor"])
    matches = find_text(doc, "Mock Harbor Rowing Club")
    assert len(matches) == 1 and matches[0].exact and matches[0].line.number == 2


def test_text_ignores_case_and_punctuation():
    assert find_text(document(["FERNWHISTLE GASKET WORKS, LLC"]), "Fernwhistle Gasket Works LLC")[0].exact


def test_text_wrapped_onto_the_next_row_is_found_with_both_rows():
    doc = document(["Tumbledown Field", "Instruments Co."])
    match = find_text(doc, "Tumbledown Field Instruments Co.")[0]
    assert match.exact and [word.text for word in match.words][-1] == "Co."
    assert len(boxes(match)) == 2


def test_wrapped_text_survives_another_column_on_the_same_rows():
    doc = document(["Tumbledown Field    Invoice", "Instruments Co.    Date"])
    assert find_text(doc, "Tumbledown Field Instruments Co.")[0].exact


def test_wrapped_text_can_span_three_rows():
    doc = document(["Preventive maintenance,", "includes filter swap", "and belt inspection"])
    match = find_text(doc, "Preventive maintenance, includes filter swap and belt inspection")[0]
    assert match.exact and len(boxes(match)) == 3


def test_glued_identifier_is_found_inside_a_word():
    match = find_text(document(["Invoice#A087865"]), "A087865")[0]
    assert match.exact


def test_ocr_slip_is_a_near_match_and_says_so():
    match = find_text(document(["Thistlewlck Gasket Works"]), "Thistlewick Gasket Works")[0]
    assert not match.exact and 0.72 <= match.similarity < 1


def test_unrelated_text_is_not_found():
    assert find_text(document(["Total $5.00"]), "Quillfeather Paper Goods") == []


def test_choose_prefers_the_cited_line():
    doc = document(["Subtotal    10.00", "Total    10.00"])
    matches = find_amount(doc, Decimal("10.00"))
    match, held = choose(matches, 2)
    assert match.line.number == 2 and held
    match, held = choose(matches, 9)
    assert match.line.number == 1 and not held
    assert choose([], 1) == (None, False)


def test_label_context_reads_left_and_above():
    doc = document(["Invoice No.: A1047", "Date", "03/05/2026"])
    left, _ = label_context(doc, find_text(doc, "A1047")[0])
    assert "invoice no.:" in left
    match = find_date(doc, date(2026, 3, 5))[0]
    _, above = label_context(doc, match)
    assert above == "date"


def test_boxes_cover_the_matched_words():
    doc = document(["Total    $12.00"])
    box = boxes(find_amount(doc, Decimal("12.00"))[0])[0]
    word = doc.lines[0].words[-1]
    assert (box.x0, box.x1, box.top, box.bottom) == (word.x0, word.x1, word.top, word.bottom)
