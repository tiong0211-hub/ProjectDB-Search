"""Locates the bundled Tesseract OCR binary at runtime.

On a normal dev machine (Tesseract installed system-wide, on PATH) this is
a no-op. Inside the packaged embeddable-Python bundle (see
packaging/embeddable/BUILD.md), Tesseract ships as a plain folder next to
the interpreter rather than being installed system-wide, so pytesseract
needs to be told exactly where to find it. (PDF page rendering uses
pypdfium2, a compiled wheel -- no separate Poppler binary is needed.)

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
