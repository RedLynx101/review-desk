"""OCR through the Tesseract program, with word boxes mapped back to the original page.

Order matters on a bad scan. Noise has to come off at the scan's own resolution: upscale first and each
speckle turns into a blob that Tesseract spends a minute reading as letters. So the steps are render at
native resolution, straighten, denoise, upscale, flatten the lighting. Boxes go back through the same
transform, so a highlight lands on the page image a person is looking at.
"""

from __future__ import annotations

import csv
import io
import math
import os
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageFilter

from .tesseract import find_tesseract

if TYPE_CHECKING:
    from .pagetext import Word


class OcrUnavailable(Exception):
    """Tesseract is missing, or it failed on this page."""


@dataclass(frozen=True)
class OcrOptions:
    psm: int = 6
    deskew: bool = True
    denoise_level: int | None = None  # Index into DENOISE_LEVELS. None decides from measured noise.
    flatten: bool = True
    target_dpi: int = 300

    def as_dict(self) -> dict:
        return {"psm": self.psm, "deskew": self.deskew, "denoise_level": self.denoise_level, "flatten": self.flatten, "target_dpi": self.target_dpi}


DEFAULT_OPTIONS = OcrOptions()

#: (median filter size, Gaussian blur radius) applied at the scan's native resolution.
DENOISE_LEVELS = ((0, 0.0), (3, 0.0), (3, 0.6), (3, 1.0), (5, 1.0), (5, 1.4))
#: Measured on the dev scans: a median filter costs a clean scan about 2% of its values and saves a
#: noisy one from thousands of junk words. Stronger filtering erased more text than noise.
MEDIAN_ABOVE_NOISE = 2.5
#: Words Tesseract rates below this are nearly all noise read as letters. Dropping them cut a third of
#: the words on bad scans, and 1.3% of the values that could be read.
MIN_WORD_CONFIDENCE = 0.30


@dataclass(frozen=True)
class OcrResult:
    words: list["Word"]
    settings: dict


def estimate_noise(gray: Image.Image) -> float:
    """How grainy the paper is: the spread of what a blur removes, measured robustly.

    Most of a page is blank paper, so the median ignores the text. A fine-scale filter would miss
    JPEG-compressed noise, which survives as blobs several pixels wide, hence the wide blur.
    """
    blurred = gray.filter(ImageFilter.GaussianBlur(3))
    difference = np.asarray(gray, dtype=np.float32) - np.asarray(blurred, dtype=np.float32)
    return float(np.median(np.abs(difference)) * 1.4826)


def estimate_skew(gray: Image.Image, *, max_angle: float = 5.0) -> float:
    """Estimate text rotation in degrees by finding the shear that makes rows sharpest.

    A rotated page's dark pixels smear across row histograms. At the right angle, rows of text
    line up and the histogram gets spiky, which `sum(diff^2)` measures.
    """
    small = gray.copy()
    small.thumbnail((1100, 1100))
    pixels = np.asarray(small.filter(ImageFilter.MedianFilter(3)), dtype=np.float32)
    background = np.percentile(pixels, 90)
    ys, xs = np.nonzero(pixels < background * 0.72)
    if xs.size < 200:
        return 0.0
    if xs.size > 150_000:
        keep = np.linspace(0, xs.size - 1, 150_000).astype(int)
        xs, ys = xs[keep], ys[keep]

    def sharpness(degrees: float) -> float:
        shifted = ys + xs * math.tan(math.radians(degrees))
        rows = np.bincount((shifted - shifted.min()).astype(np.int64))
        return float(np.sum(np.diff(rows.astype(np.float64)) ** 2))

    def best(angles) -> float:
        return max(angles, key=sharpness)

    coarse = best(np.arange(-max_angle, max_angle + 0.001, 0.25))
    return float(best(np.arange(coarse - 0.25, coarse + 0.2501, 0.05)))


def _straighten(gray: Image.Image, degrees: float):
    """Rotate by `degrees` about the center. Returns the image and the output-to-input matrix."""
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    width, height = gray.size
    out_w = int(math.ceil(abs(width * cos) + abs(height * sin)))
    out_h = int(math.ceil(abs(width * sin) + abs(height * cos)))
    # PIL's AFFINE maps output pixels to input pixels: x_in = a x_out + b y_out + c.
    a, b = cos, sin
    d, e = -sin, cos
    c = width / 2 - a * out_w / 2 - b * out_h / 2
    f = height / 2 - d * out_w / 2 - e * out_h / 2
    fill = int(np.percentile(np.asarray(gray, dtype=np.uint8)[::8, ::8], 90))
    rotated = gray.transform((out_w, out_h), Image.Transform.AFFINE, (a, b, c, d, e, f), Image.Resampling.BICUBIC, fillcolor=fill)
    return rotated, (a, b, c, d, e, f)


@dataclass(frozen=True)
class Prepared:
    image: Image.Image
    matrix: tuple | None  # Output-to-input rotation matrix in native pixels, or None.
    upscale: float  # Prepared pixels per native pixel.
    skew: float
    noise: float
    denoise_level: int


def prepare(image: Image.Image, scale: float, options: OcrOptions) -> Prepared:
    """Clean a page rendered at `scale` pixels per point (ideally its native resolution)."""
    gray = image.convert("L")
    noise = estimate_noise(gray)
    matrix = None
    skew = 0.0
    if options.deskew:
        skew = estimate_skew(gray)
        if abs(skew) >= 0.1:
            gray, matrix = _straighten(gray, skew)
    level = options.denoise_level
    if level is None:
        level = 1 if noise >= MEDIAN_ABOVE_NOISE else 0
    median, blur = DENOISE_LEVELS[level]
    if median:
        gray = gray.filter(ImageFilter.MedianFilter(median))
    if blur:
        gray = gray.filter(ImageFilter.GaussianBlur(blur))
    upscale = max(1.0, options.target_dpi / (scale * 72.0))
    if upscale > 1.01:
        gray = gray.resize((int(gray.width * upscale), int(gray.height * upscale)), Image.Resampling.LANCZOS)
    else:
        upscale = 1.0
    if options.flatten:
        radius = max(gray.width, gray.height) / 40
        background = np.asarray(gray.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
        pixels = np.asarray(gray, dtype=np.float32)
        flattened = np.clip(pixels * (np.percentile(background, 95) / np.maximum(background, 1.0)), 0, 255)
        gray = Image.fromarray(flattened.astype(np.uint8))
    return Prepared(gray, matrix, upscale, skew, noise, level)


def run_tesseract(image: Image.Image, *, psm: int, dpi: int, tesseract: str | None, timeout: float) -> list[dict]:
    """Run Tesseract and return its word rows. The binary comes from `find_tesseract` by default."""
    binary = tesseract or find_tesseract()
    if not binary:
        raise OcrUnavailable("Tesseract wasn't found. Install it, or set TESSERACT_CMD to its path.")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    command = [binary, "stdin", "stdout", "--dpi", str(dpi), "--psm", str(psm), "-l", "eng", "tsv"]
    # Extra threads don't speed up this workload and they crowd out the model on a shared machine.
    environment = dict(os.environ, OMP_THREAD_LIMIT="1")
    try:
        completed = subprocess.run(command, input=buffer.getvalue(), capture_output=True, timeout=timeout, check=False, env=environment)
    except subprocess.TimeoutExpired as error:
        raise OcrUnavailable(f"Tesseract took longer than {timeout:.0f} seconds on this page.") from error
    except OSError as error:
        raise OcrUnavailable(f"Tesseract couldn't start: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise OcrUnavailable(f"Tesseract failed on this page: {detail}")
    text = completed.stdout.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [row for row in reader if row.get("level") == "5" and (row.get("text") or "").strip()]


def ocr_page(
    image: Image.Image,
    scale: float,
    page_number: int,
    *,
    options: OcrOptions = DEFAULT_OPTIONS,
    tesseract: str | None = None,
    timeout: float = 180.0,
) -> OcrResult:
    """OCR one rendered page. `scale` is image pixels per PDF point."""
    from .pagetext import Word

    prepared = prepare(image, scale, options)
    dpi = int(round(scale * 72 * prepared.upscale))
    try:
        rows = run_tesseract(prepared.image, psm=options.psm, dpi=dpi, tesseract=tesseract, timeout=timeout)
    except OcrUnavailable as error:
        if "bad_alloc" not in str(error):
            raise
        time.sleep(20.0)
        rows = run_tesseract(prepared.image, psm=options.psm, dpi=dpi, tesseract=tesseract, timeout=timeout)
    words = []
    for row in rows:
        confidence = max(0.0, min(1.0, float(row["conf"]) / 100.0))
        if confidence < MIN_WORD_CONFIDENCE:
            continue
        left = float(row["left"]) / prepared.upscale
        top = float(row["top"]) / prepared.upscale
        right = left + float(row["width"]) / prepared.upscale
        bottom = top + float(row["height"]) / prepared.upscale
        if prepared.matrix is None:
            x0, y0, x1, y1 = left, top, right, bottom
        else:
            a, b, c, d, e, f = prepared.matrix
            corners = [(a * x + b * y + c, d * x + e * y + f) for x, y in ((left, top), (right, top), (left, bottom), (right, bottom))]
            xs, ys = zip(*corners)
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        words.append(Word(row["text"].strip(), x0 / scale, y0 / scale, x1 / scale, y1 / scale, page_number, confidence))
    settings = {
        **options.as_dict(), "denoise_level": prepared.denoise_level, "skew_degrees": round(prepared.skew, 2),
        "noise": round(prepared.noise, 1), "ocr_dpi": dpi, "native_dpi": round(scale * 72),
    }
    return OcrResult(words, settings)
