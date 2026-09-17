"""Tesseract-based OCR backend (local, open-source, free — no network call).

Chosen as the first OCR engine per the project plan; if accuracy on real
scanned documents turns out insufficient, a cloud OCR backend can be added
later as another `OCRBackend` implementation with no changes to the
pipeline, since callers only depend on the interface in `base.py`.
"""

from __future__ import annotations

import pytesseract
from PIL import Image

from projectdb_search.indexer.ocr.base import OCRBackend, OCRResult


class TesseractBackend(OCRBackend):
    def __init__(self, tesseract_cmd: str | None = None):
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def recognize(self, images: list[Image.Image]) -> OCRResult:
        page_texts: list[str] = []
        word_confidences: list[float] = []

        for image in images:
            # Grayscale cuts Tesseract's per-pixel work (1 channel instead
            # of 3) with no accuracy loss for this tool's purposes; PDF
            # pages already come in pre-rendered as grayscale (see
            # pdf_extractor.render_pages_to_images), but photos from image
            # files still arrive in color, hence converting unconditionally
            # here rather than relying on the caller.
            if image.mode != "L":
                image = image.convert("L")
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            words = [w for w in data["text"] if w.strip()]
            page_texts.append(" ".join(words))
            word_confidences.extend(float(c) for c in data["conf"] if str(c) not in ("-1", "-1.0"))

        overall_confidence = (sum(word_confidences) / len(word_confidences) / 100) if word_confidences else 0.0
        return OCRResult(text="\n".join(page_texts), confidence=overall_confidence)
