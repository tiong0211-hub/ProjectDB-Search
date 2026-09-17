"""Indexing pipeline: walk a corpus, parse filenames, and fall back to PDF
text extraction / OCR only for documents the filename pass couldn't fully
identify.

Sequence (per the project plan): filename/folder parsing runs first for
every file (fast, always). PDF text extraction / OCR only runs for
documents where `needs_fallback()` says required fields are still missing
— never indiscriminately across the whole corpus, to keep indexing time
bounded as the corpus grows into the thousands.

Only PDFs and plain image files are ever opened here (see
`config.extraction.pdf_extensions` / `image_extensions`). Office formats
(Word/Excel/PowerPoint) are out of scope on purpose — many real copies of
those are internal security-restricted files — and are never walked in the
first place, regardless of `needs_fallback`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from projectdb_search.config import AppConfig
from projectdb_search.indexer import logging_utils, pdf_extractor
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.indexer.ocr import get_ocr_backend
from projectdb_search.indexer.ocr.base import OCRBackend
from projectdb_search.models import DocumentRecord
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import build_inverted_index, save_inverted_index

REQUIRED_FIELDS = ("project_name", "doc_type")


@dataclass
class IndexRunSummary:
    total_files: int
    processed: int
    skipped_unchanged: int


def needs_fallback(record: DocumentRecord) -> bool:
    """Whether filename-only metadata is incomplete enough to warrant PDF
    text extraction / OCR (i.e. any required field is still missing).
    """
    return any(getattr(record, field_name) is None for field_name in REQUIRED_FIELDS)


def _walk_corpus(corpus_root: Path, supported_extensions: set[str]) -> list[Path]:
    return sorted(
        p for p in corpus_root.rglob("*") if p.is_file() and p.suffix.lower() in supported_extensions
    )


def run_pdf_fallback(
    file_path: Path,
    record: DocumentRecord,
    parser: FilenameParser,
    ocr_backend: OCRBackend,
    config: AppConfig,
    log_dir: Path,
) -> DocumentRecord:
    text = pdf_extractor.extract_text_layer(file_path)

    if text and len(text) >= config.extraction.min_text_chars:
        meta = parser.parse_freetext(text)
        record.merge_partial(meta, source="pdf_text")
        logging_utils.log_extraction(log_dir, record.file_path, source="pdf_text", text=text)
        return record

    # No usable text layer (scanned document) -> OCR.
    images = pdf_extractor.render_pages_to_images(file_path, dpi=config.extraction.ocr_dpi)
    if not images:
        record.extraction_status = "needs_review"
        logging_utils.append_review_queue(log_dir, record.file_path, reason="unreadable_pdf", confidence=0.0)
        return record

    ocr_result = ocr_backend.recognize(images)
    return _apply_ocr_result(record, ocr_result, parser, config, log_dir)


def run_image_ocr_fallback(
    file_path: Path,
    record: DocumentRecord,
    parser: FilenameParser,
    ocr_backend: OCRBackend,
    config: AppConfig,
    log_dir: Path,
) -> DocumentRecord:
    try:
        image = Image.open(file_path)
    except Exception:
        record.extraction_status = "needs_review"
        logging_utils.append_review_queue(log_dir, record.file_path, reason="unreadable_image", confidence=0.0)
        return record

    ocr_result = ocr_backend.recognize([image])
    return _apply_ocr_result(record, ocr_result, parser, config, log_dir)


def _apply_ocr_result(record, ocr_result, parser, config, log_dir):
    logging_utils.log_extraction(
        log_dir, record.file_path, source="ocr", text=ocr_result.text, confidence=ocr_result.confidence
    )
    record.ocr_confidence = ocr_result.confidence

    if ocr_result.confidence >= config.extraction.ocr_confidence_threshold:
        meta = parser.parse_freetext(ocr_result.text)
        record.merge_partial(meta, source="ocr")
    else:
        record.extraction_status = "needs_review"
        logging_utils.append_review_queue(
            log_dir, record.file_path, reason="low_confidence_ocr", confidence=ocr_result.confidence
        )

    return record


def run_pipeline(
    corpus_root: Path,
    index_dir: Path,
    config: AppConfig,
    force_rebuild: bool = False,
    log_dir: Path | None = None,
    ocr_backend: OCRBackend | None = None,
) -> IndexRunSummary:
    corpus_root = corpus_root.resolve()
    index_dir.mkdir(parents=True, exist_ok=True)
    log_dir = log_dir or (index_dir.parent / "logs")
    ocr_backend = ocr_backend or get_ocr_backend()

    manifest = {} if force_rebuild else index_store.load_manifest(index_dir)
    supported_extensions = config.extraction.pdf_extensions | config.extraction.image_extensions
    files = _walk_corpus(corpus_root, supported_extensions)
    parser = FilenameParser(config)

    processed = 0
    skipped = 0
    for file_path in files:
        if not force_rebuild and not index_store.has_changed(corpus_root, file_path, manifest):
            skipped += 1
            continue

        meta = parser.parse(corpus_root, file_path)
        record = DocumentRecord.from_partial(corpus_root, file_path, meta, source="filename")

        if needs_fallback(record):
            ext = file_path.suffix.lower()
            try:
                if ext in config.extraction.pdf_extensions:
                    record = run_pdf_fallback(file_path, record, parser, ocr_backend, config, log_dir)
                elif ext in config.extraction.image_extensions:
                    record = run_image_ocr_fallback(file_path, record, parser, ocr_backend, config, log_dir)
            except Exception as exc:
                # A single bad file (corrupt, or a missing Tesseract/Poppler
                # binary) must never abort indexing the rest of the corpus —
                # keep filename-only metadata and flag it for a human.
                record.extraction_status = "needs_review"
                logging_utils.append_review_queue(
                    log_dir, record.file_path, reason=f"extraction_error: {exc}", confidence=None
                )

        index_store.write_record(index_dir, record)
        index_store.update_manifest_entry(manifest, corpus_root, file_path, record.doc_id)
        processed += 1

    index_store.save_manifest(index_dir, manifest)

    all_records = index_store.load_all_records(index_dir)
    inverted = build_inverted_index(all_records)
    save_inverted_index(index_dir, inverted)

    return IndexRunSummary(total_files=len(files), processed=processed, skipped_unchanged=skipped)
