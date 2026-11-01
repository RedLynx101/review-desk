"""Uploads are untrusted: limits hold, and scripts inside a PDF never run."""

from __future__ import annotations

from io import BytesIO

import pypdfium2 as pdfium
import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen.canvas import Canvas

from review_desk.config import Settings
from review_desk.intake import RejectedDocument, inspect_upload
from review_desk.pagetext import read_document

SETTINGS = Settings(max_upload_bytes=200_000, max_pages=3)


def _pdf(pages: int = 1, encrypt=None, text: str = "Invoice number A1047 for testing purposes, total 12.00") -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, encrypt=encrypt)
    for _ in range(pages):
        canvas.drawString(72, 720, text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _code(data: bytes, settings=SETTINGS) -> str:
    with pytest.raises(RejectedDocument) as caught:
        inspect_upload(data, settings)
    return caught.value.code


def test_a_normal_pdf_passes():
    intake = inspect_upload(_pdf(2), SETTINGS)
    assert intake.page_count == 2 and intake.active_content == ()


def test_empty_file_is_refused():
    assert _code(b"") == "empty"


def test_oversized_file_is_refused_before_parsing():
    assert _code(b"%PDF-1.7" + b"x" * 300_000) == "too_large"


def test_a_file_that_is_not_a_pdf_is_refused():
    assert _code(b"MZ\x90\x00 definitely an executable") == "not_a_pdf"
    assert _code(b"<html><script>alert(1)</script></html>") == "not_a_pdf"


def test_too_many_pages_is_refused():
    assert _code(_pdf(4)) == "too_many_pages"


def test_a_damaged_pdf_is_refused():
    assert _code(b"%PDF-1.7\nthis is not really a pdf at all\n") == "unreadable"


def test_a_password_protected_pdf_is_refused():
    locked = StandardEncryption("secret-password", canPrint=0)
    assert _code(_pdf(encrypt=locked)) == "encrypted"


def _pdf_with_script() -> bytes:
    """A PDF that would run JavaScript on open, in a viewer that runs scripts."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R/OpenAction 4 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"4 0 obj<</S/JavaScript/JS(app.alert('should never run'))>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def test_active_content_is_detected_and_reported():
    intake = inspect_upload(_pdf_with_script(), SETTINGS)
    assert "javascript" in intake.active_content
    assert "auto_action" in intake.active_content


def test_reading_a_pdf_with_a_script_never_creates_a_form_environment(monkeypatch):
    """PDFium only runs JavaScript through a form environment. Prove nothing creates one."""

    def forbidden(self, *args, **kwargs):
        raise AssertionError("a form environment was created")

    monkeypatch.setattr(pdfium.PdfDocument, "init_forms", forbidden)
    document = read_document(_pdf_with_script(), SETTINGS, tesseract="does-not-exist")
    assert document.intake.active_content
    assert document.lines == []


def test_a_missing_ocr_binary_is_recorded_on_the_page_not_raised(monkeypatch):
    def unavailable(*args, **kwargs):
        from review_desk.ocr import OcrUnavailable

        raise OcrUnavailable("Tesseract wasn't found.")

    monkeypatch.setattr("review_desk.pagetext.ocr_page", unavailable)
    document = read_document(_pdf(text=""), SETTINGS)
    assert document.pages[0].source == "ocr"
    assert "Tesseract" in document.pages[0].error
    assert not document.has_text


def test_a_huge_page_stays_under_the_pixel_cap():
    from review_desk.pagetext import render_page

    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(14000, 14000))
    canvas.showPage()
    canvas.save()
    settings = Settings(max_render_pixels=1_000_000)
    document = pdfium.PdfDocument(buffer.getvalue())
    try:
        image, _ = render_page(document, 0, 300, settings)
    finally:
        document.close()
    assert image.width * image.height <= 1_100_000
