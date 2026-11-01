"""A ReportLab canvas with a top-left origin that records where each field value lands.

Coordinates are PDF points measured from the top-left corner, the same frame pdfplumber
reports. `y` is the text baseline. A recorded bbox is `[x0, top, x1, bottom]`.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

INK = (0.10, 0.10, 0.12)
GRAY = (0.38, 0.38, 0.42)
LIGHT = (0.84, 0.84, 0.86)
PALE = (0.95, 0.95, 0.96)
WHITE = (1.0, 1.0, 1.0)

SYNTHETIC_NOTICE = "SYNTHETIC TEST DOCUMENT. Invented data from the Review Desk generator. Not a real invoice or receipt."
PDF_SUBJECT = "Synthetic test document with known answers. Not a real invoice or receipt."
PDF_CREATOR = "review-desk synthetic generator"


def _register_fonts() -> None:
    # Bitstream Vera ships inside ReportLab. It gets embedded, unlike the base-14 fonts, so
    # some documents carry subset fonts the way documents from real accounting software do.
    registered = pdfmetrics.getRegisteredFontNames()
    if "Vera" not in registered:
        pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    if "VeraBd" not in registered:
        pdfmetrics.registerFont(TTFont("VeraBd", "VeraBd.ttf"))


def font_metrics(font: str) -> tuple[float, float]:
    """Return ascent and descent per unit of font size, descent as a positive number."""
    face = pdfmetrics.getFont(font).face
    return face.ascent / 1000.0, abs(face.descent) / 1000.0


class FieldCanvas:
    def __init__(self, width: float, height: float, title: str):
        _register_fonts()
        self.w = width
        self.h = height
        self.page = 0
        self.locations: dict[str, list[dict]] = {}
        self._buffer = BytesIO()
        self.c = Canvas(self._buffer, pagesize=(width, height), invariant=1, pageCompression=1)
        self.c.setTitle(title)
        self.c.setSubject(PDF_SUBJECT)
        self.c.setCreator(PDF_CREATOR)
        self.c.setAuthor(PDF_CREATOR)

    def text(self, x, y, value, font, size, *, align="left", color=INK, key=None) -> float:
        """Draw one string and return its right edge. `key` records where a field landed."""
        width = stringWidth(value, font, size)
        x0 = {"left": x, "right": x - width, "center": x - width / 2}[align]
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        self.c.drawString(x0, self.h - y, value)
        if key:
            self.mark(key, value, x0, y, width, font, size)
        return x0 + width

    def spans(self, x, y, parts, font, size, *, color=INK) -> float:
        """Draw prose in which some runs are fields. `parts` is a list of (text, key or None)."""
        for value, key in parts:
            x = self.text(x, y, value, font, size, color=color, key=key)
        return x

    def mark(self, key, value, x0, y, width, font, size) -> None:
        ascent, descent = font_metrics(font)
        bbox = [x0, y - ascent * size, x0 + width, y + descent * size]
        self.locations.setdefault(key, []).append(
            {"page": self.page, "bbox": [round(v, 2) for v in bbox], "printed": value}
        )

    def rect(self, x, y, width, height, *, fill=None, stroke=None, line_width=0.5) -> None:
        """Draw a rectangle whose top-left corner is (x, y)."""
        if fill:
            self.c.setFillColorRGB(*fill)
        if stroke:
            self.c.setStrokeColorRGB(*stroke)
            self.c.setLineWidth(line_width)
        self.c.rect(x, self.h - y - height, width, height, stroke=int(bool(stroke)), fill=int(bool(fill)))

    def line(self, x0, y0, x1, y1, *, color=LIGHT, width=0.5) -> None:
        self.c.setStrokeColorRGB(*color)
        self.c.setLineWidth(width)
        self.c.line(x0, self.h - y0, x1, self.h - y1)

    def _footer(self) -> None:
        size = 6.0
        font = "Helvetica"
        limit = self.w - 16
        if stringWidth(SYNTHETIC_NOTICE, font, size) <= limit:
            self.text(self.w / 2, self.h - 10, SYNTHETIC_NOTICE, font, size, align="center", color=GRAY)
            return
        # Narrow receipt paper: break the notice over as many lines as it needs.
        lines, current = [], ""
        for word in SYNTHETIC_NOTICE.split():
            candidate = f"{current} {word}".strip()
            if stringWidth(candidate, font, size) > limit:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
        y = self.h - 8 - (len(lines) - 1) * 7
        for line in lines:
            self.text(self.w / 2, y, line, font, size, align="center", color=GRAY)
            y += 7

    def next_page(self) -> None:
        self._footer()
        self.c.showPage()
        self.page += 1

    def finish(self) -> bytes:
        self._footer()
        self.c.showPage()
        self.c.save()
        return self._buffer.getvalue()


def image_pages_to_pdf(pages: list[tuple[bytes, float, float]], title: str) -> bytes:
    """Wrap JPEG pages in a PDF with no text layer. Each page is (jpeg bytes, width pt, height pt)."""
    buffer = BytesIO()
    canvas = Canvas(buffer, invariant=1, pageCompression=0)
    canvas.setTitle(title)
    canvas.setSubject(PDF_SUBJECT)
    canvas.setCreator(PDF_CREATOR)
    canvas.setAuthor(PDF_CREATOR)
    for jpeg, width, height in pages:
        canvas.setPageSize((width, height))
        canvas.drawImage(ImageReader(BytesIO(jpeg)), 0, 0, width, height)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
