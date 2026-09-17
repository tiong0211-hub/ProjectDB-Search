from __future__ import annotations

from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.indexer import logging_utils
from projectdb_search.indexer.ocr.base import OCRBackend
from projectdb_search.indexer.pipeline import needs_fallback, run_pipeline
from projectdb_search.models import DocumentRecord, make_doc_id
from projectdb_search.storage.index_store import load_record
from projectdb_search.storage.inverted_index import InvertedIndex, load_inverted_index


def _record(**overrides) -> DocumentRecord:
    base = dict(
        doc_id="d1", file_path="a.pdf", file_name="a.pdf", file_ext=".pdf",
        project_name=None, doc_type=None,
    )
    base.update(overrides)
    return DocumentRecord(**base)


def test_needs_fallback_true_when_required_field_missing():
    assert needs_fallback(_record(project_name=None, doc_type="memo")) is True
    assert needs_fallback(_record(project_name="Riverside Plant", doc_type=None)) is True
    assert needs_fallback(_record(project_name=None, doc_type=None)) is True


def test_needs_fallback_false_when_both_required_fields_present():
    assert needs_fallback(_record(project_name="Riverside Plant", doc_type="memo")) is False


def _find_record(index_dir: Path, relative_path: str) -> DocumentRecord:
    return load_record(index_dir, make_doc_id(relative_path))


def test_filename_sufficient_documents_skip_fallback_entirely(built_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf")

    assert record.source_of_metadata == "filename"
    assert record.extraction_status == "ok"

    log_entries = _read_jsonl(log_dir / logging_utils.PDF_EXTRACTION_LOG_FILENAME)
    assert not any(e["file_path"] == record.file_path for e in log_entries)


def test_pdf_text_fallback_merges_metadata(built_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "Unsorted/old_backup_copy_final_v2.pdf")

    assert record.project_name == "Compressor Station"
    assert record.doc_type == "memo"
    assert record.year == 2015
    assert record.source_of_metadata == "filename+pdf_text"
    assert record.extraction_status == "ok"

    log_entries = _read_jsonl(log_dir / logging_utils.PDF_EXTRACTION_LOG_FILENAME)
    entry = next(e for e in log_entries if e["file_path"] == record.file_path)
    assert entry["source"] == "pdf_text"
    assert "Compressor Station" in entry["text"]


def test_ocr_fallback_merges_metadata_for_scanned_pdf(built_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "Unsorted/nameplate_photo_scan.pdf")

    assert record.equipment_tag == "HX-450"
    assert record.source_of_metadata == "filename+ocr"
    assert record.extraction_status == "ok"
    assert record.ocr_confidence is not None
    assert record.ocr_confidence > 0.55


def test_ocr_fallback_for_plain_image_file(built_index: tuple[Path, Path, InvertedIndex]):
    index_dir, _, _ = built_index
    record = _find_record(index_dir, "Unsorted/photo_0042.jpg")

    assert record.equipment_tag == "P-330"
    assert record.department == "Electrical"
    assert record.source_of_metadata == "filename+ocr"


def test_low_confidence_ocr_flagged_for_review_and_not_merged(built_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "Legacy/blurry_scan_noname.pdf")

    assert record.extraction_status == "needs_review"
    assert record.ocr_confidence == 0.0
    # Nothing usable came from OCR, so metadata stays filename-only (empty).
    assert record.project_name is None
    assert record.doc_type is None

    review_entries = logging_utils.read_review_queue(log_dir)
    assert any(e["file_path"] == record.file_path for e in review_entries)


class _AlwaysFailsOCRBackend(OCRBackend):
    def recognize(self, images):
        raise RuntimeError("simulated: tesseract binary not found")


def test_extraction_error_is_isolated_and_never_aborts_the_whole_run(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    log_dir = tmp_path / "logs"

    summary = run_pipeline(
        corpus_root, index_dir, app_config, log_dir=log_dir, ocr_backend=_AlwaysFailsOCRBackend()
    )

    # Every file still got processed -- one OCR backend failure didn't
    # abort indexing the rest of the corpus.
    assert summary.processed == summary.total_files
    assert load_inverted_index(index_dir) is not None

    failed_record = _find_record(index_dir, "Unsorted/nameplate_photo_scan.pdf")
    assert failed_record.extraction_status == "needs_review"

    review_entries = logging_utils.read_review_queue(log_dir)
    assert any(
        e["file_path"] == failed_record.file_path and "extraction_error" in e["reason"]
        for e in review_entries
    )

    # A document that never needed fallback in the first place is unaffected.
    unaffected = _find_record(index_dir, "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf")
    assert unaffected.extraction_status == "ok"


def test_reindexing_same_corpus_root_accumulates_incrementally(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"

    first = run_pipeline(corpus_root, index_dir, app_config)
    assert first.corpus_root_changed is False
    assert first.skipped_unchanged == 0

    second = run_pipeline(corpus_root, index_dir, app_config)
    assert second.corpus_root_changed is False
    # Same folder, nothing changed -- everything should be skipped, not re-parsed.
    assert second.skipped_unchanged == second.total_files
    assert second.processed == 0


def test_reindexing_a_different_corpus_root_clears_the_old_index(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    run_pipeline(corpus_root, index_dir, app_config)
    old_record = _find_record(index_dir, "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf")
    assert old_record is not None

    other_root = tmp_path / "other_corpus"
    other_root.mkdir()
    (other_root / "SomeOtherDoc.pdf").write_bytes(b"%PDF-1.4\n")

    summary = run_pipeline(other_root, index_dir, app_config)

    assert summary.corpus_root_changed is True
    # The old folder's records are gone -- searching wouldn't surface a
    # document "Open file" could no longer actually open.
    from projectdb_search.storage.index_store import load_all_records

    all_records = load_all_records(index_dir)
    assert all(r.file_path != old_record.file_path for r in all_records)
    assert any(r.file_path == "SomeOtherDoc.pdf" for r in all_records)


def _read_jsonl(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
