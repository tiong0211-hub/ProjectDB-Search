"""Indexing pipeline, split into two stages so a search-ready index exists
almost immediately even over a large corpus:

Stage 1 -- `run_pipeline()`: walks the corpus and parses filenames/folder
names only. This is always fast (no file content is ever opened) and is
what builds/refreshes the inverted index used by search. Documents whose
filename alone couldn't fill in the required fields (see `needs_fallback`)
are flagged `extraction_status = "pending_deep_scan"` and left as-is --
never opened here.

Stage 2 -- `run_deep_scan()`: an explicit, separate, resumable pass that
only touches documents still flagged `pending_deep_scan`, running PDF text
extraction / OCR on each (see pdf_extractor.py / ocr/tesseract_backend.py).
It does not re-walk the corpus -- it works entirely off records already on
disk -- and can run in parallel across documents, be cancelled mid-run, and
be re-run later to pick up wherever it left off (state lives in each
record's extraction_status, not in memory).

Why split like this: PDF/OCR fallback is 10-1000x slower per file than
filename parsing, and on a real corpus where project names aren't yet all
registered in config (see default_config.toml's project_name_patterns),
`needs_fallback` can trip for nearly every document -- making a single
combined pass take hours on a few thousand files. Splitting means the
(fast, always necessary) part isn't held hostage by the (slow, often
skippable in practice once project names are registered) part.

Only PDFs and plain image files are ever opened here (see
`config.extraction.pdf_extensions` / `image_extensions`). Office formats
(Word/Excel/PowerPoint) are out of scope on purpose — many real copies of
those are internal security-restricted files — and are never walked in the
first place, regardless of `needs_fallback`.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from projectdb_search import runtime_paths
from projectdb_search.config import AppConfig
from projectdb_search.indexer import logging_utils, pdf_extractor
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.indexer.ocr import get_ocr_backend
from projectdb_search.indexer.ocr.base import OCRBackend
from projectdb_search.models import DocumentRecord
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import (
    build_inverted_index,
    load_inverted_index,
    save_inverted_index,
)

REQUIRED_FIELDS = ("project_name", "doc_type")


@dataclass
class IndexRunSummary:
    total_files: int
    processed: int
    skipped_unchanged: int
    corpus_root_changed: bool = False
    pending_deep_scan: int = 0


@dataclass
class DeepScanSummary:
    total_pending: int
    processed: int
    cancelled: bool = False


def needs_fallback(record: DocumentRecord) -> bool:
    """Whether filename-only metadata is incomplete enough to warrant PDF
    text extraction / OCR (i.e. any required field is still missing).
    """
    return any(getattr(record, field_name) is None for field_name in REQUIRED_FIELDS)


def _walk_corpus(corpus_root: Path, supported_extensions: set[str]) -> list[Path]:
    """Every supported file under the corpus, as (sorted) absolute paths.

    Uses os.walk rather than `Path.rglob("*")` + `p.is_file()`: os.walk is
    scandir-based and already knows which entries are files, whereas the
    rglob form pays an extra stat syscall per entry to ask. That extra
    syscall is nearly free locally but is a network round-trip each on a
    shared/OneDrive drive, which is exactly where these corpora live.
    """
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(corpus_root):
        directory = Path(dirpath)
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() in supported_extensions:
                found.append(directory / filename)
    return sorted(found)


def run_pdf_fallback(
    file_path: Path,
    record: DocumentRecord,
    parser: FilenameParser,
    ocr_backend: OCRBackend,
    config: AppConfig,
    log_dir: Path,
) -> DocumentRecord:
    max_pages = config.extraction.max_fallback_pages
    text = pdf_extractor.extract_text_layer(file_path, max_pages=max_pages)

    if text and len(text) >= config.extraction.min_text_chars:
        meta = parser.parse_freetext(text)
        record.merge_partial(meta, source="pdf_text")
        logging_utils.log_extraction(log_dir, record.file_path, source="pdf_text", text=text)
        return record

    # No usable text layer (scanned document) -> OCR.
    images = pdf_extractor.render_pages_to_images(
        file_path,
        dpi=config.extraction.ocr_dpi,
        max_pages=max_pages,
        max_side_px=config.extraction.ocr_max_side_px,
    )
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


def _process_pending_record(
    file_path: Path,
    record: DocumentRecord,
    parser: FilenameParser,
    ocr_backend: OCRBackend,
    config: AppConfig,
    log_dir: Path,
) -> DocumentRecord:
    """Runs the PDF-text/OCR fallback for one `pending_deep_scan` record and
    resolves its final status. Shared by run_deep_scan's sequential loop and
    its parallel worker function, so both take the same code path.
    """
    ext = file_path.suffix.lower()
    try:
        if ext in config.extraction.pdf_extensions:
            record = run_pdf_fallback(file_path, record, parser, ocr_backend, config, log_dir)
        elif ext in config.extraction.image_extensions:
            record = run_image_ocr_fallback(file_path, record, parser, ocr_backend, config, log_dir)
        else:
            # Shouldn't happen (only pdf/image extensions are ever walked),
            # but never leave a record stuck in pending_deep_scan forever.
            record.extraction_status = "needs_review"
            logging_utils.append_review_queue(
                log_dir, record.file_path, reason="unsupported_extension_for_deep_scan", confidence=None
            )
    except Exception as exc:
        # A single bad file (corrupt, or a missing Tesseract binary) must
        # never abort the rest of the deep scan -- keep filename-only
        # metadata and flag it for a human.
        record.extraction_status = "needs_review"
        logging_utils.append_review_queue(
            log_dir, record.file_path, reason=f"extraction_error: {exc}", confidence=None
        )

    if record.extraction_status == "pending_deep_scan":
        record.extraction_status = "ok"
    return record


def run_pipeline(
    corpus_root: Path,
    index_dir: Path,
    config: AppConfig,
    force_rebuild: bool = False,
    log_dir: Path | None = None,
) -> IndexRunSummary:
    """Stage 1: filename/folder metadata only. Never opens a PDF or image's
    contents -- see the module docstring for why. Always fast, and always
    what makes the index searchable, even before any deep scan has run.
    """
    corpus_root = corpus_root.resolve()
    index_dir.mkdir(parents=True, exist_ok=True)
    log_dir = log_dir or (index_dir.parent / "logs")

    previous_corpus_root = index_store.load_corpus_root(index_dir)
    corpus_root_changed = previous_corpus_root is not None and previous_corpus_root.resolve() != corpus_root
    if corpus_root_changed:
        # Old records point at file_paths relative to a root this index no
        # longer serves -- keeping them would let search surface documents
        # "Open file" can no longer open. Start clean rather than merge.
        index_store.clear_index(index_dir)
        force_rebuild = True

    index_store.save_corpus_root(index_dir, corpus_root)

    manifest = {} if force_rebuild else index_store.load_manifest(index_dir)
    supported_extensions = config.extraction.pdf_extensions | config.extraction.image_extensions
    files = _walk_corpus(corpus_root, supported_extensions)
    parser = FilenameParser(config)

    processed = 0
    skipped = 0
    # Records written during this run, kept so the index rebuild below
    # doesn't have to read back from disk what we just wrote.
    fresh_records: dict[str, DocumentRecord] = {}
    for file_path in files:
        relative = file_path.relative_to(corpus_root)
        relative_str = str(relative)
        stat_result = file_path.stat()  # the loop's one stat per file

        if not force_rebuild and not index_store.has_changed(file_path, manifest, relative_str, stat_result):
            skipped += 1
            continue

        meta = parser.parse(corpus_root, file_path, relative=relative)
        record = DocumentRecord.from_partial(
            corpus_root, file_path, meta, source="filename", relative_path=relative_str
        )
        if needs_fallback(record):
            record.extraction_status = "pending_deep_scan"

        index_store.write_record(index_dir, record)
        index_store.update_manifest_entry(manifest, relative_str, record.doc_id, stat_result)
        fresh_records[record.doc_id] = record
        processed += 1

    existing = load_inverted_index(index_dir)
    if processed == 0 and existing is not None and existing.documents:
        # Nothing changed on disk, so the index on disk is already correct.
        # Re-reading every record and rewriting the index here would be
        # pure waste -- and it's the common case, since "re-run indexing to
        # pick up new files" usually finds nothing new.
        index_store.save_manifest(index_dir, manifest)
        return IndexRunSummary(
            total_files=len(files),
            processed=0,
            skipped_unchanged=skipped,
            corpus_root_changed=corpus_root_changed,
            pending_deep_scan=sum(
                1 for d in existing.documents.values() if d.get("extraction_status") == "pending_deep_scan"
            ),
        )

    index_store.save_manifest(index_dir, manifest)

    all_records = index_store.load_all_records(index_dir, skip_doc_ids=set(fresh_records))
    all_records.extend(fresh_records.values())
    inverted = build_inverted_index(all_records)
    save_inverted_index(index_dir, inverted)

    pending_count = sum(1 for r in all_records if r.extraction_status == "pending_deep_scan")

    return IndexRunSummary(
        total_files=len(files),
        processed=processed,
        skipped_unchanged=skipped,
        corpus_root_changed=corpus_root_changed,
        pending_deep_scan=pending_count,
    )


def _deep_scan_worker_init() -> None:
    # Each worker process gets its own pytesseract configuration -- imports
    # and env vars don't cross process boundaries.
    runtime_paths.configure_tesseract()


def _deep_scan_worker(payload: tuple[dict, str, str, AppConfig]) -> dict:
    """Runs in a worker process (see run_deep_scan's parallel path). Takes
    plain/picklable arguments only and returns a plain dict -- the parent
    process is the sole writer to the index directory.
    """
    record_dict, corpus_root_str, log_dir_str, config = payload
    record = DocumentRecord.from_dict(record_dict)
    corpus_root = Path(corpus_root_str)
    log_dir = Path(log_dir_str)
    file_path = corpus_root / record.file_path
    parser = FilenameParser(config)
    ocr_backend = get_ocr_backend()

    updated = _process_pending_record(file_path, record, parser, ocr_backend, config, log_dir)
    return updated.to_dict()


def run_deep_scan(
    index_dir: Path,
    config: AppConfig,
    corpus_root: Path | None = None,
    log_dir: Path | None = None,
    ocr_backend: OCRBackend | None = None,
    max_workers: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DeepScanSummary:
    """Stage 2: PDF-text/OCR fallback for documents Stage 1 flagged
    `pending_deep_scan`. Resumable -- only records still in that state are
    picked up, so an interrupted run (cancelled, crashed, or just stopped)
    can simply be re-run later to finish the rest; already-processed
    records are untouched.

    `progress_callback(done, total, last_file_path)` is invoked after each
    document finishes (parallel mode: in completion order, not corpus
    order). `should_cancel()` is polled between documents (sequential mode)
    or between completions (parallel mode) -- in parallel mode, futures
    already dispatched to a worker are allowed to finish rather than
    interrupted mid-file, so their work isn't wasted; only additional work
    stops being scheduled.
    """
    if corpus_root is None:
        corpus_root = index_store.load_corpus_root(index_dir)
    if corpus_root is None:
        raise ValueError("corpus_root is unknown -- pass it explicitly, or run `index` first to record it")
    corpus_root = corpus_root.resolve()
    log_dir = log_dir or (index_dir.parent / "logs")

    all_records = index_store.load_all_records(index_dir)
    pending = [r for r in all_records if r.extraction_status == "pending_deep_scan"]
    total = len(pending)

    workers = max_workers if max_workers is not None else config.extraction.deep_scan_workers
    if workers == 0:
        workers = min(os.cpu_count() or 1, 4)

    processed = 0
    cancelled = False

    if total == 0:
        return DeepScanSummary(total_pending=0, processed=0, cancelled=False)

    if workers <= 1 or total == 1:
        parser = FilenameParser(config)
        backend = ocr_backend or get_ocr_backend()
        for record in pending:
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            file_path = corpus_root / record.file_path
            try:
                updated = _process_pending_record(file_path, record, parser, backend, config, log_dir)
            except KeyboardInterrupt:
                # Ctrl+C mid-file: stop here rather than aborting silently.
                # Everything processed so far is already written and the
                # inverted index is rebuilt below, so re-running later
                # resumes from exactly this point.
                cancelled = True
                break
            index_store.write_record(index_dir, updated)
            processed += 1
            if progress_callback is not None:
                progress_callback(processed, total, updated.file_path)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_deep_scan_worker_init) as pool:
            futures = {
                pool.submit(_deep_scan_worker, (record.to_dict(), str(corpus_root), str(log_dir), config)): record
                for record in pending
            }
            try:
                for future in as_completed(futures):
                    record_dict = future.result()
                    index_store.write_record(index_dir, DocumentRecord.from_dict(record_dict))
                    processed += 1
                    if progress_callback is not None:
                        progress_callback(processed, total, record_dict["file_path"])
                    if should_cancel is not None and should_cancel():
                        cancelled = True
                        for pending_future in futures:
                            pending_future.cancel()
                        break
            except KeyboardInterrupt:
                # Not-yet-started futures are cancelled; any already running
                # are left to finish in the background (their result is
                # simply not collected) rather than killed mid-write.
                cancelled = True
                for pending_future in futures:
                    pending_future.cancel()

    if processed > 0:
        all_records = index_store.load_all_records(index_dir)
        inverted = build_inverted_index(all_records)
        save_inverted_index(index_dir, inverted)

    return DeepScanSummary(total_pending=total, processed=processed, cancelled=cancelled)
