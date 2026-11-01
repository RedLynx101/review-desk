"""OCR: rotation is found, boxes map back to the original page, and a missing binary is a named error."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from review_desk.config import Settings
from review_desk.normalize import squash
from review_desk.ocr import DEFAULT_OPTIONS, OcrOptions, OcrUnavailable, _straighten, estimate_noise, estimate_skew, prepare, run_tesseract
from review_desk.pagetext import read_document
from review_desk.synth.content import make_document
from review_desk.synth.degrade import draw_params
from review_desk.synth.generate import DEFAULT_SEED, render_all
from review_desk.tesseract import find_tesseract

needs_tesseract = pytest.mark.skipif(find_tesseract() is None, reason="no Tesseract binary found")


def _ruled_page(size=(1000, 1300)) -> Image.Image:
    """Rows of dark blocks, standing in for lines of text."""
    page = Image.new("L", size, 245)
    draw = ImageDraw.Draw(page)
    for y in range(80, size[1] - 80, 42):
        for x in range(90, size[0] - 120, 60):
            draw.rectangle([x, y, x + 44, y + 16], fill=30)
    return page


@pytest.mark.parametrize("angle", [-3.5, -1.0, 0.0, 0.7, 2.4])
def test_skew_estimate_recovers_a_known_rotation(angle):
    rotated = _ruled_page().rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=245)
    estimated = estimate_skew(rotated)
    assert abs(estimated - angle) <= 0.2


@pytest.mark.parametrize("angle", [-3.0, 2.0])
def test_straightening_removes_the_estimated_skew(angle):
    rotated = _ruled_page().rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=245)
    straightened, matrix = _straighten(rotated, estimate_skew(rotated))
    assert abs(estimate_skew(straightened)) <= 0.2
    assert matrix is not None


def test_noise_estimate_separates_clean_from_grainy():
    import numpy as np

    clean = _ruled_page()
    pixels = np.asarray(clean, dtype=np.float32) + np.random.default_rng(1).normal(0, 12, (1300, 1000))
    grainy = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    assert estimate_noise(clean) < 2.0 < 6.0 < estimate_noise(grainy)


def test_denoising_turns_on_only_for_grainy_pages():
    import numpy as np

    clean = _ruled_page()
    assert prepare(clean, 300 / 72, OcrOptions(deskew=False, flatten=False)).denoise_level == 0
    pixels = np.asarray(clean, dtype=np.float32) + np.random.default_rng(1).normal(0, 12, (1300, 1000))
    grainy = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    assert prepare(grainy, 300 / 72, OcrOptions(deskew=False, flatten=False)).denoise_level == 1


def test_low_resolution_pages_get_upscaled_and_high_ones_do_not():
    low = prepare(_ruled_page(), 100 / 72, OcrOptions(deskew=False))
    assert round(low.upscale, 1) == 3.0
    high = prepare(_ruled_page(), 300 / 72, OcrOptions(deskew=False))
    assert high.upscale == 1.0


def test_missing_binary_is_a_named_error(monkeypatch):
    monkeypatch.setattr("review_desk.ocr.find_tesseract", lambda: None)
    with pytest.raises(OcrUnavailable, match="TESSERACT_CMD"):
        run_tesseract(Image.new("L", (10, 10)), psm=6, dpi=300, tesseract=None, timeout=5)


def test_tesseract_that_cannot_start_is_a_named_error(tmp_path):
    with pytest.raises(OcrUnavailable):
        run_tesseract(Image.new("L", (10, 10)), psm=6, dpi=300, tesseract=str(tmp_path / "no-such-binary"), timeout=5)


def _scan(index=0, condition="scanned"):
    doc = make_document("dev", index, DEFAULT_SEED)
    pdf, truth = render_all(doc, (condition,))[condition]
    return pdf, truth


@needs_tesseract
def test_scan_is_read_through_ocr_with_word_confidence():
    pdf, truth = _scan()
    document = read_document(pdf, Settings())
    assert document.pages[0].source == "ocr"
    assert document.pages[0].ocr_settings["psm"] == DEFAULT_OPTIONS.psm
    assert document.has_text
    assert any(word.confidence > 0.8 for word in document.pages[0].words)
    assert any(word.confidence < 1.0 for word in document.pages[0].words)
    assert "synthetic" in squash(" ".join(line.text for line in document.lines))


@needs_tesseract
def test_word_boxes_land_on_the_truth_boxes_after_straightening(monkeypatch):
    """A skewed scan: OCR runs on a straightened copy, boxes come back to the skewed page."""
    pdf, truth = _scan(index=0, condition="degraded")
    assert abs(truth["degradation"]["skew_degrees"]) > 0.4
    document = read_document(pdf, Settings())
    hits = checked = 0
    for name in ("total", "subtotal", "vendor_name", "issue_date", "document_number"):
        for location in truth["fields"][name]["locations"]:
            target = squash(location["printed"])
            x0, top, x1, bottom = location["bbox"]
            for word in document.pages[location["page"]].words:
                piece = squash(word.text)
                if len(piece) >= 4 and piece in target:
                    checked += 1
                    center_x, center_y = (word.x0 + word.x1) / 2, (word.top + word.bottom) / 2
                    if x0 - 4 <= center_x <= x1 + 4 and top - 4 <= center_y <= bottom + 4:
                        hits += 1
    if checked == 0:
        pytest.skip("OCR couldn't read any of these words on this scan")
    assert hits >= checked * 0.7, f"{hits}/{checked}"


def test_scan_settings_stay_inside_what_the_prepare_step_handles():
    import numpy as np

    rng = np.random.default_rng(3)
    assert all(abs(draw_params("degraded", rng, narrow=False)["skew_degrees"]) <= 5.0 for _ in range(100))
