"""Turn a clean page into a scan with known geometry.

Each page goes through an affine transform we compute ourselves, so every field location in
the truth can be moved along with the pixels. A rotated scan keeps exact answers about where
each value sits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

CONDITIONS = ("clean", "scanned", "degraded")


@dataclass(frozen=True)
class PageTransform:
    """Maps a point on the source page to the output page, both in points, top-left origin."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    width: float
    height: float

    def point(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f

    def bbox(self, box: list[float]) -> list[float]:
        """Axis-aligned bounds of a transformed box. A skewed box grows a little, which is honest."""
        x0, top, x1, bottom = box
        corners = [self.point(x, y) for x, y in ((x0, top), (x1, top), (x0, bottom), (x1, bottom))]
        xs, ys = zip(*corners)
        return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def draw_params(condition: str, rng: np.random.Generator, *, narrow: bool) -> dict:
    """Pick the scan settings for a document. These get written into its truth file."""
    if condition == "scanned":
        return {
            "severity": "light",
            "dpi": int(rng.integers(170, 241)),
            "skew_degrees": round(float(rng.uniform(-0.4, 0.4)), 3),
            "blur_radius": round(float(rng.uniform(0.0, 0.35)), 3),
            "noise_sigma": round(float(rng.uniform(1.5, 4.0)), 2),
            "speckle_fraction": 0.0,
            "contrast": round(float(rng.uniform(0.92, 1.0)), 3),
            "brightness": round(float(rng.uniform(-3, 3)), 1),
            "jpeg_quality": int(rng.integers(68, 88)),
            "grayscale": bool(rng.random() < 0.7),
            "shadow": 0.0,
            "bed_points": int(rng.integers(14, 36)) if narrow else 0,
            "bed_gray": int(rng.integers(226, 246)),
        }
    if condition != "degraded":
        raise ValueError(f"no scan parameters for condition: {condition}")
    heavy = bool(rng.random() < 0.45)
    if heavy:
        settings = {
            "severity": "heavy",
            "dpi": int(rng.integers(84, 111)),
            "skew_degrees": round(float(rng.uniform(1.5, 4.0)) * rng.choice((-1, 1)), 3),
            "blur_radius": round(float(rng.uniform(0.6, 1.1)), 3),
            "noise_sigma": round(float(rng.uniform(11, 20)), 2),
            "speckle_fraction": round(float(rng.uniform(0.002, 0.008)), 4),
            "contrast": round(float(rng.uniform(0.55, 0.75)), 3),
            "brightness": round(float(rng.uniform(-14, 18)), 1),
            "jpeg_quality": int(rng.integers(22, 40)),
            "shadow": round(float(rng.uniform(0.15, 0.38)), 3),
        }
    else:
        settings = {
            "severity": "moderate",
            "dpi": int(rng.integers(110, 151)),
            "skew_degrees": round(float(rng.uniform(0.5, 2.0)) * rng.choice((-1, 1)), 3),
            "blur_radius": round(float(rng.uniform(0.4, 0.8)), 3),
            "noise_sigma": round(float(rng.uniform(5, 11)), 2),
            "speckle_fraction": round(float(rng.uniform(0.0005, 0.002)), 4),
            "contrast": round(float(rng.uniform(0.72, 0.9)), 3),
            "brightness": round(float(rng.uniform(-8, 10)), 1),
            "jpeg_quality": int(rng.integers(40, 62)),
            "shadow": round(float(rng.uniform(0.0, 0.18)), 3),
        }
    settings["grayscale"] = bool(rng.random() < 0.8)
    settings["bed_points"] = int(rng.integers(18, 48)) if narrow else 0
    settings["bed_gray"] = int(rng.integers(120, 205))
    return settings


def rasterize(pdf_bytes: bytes, dpi: int) -> list[Image.Image]:
    """Render every page of a PDF to RGB. PDFium doesn't run document scripts in this path."""
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return [
            page.render(scale=dpi / 72.0, rev_byteorder=True).to_pil().convert("RGB")
            for page in document
        ]
    finally:
        document.close()


def _rotation(width: int, height: int, degrees: float):
    """Forward matrix (source pixel to output pixel) about the page center, plus the output size."""
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    out_w = int(math.ceil(abs(width * cos) + abs(height * sin)))
    out_h = int(math.ceil(abs(width * sin) + abs(height * cos)))
    c = out_w / 2 - cos * width / 2 + sin * height / 2
    f = out_h / 2 - sin * width / 2 - cos * height / 2
    return (cos, -sin, c, sin, cos, f), (out_w, out_h)


def degrade_page(image: Image.Image, params: dict, rng: np.random.Generator) -> tuple[Image.Image, PageTransform]:
    """Apply bed, skew, blur, fade, noise, speckle and shadow. Return the page and its transform."""
    dpi = params["dpi"]
    scale = dpi / 72.0
    bed_px = int(round(params["bed_points"] * scale))
    paper = image.convert("L").convert("RGB") if params["grayscale"] else image
    if bed_px:
        canvas = Image.new("RGB", (paper.width + 2 * bed_px, paper.height + 2 * bed_px), (params["bed_gray"],) * 3)
        canvas.paste(paper, (bed_px, bed_px))
        paper = canvas

    forward, (out_w, out_h) = _rotation(paper.width, paper.height, params["skew_degrees"])
    a, b, c, d, e, f = forward
    # PIL wants the output-to-source matrix. For a rotation the inverse is the transpose.
    inverse = (a, d, -(a * c + d * f), b, e, -(b * c + e * f))
    fill = (params["bed_gray"],) * 3 if bed_px else (248, 248, 246)
    rotated = paper.transform((out_w, out_h), Image.Transform.AFFINE, inverse, Image.Resampling.BICUBIC, fillcolor=fill)

    if params["blur_radius"] > 0:
        rotated = rotated.filter(ImageFilter.GaussianBlur(params["blur_radius"]))

    pixels = np.asarray(rotated, dtype=np.float32)
    pixels = 128 + (pixels - 128) * params["contrast"] + params["brightness"]
    if params["shadow"] > 0:
        # A dark band falling off from one edge, as a book spine or scanner lid leaves.
        ramp = np.linspace(1.0 - params["shadow"], 1.0, num=out_w // 4, dtype=np.float32)
        band = np.ones(out_w, dtype=np.float32)
        band[: ramp.size] = ramp
        if rng.random() < 0.5:
            band = band[::-1]
        pixels = pixels * band[None, :, None]
    noise = rng.normal(0.0, params["noise_sigma"], size=pixels.shape[:2]).astype(np.float32)
    pixels = pixels + noise[..., None]
    if params["speckle_fraction"] > 0:
        mask = rng.random(size=pixels.shape[:2]) < params["speckle_fraction"]
        values = rng.choice((0.0, 255.0), size=int(mask.sum()))
        pixels[mask] = values[:, None]
    result = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")

    # The transform works in points on both sides, so scale cancels out of the rotation terms.
    transform = PageTransform(
        a=a, b=b, c=(a * bed_px + b * bed_px + c) / scale,
        d=d, e=e, f=(d * bed_px + e * bed_px + f) / scale,
        width=round(out_w / scale, 2), height=round(out_h / scale, 2),
    )
    return result, transform


def encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=False, subsampling=2)
    return buffer.getvalue()
