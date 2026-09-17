from __future__ import annotations

from pathlib import Path

from projectdb_search.models import DocumentRecord, PartialMetadata
from projectdb_search.storage import index_store


def _make_record(corpus_root: Path, relative: str) -> DocumentRecord:
    file_path = corpus_root / relative
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("placeholder")
    meta = PartialMetadata(project_name="Riverside Plant", doc_type="memo", keywords=["memo"])
    return DocumentRecord.from_partial(corpus_root, file_path, meta)


def test_record_round_trips_through_json(tmp_path: Path):
    corpus_root = tmp_path / "corpus"
    index_dir = tmp_path / "index"
    record = _make_record(corpus_root, "a/b.pdf")

    index_store.write_record(index_dir, record)
    loaded = index_store.load_record(index_dir, record.doc_id)

    assert loaded == record


def test_load_all_records_returns_every_written_record(tmp_path: Path):
    corpus_root = tmp_path / "corpus"
    index_dir = tmp_path / "index"
    r1 = _make_record(corpus_root, "a/one.pdf")
    r2 = _make_record(corpus_root, "b/two.pdf")
    index_store.write_record(index_dir, r1)
    index_store.write_record(index_dir, r2)

    all_records = index_store.load_all_records(index_dir)

    assert {r.doc_id for r in all_records} == {r1.doc_id, r2.doc_id}


def test_manifest_detects_unchanged_vs_changed_files(tmp_path: Path):
    # has_changed/update_manifest_entry take the relative path and stat
    # result the indexing loop already computed, rather than recomputing
    # them per call -- see index_store.has_changed's docstring.
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    file_path = corpus_root / "doc.pdf"
    file_path.write_text("v1")

    manifest = {}
    assert index_store.has_changed(file_path, manifest, "doc.pdf", file_path.stat()) is True

    index_store.update_manifest_entry(manifest, "doc.pdf", "doc_id_1", file_path.stat())
    assert index_store.has_changed(file_path, manifest, "doc.pdf", file_path.stat()) is False

    file_path.write_text("v2 - different size")
    assert index_store.has_changed(file_path, manifest, "doc.pdf", file_path.stat()) is True


def test_manifest_saves_and_loads(tmp_path: Path):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    index_dir = tmp_path / "index"
    file_path = corpus_root / "doc.pdf"
    file_path.write_text("v1")

    manifest = {}
    index_store.update_manifest_entry(manifest, "doc.pdf", "doc_id_1", file_path.stat())
    index_store.save_manifest(index_dir, manifest)

    reloaded = index_store.load_manifest(index_dir)
    assert reloaded["doc.pdf"].doc_id == "doc_id_1"
