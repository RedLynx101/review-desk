"""Parse and render untrusted PDFs in a child process with a time limit.

PDF parsers are large native code, and a hostile file can hang one or crash it. Keeping that work out
of the server means a bad file costs one failed document. It also means the server never parses a PDF
at all: it serves images this process already wrote, and page text as plain data.
"""

from __future__ import annotations

import multiprocessing
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium

from ..config import Settings
from ..intake import RejectedDocument
from ..pagetext import read_document, render_page

#: Rendered resolution for the review screen. High enough to zoom in on small print.
PAGE_DPI = 170


class ReaderFailed(Exception):
    def __init__(self, code: str, message: str, *, rejected: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.rejected = rejected


def read_and_render(pdf_path: Path, image_dir: Path, document_id: str, settings: Settings, *, tesseract: str | None = None) -> dict:
    """Read the words and write one image per page. Scans become JPEG, digital pages PNG."""
    data = Path(pdf_path).read_bytes()
    document = read_document(data, settings, tesseract=tesseract)
    pages = []
    renderer = pdfium.PdfDocument(BytesIO(data))
    try:
        for page in document.pages:
            image, _ = render_page(renderer, page.number, PAGE_DPI, settings)
            if page.source == "ocr":
                name = f"{document_id}-{page.number}.jpg"
                image.convert("RGB").save(Path(image_dir) / name, format="JPEG", quality=85)
            else:
                name = f"{document_id}-{page.number}.png"
                image.convert("RGB").save(Path(image_dir) / name, format="PNG", optimize=False)
            pages.append({
                "number": page.number, "width": page.width, "height": page.height, "source": page.source,
                "image": name, "warnings": page.warnings, "error": page.error,
                "text_layer_agreement": page.text_layer_agreement,
            })
    finally:
        renderer.close()
    return {"document": document.to_cache(), "pages": pages}


def _child(connection, pdf_path, image_dir, document_id, settings) -> None:
    try:
        connection.send({"ok": True, "result": read_and_render(Path(pdf_path), Path(image_dir), document_id, settings)})
    except RejectedDocument as rejected:
        connection.send({"ok": False, "code": rejected.code, "message": rejected.message, "rejected": True})
    except Exception as error:  # A hostile file can fail in any way at all. It all means the same thing here.
        connection.send({"ok": False, "code": "unreadable", "message": f"This PDF couldn't be read ({type(error).__name__}).", "rejected": False})
    finally:
        connection.close()


def read_isolated(pdf_path: Path, image_dir: Path, document_id: str, settings: Settings, *, timeout: float) -> dict:
    """Run `read_and_render` in a spawned process. Raises ReaderFailed if it fails, hangs or dies."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(sender, str(pdf_path), str(image_dir), document_id, settings), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            process.kill()
            raise ReaderFailed("reader_timeout", f"Reading this PDF took longer than {timeout:.0f} seconds, so it was stopped.")
        try:
            outcome = receiver.recv()
        except EOFError as error:
            raise ReaderFailed("reader_crashed", "The PDF reader crashed on this file.") from error
    finally:
        receiver.close()
        process.join(10)
        if process.is_alive():
            process.kill()
    if not outcome["ok"]:
        raise ReaderFailed(outcome["code"], outcome["message"], rejected=outcome["rejected"])
    return outcome["result"]
