"""The review rules, enforced by the server, tested through the HTTP layer a browser would use."""

from __future__ import annotations

import csv
import io

import pytest

from review_desk.config import Settings
from review_desk.desk.app import create_app
from review_desk.desk.store import Store
from review_desk.pipeline import ModelRead, build_result
from review_desk.schema import ExtractionResult, ModelReading

from helpers import INVOICE_ROWS, AsgiClient, document, invoice_reading

REVIEWER = "Testa McExample"
SETTINGS = Settings()


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "desk")
    yield instance
    instance.close()


@pytest.fixture
def client(store):
    return AsgiClient(create_app(store, SETTINGS))


def extraction(rows=INVOICE_ROWS, **overrides) -> ExtractionResult:
    reading = ModelReading.model_validate(invoice_reading(**overrides))
    return build_result(document(rows), ModelRead(reading, None, None, None), SETTINGS)


def add_ready(store, rows=INVOICE_ROWS, name="invoice.pdf", **overrides) -> str:
    """A document with a stored extraction, as if the worker had finished it."""
    created = store.add_document(name, b"%PDF-1.4 synthetic test bytes " + name.encode(), actor="tester")
    assert store.claim_next() == created["id"]
    store.store_extraction(created["id"], extraction(rows, **overrides))
    return created["id"]


def get(client, document_id) -> dict:
    return client.get(f"/api/documents/{document_id}").json()


def act(client, method, path, **body):
    body.setdefault("reviewer", REVIEWER)
    return client.request(method, path, json_body=body)


def resolve_all(client, document_id) -> dict:
    """Confirm everything flagged and acknowledge failed checks, the way a reviewer would."""
    current = get(client, document_id)
    for path in current["remaining"]:
        current = act(client, "POST", f"/api/documents/{document_id}/fields/{path}/confirm", revision=current["revision"]).json()
    for name in current["unacknowledged_checks"]:
        current = act(client, "POST", f"/api/documents/{document_id}/checks/{name}/acknowledge", revision=current["revision"]).json()
    return current


def approve(client, document_id):
    current = resolve_all(client, document_id)
    response = act(client, "POST", f"/api/documents/{document_id}/approve", revision=current["revision"])
    assert response.status == 200, response.body
    return response.json()


# --- Export needs approval ---------------------------------------------------------------------


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_export_is_refused_before_approval(client, store, extension):
    document_id = add_ready(store)
    response = client.get(f"/api/documents/{document_id}/export.{extension}")
    assert response.status == 409 and response.json()["code"] == "not_approved"
    assert b"Fernwhistle" not in response.body


def test_export_works_after_approval_and_carries_provenance(client, store):
    document_id = add_ready(store)
    approve(client, document_id)
    response = client.get(f"/api/documents/{document_id}/export.json", headers={"x-reviewer": REVIEWER})
    assert response.status == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["fields"]["total"] == "72.05"
    assert payload["fields"]["issue_date"] == "2026-03-05"
    assert [row["amount"] for row in payload["line_items"]] == ["40.00", "25.50"]
    provenance = payload["provenance"]
    assert provenance["approved_by"] == REVIEWER
    assert provenance["audit_head"] == store.audit.head()["hash"]
    assert provenance["source_sha256"] == get(client, document_id)["sha256"]


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_a_later_edit_voids_the_approval_and_export_stops(client, store, extension):
    document_id = add_ready(store)
    approved = approve(client, document_id)
    assert client.get(f"/api/documents/{document_id}/export.{extension}").status == 200

    edited = act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="99.00", revision=approved["revision"])
    assert edited.status == 200
    assert edited.json()["approval"] is None and edited.json()["status"] == "needs_review"

    refused = client.get(f"/api/documents/{document_id}/export.{extension}")
    assert refused.status == 409 and refused.json()["code"] == "not_approved"


def test_approving_again_after_an_edit_exports_the_new_value(client, store):
    document_id = add_ready(store)
    approved = approve(client, document_id)
    act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="99.00", revision=approved["revision"])
    approve(client, document_id)
    payload = client.get(f"/api/documents/{document_id}/export.json").json()
    assert payload["fields"]["total"] == "99.00"


def test_an_edit_that_changes_nothing_keeps_the_approval_and_a_new_row_loses_it(client, store):
    document_id = add_ready(store)
    approved = approve(client, document_id)
    same = act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="72.05", revision=approved["revision"]).json()
    assert same["approval"] is not None
    added = act(client, "POST", f"/api/documents/{document_id}/rows", revision=same["revision"]).json()
    assert added["approval"] is None


def test_deleting_a_row_voids_the_approval(client, store):
    document_id = add_ready(store)
    approved = approve(client, document_id)
    deleted = act(client, "DELETE", f"/api/documents/{document_id}/rows/1", revision=approved["revision"])
    assert deleted.status == 200 and deleted.json()["approval"] is None
    assert client.get(f"/api/documents/{document_id}/export.json").status == 409


def test_a_value_changed_behind_the_apps_back_stops_export_and_is_logged(client, store):
    """Someone edits the database directly. The approval hash no longer matches what's there."""
    document_id = add_ready(store)
    approve(client, document_id)
    store.raw_connection().execute("UPDATE fields SET value = '1.00' WHERE document_id = ? AND path = 'total'", (document_id,))
    refused = client.get(f"/api/documents/{document_id}/export.json")
    assert refused.status == 409 and refused.json()["code"] == "approval_mismatch"
    actions = [event["action"] for event in store.audit.events(document_id=document_id)]
    assert actions[0] == "approval_voided" and actions[1] == "export_refused"
    assert get(client, document_id)["approval"] is None


def test_a_field_row_inserted_behind_the_apps_back_stops_export(client, store):
    document_id = add_ready(store)
    approve(client, document_id)
    store.raw_connection().execute(
        "INSERT INTO fields (document_id, path, kind, row_key, position, value, state) VALUES (?, 'row.99.amount', 'amount', 99, 999, '5.00', 'auto')",
        (document_id,),
    )
    assert client.get(f"/api/documents/{document_id}/export.json").status == 409


def test_a_document_nobody_has_seen_cannot_be_exported_even_if_nothing_is_flagged(client, store):
    document_id = add_ready(store)
    resolve_all(client, document_id)
    assert client.get(f"/api/documents/{document_id}/export.json").status == 409


# --- Approval rules ----------------------------------------------------------------------------


def test_approval_is_refused_while_flagged_values_remain(client, store):
    document_id = add_ready(store, document_number={"text": "ZZ-NOT-ON-PAGE", "line": 3})
    current = get(client, document_id)
    assert "document_number" in current["remaining"]
    response = act(client, "POST", f"/api/documents/{document_id}/approve", revision=current["revision"])
    assert response.status == 409 and response.json()["code"] == "flagged_values_remain"
    assert "document_number" in response.json()["remaining"]
    assert get(client, document_id)["approval"] is None


def test_approval_is_refused_while_a_failed_check_is_unacknowledged(client, store):
    rows = [row.replace("Total    $72.05", "Total    $72.95") for row in INVOICE_ROWS]
    document_id = add_ready(store, rows=rows, total={"text": "$72.95", "line": 12})
    current = get(client, document_id)
    assert current["unacknowledged_checks"] == ["totals_sum"]
    for path in current["remaining"]:
        current = act(client, "POST", f"/api/documents/{document_id}/fields/{path}/confirm", revision=current["revision"]).json()
    refused = act(client, "POST", f"/api/documents/{document_id}/approve", revision=current["revision"])
    assert refused.status == 409 and refused.json()["code"] == "checks_not_acknowledged"
    current = act(client, "POST", f"/api/documents/{document_id}/checks/totals_sum/acknowledge", revision=current["revision"]).json()
    assert act(client, "POST", f"/api/documents/{document_id}/approve", revision=current["revision"]).status == 200


def test_a_stale_revision_cannot_approve(client, store):
    """Two people have the screen open. The one looking at old values can't approve them."""
    document_id = add_ready(store)
    current = resolve_all(client, document_id)
    old_revision = current["revision"]
    act(client, "PATCH", f"/api/documents/{document_id}/fields/vendor_name", value="Changed Co.", revision=old_revision)
    stale = act(client, "POST", f"/api/documents/{document_id}/approve", revision=old_revision)
    assert stale.status == 409 and stale.json()["code"] == "stale_revision"
    assert stale.json()["current_revision"] == old_revision + 1


def test_approval_must_name_a_revision(client, store):
    document_id = add_ready(store)
    resolve_all(client, document_id)
    response = act(client, "POST", f"/api/documents/{document_id}/approve")
    assert response.status == 400 and response.json()["code"] == "revision_required"


@pytest.mark.parametrize("reviewer", [None, "", "   "])
def test_every_review_action_needs_a_name(client, store, reviewer):
    document_id = add_ready(store)
    current = get(client, document_id)
    for method, path, extra in (
        ("POST", "approve", {"revision": current["revision"]}),
        ("PATCH", "fields/total", {"value": "1.00"}),
        ("POST", "fields/total/confirm", {}),
        ("POST", "rows", {}),
        ("DELETE", "rows/1", {}),
    ):
        response = client.request(method, f"/api/documents/{document_id}/{path}", json_body={"reviewer": reviewer, **extra})
        assert response.status == 400, (path, response.body)
        assert response.json()["code"] == "reviewer_required"


def test_approving_twice_is_refused(client, store):
    document_id = add_ready(store)
    approved = approve(client, document_id)
    assert act(client, "POST", f"/api/documents/{document_id}/approve", revision=approved["revision"]).status == 409


def test_documents_still_being_read_cannot_be_reviewed(client, store):
    created = store.add_document("new.pdf", b"%PDF-1.4 x", actor="tester")
    for method, path, extra in (("POST", "approve", {"revision": 0}), ("POST", "rows", {})):
        assert act(client, method, f"/api/documents/{created['id']}/{path}", **extra).status == 409
    assert client.get(f"/api/documents/{created['id']}/export.json").status == 409


# --- Editing -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path,typed,stored", [
    ("total", "$1,234.50", "1234.50"),
    ("total", "1.234,50 €", "1234.50"),
    ("issue_date", "March 5, 2026", "2026-03-05"),
    ("issue_date", "13/03/2026", "2026-03-13"),
    ("currency", "usd", "USD"),
    ("document_type", "Receipt", "receipt"),
    ("vendor_name", "  Two   Spaces  Co. ", "Two Spaces Co."),
    ("purchase_order", "", None),
])
def test_edits_are_normalized(client, store, path, typed, stored):
    document_id = add_ready(store)
    response = act(client, "PATCH", f"/api/documents/{document_id}/fields/{path}", value=typed)
    assert response.status == 200
    field = next(f for f in response.json()["fields"] if f["path"] == path)
    assert field["value"] == stored and field["state"] == "edited"


@pytest.mark.parametrize("path,typed", [
    ("total", "lots"),
    ("total", "1,234"),
    ("issue_date", "soon"),
    ("issue_date", "05/03/2026"),
    ("currency", "dollars"),
    ("document_type", "memo"),
])
def test_bad_edits_are_refused_with_a_reason_and_change_nothing(client, store, path, typed):
    document_id = add_ready(store)
    before = next(f for f in get(client, document_id)["fields"] if f["path"] == path)
    response = act(client, "PATCH", f"/api/documents/{document_id}/fields/{path}", value=typed)
    assert response.status == 422 and response.json()["code"] == "invalid_value" and response.json()["message"]
    assert next(f for f in get(client, document_id)["fields"] if f["path"] == path) == before


def test_an_edit_reruns_the_sums(client, store):
    rows = [row.replace("Total    $72.05", "Total    $72.95") for row in INVOICE_ROWS]
    document_id = add_ready(store, rows=rows, total={"text": "$72.95", "line": 12})
    assert get(client, document_id)["unacknowledged_checks"] == ["totals_sum"]
    fixed = act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="72.05").json()
    assert fixed["unacknowledged_checks"] == []
    assert next(c for c in fixed["checks"] if c["name"] == "totals_sum")["outcome"] == "pass"


def test_an_acknowledgment_does_not_survive_a_different_failure(client, store):
    rows = [row.replace("Total    $72.05", "Total    $72.95") for row in INVOICE_ROWS]
    document_id = add_ready(store, rows=rows, total={"text": "$72.95", "line": 12})
    act(client, "POST", f"/api/documents/{document_id}/checks/totals_sum/acknowledge")
    assert get(client, document_id)["unacknowledged_checks"] == []
    changed = act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="80.00").json()
    assert changed["unacknowledged_checks"] == ["totals_sum"]


def test_a_check_that_did_not_fail_cannot_be_acknowledged(client, store):
    document_id = add_ready(store)
    assert act(client, "POST", f"/api/documents/{document_id}/checks/totals_sum/acknowledge").status == 409


def test_a_new_row_starts_flagged_and_blocks_approval_until_filled(client, store):
    document_id = add_ready(store)
    resolve_all(client, document_id)
    added = act(client, "POST", f"/api/documents/{document_id}/rows").json()
    assert sorted(added["remaining"]) == ["row.3.amount", "row.3.description"]
    assert act(client, "POST", f"/api/documents/{document_id}/approve", revision=added["revision"]).status == 409
    act(client, "PATCH", f"/api/documents/{document_id}/fields/row.3.description", value="Found by a person")
    filled = act(client, "PATCH", f"/api/documents/{document_id}/fields/row.3.amount", value="5.00").json()
    assert filled["remaining"] == []


def test_deleting_a_row_reruns_the_column_sum(client, store):
    document_id = add_ready(store)
    deleted = act(client, "DELETE", f"/api/documents/{document_id}/rows/2").json()
    assert not any(f["row_key"] == 2 for f in deleted["fields"])
    assert deleted["unacknowledged_checks"] == ["items_sum"]


def test_confirming_keeps_the_value_and_records_it(client, store):
    document_id = add_ready(store, document_number={"text": "ZZ-NOT-ON-PAGE", "line": 3})
    confirmed = act(client, "POST", f"/api/documents/{document_id}/fields/document_number/confirm").json()
    field = next(f for f in confirmed["fields"] if f["path"] == "document_number")
    assert field["value"] == "ZZ-NOT-ON-PAGE" and field["state"] == "confirmed"


def test_unknown_things_are_404(client, store):
    document_id = add_ready(store)
    assert client.get("/api/documents/" + "f" * 32).status == 404
    assert client.get("/api/documents/not-an-id").status == 404
    assert client.get(f"/api/documents/{document_id}/pages/7").status == 404
    assert act(client, "PATCH", f"/api/documents/{document_id}/fields/nonsense", value="1").status == 404
    assert act(client, "DELETE", f"/api/documents/{document_id}/rows/77").status == 404
    assert act(client, "POST", f"/api/documents/{document_id}/checks/nonsense/acknowledge").status == 404


# --- Audit trail -------------------------------------------------------------------------------


def test_the_audit_log_tells_the_whole_story(client, store):
    document_id = add_ready(store, document_number={"text": "ZZ-NOT-ON-PAGE", "line": 3})
    act(client, "PATCH", f"/api/documents/{document_id}/fields/document_number", value="A1047")
    approved = approve(client, document_id)
    client.get(f"/api/documents/{document_id}/export.csv", headers={"x-reviewer": REVIEWER})
    act(client, "PATCH", f"/api/documents/{document_id}/fields/total", value="99.00", revision=approved["revision"])
    client.get(f"/api/documents/{document_id}/export.csv", headers={"x-reviewer": REVIEWER})

    payload = client.get(f"/api/audit?document={document_id}").json()
    events = list(reversed(payload["events"]))
    assert [event["action"] for event in events] == [
        "document_received", "reading_started", "extraction_stored", "field_edited", "approved", "exported",
        "field_edited", "approval_voided", "export_refused",
    ]
    edit = events[3]
    assert edit["actor"] == REVIEWER
    assert edit["detail"] == {"field": "document_number", "old": "ZZ-NOT-ON-PAGE", "new": "A1047"}
    assert events[4]["actor"] == REVIEWER and events[4]["detail"]["content_hash"]
    assert events[7]["detail"]["reason"] == "total was edited"
    verified = client.get("/api/audit/verify").json()
    assert verified["ok"] and verified["events"] == len(events)


def test_verify_endpoint_reports_tampering(client, store):
    add_ready(store)
    db = store.raw_connection()
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit_events SET actor = 'someone else' WHERE seq = 1")
    verified = client.get("/api/audit/verify").json()
    assert not verified["ok"] and verified["first_bad_seq"] == 1


# --- Exports hold up against hostile document text -----------------------------------------------


def test_csv_neutralizes_formulas_a_document_might_carry(client, store):
    document_id = add_ready(store)
    act(client, "PATCH", f"/api/documents/{document_id}/fields/vendor_name", value='=HYPERLINK("http://evil.example","x")')
    act(client, "PATCH", f"/api/documents/{document_id}/fields/row.1.description", value="@SUM(A1:A9)")
    approve(client, document_id)
    text = client.get(f"/api/documents/{document_id}/export.csv").body.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["vendor_name"].startswith("'=")
    assert rows[0]["item_description"].startswith("'@")


def test_csv_has_one_row_per_line_item_with_the_document_fields_repeated(client, store):
    document_id = add_ready(store)
    approve(client, document_id)
    text = client.get(f"/api/documents/{document_id}/export.csv").body.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["item_amount"] for row in rows] == ["40.00", "25.50"]
    assert {row["total"] for row in rows} == {"72.05"}
    assert {row["approved_by"] for row in rows} == {REVIEWER}


def test_filenames_are_display_only_and_never_reach_a_path(client, store):
    response = client.post("/api/documents?filename=..%2F..%2Fetc%2Fpasswd.pdf", body=b"%PDF-1.4 x")
    created = response.json()
    assert "/" not in created["filename"] and "\\" not in created["filename"]
    stored = list(store.files.iterdir())
    assert [path.name for path in stored] == [f"{created['id']}.pdf"]


# --- Upload and request guards -------------------------------------------------------------------


def test_upload_stores_and_queues(client, store):
    woken = []
    app_client = AsgiClient(create_app(store, SETTINGS, on_new_document=lambda: woken.append(1)))
    response = app_client.post("/api/documents?filename=a.pdf", body=b"%PDF-1.4 hello", headers={"x-reviewer": REVIEWER})
    assert response.status == 201 and response.json()["status"] == "queued"
    assert woken == [1]
    assert store.audit.events()[0]["actor"] == REVIEWER


@pytest.mark.parametrize("body,status,code", [
    (b"", 400, "empty"),
    (b"MZ not a pdf at all", 415, "not_a_pdf"),
    (b"<html><script>alert(1)</script>", 415, "not_a_pdf"),
])
def test_bad_uploads_are_refused_logged_and_not_stored(client, store, body, status, code):
    response = client.post("/api/documents?filename=x.pdf", body=body)
    assert response.status == status and response.json()["code"] == code
    assert list(store.files.iterdir()) == []
    assert store.audit.events()[0]["action"] == "upload_refused"


def test_an_oversized_upload_stops_at_the_limit(store):
    small = Settings(max_upload_bytes=100)
    client = AsgiClient(create_app(store, small))
    response = client.post("/api/documents?filename=x.pdf", body=b"%PDF-1.4 " + b"x" * 200)
    assert response.status == 413 and response.json()["code"] == "too_large"
    assert list(store.files.iterdir()) == []


def test_state_changing_requests_need_the_app_header(client, store):
    """A cross-site form post can't set a custom header, and a preflight would never be approved."""
    response = client.post("/api/documents?filename=x.pdf", body=b"%PDF-1.4 x", headers={"x-review-desk": None})
    assert response.status == 403 and response.json()["code"] == "missing_header"
    assert list(store.files.iterdir()) == []


def test_requests_from_another_origin_are_refused(client, store):
    response = client.post("/api/documents?filename=x.pdf", body=b"%PDF-1.4 x", headers={"origin": "https://evil.example"})
    assert response.status == 403 and response.json()["code"] == "bad_origin"


def test_the_apps_own_origin_is_accepted(client):
    response = client.post("/api/documents?filename=x.pdf", body=b"%PDF-1.4 x", headers={"origin": "http://127.0.0.1:8765"})
    assert response.status == 201


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example:8765"])
def test_a_rebound_hostname_is_refused(client, host):
    response = client.get("/api/documents", host=host)
    assert response.status == 403 and response.json()["code"] == "bad_host"


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "localhost"])
def test_local_addresses_are_accepted(client, host):
    assert client.get("/api/documents", host=host).status == 200


def test_every_response_carries_the_security_headers(client):
    for path in ("/", "/api/documents", "/api/documents/nope", "/api/audit"):
        headers = client.get(path).headers
        policy = headers["content-security-policy"]
        assert "default-src 'none'" in policy and "script-src 'self'" in policy and "unsafe" not in policy
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"


def test_oversized_json_is_refused(client, store):
    document_id = add_ready(store)
    response = client.request("PATCH", f"/api/documents/{document_id}/fields/total", body=b'{"value": "' + b"x" * 70000 + b'"}')
    assert response.status == 413


def test_malformed_json_is_refused(client, store):
    document_id = add_ready(store)
    assert client.request("PATCH", f"/api/documents/{document_id}/fields/total", body=b"{nope").status == 400
    assert client.request("PATCH", f"/api/documents/{document_id}/fields/total", body=b"[1,2]").status == 400


# --- Queue listing -------------------------------------------------------------------------------


def test_queue_lists_documents_with_what_a_reviewer_needs(client, store):
    document_id = add_ready(store, document_number={"text": "ZZ-NOT-ON-PAGE", "line": 3})
    store.add_document("waiting.pdf", b"%PDF-1.4 w", actor="tester")
    listed = client.get("/api/documents").json()["documents"]
    assert {entry["status"] for entry in listed} == {"queued", "needs_review"}
    ready = next(entry for entry in listed if entry["id"] == document_id)
    assert ready["vendor_name"] == "Fernwhistle Gasket Works LLC" and ready["total"] == "72.05" and ready["flagged"] >= 1
    only_ready = client.get("/api/documents?status=needs_review").json()["documents"]
    assert [entry["id"] for entry in only_ready] == [document_id]


def test_extraction_that_failed_is_kept_with_its_reason_and_can_be_retried_or_typed_in(client, store):
    created = store.add_document("bad.pdf", b"%PDF-1.4 x", actor="tester")
    store.claim_next()
    store.mark_failed(created["id"], code="no_text", message="No readable text was found on any page.")
    current = get(client, created["id"])
    assert current["status"] == "failed" and "readable text" in current["failure"]

    assert act(client, "POST", f"/api/documents/{created['id']}/retry").json()["status"] == "queued"
    store.claim_next()
    store.mark_failed(created["id"], code="no_text", message="No readable text was found on any page.")

    manual = act(client, "POST", f"/api/documents/{created['id']}/manual").json()
    assert manual["status"] == "needs_review"
    assert set(manual["remaining"]) == {"document_type", "vendor_name", "total"}
    assert all(field["value"] is None for field in manual["fields"])
    for path, value in (("document_type", "invoice"), ("vendor_name", "Typed In Ltd"), ("total", "10.00")):
        manual = act(client, "PATCH", f"/api/documents/{created['id']}/fields/{path}", value=value, revision=manual["revision"]).json()
    assert manual["remaining"] == []


def test_retry_and_manual_entry_only_apply_to_failures(client, store):
    document_id = add_ready(store)
    assert act(client, "POST", f"/api/documents/{document_id}/retry").status == 409
    assert act(client, "POST", f"/api/documents/{document_id}/manual").status == 409


def test_a_failed_model_call_lands_as_a_failed_document(store):
    created = store.add_document("x.pdf", b"%PDF-1.4 x", actor="tester")
    store.claim_next()
    failed = build_result(document(INVOICE_ROWS), ModelRead(None, "timeout", "No answer from the model within 1200 seconds.", None), SETTINGS)
    store.store_extraction(created["id"], failed)
    current = store.get_document(created["id"])
    assert current["status"] == "failed" and "1200 seconds" in current["failure"]
