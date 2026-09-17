from __future__ import annotations

from pathlib import Path

from projectdb_search.models import DocumentRecord
from projectdb_search.search import feedback
from projectdb_search.search.query_parser import ParsedQuery
from projectdb_search.search.ranker import ScoredMatch, SearchResult


def _result() -> SearchResult:
    record = DocumentRecord(doc_id="d1", file_path="a.pdf", file_name="a.pdf", file_ext=".pdf")
    match = ScoredMatch(record=record, score=42.0, matched_fields=[])
    return SearchResult(
        query=ParsedQuery(raw_query="hello"),
        top=[match],
        ambiguous=False,
        candidate_count=1,
        used_llm_rerank=False,
    )


def test_log_feedback_appends_jsonl_entry(tmp_path: Path):
    feedback.log_feedback(
        tmp_path, "hello", ["d1"], [42.0], ambiguous=False, used_llm_rerank=False, chosen_doc_id="d1", correct=True
    )

    entries = feedback.read_feedback(tmp_path)

    assert len(entries) == 1
    assert entries[0]["query"] == "hello"
    assert entries[0]["chosen_doc_id"] == "d1"
    assert entries[0]["correct"] is True


def test_log_feedback_for_result_extracts_fields_from_search_result(tmp_path: Path):
    result = _result()

    feedback.log_feedback_for_result(tmp_path, result, chosen_doc_id="d1", correct=True)

    entries = feedback.read_feedback(tmp_path)
    assert entries[0]["top_doc_ids"] == ["d1"]
    assert entries[0]["scores"] == [42.0]


def test_log_feedback_supports_none_chosen_when_nothing_matched(tmp_path: Path):
    result = _result()

    feedback.log_feedback_for_result(tmp_path, result, chosen_doc_id=None, correct=False)

    entries = feedback.read_feedback(tmp_path)
    assert entries[0]["chosen_doc_id"] is None
    assert entries[0]["correct"] is False


def test_read_feedback_returns_empty_list_when_no_log_exists(tmp_path: Path):
    assert feedback.read_feedback(tmp_path / "nonexistent") == []


def test_multiple_feedback_entries_accumulate(tmp_path: Path):
    feedback.log_feedback(tmp_path, "q1", ["d1"], [1.0], False, False, "d1", True)
    feedback.log_feedback(tmp_path, "q2", ["d2"], [2.0], True, True, "d2", False)

    entries = feedback.read_feedback(tmp_path)

    assert len(entries) == 2
    assert [e["query"] for e in entries] == ["q1", "q2"]
