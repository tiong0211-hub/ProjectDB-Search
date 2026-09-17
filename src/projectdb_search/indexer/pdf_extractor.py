"""PDF-only text/image extraction for the fallback pass.

Deliberately scoped to PDFs (and, via `ocr/tesseract_backend.py`, plain
image files). Office formats (Word/Excel/PowerPoint) are never opened by
this project — many real-world copies of those are internal
security-restricted files, and parsing them is out of scope regardless.
`indexer/pipeline.py` only ever calls this module for paths whose extension
is in `config.extraction.pdf_extensions`.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


def extract_text_layer(path: Path) -> str | None:
    """Direct text extraction from a PDF's embedded text layer, if any.

    Returns None if the PDF can't be opened at all. Returns "" (not None)
    for a PDF that opens fine but has no extractable text (e.g. a scanned
    document with no OCR layer) — the caller decides the "usable" threshold
    (see config.extraction.min_text_chars), not this function.
    """
    try:
        with pdfplumber.open(path) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
    except Exception:
        return None
    return "\n".join(pages_text).strip()


def render_pages_to_images(path: Path, dpi: int = 200) -> list[Image.Image]:
    """Rasterize every page of a PDF to a PIL image, for OCR fallback.

    Uses pypdfium2 (a compiled wheel, already a transitive dependency via
    pdfplumber) rather than shelling out to Poppler's pdftoppm -- one fewer
    external binary to vendor/locate at runtime, and no `poppler_path`
    plumbing needed.

    Returns [] (rather than raising) for a PDF that can't be rasterized at
    all (corrupt/truncated file) — a legacy archive will have some of
    these, and one unreadable file must never abort the whole indexing run.
    """
    try:
        pdf = pdfium.PdfDocument(str(path))
        scale = dpi / 72  # pypdfium2 renders at a scale relative to the PDF's native 72 dpi
        return [page.render(scale=scale).to_pil() for page in pdf]
    except Exception:
        return []
