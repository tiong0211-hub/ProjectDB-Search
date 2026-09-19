from __future__ import annotations

from pathlib import Path

from projectdb_search.config import AppConfig, RankingConfig
from projectdb_search.models import DocumentRecord
from projectdb_search.search.llm.noop_backend import NoOpBackend
from projectdb_search.search.query_parser import ParsedQuery
from projectdb_search.search.ranker import build_justification, is_ambiguous, score_document
from projectdb_search.search.ranker import search as run_search
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import build_inverted_index, save_inverted_index


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


def test_tied_documents_sort_deterministically_by_file_path(app_config: AppConfig, tmp_path: Path):
    """A generic query (e.g. a doc_type shared by hundreds of documents,
    reported by a user searching "Data sheet") ties every candidate at the
    same score. Which of them ends up visible used to depend on
    candidate_ids' set iteration order -- effectively random from one
    process/run to the next. The same query against the same index must
    now always return results in the same order.
    """
    index_dir = tmp_path / "tied_index"
    records = [
        DocumentRecord(
            doc_id=f"doc{i:03d}",
            # Deliberately not inserted in sorted order, so a correct sort
            # can't be an accident of insertion/set-iteration order.
            file_path=f"synthetic/{(97 * i) % 200:05d}.pdf",
            file_name=f"{i:03d}.pdf",
            file_ext=".pdf",
            project_name="Riverside Plant",
            doc_type="datasheet",
            year=2020,
            department=None,
            equipment_tag=None,
            keywords=["data", "sheet"],
        )
        for i in range(200)
    ]
    for record in records:
        index_store.write_record(index_dir, record)
    inverted = build_inverted_index(records)
    save_inverted_index(index_dir, inverted)

    from projectdb_search.indexer.filename_parser import FilenameParser
    from projectdb_search.search.query_parser import parse_query

    parser = FilenameParser(app_config)
    query = parse_query("Data sheet", parser)

    first = run_search(query, index_dir, inverted, app_config.ranking, NoOpBackend(), top_n=200)
    second = run_search(query, index_dir, inverted, app_config.ranking, NoOpBackend(), top_n=200)

    first_paths = [m.record.file_path for m in first.top]
    second_paths = [m.record.file_path for m in second.top]
    assert len(first_paths) > 1, "expected a genuinely tied candidate set"
    assert first_paths == second_paths
    assert first_paths == sorted(first_paths)


def _write_and_build_index(index_dir: Path, records: list[DocumentRecord]):
    for record in records:
        index_store.write_record(index_dir, record)
    inverted = build_inverted_index(records)
    save_inverted_index(index_dir, inverted)
    return inverted


def _tied_query(app_config: AppConfig):
    from projectdb_search.indexer.filename_parser import FilenameParser
    from projectdb_search.search.query_parser import parse_query

    parser = FilenameParser(app_config)
    return parse_query("Data sheet", parser)


def test_sort_by_newest_orders_tied_results_by_indexed_at(app_config: AppConfig, tmp_path: Path):
    records = [
        DocumentRecord(
            doc_id=f"doc{i:02d}",
            file_path=f"synthetic/{i:02d}.pdf",
            file_name=f"{i:02d}.pdf",
            file_ext=".pdf",
            project_name="Riverside Plant",
            doc_type="datasheet",
            year=2020,
            department=None,
            equipment_tag=None,
            keywords=["data", "sheet"],
            indexed_at=f"2024-01-{i + 1:02d}T00:00:00+00:00",
        )
        for i in range(5)
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    result = run_search(query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=5, sort_by="newest")

    assert [m.record.doc_id for m in result.top] == ["doc04", "doc03", "doc02", "doc01", "doc00"]


def test_sort_by_relevance_is_unaffected_by_indexed_at(app_config: AppConfig, tmp_path: Path):
    records = [
        DocumentRecord(
            doc_id=f"doc{i:02d}",
            file_path=f"synthetic/{i:02d}.pdf",
            file_name=f"{i:02d}.pdf",
            file_ext=".pdf",
            project_name="Riverside Plant",
            doc_type="datasheet",
            year=2020,
            department=None,
            equipment_tag=None,
            keywords=["data", "sheet"],
            indexed_at=f"2024-01-{i + 1:02d}T00:00:00+00:00",
        )
        for i in range(5)
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    result = run_search(query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=5)

    # Tied on score, so falls back to the deterministic file_path tie-break
    # regardless of indexed_at -- unaffected by the newest-first feature.
    assert [m.record.file_path for m in result.top] == sorted(r.file_path for r in records)


def test_sort_by_file_type_groups_by_extension_then_relevance(app_config: AppConfig, tmp_path: Path):
    records = [
        DocumentRecord(
            doc_id="pdf_b", file_path="b.pdf", file_name="b.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="pdf_a", file_path="a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet", "extra"],
        ),
        DocumentRecord(
            doc_id="docx_a", file_path="a.docx", file_name="a.docx", file_ext=".docx",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    result = run_search(
        query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=3, sort_by="file_type"
    )

    # .docx group sorts before .pdf alphabetically; within .pdf, pdf_a
    # scores higher (extra keyword hit) so it comes before pdf_b.
    assert [m.record.doc_id for m in result.top] == ["docx_a", "pdf_a", "pdf_b"]


def test_extensions_filter_narrows_results(app_config: AppConfig, tmp_path: Path):
    records = [
        DocumentRecord(
            doc_id="pdf1", file_path="a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="docx1", file_path="a.docx", file_name="a.docx", file_ext=".docx",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    result = run_search(
        query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=5, extensions={".pdf"}
    )

    assert [m.record.doc_id for m in result.top] == ["pdf1"]
    assert result.candidate_count == 1  # post-filter, not the pre-filter candidate pool of 2


def test_folder_queries_filters_by_top_level_folder_with_or_and_prefix_match(
    app_config: AppConfig, tmp_path: Path
):
    records = [
        DocumentRecord(
            doc_id="riverside", file_path="RiversidePlant/a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="compressor", file_path="CompressorStation/a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="unsorted", file_path="Unsorted/a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    # Case-insensitive prefix match, OR'd across multiple folder queries.
    result = run_search(
        query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=5,
        folder_queries=["riverside", "Compressor"],
    )

    assert {m.record.doc_id for m in result.top} == {"riverside", "compressor"}
    assert result.candidate_count == 2


def test_extensions_and_folder_queries_combine_as_intersection(app_config: AppConfig, tmp_path: Path):
    records = [
        DocumentRecord(
            doc_id="match", file_path="RiversidePlant/a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="wrong_folder", file_path="Unsorted/a.pdf", file_name="a.pdf", file_ext=".pdf",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
        DocumentRecord(
            doc_id="wrong_ext", file_path="RiversidePlant/a.docx", file_name="a.docx", file_ext=".docx",
            project_name="Riverside Plant", doc_type="datasheet", year=2020,
            department=None, equipment_tag=None, keywords=["data", "sheet"],
        ),
    ]
    inverted = _write_and_build_index(tmp_path / "index", records)
    query = _tied_query(app_config)

    result = run_search(
        query, tmp_path / "index", inverted, app_config.ranking, NoOpBackend(), top_n=5,
        extensions={".pdf"}, folder_queries=["Riverside"],
    )

    assert [m.record.doc_id for m in result.top] == ["match"]
