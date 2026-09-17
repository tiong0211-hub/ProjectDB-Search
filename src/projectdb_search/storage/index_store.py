"""Per-document JSON storage + a manifest for cheap incremental re-indexing.

Design choice: one small JSON file per document (under `records/`) rather
than a single big JSON array. At the "thousands of documents" scale this
project targets, a few thousand small files costs nothing on any real
filesystem, while incremental updates (re-processing one document later,
e.g. once PDF/OCR fallback exists) become an O(1) file rewrite instead of a
full rewrite of one large array with the risk of a partial-write corrupting
the whole index.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from projectdb_search.models import DocumentRecord
from projectdb_search.storage.inverted_index import INVERTED_INDEX_FILENAME

RECORDS_DIRNAME = "records"
MANIFEST_FILENAME = "manifest.json"
META_FILENAME = "meta.json"


@dataclass
class ManifestEntry:
    mtime: float
    size: int
    doc_id: str


def records_dir(index_dir: Path) -> Path:
    return index_dir / RECORDS_DIRNAME


def manifest_path(index_dir: Path) -> Path:
    return index_dir / MANIFEST_FILENAME


def load_manifest(index_dir: Path) -> dict[str, ManifestEntry]:
    path = manifest_path(index_dir)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {rel_path: ManifestEntry(**entry) for rel_path, entry in raw.get("entries", {}).items()}


def save_manifest(index_dir: Path, manifest: dict[str, ManifestEntry]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {"entries": {rel_path: vars(entry) for rel_path, entry in manifest.items()}}
    with open(manifest_path(index_dir), "w", encoding="utf-8") as f:
        # No indent: nothing reads these files by eye, and at tens of
        # thousands of documents the pretty-printing costs real time on
        # every write and every read, plus ~40% more bytes on disk.
        json.dump(payload, f, separators=(",", ":"))


def has_changed(
    file_path: Path, manifest: dict[str, ManifestEntry], relative: str, stat_result: os.stat_result
) -> bool:
    """Whether a file differs from what the manifest recorded for it.

    Takes the caller's already-computed relative path and stat result:
    resolving `file_path.relative_to(corpus_root)` and calling `.stat()`
    again here would double the per-file syscalls during a walk, which is
    the dominant cost when the corpus sits on a network/OneDrive drive.
    """
    entry = manifest.get(relative)
    if entry is None:
        return True
    return entry.mtime != stat_result.st_mtime or entry.size != stat_result.st_size


def update_manifest_entry(
    manifest: dict[str, ManifestEntry], relative: str, doc_id: str, stat_result: os.stat_result
) -> None:
    manifest[relative] = ManifestEntry(
        mtime=stat_result.st_mtime, size=stat_result.st_size, doc_id=doc_id
    )


def write_record(index_dir: Path, record: DocumentRecord) -> None:
    rdir = records_dir(index_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    with open(rdir / f"{record.doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(record.to_dict(), f, separators=(",", ":"))


def load_record(index_dir: Path, doc_id: str) -> DocumentRecord:
    with open(records_dir(index_dir) / f"{doc_id}.json", encoding="utf-8") as f:
        return DocumentRecord.from_dict(json.load(f))


def save_corpus_root(index_dir: Path, corpus_root: Path) -> None:
    """Remembers where the indexed documents actually live, so `serve`
    (the web UI) can open the original file for a search result without
    requiring the user to pass --corpus-root again every time.
    """
    index_dir.mkdir(parents=True, exist_ok=True)
    with open(index_dir / META_FILENAME, "w", encoding="utf-8") as f:
        # This one stays indented -- it's two lines, and being able to read
        # "which folder is this index for?" by eye is genuinely useful.
        json.dump({"corpus_root": str(corpus_root)}, f, indent=2)


def load_corpus_root(index_dir: Path) -> Path | None:
    path = index_dir / META_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Path(data["corpus_root"])


def clear_index(index_dir: Path) -> None:
    """Wipes all indexed records + the manifest + the inverted index --
    used when re-indexing against a different corpus_root than the one
    already on record: old records point at file_paths relative to a root
    this index no longer serves, so keeping them around would let search
    surface documents "Open file" can no longer actually open.
    """
    shutil.rmtree(records_dir(index_dir), ignore_errors=True)
    manifest_path(index_dir).unlink(missing_ok=True)
    (index_dir / INVERTED_INDEX_FILENAME).unlink(missing_ok=True)


def load_all_records(index_dir: Path, skip_doc_ids: set[str] | None = None) -> list[DocumentRecord]:
    """Every record on disk, optionally skipping doc_ids the caller already
    holds in memory.

    `skip_doc_ids` is what keeps a full re-index from reading back all the
    record files it just finished writing -- at tens of thousands of
    documents that re-read is hundreds of milliseconds of pure waste.
    """
    rdir = records_dir(index_dir)
    if not rdir.exists():
        return []
    records = []
    for path in rdir.glob("*.json"):
        if skip_doc_ids and path.stem in skip_doc_ids:
            continue
        with open(path, encoding="utf-8") as f:
            records.append(DocumentRecord.from_dict(json.load(f)))
    return records
