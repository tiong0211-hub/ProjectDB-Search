from __future__ import annotations

from pathlib import Path

from projectdb_search.config import AppConfig
from projectdb_search.models import DocumentRecord
from projectdb_search.search import tuning
from projectdb_search.storage import index_store


def _write_record(index_dir: Path, doc_id: str, **fields) -> DocumentRecord:
    base = dict(doc_id=doc_id, file_path=f"{doc_id}.pdf", file_name=f"{doc_id}.pdf", file_ext=".pdf")
    base.update(fields)
    record = DocumentRecord(**base)
    index_store.write_record(index_dir, record)
    return record


def _feedback(query: str, chosen_doc_id: str | None, correct: bool) -> dict:
    return {
        "query": query,
        "top_doc_ids": [chosen_doc_id] if chosen_doc_id else [],
        "scores": [10.0] if chosen_doc_id else [],
        "ambiguous": False,
        "used_llm_rerank": False,
        "chosen_doc_id": chosen_doc_id,
        "correct": correct,
    }


def test_analyze_feedback_counts_correct_and_incorrect_per_field(tmp_path: Path, app_config: AppConfig):
    index_dir = tmp_path / "index"
    _write_record(index_dir, "d1", equipment_tag="HX-203")
    _write_record(index_dir, "d2", project_name="Riverside Plant")

    entries = [
        _feedback("HX-203", "d1", correct=True),
        _feedback("HX-203", "d1", correct=True),
        _feedback("riverside plant", "d2", correct=False),
    ]

    stats = tuning.analyze_feedback(entries, index_dir, app_config)

    assert stats["equipment_tag"].correct == 2
    assert stats["equipment_tag"].incorrect == 0
    assert stats["project_name"].correct == 0
    assert stats["project_name"].incorrect == 1


def test_analyze_feedback_skips_entries_with_no_chosen_doc(tmp_path: Path, app_config: AppConfig):
    index_dir = tmp_path / "index"
    entries = [_feedback("something", None, correct=False)]

    stats = tuning.analyze_feedback(entries, index_dir, app_config)

    assert all(s.total == 0 for s in stats.values())


def test_analyze_feedback_skips_entries_referencing_missing_documents(tmp_path: Path, app_config: AppConfig):
    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True)
    entries = [_feedback("something", "does-not-exist", correct=True)]

    stats = tuning.analyze_feedback(entries, index_dir, app_config)

    assert all(s.total == 0 for s in stats.values())


def test_suggest_weight_changes_flags_insufficient_data():
    stats = {"equipment_tag": tuning.FieldStats("equipment_tag", correct=2, incorrect=0)}
    weights = {"equipment_tag": 40}

    suggestions = tuning.suggest_weight_changes(stats, weights, min_samples=5)

    assert suggestions[0].suggested_weight is None
    assert "insufficient data" in suggestions[0].reason


def test_suggest_weight_changes_recommends_increase_for_high_precision():
    stats = {"equipment_tag": tuning.FieldStats("equipment_tag", correct=9, incorrect=1)}
    weights = {"equipment_tag": 40}

    suggestions = tuning.suggest_weight_changes(stats, weights, min_samples=5)

    assert suggestions[0].suggested_weight == 48.0  # 40 * 1.2
    assert "increasing" in suggestions[0].reason


def test_suggest_weight_changes_recommends_decrease_for_low_precision():
    stats = {"keyword": tuning.FieldStats("keyword", correct=2, incorrect=8)}
    weights = {"keyword": 3}

    suggestions = tuning.suggest_weight_changes(stats, weights, min_samples=5)

    assert suggestions[0].suggested_weight == 2.4  # 3 * 0.8
    assert "decreasing" in suggestions[0].reason


def test_suggest_weight_changes_no_change_for_middling_precision():
    stats = {"doc_type": tuning.FieldStats("doc_type", correct=7, incorrect=3)}
    weights = {"doc_type": 15}

    suggestions = tuning.suggest_weight_changes(stats, weights, min_samples=5)

    assert suggestions[0].suggested_weight == 15
    assert "no change" in suggestions[0].reason


def test_suggestions_never_exceed_the_bounded_adjustment_factor():
    # Even a perfect 100% precision score should not suggest more than +20%.
    stats = {"equipment_tag": tuning.FieldStats("equipment_tag", correct=100, incorrect=0)}
    weights = {"equipment_tag": 40}

    suggestions = tuning.suggest_weight_changes(stats, weights, min_samples=5)

    assert suggestions[0].suggested_weight <= 40 * (1 + tuning.MAX_ADJUSTMENT_FACTOR)


def test_render_suggested_weights_toml_falls_back_to_current_when_no_suggestion():
    suggestions = [
        tuning.WeightSuggestion("equipment_tag", 40, 2, 1.0, None, "insufficient data"),
        tuning.WeightSuggestion("keyword", 3, 10, 0.2, 2.4, "low precision"),
    ]

    rendered = tuning.render_suggested_weights_toml(suggestions)

    assert "[ranking_weights]" in rendered
    assert "equipment_tag = 40" in rendered
    assert "keyword = 2.4" in rendered
