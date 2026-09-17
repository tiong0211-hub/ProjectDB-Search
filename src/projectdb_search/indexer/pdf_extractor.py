"""PDF-only text/image extraction for the fallback pass.

Deliberately scoped to PDFs (and, via `ocr/tesseract_backend.py`, plain
image files). Office formats (Word/Excel/PowerPoint) are never opened by
this project — many real-world copies of those are internal
security-restricted files, and parsing them is out of scope regardless.
`indexer/pipeline.py` only ever calls this module for paths whose extension
is in `config.extraction.pdf_extensions`.

Both text extraction and page rasterization go through pypdfium2 alone
(no pdfplumber): pdfplumber builds a full character-object tree per page,
which is 10-30x slower than pypdfium2's text API for the same PDF, and
using one library for both jobs means a PDF is only opened once.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def extract_text_layer(path: Path, max_pages: int | None = None) -> str | None:
    """Direct text extraction from a PDF's embedded text layer, if any.

    Only reads the first `max_pages` pages (None = all) -- the metadata
    this tool needs (project/doc-type/tag) lives on a cover page or title
    block, not deep in a long document, and skipping the rest is one of
    the biggest levers on indexing time for multi-page manuals.

    Returns None if the PDF can't be opened at all. Returns "" (not None)
    for a PDF that opens fine but has no extractable text (e.g. a scanned
    document with no OCR layer) — the caller decides the "usable" threshold
    (see config.extraction.min_text_chars), not this function.
    """
    try:
        pdf = pdfium.PdfDocument(str(path))
        pages = pdf if max_pages is None else [pdf[i] for i in range(min(max_pages, len(pdf)))]
        pages_text = []
        for page in pages:
            textpage = page.get_textpage()
            try:
                pages_text.append(textpage.get_text_range())
            finally:
                textpage.close()
    except Exception:
        return None
    return "\n".join(pages_text).strip()


def render_pages_to_images(
    path: Path, dpi: int = 200, max_pages: int | None = None, max_side_px: int | None = None
) -> list[Image.Image]:
    """Rasterize a PDF's pages to PIL images, for OCR fallback.

    Only the first `max_pages` pages are rendered (None = all) -- same
    rationale as `extract_text_layer`.

    `max_side_px`, if given, caps the longer side of each rendered image:
    large-format drawings (A0/A1) rasterized at a flat DPI can exceed 30
    megapixels, which takes Tesseract tens of seconds per page. The scale
    actually used is whichever is smaller between the DPI-implied scale and
    the scale needed to respect the cap -- normal page sizes are unaffected.

    Returns [] (rather than raising) for a PDF that can't be rasterized at
    all (corrupt/truncated file) — a legacy archive will have some of
    these, and one unreadable file must never abort the whole indexing run.
    """
    try:
        pdf = pdfium.PdfDocument(str(path))
        pages = pdf if max_pages is None else [pdf[i] for i in range(min(max_pages, len(pdf)))]
        images = []
        for page in pages:
            scale = dpi / 72  # pypdfium2 renders at a scale relative to the PDF's native 72 dpi
            if max_side_px is not None:
                width_pt, height_pt = page.get_size()
                longer_side_pt = max(width_pt, height_pt)
                if longer_side_pt > 0:
                    scale = min(scale, max_side_px / longer_side_pt)
            images.append(page.render(scale=scale, grayscale=True).to_pil())
        return images
    except Exception:
        return []
