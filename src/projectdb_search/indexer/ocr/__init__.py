"""OCR backend selection. Tesseract only for now — swapping in a cloud OCR
backend later (if accuracy is insufficient) is a new class plus one branch
here, per `OCRBackend` in base.py.
"""

from __future__ import annotations

from projectdb_search.indexer.ocr.base import OCRBackend
from projectdb_search.indexer.ocr.tesseract_backend import TesseractBackend


def get_ocr_backend() -> OCRBackend:
    return TesseractBackend()
