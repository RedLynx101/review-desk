"""Scans have no text layer, get worse as promised, and keep their field boxes on the ink."""

from __future__ import annotations

import io
import subprocess

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from review_desk.synth.degrade import degrade_page, draw_params, rasterize
from review_desk.tesseract import find_tesseract

TESSERACT = find_tesseract()
needs_ocr = pytest.mark.skipif(TESSERACT is None, reason="no Tesseract binary found")

_QUIET = {
    "dpi": 100,
    "blur_radius": 0.0,
    "noise_sigma": 0.0,
    "speckle_fraction": 0.0,
    "contrast": 1.0,
    "brightness": 0.0,
    "jpeg_quality": 95,
    "grayscale": False,
    "shadow": 0.0,
    "bed_points": 0,
    "bed_gray": 200,
}


def _ocr(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = subprocess.run(
        [TESSERACT, "stdin", "stdout", "--psm", "6"], input=buffer.getvalue(), capture_output=True, check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def test_scans_have_no_text_layer_and_one_image_per_page(scan_corpus):
    for doc, condition, pdf, truth in scan_corpus:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            assert len(document.pages) == truth["page_count"]
            for page in document.pages:
                assert page.chars == [], (doc.base_id, condition)
                assert len(page.images) == 1
            assert "synthetic" in document.metadata["Subject"].lower()
            assert truth["synthetic"] is True


def test_truth_records_the_scan_settings(scan_corpus):
    for _, condition, _, truth in scan_corpus:
        settings = truth["degradation"]
        assert settings["severity"] in {"light", "moderate", "heavy"}
        assert (condition == "scanned") == (settings["severity"] == "light")
        assert truth["condition"] == condition


def test_degraded_settings_are_harsher_than_scanned_ones():
    rng = np.random.default_rng(7)
    scanned = [draw_params("scanned", rng, narrow=False) for _ in range(200)]
    degraded = [draw_params("degraded", rng, narrow=False) for _ in range(200)]
    assert max(p["dpi"] for p in degraded) <= min(p["dpi"] for p in scanned)
    assert max(abs(p["skew_degrees"]) for p in scanned) < min(abs(p["skew_degrees"]) for p in degraded)
    assert np.mean([p["noise_sigma"] for p in degraded]) > 2 * np.mean([p["noise_sigma"] for p in scanned])
    assert {p["severity"] for p in degraded} == {"moderate", "heavy"}


@pytest.mark.parametrize("skew", [-4.0, -1.0, 0.0, 2.5])
@pytest.mark.parametrize("bed", [0, 30])
def test_transform_moves_a_box_exactly_with_the_pixels(skew, bed):
    """Draw one black block, push it through skew and padding, check the math landed on it."""
    dpi = 100
    scale = dpi / 72.0
    box_pt = [150.0, 300.0, 300.0, 340.0]
    page = Image.new("RGB", (int(612 * scale), int(792 * scale)), "white")
    ImageDraw.Draw(page).rectangle([v * scale for v in box_pt], fill="black")

    params = {**_QUIET, "skew_degrees": skew, "bed_points": bed}
    result, transform = degrade_page(page, params, np.random.default_rng(1))
    assert abs(result.width / scale - transform.width) < 0.5

    pixels = np.asarray(result.convert("L"), dtype=np.float32)
    cx, cy = transform.point((box_pt[0] + box_pt[2]) / 2, (box_pt[1] + box_pt[3]) / 2)
    assert pixels[int(cy * scale), int(cx * scale)] < 40

    # Inside corners of the moved block stay dark; a point past its end stays light.
    for x, y in ((box_pt[0] + 6, box_pt[1] + 6), (box_pt[2] - 6, box_pt[3] - 6)):
        px, py = transform.point(x, y)
        assert pixels[int(py * scale), int(px * scale)] < 60, (skew, bed, x, y)
    for x, y in ((box_pt[0] - 8, box_pt[1] + 20), (box_pt[2] + 8, box_pt[3] - 20)):
        px, py = transform.point(x, y)
        assert pixels[int(py * scale), int(px * scale)] > 200, (skew, bed, x, y)


def test_moved_field_boxes_stay_on_the_page_and_cover_ink(scan_corpus):
    for doc, condition, pdf, truth in scan_corpus:
        dpi = truth["degradation"]["dpi"]
        images = rasterize(pdf, dpi)
        scale = dpi / 72.0
        for name in ("total", "vendor_name", "issue_date"):
            for location in truth["fields"][name]["locations"]:
                x0, top, x1, bottom = location["bbox"]
                width, height = truth["page_sizes"][location["page"]]
                assert -1 <= x0 < x1 <= width + 1 and -1 <= top < bottom <= height + 1, doc.base_id
                if condition != "scanned":
                    continue  # Heavy noise would pass this contrast check on a blank patch.
                image = images[location["page"]].convert("L")
                region = np.asarray(image.crop((int(x0 * scale), int(top * scale), int(x1 * scale) + 1, int(bottom * scale) + 1)), dtype=np.float32)
                # Banner text is white on dark, so measure contrast inside the box, not darkness.
                assert region.std() > 12, (doc.base_id, condition, name)


@needs_ocr
def test_ocr_reads_the_total_at_its_box_on_a_light_scan(scan_corpus):
    hits = attempts = 0
    for _, condition, pdf, truth in scan_corpus:
        if condition != "scanned":
            continue
        dpi = truth["degradation"]["dpi"]
        scale = dpi / 72.0
        location = truth["fields"]["total"]["locations"][-1]
        image = rasterize(pdf, dpi)[location["page"]].convert("L")
        x0, top, x1, bottom = location["bbox"]
        crop = image.crop((int(x0 * scale) - 8, int(top * scale) - 8, int(x1 * scale) + 8, int(bottom * scale) + 8))
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
        digits = "".join(ch for ch in location["printed"] if ch.isdigit())
        attempts += 1
        if digits in "".join(ch for ch in _ocr(crop) if ch.isdigit()):
            hits += 1
    assert attempts >= 8
    # Not a claim about OCR quality. It only proves the boxes point at the right digits.
    assert hits >= attempts * 0.75, f"{hits}/{attempts}"


@needs_ocr
def test_scanned_pages_show_the_synthetic_label_to_a_reader(scan_corpus):
    seen = checked = 0
    for _, condition, pdf, truth in scan_corpus:
        if condition != "scanned":
            continue
        checked += 1
        document = pdfium.PdfDocument(pdf)
        try:
            image = document[0].render(scale=3.0).to_pil().convert("L")
        finally:
            document.close()
        tail = image.crop((0, int(image.height * 0.8), image.width, image.height))
        if "SYNTHETIC" in _ocr(tail).upper():
            seen += 1
    assert seen >= checked * 0.75, f"{seen}/{checked}"


def test_tesseract_lookup_honors_the_environment(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract-custom"
    fake.write_text("")
    monkeypatch.setenv("TESSERACT_CMD", str(fake))
    assert find_tesseract() == str(fake)
    monkeypatch.setenv("TESSERACT_CMD", str(tmp_path / "missing"))
    assert find_tesseract() is None
