from __future__ import annotations

from projectdb_search.models import DocumentRecord
from projectdb_search.storage.inverted_index import build_inverted_index


def _record(doc_id: str, **overrides) -> DocumentRecord:
    base = dict(doc_id=doc_id, file_path=f"{doc_id}.pdf", file_name=f"{doc_id}.pdf", file_ext=".pdf")
    base.update(overrides)
    return DocumentRecord(**base)


def test_keyword_matches_loose_finds_substring_matches_not_caught_by_exact():
    inverted = build_inverted_index([_record("d1", keywords=["compressorstation"])])

    assert inverted.keyword_matches("compressor") == []  # exact lookup misses the merged token
    assert inverted.keyword_matches_loose("compressor") == ["d1"]


def test_keyword_matches_loose_excludes_exact_matches_to_avoid_double_counting():
    inverted = build_inverted_index([_record("d1", keywords=["compressor"])])

    assert inverted.keyword_matches("compressor") == ["d1"]
    assert inverted.keyword_matches_loose("compressor") == []


def test_keyword_matches_loose_tolerates_typos():
    inverted = build_inverted_index([_record("d1", keywords=["datasheet"])])

    assert inverted.keyword_matches_loose("datasheat") == ["d1"]


def test_year_is_indexed_so_year_only_queries_do_not_scan_the_whole_corpus():
    records = [_record(f"d{i}", year=2020 + (i % 3)) for i in range(9)]
    inverted = build_inverted_index(records)

    # Without year in the index a year-only query matches no bucket, and
    # search falls back to scoring every document in the corpus.
    assert inverted.field_matches("year", "2021") == ["d1", "d4", "d7"]


def test_documents_snapshot_carries_each_record_for_search():
    records = [_record("d1", project_name="Riverside Plant", keywords=["pump"])]
    inverted = build_inverted_index(records)

    # Scoring reads this instead of opening records/<doc_id>.json per
    # candidate -- see ranker._candidate_record.
    assert inverted.documents["d1"]["project_name"] == "Riverside Plant"
    assert inverted.documents["d1"]["keywords"] == ["pump"]


def test_many_documents_sharing_a_field_value_and_keyword_are_all_indexed():
    # Regression test for a real O(n^2) bug: build_inverted_index used to
    # dedupe each bucket with `if doc_id not in a_list`, which is O(bucket
    # size) per insert -- a bucket shared by many documents (a common
    # project name, a common keyword) made the whole build superlinear.
    # This doesn't reproduce the *timing* (that needs thousands of records
    # to show up), but it does pin down the actual contract: every
    # document sharing a value ends up in that value's bucket exactly
    # once, regardless of how many others share it.
    records = [
        _record(f"d{i}", project_name="Riverside Plant", keywords=["compressor"]) for i in range(50)
    ]
    inverted = build_inverted_index(records)

    expected = sorted(f"d{i}" for i in range(50))
    assert inverted.field_matches("project_name", "Riverside Plant") == expected
    assert inverted.keyword_matches("compressor") == expected
