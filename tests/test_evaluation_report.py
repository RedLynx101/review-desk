"""The report's arithmetic and the model bar, on small made-up runs."""

from __future__ import annotations

import json

import pytest

from review_desk.evaluation.compare import Run, bad_sum_table, choose_default, false_alarm_table, main, outside_tables, skip_stats, wilson_upper


def record(correct=True, confidence=0.95, condition="clean", truth="1.00", **extra):
    return {
        "doc_id": "synthetic-x.clean", "base_id": "synthetic-x", "condition": condition, "severity": None if condition == "clean" else "light",
        "layout": "classic", "document_type": "invoice", "anomalies": [], "kind": "field", "field": "total", "row": None, "truth": truth,
        "predicted": truth, "printed": truth, "correct": correct, "confidence": confidence, "needs_review": confidence < 0.9, "location": "hit",
        "readable": True, "signals": [], **extra,
    }


def write_run(folder, model, records, documents):
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({
        "model": {"id": model, "quantization": "Q4", "loaded_context_length": 8192},
        "model_settings": {"temperature": 0.0, "max_tokens": 3500},
    }), encoding="utf-8")
    (folder / "scored.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (folder / "documents.jsonl").write_text("\n".join(json.dumps(d) for d in documents) + "\n", encoding="utf-8")
    return Run(folder)


def document(status="ok", seconds=10.0, failures=(), condition="clean"):
    return {
        "doc_id": "synthetic-x.clean", "base_id": "synthetic-x", "condition": condition, "severity": None, "layout": "classic", "document_type": "invoice",
        "anomalies": [], "status": status, "failure": None, "rows_truth": 1, "rows_predicted": 1, "rows_missing": 0, "rows_extra": 0, "wrong_values": 0,
        "all_correct": True, "any_flagged": False, "checks": {}, "model_seconds": seconds, "failure_history": list(failures),
    }


def test_skip_stats_count_only_values_at_or_above_the_threshold():
    records = [record(confidence=0.9), record(correct=False, confidence=0.95), record(correct=False, confidence=0.5), record(confidence=0.1)]
    stats = skip_stats(records)
    assert stats["skipped"] == 2 and stats["wrong"] == 1 and stats["coverage"] == 0.5 and stats["error"] == 0.5


def test_upper_bound_is_above_zero_even_with_no_errors():
    assert 0 < wilson_upper(0, 100) < 0.05
    assert wilson_upper(0, 0) is None
    assert wilson_upper(50, 100) > 0.5


def test_model_that_cannot_finish_loses_even_with_better_numbers(tmp_path):
    good = [record() for _ in range(100)]
    flaky = write_run(tmp_path / "flaky", "flaky", good, [document(status="failed") for _ in range(3)])
    steady = write_run(tmp_path / "steady", "steady", [*good[:50], *[record(confidence=0.1) for _ in range(50)]], [document()])
    choice = choose_default([flaky, steady])
    assert choice["default"] == "steady" and choice["bar_met"]


def test_error_above_the_bar_disqualifies(tmp_path):
    sloppy = write_run(tmp_path / "sloppy", "sloppy", [*[record() for _ in range(95)], *[record(correct=False) for _ in range(5)]], [document()])
    careful = write_run(tmp_path / "careful", "careful", [*[record() for _ in range(40)], *[record(confidence=0.1) for _ in range(60)]], [document()])
    assert choose_default([sloppy, careful])["default"] == "careful"


def test_higher_coverage_wins_among_models_that_clear_the_bar(tmp_path):
    more = write_run(tmp_path / "more", "more", [*[record() for _ in range(90)], *[record(confidence=0.1) for _ in range(10)]], [document(seconds=99)])
    less = write_run(tmp_path / "less", "less", [*[record() for _ in range(70)], *[record(confidence=0.1) for _ in range(30)]], [document(seconds=5)])
    choice = choose_default([less, more])
    assert choice["default"] == "more" and choice["bar_met"]


def test_a_tie_goes_to_the_faster_model(tmp_path):
    slow = write_run(tmp_path / "slow", "slow", [*[record() for _ in range(80)], *[record(confidence=0.1) for _ in range(20)]], [document(seconds=99)])
    fast = write_run(tmp_path / "fast", "fast", [*[record() for _ in range(796)], *[record(confidence=0.1) for _ in range(204)]], [document(seconds=5)])
    assert choose_default([slow, fast])["default"] == "fast"


def test_when_nobody_clears_the_bar_the_lower_error_wins_and_it_says_so(tmp_path):
    bad = write_run(tmp_path / "bad", "bad", [*[record() for _ in range(80)], *[record(correct=False) for _ in range(20)]], [document()])
    worse = write_run(tmp_path / "worse", "worse", [*[record() for _ in range(50)], *[record(correct=False) for _ in range(50)]], [document()])
    choice = choose_default([bad, worse])
    assert choice["default"] == "bad" and not choice["bar_met"]


def test_report_writes_side_by_side_and_labels_the_data_synthetic(tmp_path):
    documents = [document(condition=c) for c in ("clean", "scanned", "degraded")]
    records = [record(condition=c) for c in ("clean", "scanned", "degraded")]
    for number, model in enumerate(("org/first", "second")):
        write_run(tmp_path / f"dev{number}", model, records, documents)
        write_run(tmp_path / f"held{number}", model, records, documents)
    out = tmp_path / "out" / "REPORT.md"
    assert main([str(tmp_path / "dev0"), str(tmp_path / "dev1"), "--heldout", str(tmp_path / "held0"), str(tmp_path / "held1"), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "synthetic" in text and "no client deployment yet" in text
    assert "first" in text and "second" in text and "Default model" in text
    assert json.loads((out.parent / "model-choice.json").read_text(encoding="utf-8"))["default"] in ("org/first", "second")


@pytest.mark.parametrize("wrong,total", [(0, 10), (3, 10), (10, 10)])
def test_upper_bound_never_exceeds_one(wrong, total):
    assert wilson_upper(wrong, total) <= 1.0


def test_a_bad_sum_counts_as_caught_only_when_its_own_check_fails(tmp_path):
    total_caught = {**document(), "doc_id": "a.clean", "anomalies": ["total_mismatch"], "checks": {"totals_sum": "fail", "items_sum": "pass"}, "any_flagged": True}
    # Flagged for some other reason, with the sum check passing, isn't a catch.
    total_missed = {**document(), "doc_id": "b.clean", "anomalies": ["total_mismatch"], "checks": {"totals_sum": "pass", "items_sum": "pass"}, "any_flagged": True}
    line_caught = {**document(), "doc_id": "c.clean", "anomalies": ["line_amount_mismatch"], "checks": {"totals_sum": "pass", "items_sum": "pass"}}
    records = [record(doc_id="c.clean", kind="cell", signals=["arithmetic.fail"])]
    run = write_run(tmp_path / "bad", "m", records, [total_caught, total_missed, line_caught])
    table = "\n".join(bad_sum_table(run))
    assert "| Total doesn't add up | clean | 2 | 1 | 2 |" in table
    assert "| Line amount isn't quantity times price | clean | 1 | 1 | 0 |" in table


def test_false_alarms_leave_out_documents_with_a_bad_sum(tmp_path):
    fine = {**document(), "doc_id": "a.clean", "checks": {"totals_sum": "pass"}}
    alarm = {**document(), "doc_id": "b.clean", "checks": {"items_sum": "fail"}}
    bad = {**document(), "doc_id": "c.clean", "anomalies": ["total_mismatch"], "checks": {"totals_sum": "fail"}}
    run = write_run(tmp_path / "held", "m", [record(doc_id="a.clean")], [fine, alarm, bad])
    assert "| clean | 2 | 1 |" in "\n".join(false_alarm_table(run))


def test_outside_tables_read_the_committed_counts():
    block = {"values": 10, "right": 8, "readable": 9, "skipped": 5, "coverage": 0.5, "wrong_among_skipped": 1, "error": 0.2, "error_upper_95": 0.6, "digits_right": 9, "with_digits": 10}
    summary = {
        "dataset": {"name": "Invented set"}, "all": block, "by_field": {"total": block},
        "documents": {"documents": 3, "failed": 1, "all_labeled_right": 1, "nothing_flagged": 0, "wrong_though_nothing_flagged": 0, "rows_labeled": 4, "rows_missing": 2, "rows_extra": 1, "mean_seconds": 30.0},
    }
    text = "\n".join(outside_tables([summary]))
    assert "| Invented set | 3 | 10 | 50.0% | 1 of 5 | 20.00% | 60.00% |" in text
    assert "| Invented set | total | 10 | 80.0% (8/10) | 90.0% (9/10) | 90.0% (9/10) | 50.0% | 1 of 5 |" in text
