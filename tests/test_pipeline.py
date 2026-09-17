from __future__ import annotations

from pathlib import Path

import pytest

from projectdb_search.config import AppConfig
from projectdb_search.indexer import logging_utils, pdf_extractor
from projectdb_search.indexer.ocr.base import OCRBackend
from projectdb_search.indexer.pipeline import needs_fallback, run_deep_scan, run_pipeline
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


def _read_jsonl(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- Stage 1 (run_pipeline): filename-only, never opens file contents -----


def test_filename_sufficient_documents_are_ok_without_any_deep_scan(
    built_index: tuple[Path, Path, InvertedIndex]
):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "RiversidePlant/PID/RiversidePlant_PID_Unit3_2021.pdf")

    assert record.source_of_metadata == "filename"
    assert record.extraction_status == "ok"

    log_entries = _read_jsonl(log_dir / logging_utils.PDF_EXTRACTION_LOG_FILENAME)
    assert not any(e["file_path"] == record.file_path for e in log_entries)


def test_filename_insufficient_documents_are_flagged_pending_not_processed(
    built_index: tuple[Path, Path, InvertedIndex]
):
    index_dir, log_dir, _ = built_index
    record = _find_record(index_dir, "Unsorted/nameplate_photo_scan.pdf")

    # Stage 1 never opens the file -- metadata is still filename-only, and
    # nothing was logged to the PDF-extraction log.
    assert record.extraction_status == "pending_deep_scan"
    assert record.source_of_metadata == "filename"
    log_entries = _read_jsonl(log_dir / logging_utils.PDF_EXTRACTION_LOG_FILENAME)
    assert not any(e["file_path"] == record.file_path for e in log_entries)


def test_run_pipeline_never_touches_pdf_or_ocr(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig, monkeypatch
):
    """The core contract of the Stage1/Stage2 split: run_pipeline must be
    fast and safe to call over a huge corpus, which requires that it never
    opens a document's actual contents -- only filenames/folder names.
    """
    calls: list[str] = []
    monkeypatch.setattr(pdf_extractor, "extract_text_layer", lambda *a, **k: calls.append("text"))
    monkeypatch.setattr(pdf_extractor, "render_pages_to_images", lambda *a, **k: calls.append("render"))

    index_dir = tmp_path / "index"
    summary = run_pipeline(corpus_root, index_dir, app_config)

    assert calls == []
    assert summary.pending_deep_scan > 0  # sanity: the fixture corpus does have some


def test_office_formats_are_never_walked(corpus_root: Path, tmp_path: Path, app_config: AppConfig):
    """Security constraint: Office documents (many are internal
    security-restricted files at the target company) must never be opened
    or even discovered by the indexer, regardless of extraction settings.
    """
    (corpus_root / "Unsorted" / "confidential_budget.xlsx").write_bytes(b"not a real xlsx, just a probe")
    (corpus_root / "Unsorted" / "contract_draft.docx").write_bytes(b"not a real docx, just a probe")

    index_dir = tmp_path / "index"
    run_pipeline(corpus_root, index_dir, app_config)

    from projectdb_search.storage.index_store import load_all_records

    all_paths = {r.file_path for r in load_all_records(index_dir)}
    assert not any(p.endswith(".xlsx") or p.endswith(".docx") for p in all_paths)


# --- Stage 2 (run_deep_scan): PDF-text/OCR fallback, resumable -------------


def test_pdf_text_fallback_merges_metadata(deep_scanned_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = deep_scanned_index
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


def test_ocr_fallback_merges_metadata_for_scanned_pdf(deep_scanned_index: tuple[Path, Path, InvertedIndex]):
    index_dir, log_dir, _ = deep_scanned_index
    record = _find_record(index_dir, "Unsorted/nameplate_photo_scan.pdf")

    assert record.equipment_tag == "HX-450"
    assert record.source_of_metadata == "filename+ocr"
    assert record.extraction_status == "ok"
    assert record.ocr_confidence is not None
    assert record.ocr_confidence > 0.55


def test_ocr_fallback_for_plain_image_file(deep_scanned_index: tuple[Path, Path, InvertedIndex]):
    index_dir, _, _ = deep_scanned_index
    record = _find_record(index_dir, "Unsorted/photo_0042.jpg")

    assert record.equipment_tag == "P-330"
    assert record.department == "Electrical"
    assert record.source_of_metadata == "filename+ocr"


def test_low_confidence_ocr_flagged_for_review_and_not_merged(
    deep_scanned_index: tuple[Path, Path, InvertedIndex]
):
    index_dir, log_dir, _ = deep_scanned_index
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


def test_extraction_error_is_isolated_and_never_aborts_the_whole_scan(
    built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    index_dir, log_dir, _ = built_index

    summary = run_deep_scan(
        index_dir, app_config, log_dir=log_dir, ocr_backend=_AlwaysFailsOCRBackend(), max_workers=1
    )

    # Every pending file still got processed -- one OCR backend failure
    # didn't abort the rest of the deep scan.
    assert summary.processed == summary.total_pending
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


def test_run_deep_scan_is_resumable_and_idempotent(
    built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    index_dir, log_dir, _ = built_index

    first = run_deep_scan(index_dir, app_config, log_dir=log_dir, max_workers=1)
    assert first.total_pending > 0
    assert first.processed == first.total_pending

    # Nothing left pending -- a second run (e.g. re-opening the web UI
    # later, or re-running the CLI command) does no redundant work.
    second = run_deep_scan(index_dir, app_config, log_dir=log_dir, max_workers=1)
    assert second.total_pending == 0
    assert second.processed == 0


def test_run_deep_scan_stops_early_when_cancelled_and_can_resume(
    built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    index_dir, log_dir, _ = built_index

    cancel_after = {"count": 0}

    def should_cancel() -> bool:
        cancel_after["count"] += 1
        return cancel_after["count"] > 1  # let one document through, then stop

    first = run_deep_scan(index_dir, app_config, log_dir=log_dir, max_workers=1, should_cancel=should_cancel)
    assert first.cancelled is True
    assert 0 < first.processed < first.total_pending

    # Resuming picks up exactly the rest -- no document is processed twice
    # and none is skipped.
    second = run_deep_scan(index_dir, app_config, log_dir=log_dir, max_workers=1)
    assert second.total_pending == first.total_pending - first.processed
    assert second.processed == second.total_pending


def test_run_deep_scan_requires_a_known_corpus_root(tmp_path: Path, app_config: AppConfig):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    with pytest.raises(ValueError):
        run_deep_scan(index_dir, app_config)


# --- Corpus-root-change / incremental re-indexing (Stage 1) ---------------


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


def test_reindex_with_no_changes_leaves_the_index_file_untouched(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    """A re-run that finds nothing changed must not rewrite the index --
    rebuilding it from every record on disk is the expensive part, and it
    would produce byte-for-byte the same result.
    """
    index_dir = tmp_path / "index"
    run_pipeline(corpus_root, index_dir, app_config)
    index_file = index_dir / "inverted_index.json"
    before = index_file.stat().st_mtime_ns

    summary = run_pipeline(corpus_root, index_dir, app_config)

    assert summary.processed == 0
    assert index_file.stat().st_mtime_ns == before
    # The summary still reports the real pending count, read from the
    # existing index rather than recomputed from disk.
    assert summary.pending_deep_scan > 0


def test_search_still_works_after_a_no_op_reindex(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    run_pipeline(corpus_root, index_dir, app_config)
    run_pipeline(corpus_root, index_dir, app_config)

    inverted = load_inverted_index(index_dir)
    assert inverted is not None
    assert inverted.documents  # snapshot survived the skipped rebuild


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
