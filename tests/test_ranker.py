from __future__ import annotations

from projectdb_search.config import AppConfig, RankingConfig
from projectdb_search.models import DocumentRecord
from projectdb_search.search.query_parser import ParsedQuery
from projectdb_search.search.ranker import build_justification, is_ambiguous, score_document


def _record(**overrides) -> DocumentRecord:
    base = dict(
        doc_id="doc1",
        file_path="a/b.pdf",
        file_name="b.pdf",
        file_ext=".pdf",
        project_name="Riverside Plant",
        doc_type="P&ID",
        year=2021,
        department=None,
        equipment_tag=None,
        keywords=["unit", "riverside", "plant"],
    )
    base.update(overrides)
    return DocumentRecord(**base)


def test_exact_field_match_scores_higher_than_partial(app_config: AppConfig):
    query = ParsedQuery(raw_query="q", project_name="Riverside Plant", doc_type="P&ID")
    exact_match = score_document(query, _record(), app_config.ranking)

    partial_query = ParsedQuery(raw_query="q", project_name="Riverside", doc_type="P&ID")
    partial_match = score_document(partial_query, _record(), app_config.ranking)

    assert exact_match.score > partial_match.score


def test_equipment_tag_outweighs_any_single_other_field(app_config: AppConfig):
    record = _record(equipment_tag="HX-203")

    tag_only = score_document(ParsedQuery(raw_query="q", equipment_tag="HX-203"), record, app_config.ranking)
    for field_name, value in [("project_name", "Riverside Plant"), ("doc_type", "P&ID"), ("year", 2021)]:
        other = score_document(ParsedQuery(raw_query="q", **{field_name: value}), record, app_config.ranking)
        assert tag_only.score > other.score, f"equipment_tag should outweigh {field_name} alone"


def test_keyword_hits_are_capped(app_config: AppConfig):
    record = _record(keywords=["a", "b", "c", "d", "e", "f", "g"])
    query = ParsedQuery(raw_query="q", keywords=["a", "b", "c", "d", "e", "f", "g"])

    match = score_document(query, record, app_config.ranking)

    max_expected = app_config.ranking.max_keyword_hits * app_config.ranking.weights["keyword"]
    assert match.score == max_expected


def test_justification_mentions_matched_field_names(app_config: AppConfig):
    query = ParsedQuery(raw_query="q", project_name="Riverside Plant", doc_type="P&ID")
    match = score_document(query, _record(), app_config.ranking)

    text = build_justification(match)

    assert "project name" in text
    assert "doc type" in text


def test_justification_falls_back_to_generic_text_when_nothing_matched(app_config: AppConfig):
    query = ParsedQuery(raw_query="q", project_name="Nonexistent Project")
    match = score_document(query, _record(), app_config.ranking)

    assert match.matched_fields == []
    assert build_justification(match) == "weak overall keyword overlap only"


def test_is_ambiguous_true_when_no_candidates(app_config: AppConfig):
    assert is_ambiguous([], app_config.ranking) is True


def test_is_ambiguous_true_when_top_score_below_threshold(app_config: AppConfig):
    ranking = RankingConfig(
        weights=app_config.ranking.weights,
        ambiguous_absolute_threshold=20,
        ambiguous_margin=5,
        max_keyword_hits=5,
    )
    query = ParsedQuery(raw_query="q", keywords=["riverside"])
    weak_match = score_document(query, _record(keywords=["riverside"]), ranking)

    assert is_ambiguous([weak_match], ranking) is True


def test_is_ambiguous_true_when_top_two_scores_are_close(app_config: AppConfig):
    ranking = RankingConfig(
        weights=app_config.ranking.weights,
        ambiguous_absolute_threshold=0,
        ambiguous_margin=5,
        max_keyword_hits=5,
    )
    query = ParsedQuery(raw_query="q", project_name="Riverside Plant")
    m1 = score_document(query, _record(doc_id="doc1"), ranking)
    m2 = score_document(query, _record(doc_id="doc2"), ranking)  # identical -> same score

    assert is_ambiguous(sorted([m1, m2], key=lambda m: m.score, reverse=True), ranking) is True


def test_is_ambiguous_false_when_top_result_is_strong_and_well_separated(app_config: AppConfig):
    ranking = RankingConfig(
        weights=app_config.ranking.weights,
        ambiguous_absolute_threshold=20,
        ambiguous_margin=5,
        max_keyword_hits=5,
    )
    query = ParsedQuery(raw_query="q", equipment_tag="HX-203", project_name="Riverside Plant")
    strong = score_document(query, _record(equipment_tag="HX-203"), ranking)
    weak = score_document(ParsedQuery(raw_query="q"), _record(doc_id="doc2"), ranking)

    assert is_ambiguous([strong, weak], ranking) is False


def test_loose_keyword_match_scores_lower_than_exact(app_config: AppConfig):
    record = _record(keywords=["compressorstation"])

    exact_query = ParsedQuery(raw_query="q", keywords=["compressorstation"])
    loose_query = ParsedQuery(raw_query="q", keywords=["compressor"])  # substring of the indexed token

    exact_match = score_document(exact_query, record, app_config.ranking)
    loose_match = score_document(loose_query, record, app_config.ranking)

    assert loose_match.score > 0
    assert loose_match.score < exact_match.score


def test_loose_keyword_match_tolerates_a_single_typo(app_config: AppConfig):
    record = _record(keywords=["compressor"])
    query = ParsedQuery(raw_query="q", keywords=["compresor"])  # missing one "s"

    match = score_document(query, record, app_config.ranking)

    assert match.score > 0
    assert any(kind == "keyword_loose" for _, _, kind in match.matched_fields)


def test_justification_distinguishes_loose_from_exact_keyword_matches(app_config: AppConfig):
    record = _record(keywords=["compressorstation"])
    query = ParsedQuery(raw_query="q", keywords=["compressor"])

    match = score_document(query, record, app_config.ranking)
    text = build_justification(match)

    assert "loosely matched keywords" in text
