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


# Headroom below Windows' legacy ~260-char MAX_PATH limit -- files under
# this length are left alone (keeps them readable in logs/errors), and
# anything at or past it gets the `\\?\` treatment before it's a problem.
_LONG_PATH_THRESHOLD = 240


def to_extended_path(path: Path) -> Path:
    """Windows only: prefixes an absolute path with `\\\\?\\` (or
    `\\\\?\\UNC\\` for a UNC path) so Win32 file APIs skip the ~260-character
    MAX_PATH limit for this one call.

    This works regardless of the machine's "Enable Win32 long paths" group
    policy (`LongPathsEnabled`), which needs admin rights this app's users
    may not have -- corpora synced from OneDrive under deeply nested
    company folder names routinely exceed 260 characters. No-op on
    non-Windows and for paths already short enough that it wouldn't
    matter.

    Deliberately not applied to anything this app stores or displays
    (`record.file_path`, manifest entries, on-screen paths) -- only to the
    path handed to an actual filesystem call, so what a human reads never
    carries this prefix.
    """
    if sys.platform != "win32":
        return path
    text = str(path)
    if text.startswith("\\\\?\\") or len(text) < _LONG_PATH_THRESHOLD:
        return path
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def strip_extended_path(text: str) -> str:
    """Reverses `to_extended_path` on a string path. Needed because
    `os.walk()` builds every yielded dirpath by extending whatever root it
    was given -- walking an extended-prefixed root means every dirpath it
    yields carries the prefix too, and callers that compare against a
    plain corpus_root (relative_to, manifest keys, ...) need it gone
    again. A no-op for a path that never had the prefix.
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\") :]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\") :]
    return text
