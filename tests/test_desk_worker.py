"""The worker: untrusted PDFs parsed in a child process, one failure never stops the queue."""

from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas

from review_desk.config import Settings
from review_desk.desk.reader_process import ReaderFailed, read_and_render, read_isolated
from review_desk.desk.store import Store
from review_desk.desk.worker import Pipeline, Worker, replay_model_step
from review_desk.pipeline import ModelRead
from review_desk.schema import ModelReading
from review_desk.synth.content import make_document
from review_desk.synth.generate import DEFAULT_SEED, render_all
from helpers import invoice_reading

SETTINGS = Settings(verify_text_layer=False)


def in_process_reader(pdf_path, image_dir, document_id, settings, *, timeout):
    try:
        return read_and_render(pdf_path, image_dir, document_id, settings)
    except Exception as error:
        code = getattr(error, "code", "unreadable")
        raise ReaderFailed(code, str(error), rejected=hasattr(error, "code")) from error


def _clean_pdf(index=0) -> tuple[bytes, dict]:
    return render_all(make_document("dev", index, DEFAULT_SEED), ("clean",))["clean"]


def _reading_from(truth: dict) -> ModelRead:
    fields = truth["fields"]

    def cited(name):
        cell = fields[name]
        return {"text": cell["locations"][-1]["printed"] if cell["value"] else None, "line": None}

    reading = {
        "document_type": truth["document_type"],
        "vendor_name": {"text": fields["vendor_name"]["value"], "line": None},
        "document_number": cited("document_number"), "issue_date": cited("issue_date"), "due_date": cited("due_date"),
        "purchase_order": cited("purchase_order"),
        "bill_to_name": {"text": fields["bill_to_name"]["value"], "line": None},
        "currency": {"text": fields["currency"]["value"], "line": None},
        "subtotal": cited("subtotal"), "discount": cited("discount"), "shipping": cited("shipping"),
        "tax": [{"text": location["printed"], "line": None} for location in fields["tax"]["locations"]],
        "total": cited("total"),
        "line_items": [
            {
                "description": row["description"]["value"],
                "quantity": row["quantity"]["locations"][0]["printed"] if row["quantity"]["value"] else None,
                "unit_price": row["unit_price"]["locations"][0]["printed"] if row["unit_price"]["value"] else None,
                "amount": row["amount"]["locations"][0]["printed"], "line": None,
            }
            for row in truth["line_items"]
        ],
    }
    return ModelRead(ModelReading.model_validate(reading), None, None, None)


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "desk")
    yield instance
    instance.close()


def test_a_queued_document_becomes_reviewable_with_page_images_and_sources(store):
    pdf, truth = _clean_pdf()
    created = store.add_document("invoice.pdf", pdf, actor="tester")
    pipeline = Pipeline(store, SETTINGS, lambda document, sha: _reading_from(truth), reader=in_process_reader)
    assert pipeline.drain() == 1
    current = store.get_document(created["id"])
    assert current["status"] == "needs_review"
    assert current["pages"][0]["width"] == pytest.approx(truth["page_sizes"][0][0])
    assert (store.pages / current["pages"][0]["image"]).stat().st_size > 1000
    total = next(field for field in current["fields"] if field["path"] == "total")
    assert total["value"] == truth["fields"]["total"]["value"]
    assert total["sources"], "the total should point at the page"


def test_a_model_that_fails_leaves_a_failed_document_and_the_queue_moves_on(store):
    pdf, truth = _clean_pdf()
    first = store.add_document("first.pdf", pdf, actor="tester")
    time.sleep(0.01)
    second = store.add_document("second.pdf", pdf, actor="tester")
    calls = []

    def flaky(document, sha):
        calls.append(sha)
        if len(calls) == 1:
            return ModelRead(None, "model_crashed", "The model process crashed.", None)
        return _reading_from(truth)

    pipeline = Pipeline(store, SETTINGS, flaky, reader=in_process_reader)
    assert pipeline.drain() == 2
    assert store.get_document(first["id"])["status"] == "failed"
    assert "crashed" in store.get_document(first["id"])["failure"]
    assert store.get_document(second["id"])["status"] == "needs_review"
    # Even a failed document keeps its pages, so a person can type the values in.
    assert store.get_document(first["id"])["pages"]


def test_an_unexpected_error_fails_that_document_instead_of_leaving_it_stuck(store):
    pdf, truth = _clean_pdf()
    first = store.add_document("first.pdf", pdf, actor="tester")
    time.sleep(0.01)
    second = store.add_document("second.pdf", pdf, actor="tester")
    calls = []

    def explodes_once(document, sha):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("something nobody planned for")
        return _reading_from(truth)

    assert Pipeline(store, SETTINGS, explodes_once, reader=in_process_reader).drain() == 2
    assert store.get_document(first["id"])["status"] == "failed"
    assert store.get_document(first["id"])["failure_code"] == "internal_error"
    assert "something nobody planned for" not in store.get_document(first["id"])["failure"]
    assert store.get_document(second["id"])["status"] == "needs_review"


def test_retrying_a_failure_runs_it_again(store):
    pdf, truth = _clean_pdf()
    created = store.add_document("a.pdf", pdf, actor="tester")
    outcomes = [ModelRead(None, "timeout", "Too slow.", None), _reading_from(truth)]
    pipeline = Pipeline(store, SETTINGS, lambda document, sha: outcomes.pop(0), reader=in_process_reader)
    pipeline.drain()
    assert store.get_document(created["id"])["status"] == "failed"
    store.retry(created["id"], reviewer="Testa")
    pipeline.drain()
    assert store.get_document(created["id"])["status"] == "needs_review"


def test_a_blank_page_fails_without_asking_the_model(store, monkeypatch):
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.showPage()
    canvas.save()
    created = store.add_document("blank.pdf", buffer.getvalue(), actor="tester")

    def no_words(*args, **kwargs):
        class Result:
            words: list = []
            settings: dict = {}
        return Result()

    monkeypatch.setattr("review_desk.pagetext.ocr_page", no_words)

    def explode(document, sha):
        raise AssertionError("the model should not be called")

    Pipeline(store, SETTINGS, explode, reader=in_process_reader).drain()
    current = store.get_document(created["id"])
    assert current["status"] == "failed" and current["failure_code"] == "no_text"


def test_a_damaged_pdf_is_refused_with_a_reason(store):
    created = store.add_document("bad.pdf", b"%PDF-1.7\nnot really a pdf\n", actor="tester")
    Pipeline(store, SETTINGS, lambda d, s: None, reader=in_process_reader).drain()
    current = store.get_document(created["id"])
    assert current["status"] == "rejected" and current["failure_code"] == "unreadable"


def test_interrupted_reads_go_back_in_the_queue(store):
    created = store.add_document("a.pdf", b"%PDF-1.4 x", actor="tester")
    assert store.claim_next() == created["id"]
    assert store.claim_next() is None
    assert store.requeue_interrupted() == 1
    assert store.get_document(created["id"])["status"] == "queued"
    assert "reading_interrupted" in [event["action"] for event in store.audit.events()]


def test_each_document_is_claimed_only_once(store):
    store.add_document("a.pdf", b"%PDF-1.4 a", actor="tester")
    assert store.claim_next() is not None
    assert store.claim_next() is None


def test_the_child_process_reads_a_real_pdf(store, tmp_path):
    pdf, truth = _clean_pdf()
    path = tmp_path / "doc.pdf"
    path.write_bytes(pdf)
    outcome = read_isolated(path, store.pages, "abc", SETTINGS, timeout=120)
    assert outcome["pages"][0]["source"] == "text_layer"
    assert (store.pages / outcome["pages"][0]["image"]).is_file()
    assert any(word[0] for word in outcome["document"]["pages"][0]["words"])


def test_the_child_process_reports_a_refusal(tmp_path, store):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7\nnot really a pdf\n")
    with pytest.raises(ReaderFailed) as caught:
        read_isolated(path, store.pages, "abc", SETTINGS, timeout=120)
    assert caught.value.code == "unreadable" and caught.value.rejected


def test_a_pdf_that_hangs_the_reader_is_killed_at_the_time_limit(tmp_path, store):
    """The parent has to get control back. An unreasonably short limit stands in for a hostile file."""
    pdf, _ = _clean_pdf()
    path = tmp_path / "doc.pdf"
    path.write_bytes(pdf)
    started = time.monotonic()
    with pytest.raises(ReaderFailed) as caught:
        read_isolated(path, store.pages, "abc", SETTINGS, timeout=0.05)
    assert caught.value.code == "reader_timeout"
    assert time.monotonic() - started < 30


def test_a_worker_thread_finishes_queued_documents_and_stops(store):
    pdf, truth = _clean_pdf()
    created = store.add_document("a.pdf", pdf, actor="tester")
    worker = Worker(Pipeline(store, SETTINGS, lambda document, sha: _reading_from(truth), reader=in_process_reader))
    worker.start()
    worker.notify()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and store.get_document(created["id"])["status"] != "needs_review":
        time.sleep(0.1)
    worker.stop()
    worker.join(timeout=10)
    assert store.get_document(created["id"])["status"] == "needs_review"
    assert not worker.is_alive()


def test_replay_answers_from_a_finished_run_and_refuses_files_it_never_saw(tmp_path):
    run = tmp_path / "run"
    (run / "readings").mkdir(parents=True)
    reading = ModelReading.model_validate(invoice_reading())
    (run / "readings" / "one.json").write_text(json.dumps({"doc_id": "one", "pdf_sha256": "abc", "reading": reading.model_dump(), "seconds": 3.0}), encoding="utf-8")
    (run / "readings" / "two.json").write_text(json.dumps({"doc_id": "two", "pdf_sha256": "def", "reading": None, "error_code": "timeout"}), encoding="utf-8")
    step = replay_model_step(run)
    assert step(None, "abc").reading.total.text == "$72.05"
    missing = step(None, "zzz")
    assert missing.reading is None and missing.error_code == "no_saved_answer" and "1 documents" in missing.error_message
    assert step(None, "def").reading is None


def test_the_committed_first_run_replays(tmp_path):
    """The numbers in the README come from this run, and the review screen can replay it."""
    run = Path(__file__).resolve().parent.parent / "results" / "runs" / "dev-first"
    step = replay_model_step(run)
    payload = json.loads(next((run / "readings").glob("*.clean.json")).read_text(encoding="utf-8"))
    assert step(None, payload["pdf_sha256"]).reading is not None
