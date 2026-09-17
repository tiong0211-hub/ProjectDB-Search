from __future__ import annotations

from pathlib import Path

from projectdb_search.indexer import pdf_extractor


def test_extract_text_layer_returns_real_text_for_text_pdf(corpus_root: Path):
    path = corpus_root / "Unsorted" / "old_backup_copy_final_v2.pdf"
    text = pdf_extractor.extract_text_layer(path)
    assert text is not None
    assert "Compressor Station" in text
    assert "2015" in text


def test_extract_text_layer_returns_empty_for_scanned_pdf(corpus_root: Path):
    path = corpus_root / "Unsorted" / "nameplate_photo_scan.pdf"
    text = pdf_extractor.extract_text_layer(path)
    # The "PDF" is a rasterized image with no text operators at all.
    assert text == ""


def test_render_pages_to_images_returns_expected_page_count(corpus_root: Path):
    path = corpus_root / "Unsorted" / "nameplate_photo_scan.pdf"
    images = pdf_extractor.render_pages_to_images(path, dpi=150)
    assert len(images) == 1


def test_render_pages_to_images_returns_empty_list_for_corrupt_pdf(tmp_path: Path):
    bad_pdf = tmp_path / "not_really_a_pdf.pdf"
    bad_pdf.write_bytes(b"this is not a valid pdf at all")

    images = pdf_extractor.render_pages_to_images(bad_pdf)

    assert images == []


def _multi_page_pdf(tmp_path: Path, page_texts: list[str], pagesize: tuple[int, int] = (400, 200)) -> Path:
    from reportlab.pdfgen import canvas

    path = tmp_path / "multi.pdf"
    c = canvas.Canvas(str(path), pagesize=pagesize)
    for text in page_texts:
        c.setFont("Helvetica", 16)
        c.drawString(20, pagesize[1] / 2, text)
        c.showPage()
    c.save()
    return path


def test_extract_text_layer_respects_max_pages(tmp_path: Path):
    path = _multi_page_pdf(tmp_path, ["UNIQUEMARKER0", "UNIQUEMARKER1", "UNIQUEMARKER2"])

    text = pdf_extractor.extract_text_layer(path, max_pages=1)

    assert "UNIQUEMARKER0" in text
    assert "UNIQUEMARKER2" not in text


def test_extract_text_layer_reads_all_pages_by_default(tmp_path: Path):
    path = _multi_page_pdf(tmp_path, ["UNIQUEMARKER0", "UNIQUEMARKER1", "UNIQUEMARKER2"])

    text = pdf_extractor.extract_text_layer(path)

    assert "UNIQUEMARKER0" in text
    assert "UNIQUEMARKER2" in text


def test_render_pages_to_images_respects_max_pages(tmp_path: Path):
    path = _multi_page_pdf(tmp_path, ["a", "b", "c", "d", "e"])

    images = pdf_extractor.render_pages_to_images(path, max_pages=2)

    assert len(images) == 2


def test_render_pages_to_images_caps_the_longer_side(tmp_path: Path):
    # A large-format page (roughly A1-sized, in points) rendered at a high
    # DPI would exceed the cap without max_side_px.
    path = _multi_page_pdf(tmp_path, ["drawing"], pagesize=(1683, 2384))

    images = pdf_extractor.render_pages_to_images(path, dpi=300, max_side_px=500)

    assert max(images[0].size) <= 500


def test_render_pages_to_images_leaves_small_pages_unaffected_by_the_cap(tmp_path: Path):
    path = _multi_page_pdf(tmp_path, ["small"], pagesize=(200, 100))

    uncapped = pdf_extractor.render_pages_to_images(path, dpi=150)
    capped = pdf_extractor.render_pages_to_images(path, dpi=150, max_side_px=5000)

    assert uncapped[0].size == capped[0].size
