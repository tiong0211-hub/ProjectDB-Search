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
