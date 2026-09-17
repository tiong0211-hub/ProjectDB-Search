"""Demonstrates the actual point of the inverted index: search stays fast and
touches only a small candidate set, even as the corpus grows to a
"thousands of documents" scale — the scenario a person doing Ctrl+F across
individually-opened files does not scale to at all.
"""

from __future__ import annotations

import time
from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.models import DocumentRecord
from projectdb_search.search.llm.noop_backend import NoOpBackend
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import search
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import build_inverted_index, save_inverted_index

CORPUS_SIZE = 3000
PROJECTS = ["Riverside Plant", "Compressor Station", "North Terminal"]
DOC_TYPES = ["P&ID", "isometric", "memo", "photo", "datasheet"]


def _build_large_index(index_dir: Path) -> None:
    records = []
    for i in range(CORPUS_SIZE):
        records.append(
            DocumentRecord(
                doc_id=f"doc{i:05d}",
                file_path=f"synthetic/{i:05d}.pdf",
                file_name=f"{i:05d}.pdf",
                file_ext=".pdf",
                project_name=PROJECTS[i % len(PROJECTS)],
                doc_type=DOC_TYPES[i % len(DOC_TYPES)],
                year=2000 + (i % 24),
                department=None,
                equipment_tag=f"EQ-{i:04d}",
                keywords=["unit", f"batch{i % 50}"],
            )
        )
    for record in records:
        index_store.write_record(index_dir, record)
    inverted = build_inverted_index(records)
    save_inverted_index(index_dir, inverted)


def test_search_stays_fast_and_scores_only_a_small_candidate_set(app_config: AppConfig, tmp_path: Path):
    index_dir = tmp_path / "large_index"
    _build_large_index(index_dir)

    from projectdb_search.indexer.filename_parser import FilenameParser
    from projectdb_search.storage.inverted_index import load_inverted_index

    parser = FilenameParser(app_config)
    inverted = load_inverted_index(index_dir)
    assert inverted is not None

    target_index = 1234
    query_text = f"EQ-{target_index:04d}"
    query = parse_query(query_text, parser)

    start = time.perf_counter()
    result = search(query, index_dir, inverted, app_config.ranking, NoOpBackend(), top_n=3)
    elapsed = time.perf_counter() - start

    assert result.top, "expected at least one match"
    assert result.top[0].record.doc_id == f"doc{target_index:05d}"

    # The whole point of the inverted index: an exact equipment-tag query
    # only has to score the handful of documents sharing that tag bucket,
    # never all CORPUS_SIZE documents.
    assert result.candidate_count < 10
    assert result.candidate_count < CORPUS_SIZE

    # Generous ceiling for CI variance — in practice this runs in low
    # milliseconds. The property being tested is "doesn't scale with corpus
    # size", not a tight latency SLA.
    assert elapsed < 0.5, f"search() took {elapsed:.3f}s for a {CORPUS_SIZE}-document corpus"
