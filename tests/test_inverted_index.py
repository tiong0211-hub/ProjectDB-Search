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
