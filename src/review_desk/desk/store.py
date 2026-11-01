"""Documents, fields, approvals and the rules that connect them. The server enforces these, not the UI.

Three things can't be bypassed from the outside:

- Export needs an active approval, and the approval is bound to a hash of the exact values approved.
  An edit voids it. A value changed behind the app's back makes the hash disagree, and export refuses.
- Approval needs every flagged value handled, every failed check acknowledged, and the revision the
  reviewer was looking at, so nobody approves a version they haven't seen.
- Each state change and its audit event commit in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..confidence import check_totals
from ..normalize import ISO_CODES, format_amount, normalize_text, parse_amount, parse_date, parse_quantity
from ..schema import ExtractionResult, FieldResult
from .audit import AuditLog, canonical, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure TEXT,
    pages_json TEXT NOT NULL DEFAULT '[]',
    active_content_json TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    model_seconds REAL,
    threshold REAL,
    revision INTEGER NOT NULL DEFAULT 0,
    last_exported_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_status ON documents (status);
CREATE TABLE IF NOT EXISTS fields (
    document_id TEXT NOT NULL REFERENCES documents (id),
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    row_key INTEGER,
    position INTEGER NOT NULL,
    value TEXT,
    original_value TEXT,
    printed TEXT,
    confidence REAL,
    state TEXT NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (document_id, path)
);
CREATE TABLE IF NOT EXISTS checks (
    document_id TEXT NOT NULL REFERENCES documents (id),
    name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    PRIMARY KEY (document_id, name)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents (id),
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    voided_at TEXT,
    voided_reason TEXT
);
CREATE INDEX IF NOT EXISTS approvals_document ON approvals (document_id, voided_at);
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    document_id TEXT,
    detail_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_document ON audit_events (document_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit events cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit events cannot be deleted'); END;
"""

SYSTEM_ACTOR = "system"
MAX_REVIEWER_LENGTH = 80
MAX_TEXT_LENGTH = 400
DOCUMENT_ID = re.compile(r"^[0-9a-f]{32}$")

#: Field name, kind (decides validation) and display order. The UI groups them.
HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("document_type", "document_type"), ("vendor_name", "text"), ("document_number", "text"),
    ("issue_date", "date"), ("due_date", "date"), ("purchase_order", "text"), ("bill_to_name", "text"),
    ("currency", "currency"), ("subtotal", "amount"), ("discount", "amount"), ("shipping", "amount"),
    ("tax", "amount"), ("total", "amount"),
)
ITEM_CELLS: tuple[tuple[str, str], ...] = (
    ("description", "text"), ("quantity", "quantity"), ("unit_price", "amount"), ("amount", "amount"),
)
#: What a by-hand entry must have before it can be approved. Others may legitimately be blank.
REQUIRED_WHEN_MANUAL = {"document_type", "vendor_name", "total"}


class ReviewError(Exception):
    """The request breaks a review rule. `code` and `status` go straight to the API response."""

    def __init__(self, code: str, message: str, *, status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}


def clean_reviewer(reviewer: str | None) -> str:
    name = re.sub(r"\s+", " ", str(reviewer or "")).strip()
    if not name:
        raise ReviewError("reviewer_required", "Enter your name so the audit log can record who did this.", status=400)
    return name[:MAX_REVIEWER_LENGTH]


def _cell_path(row_key: int, cell: str) -> str:
    return f"row.{row_key}.{cell}"


def normalize_input(kind: str, raw: object) -> str | None:
    """Validate what a person typed. Returns the normalized value, or raises with what to fix."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    if kind == "amount":
        parsed = parse_amount(text)
        if parsed is None:
            raise ReviewError("invalid_value", f"\"{text}\" isn't an amount.", status=422)
        if parsed.ambiguous:
            raise ReviewError("invalid_value", f"\"{text}\" could be read two ways. Write it with a decimal point, like 1234.00.", status=422)
        return format_amount(parsed.value)
    if kind == "quantity":
        parsed_quantity = parse_quantity(text)
        if parsed_quantity is None:
            raise ReviewError("invalid_value", f"\"{text}\" isn't a number.", status=422)
        return format(parsed_quantity.normalize(), "f")
    if kind == "date":
        parsed_date = parse_date(text)
        if parsed_date is None:
            raise ReviewError("invalid_value", f"\"{text}\" isn't a date. Try 2026-03-05.", status=422)
        if parsed_date.alternative is not None:
            raise ReviewError("invalid_value", f"\"{text}\" could be two dates. Write it as year-month-day, like {parsed_date.value.isoformat()}.", status=422)
        return parsed_date.value.isoformat()
    if kind == "currency":
        code = text.upper()
        if code not in ISO_CODES and not re.fullmatch(r"[A-Z]{3}", code):
            raise ReviewError("invalid_value", f"\"{text}\" isn't a three-letter currency code, like USD.", status=422)
        return code
    if kind == "document_type":
        lowered = text.lower()
        if lowered not in ("invoice", "receipt"):
            raise ReviewError("invalid_value", "The type must be invoice or receipt.", status=422)
        return lowered
    cleaned = normalize_text(text)
    if cleaned is not None and len(cleaned) > MAX_TEXT_LENGTH:
        raise ReviewError("invalid_value", f"That's longer than {MAX_TEXT_LENGTH} characters.", status=422)
    return cleaned


@dataclass(frozen=True)
class ExportRecord:
    document_id: str
    filename: str
    sha256: str
    approved_by: str
    approved_at: str
    content_hash: str
    audit_head: str
    fields: dict[str, str | None]
    line_items: list[dict[str, str | None]]


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.files = self.root / "files"
        self.pages = self.root / "pages"
        self.files.mkdir(parents=True, exist_ok=True)
        self.pages.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.root / "desk.db", check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(SCHEMA)
        self.audit = AuditLog(self._connection, self.root / "audit.head")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _transaction(self):
        """One write at a time, all of it or none of it. The audit head updates after commit."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")
            self.audit.write_head()

    # --- Intake and extraction state --------------------------------------------------------

    def add_document(self, filename: str, data: bytes, *, actor: str) -> dict:
        document_id = uuid.uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        safe_name = _safe_filename(filename)
        (self.files / f"{document_id}.pdf").write_bytes(data)
        now = utc_now()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO documents (id, filename, sha256, byte_size, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (document_id, safe_name, digest, len(data), now, now),
            )
            self.audit.append("document_received", actor=actor, document_id=document_id, detail={"filename": safe_name, "sha256": digest, "bytes": len(data)})
        return self.get_document(document_id)

    def record_refused_upload(self, filename: str, *, code: str, byte_size: int, actor: str) -> None:
        with self._transaction():
            self.audit.append("upload_refused", actor=actor, detail={"filename": _safe_filename(filename), "code": code, "bytes": byte_size})

    def pdf_path(self, document_id: str) -> Path:
        return self.files / f"{document_id}.pdf"

    def claim_next(self) -> str | None:
        """Take the oldest queued document. A conditional update means nobody else also takes it."""
        with self._transaction() as db:
            row = db.execute("SELECT id FROM documents WHERE status = 'queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            claimed = db.execute("UPDATE documents SET status = 'reading', updated_at = ? WHERE id = ? AND status = 'queued'", (utc_now(), row[0]))
            if claimed.rowcount != 1:
                return None
            self.audit.append("reading_started", actor=SYSTEM_ACTOR, document_id=row[0])
            return row[0]

    def requeue_interrupted(self) -> int:
        """After a restart, anything left mid-read goes back in the queue."""
        with self._transaction() as db:
            rows = db.execute("SELECT id FROM documents WHERE status = 'reading'").fetchall()
            for (document_id,) in rows:
                db.execute("UPDATE documents SET status = 'queued', updated_at = ? WHERE id = ?", (utc_now(), document_id))
                self.audit.append("reading_interrupted", actor=SYSTEM_ACTOR, document_id=document_id)
            return len(rows)

    def set_pages(self, document_id: str, pages: list[dict]) -> None:
        with self._lock:
            self._connection.execute("UPDATE documents SET pages_json = ?, updated_at = ? WHERE id = ?", (json.dumps(pages), utc_now(), document_id))

    def mark_failed(self, document_id: str, *, code: str, message: str, status: str = "failed") -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE documents SET status = ?, failure_code = ?, failure = ?, updated_at = ? WHERE id = ?",
                (status, code, message, utc_now(), document_id),
            )
            self.audit.append("reading_failed", actor=SYSTEM_ACTOR, document_id=document_id, detail={"code": code, "message": message, "status": status})

    def store_extraction(self, document_id: str, result: ExtractionResult) -> None:
        """Save what the pipeline found. Everything from here on is the person's to confirm or fix."""
        if result.status != "ok":
            self.mark_failed(document_id, code="extraction_failed", message=result.failure or "The extraction failed.")
            return
        with self._transaction() as db:
            db.execute("DELETE FROM fields WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM checks WHERE document_id = ?", (document_id,))
            position = 0
            for name, kind in HEADER_FIELDS:
                self._insert_field(db, document_id, name, kind, None, position, result.fields.get(name))
                position += 1
            for item in result.line_items:
                row_key = item.index + 1
                for cell, kind in ITEM_CELLS:
                    self._insert_field(db, document_id, _cell_path(row_key, cell), kind, row_key, position, item.cells.get(cell))
                    position += 1
            for check in result.checks:
                db.execute(
                    "INSERT INTO checks (document_id, name, outcome, detail) VALUES (?, ?, ?, ?)",
                    (document_id, check.name, check.outcome, check.detail),
                )
            db.execute(
                "UPDATE documents SET status = 'needs_review', failure_code = NULL, failure = NULL, active_content_json = ?, model = ?, model_seconds = ?, threshold = ?, updated_at = ? WHERE id = ?",
                (json.dumps(result.active_content), result.model, result.model_seconds, result.review_threshold, utc_now(), document_id),
            )
            flagged = db.execute("SELECT COUNT(*) FROM fields WHERE document_id = ? AND state = 'flagged'", (document_id,)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM fields WHERE document_id = ?", (document_id,)).fetchone()[0]
            self.audit.append("extraction_stored", actor=SYSTEM_ACTOR, document_id=document_id, detail={"model": result.model, "values": total, "flagged": flagged, "threshold": result.review_threshold})

    @staticmethod
    def _insert_field(db, document_id: str, path: str, kind: str, row_key: int | None, position: int, field: FieldResult | None) -> None:
        value = field.value if field else None
        state = "flagged" if (field is None or field.needs_review) else "auto"
        db.execute(
            "INSERT INTO fields (document_id, path, kind, row_key, position, value, original_value, printed, confidence, state, signals_json, sources_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id, path, kind, row_key, position, value, value, field.printed if field else None,
                field.confidence if field else None, state,
                json.dumps([signal.model_dump() for signal in field.signals]) if field else "[]",
                json.dumps([source.model_dump() for source in field.sources]) if field else "[]",
            ),
        )

    def begin_manual_entry(self, document_id: str, *, reviewer: str) -> dict:
        """The model couldn't read it, so a person types it in. Same rules apply from here on."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            if document["status"] not in ("failed",):
                raise ReviewError("wrong_state", "Only a document that failed extraction can be entered by hand.")
            db.execute("DELETE FROM fields WHERE document_id = ?", (document_id,))
            db.execute("DELETE FROM checks WHERE document_id = ?", (document_id,))
            for position, (name, kind) in enumerate(HEADER_FIELDS):
                state = "flagged" if name in REQUIRED_WHEN_MANUAL else "auto"
                db.execute(
                    "INSERT INTO fields (document_id, path, kind, position, state) VALUES (?, ?, ?, ?, ?)",
                    (document_id, name, kind, position, state),
                )
            db.execute("UPDATE documents SET status = 'needs_review', revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self._recompute_checks(db, document_id)
            self.audit.append("manual_entry_started", actor=actor, document_id=document_id)
        return self.get_document(document_id)

    def retry(self, document_id: str, *, reviewer: str) -> dict:
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            if document["status"] != "failed":
                raise ReviewError("wrong_state", "Only a document that failed extraction can be retried.")
            db.execute("UPDATE documents SET status = 'queued', failure_code = NULL, failure = NULL, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("retry_requested", actor=actor, document_id=document_id)
        return self.get_document(document_id)

    # --- Reading ----------------------------------------------------------------------------

    @staticmethod
    def _require_document(db, document_id: str) -> dict:
        if not DOCUMENT_ID.match(document_id or ""):
            raise ReviewError("not_found", "No such document.", status=404)
        row = db.execute(
            "SELECT id, filename, sha256, byte_size, status, failure_code, failure, pages_json, active_content_json, model, model_seconds, threshold, revision, last_exported_at, created_at, updated_at FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if not row:
            raise ReviewError("not_found", "No such document.", status=404)
        keys = ("id", "filename", "sha256", "byte_size", "status", "failure_code", "failure", "pages_json", "active_content_json", "model", "model_seconds", "threshold", "revision", "last_exported_at", "created_at", "updated_at")
        document = dict(zip(keys, row))
        document["pages"] = json.loads(document.pop("pages_json"))
        document["active_content"] = json.loads(document.pop("active_content_json"))
        return document

    def get_document(self, document_id: str) -> dict:
        """A document with everything the review screen needs."""
        with self._lock:
            db = self._connection
            document = self._require_document(db, document_id)
            document["fields"] = self._fields(db, document_id)
            document["checks"] = [
                {"name": name, "outcome": outcome, "detail": detail, "acknowledged_by": by, "acknowledged_at": at}
                for name, outcome, detail, by, at in db.execute(
                    "SELECT name, outcome, detail, acknowledged_by, acknowledged_at FROM checks WHERE document_id = ? ORDER BY name", (document_id,)
                )
            ]
            document["approval"] = self._active_approval(db, document_id)
            document["remaining"] = [field["path"] for field in document["fields"] if field["state"] == "flagged"]
            document["unacknowledged_checks"] = [c["name"] for c in document["checks"] if c["outcome"] == "fail" and not c["acknowledged_by"]]
            return document

    @staticmethod
    def _fields(db, document_id: str) -> list[dict]:
        rows = db.execute(
            "SELECT path, kind, row_key, value, original_value, printed, confidence, state, signals_json, sources_json FROM fields WHERE document_id = ? ORDER BY position",
            (document_id,),
        ).fetchall()
        return [
            {
                "path": row[0], "kind": row[1], "row_key": row[2], "value": row[3], "original_value": row[4], "printed": row[5],
                "confidence": row[6], "state": row[7], "signals": json.loads(row[8]), "sources": json.loads(row[9]),
            }
            for row in rows
        ]

    @staticmethod
    def _active_approval(db, document_id: str) -> dict | None:
        row = db.execute(
            "SELECT id, revision, content_hash, approved_by, approved_at FROM approvals WHERE document_id = ? AND voided_at IS NULL ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "revision": row[1], "content_hash": row[2], "approved_by": row[3], "approved_at": row[4]}

    def list_documents(self, *, status: str | None = None, limit: int = 500) -> list[dict]:
        with self._lock:
            db = self._connection
            query = """
                SELECT d.id, d.filename, d.status, d.failure, d.created_at, d.updated_at, d.last_exported_at, d.revision,
                    (SELECT value FROM fields f WHERE f.document_id = d.id AND f.path = 'vendor_name'),
                    (SELECT value FROM fields f WHERE f.document_id = d.id AND f.path = 'document_number'),
                    (SELECT value FROM fields f WHERE f.document_id = d.id AND f.path = 'total'),
                    (SELECT value FROM fields f WHERE f.document_id = d.id AND f.path = 'currency'),
                    (SELECT value FROM fields f WHERE f.document_id = d.id AND f.path = 'document_type'),
                    (SELECT COUNT(*) FROM fields f WHERE f.document_id = d.id AND f.state = 'flagged'),
                    (SELECT COUNT(*) FROM fields f WHERE f.document_id = d.id)
                FROM documents d
            """
            arguments: tuple = ()
            if status:
                query += " WHERE d.status = ?"
                arguments = (status,)
            query += " ORDER BY d.created_at DESC LIMIT ?"
            rows = db.execute(query, (*arguments, limit)).fetchall()
            return [
                {
                    "id": row[0], "filename": row[1], "status": row[2], "failure": row[3], "created_at": row[4], "updated_at": row[5],
                    "last_exported_at": row[6], "revision": row[7], "vendor_name": row[8], "document_number": row[9], "total": row[10],
                    "currency": row[11], "document_type": row[12], "flagged": row[13], "values": row[14],
                }
                for row in rows
            ]

    # --- Review actions ---------------------------------------------------------------------

    @staticmethod
    def _check_revision(document: dict, revision: object) -> None:
        if revision is None:
            return
        if revision != document["revision"]:
            raise ReviewError(
                "stale_revision", "This document changed since you loaded it. Reload to see the latest values.",
                extra={"current_revision": document["revision"]},
            )

    @staticmethod
    def _require_reviewable(document: dict) -> None:
        if document["status"] not in ("needs_review", "approved"):
            raise ReviewError("wrong_state", "This document isn't ready for review.")

    def _void_approval(self, db, document_id: str, *, actor: str, reason: str) -> None:
        approval = self._active_approval(db, document_id)
        if not approval:
            return
        db.execute("UPDATE approvals SET voided_at = ?, voided_reason = ? WHERE id = ?", (utc_now(), reason, approval["id"]))
        db.execute("UPDATE documents SET status = 'needs_review' WHERE id = ?", (document_id,))
        self.audit.append("approval_voided", actor=actor, document_id=document_id, detail={"reason": reason, "approved_by": approval["approved_by"], "revision": approval["revision"]})

    @staticmethod
    def _require_field(db, document_id: str, path: str) -> tuple:
        row = db.execute("SELECT kind, value, state FROM fields WHERE document_id = ? AND path = ?", (document_id, path)).fetchone()
        if not row:
            raise ReviewError("not_found", "No such field.", status=404)
        return row

    def edit_field(self, document_id: str, path: str, raw_value: object, *, reviewer: str, revision: object = None) -> dict:
        """Change a value. An approved document loses its approval, because it's no longer what was approved."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            self._require_reviewable(document)
            self._check_revision(document, revision)
            kind, old_value, _ = self._require_field(db, document_id, path)
            new_value = normalize_input(kind, raw_value)
            db.execute(
                "UPDATE fields SET value = ?, state = 'edited' WHERE document_id = ? AND path = ?",
                (new_value, document_id, path),
            )
            db.execute("UPDATE documents SET revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("field_edited", actor=actor, document_id=document_id, detail={"field": path, "old": old_value, "new": new_value})
            if new_value != old_value:
                self._void_approval(db, document_id, actor=actor, reason=f"{path} was edited")
            self._recompute_checks(db, document_id)
        return self.get_document(document_id)

    def confirm_field(self, document_id: str, path: str, *, reviewer: str, revision: object = None) -> dict:
        """A person looked at this value and it's right. The value itself doesn't change."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            self._require_reviewable(document)
            self._check_revision(document, revision)
            _, value, state = self._require_field(db, document_id, path)
            if state == "edited":
                raise ReviewError("wrong_state", "This value was already changed by a person.")
            db.execute("UPDATE fields SET state = 'confirmed' WHERE document_id = ? AND path = ?", (document_id, path))
            db.execute("UPDATE documents SET revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("field_confirmed", actor=actor, document_id=document_id, detail={"field": path, "value": value, "previous_state": state})
        return self.get_document(document_id)

    def add_row(self, document_id: str, *, reviewer: str, revision: object = None) -> dict:
        """The model missed a line item. The new row starts flagged, so it can't slip through blank."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            self._require_reviewable(document)
            self._check_revision(document, revision)
            row_key = db.execute("SELECT COALESCE(MAX(row_key), 0) + 1 FROM fields WHERE document_id = ?", (document_id,)).fetchone()[0]
            position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM fields WHERE document_id = ?", (document_id,)).fetchone()[0]
            for offset, (cell, kind) in enumerate(ITEM_CELLS):
                state = "flagged" if cell in ("description", "amount") else "auto"
                db.execute(
                    "INSERT INTO fields (document_id, path, kind, row_key, position, state) VALUES (?, ?, ?, ?, ?, ?)",
                    (document_id, _cell_path(row_key, cell), kind, row_key, position + offset, state),
                )
            db.execute("UPDATE documents SET revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("row_added", actor=actor, document_id=document_id, detail={"row": row_key})
            self._void_approval(db, document_id, actor=actor, reason=f"row {row_key} was added")
            self._recompute_checks(db, document_id)
        return self.get_document(document_id)

    def delete_row(self, document_id: str, row_key: int, *, reviewer: str, revision: object = None) -> dict:
        """The model invented a line item, or doubled one."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            self._require_reviewable(document)
            self._check_revision(document, revision)
            cells = db.execute("SELECT path, value FROM fields WHERE document_id = ? AND row_key = ?", (document_id, row_key)).fetchall()
            if not cells:
                raise ReviewError("not_found", "No such row.", status=404)
            db.execute("DELETE FROM fields WHERE document_id = ? AND row_key = ?", (document_id, row_key))
            db.execute("UPDATE documents SET revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("row_deleted", actor=actor, document_id=document_id, detail={"row": row_key, "values": {path.rsplit(".", 1)[-1]: value for path, value in cells}})
            self._void_approval(db, document_id, actor=actor, reason=f"row {row_key} was deleted")
            self._recompute_checks(db, document_id)
        return self.get_document(document_id)

    def _recompute_checks(self, db, document_id: str) -> None:
        """Rerun the sums on the current values. An acknowledgment only survives if the finding is unchanged."""
        values = dict(db.execute("SELECT path, value FROM fields WHERE document_id = ? AND row_key IS NULL", (document_id,)).fetchall())
        amounts = [
            row[0] for row in db.execute(
                "SELECT value FROM fields WHERE document_id = ? AND row_key IS NOT NULL AND path LIKE '%.amount' ORDER BY position", (document_id,)
            )
        ]

        def number(text: str | None) -> Decimal | None:
            return Decimal(text) if text not in (None, "") else None

        result = check_totals(
            [number(amount) for amount in amounts], number(values.get("subtotal")), number(values.get("discount")),
            number(values.get("shipping")), number(values.get("tax")), number(values.get("total")),
        )
        for check in (result.items_sum, result.totals_sum):
            existing = db.execute("SELECT outcome, detail FROM checks WHERE document_id = ? AND name = ?", (document_id, check.name)).fetchone()
            if existing == (check.outcome, check.detail):
                continue
            db.execute(
                "INSERT OR REPLACE INTO checks (document_id, name, outcome, detail, acknowledged_by, acknowledged_at) VALUES (?, ?, ?, ?, NULL, NULL)",
                (document_id, check.name, check.outcome, check.detail),
            )

    def acknowledge_check(self, document_id: str, name: str, *, reviewer: str, revision: object = None) -> dict:
        """The printed math really is wrong, and a person has seen that. It happens on real invoices."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            self._require_reviewable(document)
            self._check_revision(document, revision)
            row = db.execute("SELECT outcome, detail FROM checks WHERE document_id = ? AND name = ?", (document_id, name)).fetchone()
            if not row:
                raise ReviewError("not_found", "No such check.", status=404)
            if row[0] != "fail":
                raise ReviewError("wrong_state", "This check didn't fail.")
            db.execute(
                "UPDATE checks SET acknowledged_by = ?, acknowledged_at = ? WHERE document_id = ? AND name = ?",
                (actor, utc_now(), document_id, name),
            )
            db.execute("UPDATE documents SET revision = revision + 1, updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("check_acknowledged", actor=actor, document_id=document_id, detail={"check": name, "finding": row[1]})
        return self.get_document(document_id)

    def _content(self, db, document_id: str) -> tuple[dict, list[dict]]:
        fields = dict(db.execute("SELECT path, value FROM fields WHERE document_id = ? AND row_key IS NULL ORDER BY position", (document_id,)).fetchall())
        rows: dict[int, dict] = {}
        for path, row_key, value in db.execute(
            "SELECT path, row_key, value FROM fields WHERE document_id = ? AND row_key IS NOT NULL ORDER BY position", (document_id,)
        ):
            rows.setdefault(row_key, {})[path.rsplit(".", 1)[-1]] = value
        return fields, list(rows.values())

    def _content_hash(self, db, document_id: str) -> str:
        fields, rows = self._content(db, document_id)
        return hashlib.sha256(canonical({"fields": fields, "line_items": rows}).encode("utf-8")).hexdigest()

    def approve(self, document_id: str, *, reviewer: str, revision: object) -> dict:
        """Bind this person's approval to these exact values."""
        actor = clean_reviewer(reviewer)
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            if document["status"] != "needs_review":
                raise ReviewError("wrong_state", "This document isn't waiting for approval.")
            if revision is None:
                raise ReviewError("revision_required", "Approval has to name the revision being approved.", status=400)
            self._check_revision(document, revision)
            remaining = [path for (path,) in db.execute("SELECT path FROM fields WHERE document_id = ? AND state = 'flagged' ORDER BY position", (document_id,))]
            if remaining:
                raise ReviewError("flagged_values_remain", f"{len(remaining)} flagged value(s) still need a look.", extra={"remaining": remaining})
            unacknowledged = [
                name for (name,) in db.execute("SELECT name FROM checks WHERE document_id = ? AND outcome = 'fail' AND acknowledged_by IS NULL", (document_id,))
            ]
            if unacknowledged:
                raise ReviewError("checks_not_acknowledged", "A failed check needs acknowledging first.", extra={"checks": unacknowledged})
            digest = self._content_hash(db, document_id)
            db.execute(
                "INSERT INTO approvals (document_id, revision, content_hash, approved_by, approved_at) VALUES (?, ?, ?, ?, ?)",
                (document_id, document["revision"], digest, actor, utc_now()),
            )
            db.execute("UPDATE documents SET status = 'approved', updated_at = ? WHERE id = ?", (utc_now(), document_id))
            self.audit.append("approved", actor=actor, document_id=document_id, detail={"revision": document["revision"], "content_hash": digest})
        return self.get_document(document_id)

    def export_record(self, document_id: str, *, actor: str, format_name: str) -> ExportRecord:
        """The one way out. Refuses unless an active approval matches the values as they are now."""
        refused: ReviewError | None = None
        record: ExportRecord | None = None
        with self._transaction() as db:
            document = self._require_document(db, document_id)
            approval = self._active_approval(db, document_id)
            if not approval:
                refused = ReviewError("not_approved", "This document hasn't been approved, so it can't be exported.")
                self.audit.append("export_refused", actor=actor, document_id=document_id, detail={"format": format_name, "reason": "not_approved"})
            else:
                digest = self._content_hash(db, document_id)
                if digest != approval["content_hash"]:
                    refused = ReviewError("approval_mismatch", "These values don't match what was approved, so the approval no longer stands.")
                    self.audit.append("export_refused", actor=actor, document_id=document_id, detail={"format": format_name, "reason": "content_changed", "approved_hash": approval["content_hash"], "current_hash": digest})
                    self._void_approval(db, document_id, actor=SYSTEM_ACTOR, reason="values changed outside the review flow")
                else:
                    fields, rows = self._content(db, document_id)
                    now = utc_now()
                    db.execute("UPDATE documents SET last_exported_at = ?, updated_at = ? WHERE id = ?", (now, now, document_id))
                    event = self.audit.append("exported", actor=actor, document_id=document_id, detail={"format": format_name, "content_hash": digest, "approved_by": approval["approved_by"]})
                    record = ExportRecord(
                        document_id=document_id, filename=document["filename"], sha256=document["sha256"], approved_by=approval["approved_by"],
                        approved_at=approval["approved_at"], content_hash=digest, audit_head=event["hash"], fields=fields, line_items=rows,
                    )
        if refused:
            raise refused
        return record

    def audit_events(self, *, document_id: str | None = None) -> dict:
        with self._lock:
            return {"events": self.audit.events(document_id=document_id), "head": self.audit.head()}

    def audit_verify(self) -> dict:
        with self._lock:
            status = self.audit.verify()
        return {"ok": status.ok, "events": status.events, "head": status.head, "problem": status.problem, "first_bad_seq": status.first_bad_seq}

    def raw_connection(self) -> sqlite3.Connection:
        """Direct database access, for tests that act like someone tampering with the file."""
        return self._connection


def _safe_filename(name: str) -> str:
    """A display name only. Files are stored under generated ids, so this never reaches a path."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:200] or "unnamed.pdf"
