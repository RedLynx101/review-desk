"""The audit log catches tampering, and says where."""

from __future__ import annotations

import sqlite3

import pytest

from review_desk.desk.__main__ import main as desk_main
from review_desk.desk.audit import GENESIS
from review_desk.desk.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "desk")
    for number in range(1, 6):
        document = instance.add_document(f"file-{number}.pdf", b"%PDF-1.4 test " + bytes([number]), actor="tester")
        assert document["status"] == "queued"
    yield instance
    instance.close()


def _drop_guards(store: Store) -> sqlite3.Connection:
    """Someone with the database file doesn't have to respect its triggers."""
    db = store.raw_connection()
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("DROP TRIGGER audit_no_delete")
    return db


def test_an_untouched_chain_verifies(store):
    status = store.audit.verify()
    assert status.ok and status.events == 5 and status.problem is None
    assert status.head == store.audit.head()["hash"] != GENESIS


def test_an_empty_log_verifies(tmp_path):
    empty = Store(tmp_path / "empty")
    status = empty.audit.verify()
    assert status.ok and status.events == 0 and status.head == GENESIS
    empty.close()


def test_each_event_links_to_the_one_before(store):
    events = list(reversed(store.audit.events()))
    assert events[0]["prev_hash"] == GENESIS
    for earlier, later in zip(events, events[1:]):
        assert later["prev_hash"] == earlier["hash"]
        assert later["seq"] == earlier["seq"] + 1


def test_the_database_refuses_changes_to_events(store):
    db = store.raw_connection()
    with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
        db.execute("UPDATE audit_events SET actor = 'someone else' WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        db.execute("DELETE FROM audit_events WHERE seq = 2")
    assert store.audit.verify().ok


def test_a_changed_detail_is_caught_at_that_event(store):
    db = _drop_guards(store)
    db.execute("UPDATE audit_events SET detail_json = '{\"filename\":\"innocent.pdf\"}' WHERE seq = 3")
    status = store.audit.verify()
    assert not status.ok and status.first_bad_seq == 3 and "changed" in status.problem


@pytest.mark.parametrize("column,value", [("actor", "someone else"), ("action", "approved"), ("at", "2020-01-01T00:00:00.000000Z"), ("document_id", "f" * 32)])
def test_every_column_is_covered_by_the_hash(store, column, value):
    db = _drop_guards(store)
    db.execute(f"UPDATE audit_events SET {column} = ? WHERE seq = 2", (value,))
    assert store.audit.verify().first_bad_seq == 2


def test_a_deleted_event_in_the_middle_is_caught(store):
    db = _drop_guards(store)
    db.execute("DELETE FROM audit_events WHERE seq = 3")
    status = store.audit.verify()
    assert not status.ok and status.first_bad_seq == 3 and "missing" in status.problem


def test_events_cut_off_the_end_are_caught_by_the_head_file(store):
    db = _drop_guards(store)
    db.execute("DELETE FROM audit_events WHERE seq >= 4")
    status = store.audit.verify()
    assert not status.ok and "once existed" in status.problem


def test_a_rewritten_newest_event_is_caught(store):
    db = _drop_guards(store)
    db.execute("UPDATE audit_events SET hash = ? WHERE seq = 5", ("a" * 64,))
    assert not store.audit.verify().ok


def test_a_forged_event_that_recomputes_its_hash_still_breaks_the_link_after_it(store):
    from review_desk.desk.audit import event_hash

    db = _drop_guards(store)
    seq, at, actor, action, document_id, detail, prev = db.execute(
        "SELECT seq, at, actor, action, document_id, detail_json, prev_hash FROM audit_events WHERE seq = 2"
    ).fetchone()
    forged_detail = '{"filename":"forged.pdf"}'
    db.execute(
        "UPDATE audit_events SET detail_json = ?, hash = ? WHERE seq = 2",
        (forged_detail, event_hash(prev, seq, at, actor, action, document_id, forged_detail)),
    )
    status = store.audit.verify()
    assert not status.ok and status.first_bad_seq == 3 and "link" in status.problem


def test_two_events_swapped_are_caught(store):
    db = _drop_guards(store)
    db.execute("UPDATE audit_events SET seq = 100 WHERE seq = 2")
    db.execute("UPDATE audit_events SET seq = 2 WHERE seq = 3")
    db.execute("UPDATE audit_events SET seq = 3 WHERE seq = 100")
    assert not store.audit.verify().ok


def test_a_failed_action_leaves_no_event(store):
    from review_desk.desk.store import ReviewError

    before = store.audit.head()["seq"]
    document = store.list_documents()[0]
    with pytest.raises(ReviewError):
        store.approve(document["id"], reviewer="Testa", revision=0)
    assert store.audit.head()["seq"] == before


def test_verify_command_reports_and_sets_its_exit_code(tmp_path, capsys):
    root = tmp_path / "desk"
    instance = Store(root)
    instance.add_document("a.pdf", b"%PDF-1.4 a", actor="tester")
    instance.add_document("b.pdf", b"%PDF-1.4 b", actor="tester")
    head = instance.audit.head()["hash"]
    db = _drop_guards(instance)
    assert desk_main(["verify", "--data", str(root)]) == 0
    assert head in capsys.readouterr().out

    db.execute("UPDATE audit_events SET actor = 'someone else' WHERE seq = 1")
    db.commit()
    assert desk_main(["verify", "--data", str(root)]) == 1
    assert "BROKEN" in capsys.readouterr().out
    instance.close()


def test_verify_command_on_a_missing_log(tmp_path, capsys):
    assert desk_main(["verify", "--data", str(tmp_path / "nothing")]) == 2
