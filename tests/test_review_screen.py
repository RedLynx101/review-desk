"""The review screen, driven through a real browser against a real server with a stand-in model."""

from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from review_desk.config import Settings  # noqa: E402
from review_desk.desk.app import create_app  # noqa: E402
from review_desk.desk.store import Store  # noqa: E402
from review_desk.desk.worker import Pipeline, Worker  # noqa: E402
from review_desk.pipeline import ModelRead  # noqa: E402
from review_desk.schema import ModelReading  # noqa: E402
from review_desk.synth.content import make_document  # noqa: E402
from review_desk.synth.generate import DEFAULT_SEED, render_all  # noqa: E402

from test_desk_worker import in_process_reader  # noqa: E402

pytestmark = pytest.mark.browser
SETTINGS = Settings(verify_text_layer=False)
REVIEWER = "Testa McExample"


def _launch(playwright):
    """The bundled browser if it's there, otherwise whatever browser the machine already has."""
    channel = os.environ.get("REVIEW_DESK_TEST_BROWSER")
    for attempt in ([channel] if channel else [None, "chrome", "msedge"]):
        try:
            return playwright.chromium.launch(channel=attempt) if attempt else playwright.chromium.launch()
        except playwright_api.Error:
            continue
    pytest.skip("no browser Playwright can start")


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as playwright:
        instance = _launch(playwright)
        yield instance
        instance.close()


class Desk:
    """A running server. The stand-in model answers from each document's known truth."""

    def __init__(self, root, model_step=None):
        self.store = Store(root)
        self.answers: dict[str, ModelRead] = {}
        step = model_step or self.answer
        self.worker = Worker(Pipeline(self.store, SETTINGS, step, reader=in_process_reader))
        self.woken = threading.Event()
        app = create_app(self.store, SETTINGS, on_new_document=self.worker.notify)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.worker.start()
        deadline = time.monotonic() + 15
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"

    def answer(self, document, sha):
        return self.answers[sha]

    def close(self):
        self.worker.stop()
        self.worker.join(timeout=10)
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.store.close()


@pytest.fixture
def desk(tmp_path):
    instance = Desk(tmp_path / "desk")
    yield instance
    instance.close()


def reading_from(truth: dict, **overrides) -> ModelRead:
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
    reading.update(overrides)
    return ModelRead(ModelReading.model_validate(reading), None, None, None)


def load(desk: Desk, tmp_path, *, index=0, condition="clean", name=None, **overrides):
    """Render a synthetic document, teach the stand-in model about it, and put the PDF on disk."""
    import hashlib

    pdf, truth = render_all(make_document("dev", index, DEFAULT_SEED), (condition,))[condition]
    desk.answers[hashlib.sha256(pdf).hexdigest()] = reading_from(truth, **overrides)
    path = tmp_path / (name or f"synthetic-{index}-{condition}.pdf")
    path.write_bytes(pdf)
    return path, truth


class Screen:
    def __init__(self, browser, desk: Desk, *, viewport=(1440, 900), reviewer=REVIEWER):
        self.desk = desk
        self.context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, accept_downloads=True)
        self.page = self.context.new_page()
        self.problems: list[str] = []
        self.requests: list[str] = []
        self.page.on("console", lambda message: self.problems.append(f"{message.type}: {message.text}") if message.type in ("error", "warning") else None)
        self.page.on("pageerror", lambda error: self.problems.append(f"pageerror: {error}"))
        self.page.on("request", lambda request: self.requests.append(request.url))
        if reviewer:
            self.page.add_init_script(f"try {{ localStorage.setItem('reviewer', {json.dumps(reviewer)}); }} catch (e) {{}}")

    def open(self, fragment="#/"):
        self.page.goto(f"{self.desk.url}/{fragment}")

    def upload(self, *paths):
        self.page.set_input_files("#file-input", [str(path) for path in paths])

    def wait_until_read(self, count=1):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            documents = self.desk.store.list_documents()
            if len(documents) >= count and all(d["status"] not in ("queued", "reading") for d in documents):
                return documents
            time.sleep(0.1)
        raise AssertionError("documents didn't finish reading")

    def open_first(self, count=1):
        documents = self.wait_until_read(count)
        self.page.goto(f"{self.desk.url}/#/doc/{documents[0]['id']}")
        self.page.wait_for_selector(".panel")
        return documents[0]["id"]

    def press(self, key):
        self.page.keyboard.press(key)

    def selected_path(self):
        return self.page.locator(".field.selected, .cell.selected").first.get_attribute("data-path")

    def close(self, allow_problems=False):
        if not allow_problems:
            # A 409 or 422 the test provoked shows up as a resource error. Anything else is a real problem.
            real = [text for text in self.problems if "status of 4" not in text]
            assert real == []
        origin = self.desk.url
        assert [url for url in self.requests if not url.startswith((origin, "blob:", "data:"))] == []
        self.context.close()


@pytest.fixture
def screen(browser, desk):
    instance = Screen(browser, desk)
    yield instance
    instance.close()


# --- States ------------------------------------------------------------------------------------


def test_empty_queue_says_what_to_do(screen):
    screen.open()
    assert "Nothing to review yet" in screen.page.locator(".empty").inner_text()
    assert screen.page.locator("#upload").is_visible()


def test_upload_reaches_the_queue_with_its_values(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.wait_until_read()
    screen.page.reload()
    row = screen.page.locator("table.docs tbody tr").first
    row.wait_for()
    text = row.inner_text()
    assert path.name[:18] in text and truth["fields"]["vendor_name"]["value"] in text and "Needs review" in text


def test_a_file_that_is_not_a_pdf_is_refused_with_a_message(screen, tmp_path):
    bad = tmp_path / "notes.pdf"
    bad.write_bytes(b"this is not a pdf")
    screen.open()
    screen.upload(bad)
    screen.page.wait_for_selector(".toast.error")
    assert "isn't a PDF" in screen.page.locator(".toast.error").inner_text()
    assert screen.desk.store.list_documents() == []


def test_failed_extraction_explains_itself_and_offers_a_way_forward(browser, tmp_path):
    def failing(document, sha):
        return ModelRead(None, "model_crashed", "The model process crashed.", None)

    desk = Desk(tmp_path / "desk", model_step=failing)
    screen = Screen(browser, desk)
    try:
        path, _ = load(desk, tmp_path)
        screen.open()
        screen.upload(path)
        documents = screen.wait_until_read()
        screen.page.goto(f"{desk.url}/#/doc/{documents[0]['id']}")
        panel = screen.page.locator("#failed")
        panel.wait_for()
        assert "Extraction failed" in panel.inner_text() and "crashed" in panel.inner_text()
        assert screen.page.locator(".page img").is_visible(), "the page should still be shown"
        assert screen.page.locator("#approve").count() == 0

        # By hand: the required fields start flagged and approval waits for them.
        screen.page.locator("#manual").click()
        screen.page.locator("input.edit").wait_for()
        assert screen.page.locator("#approve").is_disabled()
        for value in ("invoice", "Typed In Ltd", "10.00"):
            screen.page.locator("input.edit").fill(value)
            screen.press("Enter")
            screen.page.wait_for_function("() => !document.querySelector('.edit-error')")
            if value != "10.00":
                screen.page.wait_for_selector(".field.selected")
                screen.press("e")
                screen.page.locator("input.edit").wait_for()
        # No line items were typed in, which the sums point out and a person can accept.
        screen.page.locator('.alert[data-check="items_sum"] button').click()
        screen.page.wait_for_function("() => !document.querySelector('#approve').disabled")
    finally:
        screen.close(allow_problems=True)
        desk.close()


def test_a_slow_read_shows_progress_then_the_result(browser, tmp_path):
    release = threading.Event()
    box = {}

    def slow(document, sha):
        release.wait(30)
        return box["read"]

    desk = Desk(tmp_path / "desk", model_step=slow)
    screen = Screen(browser, desk)
    try:
        path, truth = load(desk, tmp_path)
        box["read"] = reading_from(truth)
        screen.open()
        screen.upload(path)
        screen.page.wait_for_function("fetch('/api/documents').then(r => r.json()).then(d => d.documents.length > 0)")
        documents = desk.store.list_documents()
        screen.page.goto(f"{desk.url}/#/doc/{documents[0]['id']}")
        screen.page.locator("#reading").wait_for()
        assert "Reading" in screen.page.locator("#reading").inner_text() or "Waiting" in screen.page.locator("#reading").inner_text()
        release.set()
        screen.page.locator(".field").first.wait_for(timeout=30000)
    finally:
        release.set()
        screen.close()
        desk.close()


# --- The highlight -----------------------------------------------------------------------------


@pytest.mark.parametrize("condition", ["clean", "scanned"])
def test_the_highlight_lands_on_the_fields_source(browser, tmp_path, condition):
    """Measure in pixels: the highlight's center has to sit inside the value's known location."""
    desk = Desk(tmp_path / "desk")
    screen = Screen(browser, desk)
    try:
        path, truth = load(desk, tmp_path, condition=condition)
        screen.open()
        screen.upload(path)
        screen.open_first()
        hits = 0
        for name in ("total", "subtotal", "issue_date", "vendor_name"):
            location = truth["fields"][name]["locations"][-1]
            screen.page.locator(f'.field[data-path="{name}"]').click()
            screen.page.wait_for_selector(f'.box.selected[data-path="{name}"]')
            geometry = screen.page.evaluate(
                """() => {
                    const box = document.querySelector('.box.selected').getBoundingClientRect();
                    const page = document.querySelector('.box.selected').closest('.page').getBoundingClientRect();
                    return {x: box.x + box.width / 2 - page.x, y: box.y + box.height / 2 - page.y, width: page.width, height: page.height};
                }"""
            )
            page_width, page_height = truth["page_sizes"][location["page"]]
            scale_x, scale_y = geometry["width"] / page_width, geometry["height"] / page_height
            x0, top, x1, bottom = location["bbox"]
            if x0 * scale_x - 6 <= geometry["x"] <= x1 * scale_x + 6 and top * scale_y - 6 <= geometry["y"] <= bottom * scale_y + 6:
                hits += 1
        assert hits >= 3, f"{hits} of 4 highlights were on the right ink"
    finally:
        screen.close()
        desk.close()


def test_clicking_a_box_on_the_page_selects_its_field(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.locator('.box[data-path="total"]').first.click(force=True)
    assert screen.selected_path() == "total"


def test_a_value_not_on_the_page_says_so_instead_of_highlighting_nothing(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path, document_number={"text": "ZZ-NOT-ON-PAGE", "line": None})
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.locator('.field[data-path="document_number"]').click()
    assert "wasn't found on the page" in screen.page.locator("#source-note").inner_text()
    assert screen.page.locator(".box.selected").count() == 0


def test_the_why_panel_shows_the_checks_behind_a_score(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path, document_number={"text": "ZZ-NOT-ON-PAGE", "line": None})
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.locator('.field[data-path="document_number"]').click()
    why = screen.page.locator("#why").inner_text()
    assert "wasn't found anywhere" in why
    assert screen.page.locator(".signals li.fail").count() >= 1


# --- Keyboard ----------------------------------------------------------------------------------


def test_the_whole_review_can_be_done_from_the_keyboard(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path, document_number={"text": "ZZ-NOT-ON-PAGE", "line": None})
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    assert screen.selected_path() == "document_number", "a flagged field should be selected on open"

    # Correct the flagged value.
    screen.press("e")
    edit = screen.page.locator("input.edit")
    edit.wait_for()
    edit.fill(truth["fields"]["document_number"]["value"])
    screen.press("Enter")
    screen.page.locator('.field[data-path="document_number"][data-state="edited"]').wait_for()

    # Everything flagged after that gets a yes.
    for _ in range(80):
        if not screen.desk.store.get_document(document_id)["remaining"]:
            break
        before = len(screen.desk.store.get_document(document_id)["remaining"])
        screen.press("Enter")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(screen.desk.store.get_document(document_id)["remaining"]) == before:
            time.sleep(0.02)
    for name in screen.desk.store.get_document(document_id)["unacknowledged_checks"]:
        screen.page.locator(f'.alert[data-check="{name}"] button').click()

    screen.page.wait_for_function("() => !document.querySelector('#approve').disabled")
    screen.press("a")
    screen.page.locator("#approve-dialog").wait_for()
    screen.press("Enter")
    screen.page.locator("#approved-note").wait_for()
    assert "Approved" in screen.page.locator("#doc-status").inner_text()
    assert screen.desk.store.get_document(document_id)["approval"]["approved_by"] == REVIEWER


def test_movement_keys(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    first = screen.selected_path()
    screen.press("j")
    second = screen.selected_path()
    assert second != first
    screen.press("k")
    assert screen.selected_path() == first
    screen.press("ArrowDown")
    assert screen.selected_path() == second
    screen.press("ArrowUp")
    assert screen.selected_path() == first


def test_n_jumps_to_the_next_flagged_value(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path, document_number={"text": "ZZ-NOT-ON-PAGE", "line": None}, purchase_order={"text": "ZZ-ALSO-MADE-UP", "line": None})
    screen.open()
    screen.upload(path)
    screen.open_first()
    assert screen.selected_path() == "document_number"
    screen.press("n")
    assert screen.selected_path() == "purchase_order"


def test_a_bad_edit_shows_why_and_stays_open_and_escape_cancels(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.locator('.field[data-path="total"]').click()
    screen.press("e")
    edit = screen.page.locator("input.edit")
    edit.fill("lots of money")
    screen.press("Enter")
    screen.page.locator(".edit-error").wait_for()
    assert "isn't an amount" in screen.page.locator(".edit-error").inner_text()
    assert edit.is_visible()
    screen.press("Escape")
    assert screen.page.locator("input.edit").count() == 0
    assert screen.page.locator('.field[data-path="total"] .value').inner_text().replace(",", "") == truth["fields"]["total"]["value"]


def test_typing_in_an_edit_box_does_not_trigger_shortcuts(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.locator('.field[data-path="vendor_name"]').click()
    screen.press("e")
    edit = screen.page.locator("input.edit")
    edit.fill("")
    edit.type("jankena")
    assert edit.input_value() == "jankena"
    assert screen.selected_path() == "vendor_name"
    assert screen.page.locator("#approve-dialog").count() == 0


def test_help_lists_the_keys(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.press("?")
    assert "Approve" in screen.page.locator("#keys-dialog").inner_text()


def test_queue_moves_with_the_keyboard(screen, desk, tmp_path):
    first, _ = load(desk, tmp_path, index=0)
    second, _ = load(desk, tmp_path, index=1)
    screen.open()
    screen.upload(first, second)
    documents = screen.wait_until_read(2)
    screen.page.reload()
    screen.page.locator("table.docs tbody tr").first.wait_for()
    assert screen.page.locator("tr.selected").get_attribute("data-id") == documents[0]["id"]
    screen.press("j")
    assert screen.page.locator("tr.selected").get_attribute("data-id") == documents[1]["id"]
    screen.press("Enter")
    screen.page.wait_for_selector(".panel")
    assert documents[1]["id"] in screen.page.url


# --- Approval and export on screen --------------------------------------------------------------


def _clear_everything(screen, document_id):
    """Use the API to clear flags, since other tests cover doing it by hand."""
    store = screen.desk.store
    current = store.get_document(document_id)
    for path in current["remaining"]:
        current = store.confirm_field(document_id, path, reviewer=REVIEWER)
    for name in current["unacknowledged_checks"]:
        current = store.acknowledge_check(document_id, name, reviewer=REVIEWER)
    screen.page.reload()
    screen.page.wait_for_selector(".panel")


def test_approve_is_blocked_on_screen_while_values_are_flagged(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path, document_number={"text": "ZZ-NOT-ON-PAGE", "line": None})
    screen.open()
    screen.upload(path)
    screen.open_first()
    assert screen.page.locator("#approve").is_disabled()
    assert "left to check" in screen.page.locator("#approve").inner_text()
    assert screen.page.locator("#export-json").is_disabled() and screen.page.locator("#export-csv").is_disabled()


def test_export_is_refused_by_the_server_even_when_the_screen_is_bypassed(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    status = screen.page.evaluate(f"fetch('/api/documents/{document_id}/export.json').then(r => r.status)")
    assert status == 409


def test_export_downloads_after_approval_and_a_later_edit_takes_it_away(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    _clear_everything(screen, document_id)

    screen.page.locator("#approve").click()
    screen.page.locator("#approve-confirm").click()
    screen.page.locator("#approved-note").wait_for()

    with screen.page.expect_download() as download_info:
        screen.page.locator("#export-json").click()
    payload = json.loads(open(download_info.value.path(), encoding="utf-8").read())
    assert payload["fields"]["total"] == truth["fields"]["total"]["value"]
    assert payload["provenance"]["approved_by"] == REVIEWER

    with screen.page.expect_download() as download_info:
        screen.page.locator("#export-csv").click()
    assert download_info.value.suggested_filename.endswith(".csv")

    screen.page.locator('.field[data-path="total"]').click()
    screen.press("e")
    screen.page.locator("input.edit").fill("1.23")
    screen.press("Enter")
    screen.page.wait_for_function("() => document.querySelector('#doc-status').innerText.includes('Needs review')")
    assert screen.page.locator("#export-json").is_disabled()
    assert screen.page.evaluate(f"fetch('/api/documents/{document_id}/export.json').then(r => r.status)") == 409


def test_after_approval_the_next_document_is_one_key_away(screen, desk, tmp_path):
    first, _ = load(desk, tmp_path, index=0)
    second, _ = load(desk, tmp_path, index=1)
    screen.open()
    screen.upload(first, second)
    documents = screen.wait_until_read(2)
    current, other = documents[0]["id"], documents[1]["id"]
    screen.page.goto(f"{desk.url}/#/doc/{current}")
    screen.page.wait_for_selector(".panel")
    _clear_everything(screen, current)
    screen.press("a")
    screen.page.locator("#approve-dialog").wait_for()
    screen.press("Enter")
    screen.page.locator("#next-document").wait_for()
    assert "Next document" in screen.page.locator("#next-document").inner_text()
    screen.press("]")
    screen.page.wait_for_function(f"() => location.hash.includes('{other}')")


def test_amounts_read_like_the_page_while_the_value_stays_plain(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    desk.store.edit_field(document_id, "total", "1234567.50", reviewer=REVIEWER)
    screen.page.reload()
    screen.page.wait_for_selector(".panel")
    assert screen.page.locator('.field[data-path="total"] .value').inner_text() == "1,234,567.50"
    screen.page.locator('.field[data-path="total"]').click()
    screen.press("e")
    assert screen.page.locator("input.edit").input_value() == "1234567.50"


def test_a_failed_check_needs_a_decision_before_approval(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path, total={"text": "999.99", "line": None})
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    assert screen.page.locator('.alert[data-check="totals_sum"]').is_visible()
    store = screen.desk.store
    current = store.get_document(document_id)
    for name in current["remaining"]:
        store.confirm_field(document_id, name, reviewer=REVIEWER)
    screen.page.reload()
    screen.page.wait_for_selector(".panel")
    assert screen.page.locator("#approve").is_disabled()
    screen.page.locator('.alert[data-check="totals_sum"] button').click()
    screen.page.wait_for_function("() => !document.querySelector('#approve').disabled")


def test_line_items_can_be_added_and_removed(screen, desk, tmp_path):
    path, truth = load(desk, tmp_path, index=0)
    screen.open()
    screen.upload(path)
    screen.open_first()
    before = screen.page.locator(".item").count()
    screen.page.locator("#add-row").click()
    screen.page.locator("input.edit").wait_for()
    assert screen.page.locator(".item").count() == before + 1
    screen.page.locator("input.edit").fill("Found by a person")
    screen.press("Enter")
    screen.page.locator(".cell.description", has_text="Found by a person").wait_for()
    screen.page.locator(".cell.description", has_text="Found by a person").click()
    screen.page.locator("#delete-row").click()
    screen.page.wait_for_function(f"() => document.querySelectorAll('.item').length === {before}")


# --- Reviewer identity, the audit screen, hostile text --------------------------------------------


def test_a_first_time_reviewer_is_asked_for_a_name(browser, desk, tmp_path):
    screen = Screen(browser, desk, reviewer=None)
    try:
        path, _ = load(desk, tmp_path)
        screen.open()
        screen.upload(path)
        screen.open_first()
        screen.page.locator('.field[data-path="total"]').click()
        screen.page.locator("#confirm").click()
        name = screen.page.locator("dialog input")
        name.wait_for()
        name.fill("Fictitia Sampleton")
        screen.press("Enter")
        screen.page.wait_for_function("() => document.querySelector('.who').innerText.includes('Fictitia Sampleton')")
        screen.page.locator("#confirm").click()
        screen.page.locator('.field[data-path="total"][data-state="confirmed"]').wait_for()
        event = next(e for e in desk.store.audit.events() if e["action"] == "field_confirmed")
        assert event["actor"] == "Fictitia Sampleton"
    finally:
        screen.close()


def test_audit_screen_shows_the_trail_and_verifies_the_chain(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    document_id = screen.open_first()
    screen.page.locator('.field[data-path="total"]').click()
    screen.press("Enter")
    screen.page.locator('.field[data-path="total"][data-state="confirmed"]').wait_for()

    screen.page.goto(f"{desk.url}/#/audit/{document_id}")
    table = screen.page.locator("table.audit")
    table.wait_for()
    text = table.inner_text()
    assert "Received" in text and "Extraction stored" in text and "Value confirmed" in text and REVIEWER in text

    screen.page.locator("#verify").click()
    screen.page.locator(".verify.good").wait_for()
    assert "Chain intact" in screen.page.locator(".verify.good").inner_text()

    db = desk.store.raw_connection()
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit_events SET actor = 'someone else' WHERE seq = 1")
    screen.page.locator("#verify").click()
    screen.page.locator(".verify.broken").wait_for()
    assert "Chain broken" in screen.page.locator(".verify.broken").inner_text()


def test_hostile_text_from_a_document_stays_text(screen, desk, tmp_path):
    """Filenames and values come from untrusted files. None of it may become markup."""
    trap = '<img src=x onerror="window.__owned=1">'
    path, _ = load(desk, tmp_path, name="invoice.pdf", vendor_name={"text": trap, "line": None}, document_number={"text": "<script>window.__owned=1</script>", "line": None})
    screen.open()
    data = path.read_bytes()
    created = desk.store.add_document(trap + ".pdf", data, actor="tester")
    desk.worker.notify()
    screen.wait_until_read()
    screen.page.goto(f"{desk.url}/#/")
    screen.page.reload()
    screen.page.locator("table.docs tbody tr").first.wait_for()
    assert trap in screen.page.locator("table.docs tbody tr").first.inner_text()
    screen.page.goto(f"{desk.url}/#/doc/{created['id']}")
    screen.page.wait_for_selector(".panel")
    assert screen.page.locator('.field[data-path="vendor_name"] .value').inner_text() == trap
    screen.page.goto(f"{desk.url}/#/audit")
    screen.page.locator("table.audit").wait_for()
    assert screen.page.evaluate("window.__owned") is None
    assert screen.page.locator("img[src='x']").count() == 0


def test_a_page_only_ever_talks_to_its_own_server(screen, desk, tmp_path):
    path, _ = load(desk, tmp_path)
    screen.open()
    screen.upload(path)
    screen.open_first()
    screen.page.goto(f"{desk.url}/#/audit")
    screen.page.locator("table.audit").wait_for()
    assert screen.requests
    assert all(url.startswith(desk.url) for url in screen.requests)


def test_narrow_window_stacks_without_sideways_scrolling(browser, desk, tmp_path):
    screen = Screen(browser, desk, viewport=(820, 900))
    try:
        path, _ = load(desk, tmp_path)
        screen.open()
        screen.upload(path)
        screen.open_first()
        assert screen.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert screen.page.locator(".panel").is_visible() and screen.page.locator(".viewer").is_visible()
    finally:
        screen.close()
