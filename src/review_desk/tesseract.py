"""Find the Tesseract binary. It's an external program, so its path differs per machine."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_COMMON_LOCATIONS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)


def find_tesseract() -> str | None:
    """Return a Tesseract path: `TESSERACT_CMD` first, then PATH, then the usual install folders."""
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        # A configured path that's wrong should fail loudly, not quietly fall back to another binary.
        return configured if Path(configured).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    return next((path for path in _COMMON_LOCATIONS if Path(path).is_file()), None)
