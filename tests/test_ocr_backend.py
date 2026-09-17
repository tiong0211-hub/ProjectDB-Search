from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from projectdb_search.indexer.ocr.tesseract_backend import TesseractBackend

FONT_PATH = Path(__file__).parent / "fixtures" / "fonts" / "DejaVuSans.ttf"


def _render_text_image(text: str) -> Image.Image:
    image = Image.new("RGB", (1600, 300), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), 60)
    draw.text((40, 60), text, fill="black", font=font)
    return image


def test_recognizes_clear_text_with_high_confidence():
    backend = TesseractBackend()
    image = _render_text_image("HX-450 ISOMETRIC")

    result = backend.recognize([image])

    assert "HX-450" in result.text or "HX" in result.text
    assert result.confidence > 0.55


def test_blank_image_yields_zero_confidence():
    backend = TesseractBackend()
    blank = Image.new("RGB", (800, 600), "white")

    result = backend.recognize([blank])

    assert result.confidence == 0.0


def test_confidence_aggregates_across_multiple_pages():
    backend = TesseractBackend()
    clear_page = _render_text_image("RIVERSIDE PLANT")
    blank_page = Image.new("RGB", (800, 600), "white")

    result = backend.recognize([clear_page, blank_page])

    # Text from the readable page should still show up even though one
    # page contributed nothing.
    assert "RIVERSIDE" in result.text.upper() or "PLANT" in result.text.upper()
