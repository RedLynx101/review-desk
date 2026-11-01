"""Run the pipeline over a split, one document at a time, resuming where it stopped."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

from ..config import Settings
from ..model_client import HttpTransport, ModelClient, ModelReply
from ..pagetext import DocumentText, read_document
from ..pipeline import SYSTEM_PROMPT, ModelRead, ask_model, build_result, scan_images
from ..schema import ModelReading

TEXT_CACHE_VERSION = 4
#: Failures that say the server is down, as opposed to something about this document.
OUTAGE_CODES = {"unreachable", "server_error", "timeout", "model_crashed"}
#: A document that fails this many times stays failed, and counts as failed in the results.
MAX_DOCUMENT_ATTEMPTS = 3


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)


def load_text(pdf: bytes, settings: Settings, cache_dir: Path) -> DocumentText:
    """Read a PDF's words, reusing earlier OCR. OCR is the slow part and it's deterministic."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(pdf).hexdigest()[:32]
    path = cache_dir / f"v{TEXT_CACHE_VERSION}-{key}.json"
    if path.is_file():
        return DocumentText.from_cache(json.loads(path.read_text(encoding="utf-8")))
    document = read_document(pdf, settings)
    # An OCR failure may just be a busy machine. Don't let it stick.
    if not any(page.error for page in document.pages):
        _atomic_write(path, json.dumps(document.to_cache()))
    return document


def _git_state() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _model_details(settings: Settings) -> dict:
    """Best effort. LM Studio reports quantization and context; other servers may not."""
    root = settings.model_base_url.rsplit("/v1", 1)[0]
    try:
        response = HttpTransport().send("GET", f"{root}/api/v0/models", {}, None, 10.0)
        for entry in json.loads(response.body).get("data", []):
            if entry.get("id") == settings.model_name:
                keep = ("id", "type", "arch", "publisher", "quantization", "max_context_length", "loaded_context_length", "state")
                return {key: entry.get(key) for key in keep}
    except (OSError, ValueError, AttributeError):
        pass
    return {"id": settings.model_name}


def write_run_metadata(run_dir: Path, settings: Settings, *, split, count, conditions, images: bool, manifest: dict) -> None:
    path = run_dir / "run.json"
    invocation = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "base_documents": count, "conditions": list(conditions),
        "git": _git_state(), "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16],
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing.setdefault("invocations", []).append(invocation)
        _atomic_write(path, json.dumps(existing, indent=1) + "\n")
        return
    metadata = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "split": split,
        "base_documents": count,
        "conditions": list(conditions),
        "synthetic": manifest.get("synthetic", True),
        "generator_version": manifest.get("generator_version"),
        "master_seed": manifest.get("master_seed"),
        "model": _model_details(settings),
        "model_settings": {
            "temperature": settings.model_temperature,
            "max_tokens": settings.model_max_tokens,
            "strict_json_schema": True,
            "images_sent_for_scans": images,
        },
        "invocations": [invocation],
        "python": platform.python_version(),
    }
    _atomic_write(path, json.dumps(metadata, indent=1) + "\n")


def _reading_payload(doc_id: str, pdf_sha: str, settings: Settings, read: ModelRead, images_sent: int) -> dict:
    reply: ModelReply | None = read.reply
    return {
        "doc_id": doc_id,
        "pdf_sha256": pdf_sha,
        "model": settings.model_name,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16],
        "reading": read.reading.model_dump() if read.reading else None,
        "error_code": read.error_code,
        "error_message": read.error_message,
        "raw_text_if_invalid": reply.raw_text if (reply and read.reading is None) else None,
        "seconds": round(reply.seconds, 1) if reply else None,
        "attempts": reply.attempts if reply else None,
        "prompt_tokens": reply.prompt_tokens if reply else None,
        "completion_tokens": reply.completion_tokens if reply else None,
        "finish_reason": reply.finish_reason if reply else None,
        "images_sent": images_sent,
    }


def load_read(payload: dict) -> ModelRead:
    """Rebuild a saved answer, so it can be rescored without calling the model again."""
    reading = ModelReading.model_validate(payload["reading"]) if payload.get("reading") else None
    reply = None
    if payload.get("seconds") is not None:
        reply = ModelReply(
            data=payload.get("reading") or {}, raw_text="", seconds=payload["seconds"], attempts=payload.get("attempts") or 1,
            prompt_tokens=payload.get("prompt_tokens"), completion_tokens=payload.get("completion_tokens"),
            finish_reason=payload.get("finish_reason"),
        )
    return ModelRead(reading, payload.get("error_code"), payload.get("error_message"), reply)


def selected_documents(manifest: dict, count: int | None, conditions) -> list[dict]:
    allowed_base = None
    if count is not None:
        ordered = []
        for entry in manifest["documents"]:
            base = entry["doc_id"].rsplit(".", 1)[0]
            if base not in ordered:
                ordered.append(base)
        allowed_base = set(ordered[:count])
    return [
        entry for entry in manifest["documents"]
        if entry["condition"] in conditions and (allowed_base is None or entry["doc_id"].rsplit(".", 1)[0] in allowed_base)
    ]


def run_split(
    split: str,
    *,
    count: int | None,
    conditions,
    run_dir: Path,
    data_root: Path,
    cache_dir: Path,
    settings: Settings,
    send_images: bool = False,
    retry_failures: bool = False,
    log=print,
    client: ModelClient | None = None,
    stop_after_outages: int = 3,
) -> dict:
    """Process a split. Finished documents are skipped, so an interrupted run just continues."""
    folder = data_root / split
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    readings = run_dir / "readings"
    readings.mkdir(parents=True, exist_ok=True)
    write_run_metadata(run_dir, settings, split=split, count=count, conditions=conditions, images=send_images, manifest=manifest)

    entries = selected_documents(manifest, count, conditions)
    done = skipped = failed = 0
    owned = client is None
    client = client or ModelClient(settings)
    consecutive_outages = 0
    try:
        for position, entry in enumerate(entries, start=1):
            path = readings / f"{entry['doc_id']}.json"
            history: list[dict] = []
            if path.is_file():
                previous = json.loads(path.read_text(encoding="utf-8"))
                history = previous.get("failure_history", [])
                if previous.get("reading") or not retry_failures:
                    skipped += 1
                    continue
                history = [*history, {"code": previous.get("error_code"), "message": previous.get("error_message")}]
                if len(history) >= MAX_DOCUMENT_ATTEMPTS:
                    log(f"[{position}/{len(entries)}] {entry['doc_id']} left as failed after {len(history)} attempts")
                    skipped += 1
                    continue
            pdf = (folder / entry["pdf"]).read_bytes()
            started = time.monotonic()
            document = load_text(pdf, settings, cache_dir)
            text_seconds = time.monotonic() - started
            images = scan_images(pdf, document, settings) if send_images else []
            read = ask_model(document, client, images=images or None)
            payload = _reading_payload(entry["doc_id"], entry["pdf_sha256"], settings, read, len(images))
            payload["text_seconds"] = round(text_seconds, 1)
            payload["failure_history"] = history
            _atomic_write(path, json.dumps(payload, indent=1) + "\n")
            if read.reading is None:
                failed += 1
            done += 1
            consecutive_outages = consecutive_outages + 1 if read.error_code in OUTAGE_CODES else 0
            if consecutive_outages >= stop_after_outages:
                log(f"Stopping: {consecutive_outages} documents in a row hit a server failure ({read.error_code}). Rerun with --retry-failures.")
                break
            seconds = read.reply.seconds if read.reply else 0.0
            log(f"[{position}/{len(entries)}] {entry['doc_id']} text {text_seconds:.1f}s model {seconds:.1f}s {read.error_code or 'ok'}")
    finally:
        if owned:
            client.close()
    return {"processed": done, "skipped": skipped, "failed": failed, "total": len(entries)}


def result_for(truth: dict, payload: dict, pdf: bytes, settings: Settings, cache_dir: Path, *, weights, threshold):
    """Rebuild a result from a saved answer. Returns the result and the words it was checked against."""
    document = load_text(pdf, settings, cache_dir)
    words = " ".join(line.text for line in document.lines)
    return build_result(document, load_read(payload), settings, weights=weights, threshold=threshold), words
