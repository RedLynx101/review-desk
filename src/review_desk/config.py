"""Runtime settings, all from the environment so a client can point this at their own model.

A `.env` file in the working directory is read if present. Real environment variables win.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal `.env` reader: KEY=VALUE lines, `#` comments, optional quotes, no interpolation."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    #: OpenAI-compatible endpoint. This is the only host the app ever contacts.
    model_base_url: str = "http://localhost:1234/v1"
    model_name: str = "prism-ml/bonsai-27b"
    model_api_key: str = "not-needed-for-local"
    #: Long on purpose: a shared local server may queue a request for minutes before it starts.
    model_timeout_seconds: float = 1200.0
    model_max_tokens: int = 3500
    model_temperature: float = 0.0
    model_retries: int = 3
    #: Attach page images to the model request for scanned pages. Vision models only.
    model_send_images: bool = False

    max_upload_bytes: int = 25 * 1024 * 1024
    max_pages: int = 20
    #: Largest image a page may render to. A hostile page size can't exhaust memory past this.
    max_render_pixels: int = 40_000_000
    ocr_dpi: int = 300
    ocr_timeout_seconds: float = 180.0
    #: OCR each text-layer page too, and compare. A PDF's hidden text can say anything.
    verify_text_layer: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        defaults = cls()
        return cls(
            model_base_url=os.environ.get("REVIEW_DESK_MODEL_BASE_URL", defaults.model_base_url).rstrip("/"),
            model_name=os.environ.get("REVIEW_DESK_MODEL", defaults.model_name),
            model_api_key=os.environ.get("REVIEW_DESK_MODEL_API_KEY", defaults.model_api_key),
            model_timeout_seconds=_float("REVIEW_DESK_MODEL_TIMEOUT_SECONDS", defaults.model_timeout_seconds),
            model_max_tokens=_int("REVIEW_DESK_MODEL_MAX_TOKENS", defaults.model_max_tokens),
            model_temperature=_float("REVIEW_DESK_MODEL_TEMPERATURE", defaults.model_temperature),
            model_retries=_int("REVIEW_DESK_MODEL_RETRIES", defaults.model_retries),
            model_send_images=os.environ.get("REVIEW_DESK_MODEL_SEND_IMAGES", "").lower() in ("1", "true", "yes"),
            max_upload_bytes=_int("REVIEW_DESK_MAX_UPLOAD_BYTES", defaults.max_upload_bytes),
            max_pages=_int("REVIEW_DESK_MAX_PAGES", defaults.max_pages),
            max_render_pixels=_int("REVIEW_DESK_MAX_RENDER_PIXELS", defaults.max_render_pixels),
            ocr_dpi=_int("REVIEW_DESK_OCR_DPI", defaults.ocr_dpi),
            ocr_timeout_seconds=_float("REVIEW_DESK_OCR_TIMEOUT_SECONDS", defaults.ocr_timeout_seconds),
            verify_text_layer=os.environ.get("REVIEW_DESK_VERIFY_TEXT_LAYER", "true").lower() not in ("0", "false", "no"),
        )
