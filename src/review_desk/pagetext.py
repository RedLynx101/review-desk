"""Read a PDF into words with coordinates, from its text layer or from OCR.

All coordinates are PDF points from the page's top-left corner, so a word's box can be drawn on the
page image at any zoom. Words are grouped into numbered lines the model cites when it answers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import BytesIO

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from .config import Settings
from .intake import Intake, inspect_upload
from .normalize import squash
from .ocr import OcrOptions, OcrUnavailable, ocr_page

#: A page with fewer real characters than this gets OCR, even if it has a text layer.
MIN_TEXT_LAYER_CHARS = 25
#: Below this share of text-layer words visible on the page, the text layer isn't trusted at all.
MIN_TEXT_LAYER_AGREEMENT = 0.6
VERIFY_DPI = 200


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    page: int
    confidence: float = 1.0  # 0 to 1. Text-layer words are exact, so they carry 1.0.

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class Line:
    number: int
    page: int
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass
class PageText:
    number: int
    width: float
    height: float
    source: str  # "text_layer" or "ocr"
    words: list[Word] = field(default_factory=list)
    error: str | None = None
    ocr_settings: dict | None = None
    #: What OCR sees on a text-layer page, for checking the text layer against the pixels.
    visible_words: list[Word] = field(default_factory=list)
    #: Share of text-layer words OCR could also see. None if the check didn't run.
    text_layer_agreement: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentText:
    pages: list[PageText]
    lines: list[Line]
    intake: Intake

    @property
    def has_text(self) -> bool:
        return bool(self.lines)

    def line(self, number: int | None) -> Line | None:
        if number is None or not 1 <= number <= len(self.lines):
            return None
        return self.lines[number - 1]

    def to_cache(self) -> dict:
        return {
            "intake": asdict(self.intake),
            "pages": [
                {
                    "number": page.number, "width": page.width, "height": page.height, "source": page.source,
                    "error": page.error, "ocr_settings": page.ocr_settings,
                    "text_layer_agreement": page.text_layer_agreement, "warnings": page.warnings,
                    "words": [_word_row(w) for w in page.words],
                    "visible_words": [_word_row(w) for w in page.visible_words],
                }
                for page in self.pages
            ],
        }

    @classmethod
    def from_cache(cls, data: dict) -> "DocumentText":
        pages = []
        for raw in data["pages"]:
            pages.append(PageText(
                number=raw["number"], width=raw["width"], height=raw["height"], source=raw["source"],
                words=[_word_from_row(w, raw["number"]) for w in raw["words"]],
                error=raw.get("error"), ocr_settings=raw.get("ocr_settings"),
                visible_words=[_word_from_row(w, raw["number"]) for w in raw.get("visible_words", [])],
                text_layer_agreement=raw.get("text_layer_agreement"), warnings=raw.get("warnings", []),
            ))
        intake = data["intake"]
        return cls(
            pages=pages,
            lines=group_lines(pages),
            intake=Intake(intake["page_count"], intake["byte_size"], tuple(intake["active_content"])),
        )


def _word_row(word: Word) -> list:
    return [word.text, round(word.x0, 2), round(word.top, 2), round(word.x1, 2), round(word.bottom, 2), round(word.confidence, 3)]


def _word_from_row(row: list, page: int) -> Word:
    return Word(text=row[0], x0=row[1], top=row[2], x1=row[3], bottom=row[4], page=page, confidence=row[5])


def group_lines(pages: list[PageText]) -> list[Line]:
    """Cluster words into visual rows across the whole page, ignoring OCR's block order.

    A row is what a reader sees: "Invoice No.:   A087865" stays together even when the label and the
    value are in different columns, which is what the model needs to tell fields apart.
    """
    lines: list[Line] = []
    for page in pages:
        rows: list[list[Word]] = []
        for word in sorted(page.words, key=lambda w: (w.center_y, w.x0)):
            placed = False
            for row in reversed(rows[-6:]):
                row_center = sum(w.center_y for w in row) / len(row)
                row_height = sum(w.height for w in row) / len(row)
                if abs(word.center_y - row_center) <= 0.5 * min(row_height, word.height) + 0.5:
                    row.append(word)
                    placed = True
                    break
            if not placed:
                rows.append([word])
        rows.sort(key=lambda row: sum(w.center_y for w in row) / len(row))
        for row in rows:
            row.sort(key=lambda w: w.x0)
            lines.append(Line(number=len(lines) + 1, page=page.number, words=row))
    return lines


def layout_text(document: DocumentText) -> str:
    """Numbered lines for the model, with spacing that keeps columns visibly apart."""
    out: list[str] = []
    current_page = None
    for line in document.lines:
        if line.page != current_page:
            current_page = line.page
            if len(document.pages) > 1:
                out.append(f"--- page {current_page + 1} of {len(document.pages)} ---")
        parts = []
        previous = None
        for word in line.words:
            if previous is not None:
                gap = word.x0 - previous.x1
                char = max(previous.height * 0.5, 1.0)
                parts.append(" " * min(8, max(1, int(gap / char))))
            parts.append(word.text)
            previous = word
        out.append(f"{line.number}| {''.join(parts)}")
    return "\n".join(out)


def _text_layer_words(page, number: int) -> list[Word]:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2.0, keep_blank_chars=False, use_text_flow=False)
    return [
        Word(text=w["text"], x0=float(w["x0"]), top=float(w["top"]), x1=float(w["x1"]), bottom=float(w["bottom"]), page=number)
        for w in words
    ]


def native_dpi(plumber_page, fallback: int) -> int:
    """The resolution a scanned page was captured at, read from its largest embedded image.

    OCR cleanup works best at this resolution, because noise hasn't been smeared by resampling yet.
    """
    best = None
    for image in plumber_page.images:
        source = image.get("srcsize")
        shown = float(image.get("width") or 0)
        if not source or not source[0] or shown <= 0:
            continue
        if best is None or source[0] * source[1] > best[0] * best[1]:
            best = (source[0], source[1], shown)
    if best is None:
        return fallback
    return int(max(50, min(600, best[0] / (best[2] / 72.0))))


def render_page(pdf: pdfium.PdfDocument, index: int, dpi: int, settings: Settings) -> tuple[Image.Image, float]:
    """Render one page in RGB, lowering the resolution if a huge page would exceed the pixel cap."""
    page = pdf[index]
    try:
        width, height = page.get_size()
        scale = dpi / 72.0
        pixels = width * height * scale * scale
        if pixels > settings.max_render_pixels:
            scale *= (settings.max_render_pixels / pixels) ** 0.5
        return page.render(scale=scale, rev_byteorder=True).to_pil().convert("RGB"), scale
    finally:
        page.close()


def _verify_text_layer(page: PageText, renderer: pdfium.PdfDocument, settings: Settings, tesseract: str | None) -> None:
    """OCR the rendered page and see whether the text layer matches what a person would see.

    If OCR isn't available, the page keeps its text layer and the check just doesn't happen.
    """
    try:
        image, scale = render_page(renderer, page.number, VERIFY_DPI, settings)
        result = ocr_page(
            image, scale, page.number,
            options=OcrOptions(deskew=False, denoise_level=0, flatten=False, target_dpi=VERIFY_DPI),
            tesseract=tesseract, timeout=settings.ocr_timeout_seconds,
        )
    except OcrUnavailable:
        return
    page.visible_words = result.words
    visible = squash(" ".join(word.text for word in result.words))
    checked = [squash(word.text) for word in page.words if len(squash(word.text)) >= 3]
    if not checked:
        return
    page.text_layer_agreement = round(sum(token in visible for token in checked) / len(checked), 3)
    if page.text_layer_agreement < MIN_TEXT_LAYER_AGREEMENT:
        page.warnings.append(
            f"Only {page.text_layer_agreement:.0%} of this page's hidden text is visible on the page, so the hidden text was ignored."
        )
        page.source = "ocr"


def read_document(data: bytes, settings: Settings | None = None, *, tesseract: str | None = None) -> DocumentText:
    """Read every page: text layer where there is one, OCR where there isn't."""
    settings = settings or Settings.from_env()
    intake = inspect_upload(data, settings)
    pages: list[PageText] = []
    with pdfplumber.open(BytesIO(data)) as plumber:
        renderer = pdfium.PdfDocument(BytesIO(data))
        try:
            for index, plumber_page in enumerate(plumber.pages):
                words = _text_layer_words(plumber_page, index)
                width, height = float(plumber_page.width), float(plumber_page.height)
                if sum(len(word.text) for word in words) >= MIN_TEXT_LAYER_CHARS:
                    page = PageText(index, width, height, "text_layer", words)
                    if settings.verify_text_layer:
                        _verify_text_layer(page, renderer, settings, tesseract)
                    if page.source == "text_layer":
                        pages.append(page)
                        continue
                    # The text layer doesn't match what's visible, so read the pixels like a scan.
                    warnings = page.warnings
                else:
                    warnings = []
                page = PageText(index, width, height, "ocr", warnings=warnings)
                try:
                    dpi = native_dpi(plumber_page, settings.ocr_dpi)
                    image, scale = render_page(renderer, index, dpi, settings)
                    result = ocr_page(image, scale, index, tesseract=tesseract, timeout=settings.ocr_timeout_seconds)
                    page.words = result.words
                    page.ocr_settings = result.settings
                except OcrUnavailable as error:
                    page.error = str(error)
                pages.append(page)
        finally:
            renderer.close()
    return DocumentText(pages=pages, lines=group_lines(pages), intake=intake)
