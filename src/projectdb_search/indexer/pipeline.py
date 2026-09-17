"""Indexing pipeline: walk a corpus, parse filenames, write records + inverted index.

Filename/folder parsing only in this phase. PDF text extraction and OCR
fallback (Phase 4 of the project plan) are represented by `needs_fallback()`
always returning False and `run_pdf_fallback()` being unimplemented — so the
sequence below is already shaped the way it will look once that phase lands:
filename pass first (fast, always), fallback only for documents that need it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.models import DocumentRecord
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import build_inverted_index, save_inverted_index

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass
class IndexRunSummary:
    total_files: int
    processed: int
    skipped_unchanged: int


def needs_fallback(record: DocumentRecord) -> bool:
    """Whether a document's filename-only metadata is incomplete enough to
    warrant PDF text extraction / OCR. Always False for now — no fallback
    parser exists yet (Phase 4); documents with missing fields are still
    indexed and remain searchable on whatever filename metadata was found.
    """
    return False


def _walk_corpus(corpus_root: Path) -> list[Path]:
    return sorted(
        p for p in corpus_root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def run_pipeline(
    corpus_root: Path, index_dir: Path, config: AppConfig, force_rebuild: bool = False
) -> IndexRunSummary:
    corpus_root = corpus_root.resolve()
    index_dir.mkdir(parents=True, exist_ok=True)

    manifest = {} if force_rebuild else index_store.load_manifest(index_dir)
    files = _walk_corpus(corpus_root)
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
            pass  # Phase 4: PDF text extraction / OCR fallback goes here.

        index_store.write_record(index_dir, record)
        index_store.update_manifest_entry(manifest, corpus_root, file_path, record.doc_id)
        processed += 1

    index_store.save_manifest(index_dir, manifest)

    all_records = index_store.load_all_records(index_dir)
    inverted = build_inverted_index(all_records)
    save_inverted_index(index_dir, inverted)

    return IndexRunSummary(total_files=len(files), processed=processed, skipped_unchanged=skipped)
