"""Decide whether an uploaded file may be processed at all. Uploads are untrusted.

Nothing here or downstream runs document scripts. PDFium's JavaScript only runs through a form
environment, which this code never creates, and pdfplumber only parses. Active content is still
detected, so a person can see that a document carries it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from .config import Settings

_ACTIVE_MARKERS = {
    "javascript": re.compile(rb"/(?:JavaScript|JS)\b"),
    "auto_action": re.compile(rb"/(?:OpenAction|AA)\b"),
    "launch": re.compile(rb"/Launch\b"),
    "embedded_file": re.compile(rb"/EmbeddedFile\b"),
    "submit_form": re.compile(rb"/SubmitForm\b"),
}
_STREAM = re.compile(rb"\bstream\r?\n.*?\bendstream\b", re.DOTALL)


class RejectedDocument(Exception):
    """The file can't be processed. `code` is stable; the message is safe to show a user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Intake:
    page_count: int
    byte_size: int
    active_content: tuple[str, ...] = field(default_factory=tuple)


def inspect_upload(data: bytes, settings: Settings) -> Intake:
    """Check size, file signature, encryption and page count before any heavy parsing."""
    if not data:
        raise RejectedDocument("empty", "The file is empty.")
    if len(data) > settings.max_upload_bytes:
        limit = settings.max_upload_bytes // (1024 * 1024)
        raise RejectedDocument("too_large", f"The file is larger than the {limit} MB limit.")
    # The PDF spec allows junk before the header, but only within the first kilobyte.
    if b"%PDF-" not in data[:1024]:
        raise RejectedDocument("not_a_pdf", "The file isn't a PDF.")
    try:
        document = pdfium.PdfDocument(BytesIO(data))
    except pdfium.PdfiumError as error:
        if "password" in str(error).lower():
            raise RejectedDocument("encrypted", "The PDF is password-protected.") from error
        raise RejectedDocument("unreadable", "The PDF is damaged or can't be read.") from error
    try:
        pages = len(document)
        scripts = pdfium_raw.FPDFDoc_GetJavaScriptActionCount(document.raw)
        attachments = pdfium_raw.FPDFDoc_GetAttachmentCount(document.raw)
    finally:
        document.close()
    if pages == 0:
        raise RejectedDocument("no_pages", "The PDF has no pages.")
    if pages > settings.max_pages:
        raise RejectedDocument("too_many_pages", f"The PDF has {pages} pages. The limit is {settings.max_pages}.")
    # Compressed fonts and images can spell "/AA" by chance, so the text search skips stream contents.
    # PDFium's counts cover what's tucked inside object streams, which the search can't see.
    outside_streams = _STREAM.sub(b" ", data)
    active = {name for name, pattern in _ACTIVE_MARKERS.items() if pattern.search(outside_streams)}
    if scripts > 0:
        active.add("javascript")
    if attachments > 0:
        active.add("embedded_file")
    return Intake(page_count=pages, byte_size=len(data), active_content=tuple(sorted(active)))
