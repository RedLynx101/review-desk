"""A hash-chained audit log.

Each event's hash covers its own content and the previous event's hash, so changing, removing or
reordering any event breaks every link after it. The newest hash is also written to a small file beside
the database. That catches the one thing a chain can't see from the inside: events cut off the end.

What this doesn't do: someone who can rewrite the database and the head file together can forge a new
chain. To rule that out, keep a copy of the head hash somewhere else. Every export carries the head
hash at that moment, which gives you exactly that.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def canonical(value: object) -> str:
    """One exact text form for hashing: sorted keys, no spacing choices, no float surprises."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(prev_hash: str, seq: int, at: str, actor: str, action: str, document_id: str | None, detail_json: str) -> str:
    body = canonical({"prev": prev_hash, "seq": seq, "at": at, "actor": actor, "action": action, "document": document_id, "detail": detail_json})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class ChainStatus:
    ok: bool
    events: int
    head: str
    problem: str | None = None
    first_bad_seq: int | None = None


class AuditLog:
    """Writes happen inside the caller's transaction, so a state change and its event land together."""

    def __init__(self, connection: sqlite3.Connection, head_file: Path | None = None):
        self._connection = connection
        self._head_file = head_file

    def append(self, action: str, *, actor: str, document_id: str | None = None, detail: dict | None = None) -> dict:
        row = self._connection.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev_hash = (row[0] + 1, row[1]) if row else (1, GENESIS)
        at = utc_now()
        detail_json = canonical(detail or {})
        digest = event_hash(prev_hash, seq, at, actor, action, document_id, detail_json)
        self._connection.execute(
            "INSERT INTO audit_events (seq, at, actor, action, document_id, detail_json, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, at, actor, action, document_id, detail_json, prev_hash, digest),
        )
        return {"seq": seq, "at": at, "actor": actor, "action": action, "document_id": document_id, "detail": detail or {}, "hash": digest}

    def write_head(self) -> None:
        """Record the newest hash. Call after the transaction holding the events has committed."""
        if self._head_file is None:
            return
        head = self.head()
        temporary = self._head_file.with_name(self._head_file.name + ".tmp")
        temporary.write_text(f"{head['seq']} {head['hash']}\n", encoding="utf-8")
        os.replace(temporary, self._head_file)

    def head(self) -> dict:
        row = self._connection.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        return {"seq": row[0], "hash": row[1]} if row else {"seq": 0, "hash": GENESIS}

    def events(self, *, document_id: str | None = None, limit: int = 500) -> list[dict]:
        query = "SELECT seq, at, actor, action, document_id, detail_json, prev_hash, hash FROM audit_events"
        arguments: tuple = ()
        if document_id:
            query += " WHERE document_id = ?"
            arguments = (document_id,)
        rows = self._connection.execute(query + " ORDER BY seq DESC LIMIT ?", (*arguments, limit)).fetchall()
        return [
            {
                "seq": row[0], "at": row[1], "actor": row[2], "action": row[3], "document_id": row[4],
                "detail": json.loads(row[5]), "prev_hash": row[6], "hash": row[7],
            }
            for row in rows
        ]

    def verify(self) -> ChainStatus:
        """Recompute the whole chain from the first event and compare the end with the head file."""
        expected_prev = GENESIS
        expected_seq = 1
        count = 0
        rows = self._connection.execute(
            "SELECT seq, at, actor, action, document_id, detail_json, prev_hash, hash FROM audit_events ORDER BY seq ASC"
        )
        for seq, at, actor, action, document_id, detail_json, prev_hash, digest in rows:
            if seq != expected_seq:
                return ChainStatus(False, count, expected_prev, f"Event {expected_seq} is missing.", expected_seq)
            if prev_hash != expected_prev:
                return ChainStatus(False, count, expected_prev, f"Event {seq} doesn't link to the event before it.", seq)
            if event_hash(prev_hash, seq, at, actor, action, document_id, detail_json) != digest:
                return ChainStatus(False, count, expected_prev, f"Event {seq} was changed after it was written.", seq)
            expected_prev = digest
            expected_seq += 1
            count += 1
        if self._head_file is not None and self._head_file.is_file():
            recorded_seq, recorded_hash = self._head_file.read_text(encoding="utf-8").split()
            if int(recorded_seq) > count:
                return ChainStatus(False, count, expected_prev, f"The log ends at event {count}, but event {recorded_seq} once existed.", count + 1)
            if int(recorded_seq) == count and recorded_hash != expected_prev:
                return ChainStatus(False, count, expected_prev, "The newest event doesn't match the recorded head.", count)
        return ChainStatus(True, count, expected_prev)
