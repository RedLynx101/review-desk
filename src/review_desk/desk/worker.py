"""Turn queued documents into reviewable ones, one at a time.

One at a time is deliberate: a local model handles a single request well and a pile of them badly.
A failure stays on its document. The queue keeps moving.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..evaluation.runner import load_read
from ..model_client import ModelClient
from ..pagetext import DocumentText
from ..pipeline import ModelRead, ask_model, build_result
from .reader_process import ReaderFailed, read_isolated
from .store import Store

#: Takes the document's words and its PDF's sha256. Returns what the model read.
ModelStep = Callable[[DocumentText, str], ModelRead]
#: Takes the PDF path, page image folder, document id, settings and timeout. Returns words and page info.
ReaderStep = Callable[..., dict]

READ_TIMEOUT_SECONDS = 600.0


def live_model_step(settings: Settings) -> ModelStep:
    """Ask the configured model. This is the only path from the desk to the network."""
    client = ModelClient(settings)

    def step(document: DocumentText, sha256: str) -> ModelRead:
        return ask_model(document, client)

    return step


def replay_model_step(run_dir: Path) -> ModelStep:
    """Answer from a finished evaluation run instead of calling a model.

    Each saved answer records the sha256 of its PDF. Files the run never saw fail with a clear reason,
    so the review screen, including its failure state, works on a machine with no model at all.
    """
    answers: dict[str, dict] = {}
    for path in sorted(Path(run_dir, "readings").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("pdf_sha256") and payload.get("reading"):
            answers[payload["pdf_sha256"]] = payload

    def step(document: DocumentText, sha256: str) -> ModelRead:
        payload = answers.get(sha256)
        if payload is None:
            return ModelRead(None, "no_saved_answer", f"Replay mode has no saved answer for this file. It knows {len(answers)} documents.", None)
        return load_read(payload)

    return step


class Pipeline:
    """Does the work for one document. Separate from the thread, so tests can drive it directly."""

    def __init__(self, store: Store, settings: Settings, model_step: ModelStep, *, reader: ReaderStep = read_isolated, read_timeout: float = READ_TIMEOUT_SECONDS):
        self.store = store
        self.settings = settings
        self.model_step = model_step
        self.reader = reader
        self.read_timeout = read_timeout

    def process(self, document_id: str) -> None:
        """Read one claimed document. Whatever goes wrong, it ends up in a state a person can see."""
        try:
            self._process(document_id)
        except Exception as error:
            print(f"review desk worker: {document_id}: {type(error).__name__}: {error}", flush=True)
            self.store.mark_failed(document_id, code="internal_error", message=f"Something went wrong while reading this document ({type(error).__name__}).")

    def _process(self, document_id: str) -> None:
        record = self.store.get_document(document_id)
        try:
            outcome = self.reader(self.store.pdf_path(document_id), self.store.pages, document_id, self.settings, timeout=self.read_timeout)
        except ReaderFailed as failure:
            self.store.mark_failed(document_id, code=failure.code, message=failure.message, status="rejected" if failure.rejected else "failed")
            return
        self.store.set_pages(document_id, outcome["pages"])
        document = DocumentText.from_cache(outcome["document"])
        if not document.has_text:
            reasons = [page.error for page in document.pages if page.error]
            self.store.mark_failed(document_id, code="no_text", message=reasons[0] if reasons else "No readable text was found on any page.")
            return
        read = self.model_step(document, record["sha256"])
        self.store.store_extraction(document_id, build_result(document, read, self.settings))

    def drain(self) -> int:
        """Process everything queued. Returns how many documents it handled."""
        handled = 0
        while True:
            document_id = self.store.claim_next()
            if document_id is None:
                return handled
            self.process(document_id)
            handled += 1


class Worker(threading.Thread):
    def __init__(self, pipeline: Pipeline):
        super().__init__(name="review-desk-worker", daemon=True)
        self.pipeline = pipeline
        self._wake = threading.Event()
        self._stop_requested = threading.Event()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop_requested.set()
        self._wake.set()

    def run(self) -> None:
        self.pipeline.store.requeue_interrupted()
        while not self._stop_requested.is_set():
            try:
                self.pipeline.drain()
            except Exception as error:  # The worker has to outlive any one bad document.
                print(f"review desk worker: {type(error).__name__}: {error}", flush=True)
            self._wake.wait(timeout=5.0)
            self._wake.clear()
