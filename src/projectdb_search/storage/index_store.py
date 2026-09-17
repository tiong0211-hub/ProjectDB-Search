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
from dataclasses import dataclass
from pathlib import Path

from projectdb_search.models import DocumentRecord

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
        json.dump(payload, f, indent=2)


def has_changed(corpus_root: Path, file_path: Path, manifest: dict[str, ManifestEntry]) -> bool:
    relative = str(file_path.relative_to(corpus_root))
    entry = manifest.get(relative)
    if entry is None:
        return True
    stat = file_path.stat()
    return entry.mtime != stat.st_mtime or entry.size != stat.st_size


def update_manifest_entry(
    manifest: dict[str, ManifestEntry], corpus_root: Path, file_path: Path, doc_id: str
) -> None:
    relative = str(file_path.relative_to(corpus_root))
    stat = file_path.stat()
    manifest[relative] = ManifestEntry(mtime=stat.st_mtime, size=stat.st_size, doc_id=doc_id)


def write_record(index_dir: Path, record: DocumentRecord) -> None:
    rdir = records_dir(index_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    with open(rdir / f"{record.doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(record.to_dict(), f, indent=2)


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
        json.dump({"corpus_root": str(corpus_root)}, f, indent=2)


def load_corpus_root(index_dir: Path) -> Path | None:
    path = index_dir / META_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Path(data["corpus_root"])


def load_all_records(index_dir: Path) -> list[DocumentRecord]:
    rdir = records_dir(index_dir)
    if not rdir.exists():
        return []
    records = []
    for path in rdir.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            records.append(DocumentRecord.from_dict(json.load(f)))
    return records
