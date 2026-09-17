"""Creates a small synthetic document corpus under tests/fixtures/raw_docs/.

Three kinds of fixtures, matching the three metadata-extraction paths:

1. Filename/folder alone is enough (project + doc type both resolvable from
   the path) -- PDF fallback never runs for these; the PDF content itself
   is a real (reportlab-generated) but otherwise unimportant text PDF.
2. Filename/folder is NOT enough, but the PDF has a real text layer
   describing the document -- exercises the PDF-text fallback path.
3. Filename/folder is NOT enough and the "PDF" is scanned (no text layer,
   just a rasterized image) or a plain image file -- exercises the OCR
   fallback path, including a deliberately blank/unreadable case that
   should land in the manual review queue.

Run directly: `python tests/fixtures/generate_fixtures.py`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

RAW_DOCS_DIR = Path(__file__).parent / "raw_docs"
FONT_PATH = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"


def _write_text_pdf(path: Path, text: str) -> None:
    """A PDF with a real, extractable text layer (drawString embeds actual
    font glyphs + text operators pdfplumber can read back)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(792, 300))
    c.setFont("Helvetica", 16)
    c.drawString(40, 150, text)
    c.save()


def _render_text_image(text: str, size: tuple[int, int] = (1600, 300), font_size: int = 60) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), font_size)
    draw.text((40, 60), text, fill="black", font=font)
    return image


def _write_scanned_pdf(path: Path, image: Image.Image) -> None:
    """A PDF containing only a rasterized image -- no text layer at all,
    same as a real scanned document. pdfplumber.extract_text() returns ""
    for these, which is what should trigger the OCR fallback."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(image.width, image.height))
    c.drawImage(ImageReader(image), 0, 0, width=image.width, height=image.height)
    c.save()


def generate_into(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Filename/folder alone is enough; PDF fallback should NOT run. ---
    _write_text_pdf(
        target_dir / "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf",
        "Riverside Plant P&ID Unit 3 2021 Rev A",
    )
    _write_text_pdf(
        target_dir / "RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf",
        "Isometric Drawing HX-203 Rev 2",
    )
    _write_text_pdf(
        target_dir / "RiversidePlant/Datasheet/TK-14_Datasheet_2019.pdf",
        "Datasheet TK-14 2019",
    )
    _write_text_pdf(
        target_dir / "RiversidePlant/Memo/RiversidePlant_Memo_2020_SafetyReview.pdf",
        "Riverside Plant Memo 2020 Safety Review",
    )
    _write_text_pdf(
        target_dir / "CompressorStation/PID/CompressorStation_PID_Unit1_2018.pdf",
        "Compressor Station P&ID Unit 1 2018",
    )
    _render_text_image("Motor Room Electrical Panel").save(
        _ensure_parent(target_dir / "CompressorStation/Electrical/IMG_20190304_Electrical_MotorRoom.jpg")
    )

    # --- 2. Filename/folder is uninformative; a real PDF text layer supplies
    #        the metadata -- exercises the PDF-text fallback path. ---
    _write_text_pdf(
        target_dir / "Unsorted/old_backup_copy_final_v2.pdf",
        "Compressor Station Memo Safety Review 2015",
    )

    # --- 3. Filename/folder is uninformative and there's no text layer
    #        (scanned) -- exercises the OCR fallback path. ---
    _write_scanned_pdf(
        target_dir / "Unsorted/nameplate_photo_scan.pdf",
        _render_text_image("RIVERSIDE PLANT ISOMETRIC HX-450 2016", font_size=50),
    )

    # A blank scanned page -- no text at all, so OCR confidence is ~0.
    # Should be indexed on filename-only metadata (none, here) and flagged
    # in the manual review queue rather than blocking the pipeline.
    _write_scanned_pdf(
        target_dir / "Legacy/blurry_scan_noname.pdf",
        Image.new("RGB", (1600, 2000), "white"),
    )

    # --- 4. A plain image file (no PDF at all) needing direct OCR. ---
    _render_text_image("Riverside Plant Electrical MotorRoom P-330").save(
        _ensure_parent(target_dir / "Unsorted/photo_0042.jpg")
    )


def _ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def generate() -> None:
    generate_into(RAW_DOCS_DIR)
    print(f"Generated fixtures under {RAW_DOCS_DIR}")


if __name__ == "__main__":
    generate()
