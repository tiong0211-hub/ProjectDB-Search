"""Locates bundled Tesseract OCR / Poppler binaries at runtime.

On a normal dev machine (Tesseract/Poppler installed system-wide, on PATH)
these are no-ops. Inside the packaged embeddable-Python bundle (see
packaging/embeddable/BUILD.md), Tesseract and Poppler ship as plain folders
next to the interpreter rather than being installed system-wide, so
pytesseract/pdf2image need to be told exactly where to find them.

Detection is layout-based, not an env var: the bundle's own folder
structure (`<root>/python/python.exe` next to `<root>/tesseract/tesseract.exe`)
is the signal, so there's nothing a launcher script can forget to set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundle_root() -> Path | None:
    candidate = Path(sys.executable).resolve().parent.parent
    if (candidate / "tesseract" / "tesseract.exe").exists():
        return candidate
    return None


def configure_tesseract() -> None:
    """Call once at process startup. Safe/no-op outside the bundle."""
    root = _bundle_root()
    if root is None:
        return

    import pytesseract

    tesseract_dir = root / "tesseract"
    pytesseract.pytesseract.tesseract_cmd = str(tesseract_dir / "tesseract.exe")
    os.environ["TESSDATA_PREFIX"] = str(tesseract_dir / "tessdata")


def get_poppler_path() -> str | None:
    """Directory containing pdftoppm.exe etc., or None to rely on PATH."""
    root = _bundle_root()
    return str(root / "poppler") if root else None
