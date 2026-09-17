"""Semi-automatic ranking-weight tuning: analyze data/logs/feedback.jsonl and
SUGGEST ranking_weights changes -- never applies them automatically.

Why not fully automatic: feedback volume is typically small, especially
early on, so blindly auto-adjusting weights risks tuning the ranker in the
wrong direction from noise. Instead this only surfaces a suggestion (with
its sample size and precision shown), bounded to a small per-run change, so
a human decides whether to actually edit config/default_config.toml.

Limitation: only feedback entries with a specific `chosen_doc_id` (the user
picked one of the Top-3 as correct, or flagged one as wrong) can be
attributed to specific matched fields. "None of these were right" feedback
(chosen_doc_id is None) has no single document to attribute blame to, so
it's counted in the report but not used for per-field suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from projectdb_search.config import AppConfig
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import score_document
from projectdb_search.storage import index_store

# matched_fields uses "keywords" (plural); ranking_weights config uses "keyword" (singular).
FIELD_TO_WEIGHT_KEY = {
    "equipment_tag": "equipment_tag",
    "project_name": "project_name",
    "doc_type": "doc_type",
    "department": "department",
    "year": "year",
    "keywords": "keyword",
}

DEFAULT_MIN_SAMPLES = 5
MAX_ADJUSTMENT_FACTOR = 0.2  # never suggest more than a +/-20% change per run
HIGH_PRECISION_THRESHOLD = 0.85
LOW_PRECISION_THRESHOLD = 0.55


@dataclass
class FieldStats:
    field_name: str
    correct: int = 0
    incorrect: int = 0

    @property
    def total(self) -> int:
        return self.correct + self.incorrect

    @property
    def precision(self) -> float | None:
        return self.correct / self.total if self.total else None


@dataclass
class WeightSuggestion:
    field_name: str
    current_weight: float
    samples: int
    precision: float | None
    suggested_weight: float | None  # None means "no change suggested"
    reason: str


def analyze_feedback(
    entries: list[dict[str, Any]], index_dir: Path, config: AppConfig
) -> dict[str, FieldStats]:
    """For each feedback entry with a chosen_doc_id, re-derive which fields
    matched that document for that query, and count correct/incorrect
    outcomes per field.
    """
    parser = FilenameParser(config)
    stats = {key: FieldStats(field_name=key) for key in config.ranking.weights}

    for entry in entries:
        chosen_doc_id = entry.get("chosen_doc_id")
        if not chosen_doc_id:
            continue
        try:
            record = index_store.load_record(index_dir, chosen_doc_id)
        except FileNotFoundError:
            continue  # Document no longer in the index (removed/reindexed) -- skip.

        query = parse_query(entry["query"], parser)
        scored = score_document(query, record, config.ranking)

        for field_name, _value, _kind in scored.matched_fields:
            weight_key = FIELD_TO_WEIGHT_KEY.get(field_name)
            if weight_key is None or weight_key not in stats:
                continue
            if entry.get("correct"):
                stats[weight_key].correct += 1
            else:
                stats[weight_key].incorrect += 1

    return stats


def suggest_weight_changes(
    stats: dict[str, FieldStats],
    current_weights: dict[str, float],
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> list[WeightSuggestion]:
    suggestions = []
    for field_name, field_stats in stats.items():
        current = current_weights.get(field_name, 0)

        if field_stats.total < min_samples:
            suggestions.append(
                WeightSuggestion(
                    field_name, current, field_stats.total, field_stats.precision, None,
                    f"insufficient data (n={field_stats.total} < {min_samples}) -- no suggestion",
                )
            )
            continue

        precision = field_stats.precision
        if precision >= HIGH_PRECISION_THRESHOLD:
            suggested = round(current * (1 + MAX_ADJUSTMENT_FACTOR), 1)
            reason = f"high precision ({precision:.0%} over {field_stats.total} samples) -- consider increasing"
        elif precision <= LOW_PRECISION_THRESHOLD:
            suggested = round(current * (1 - MAX_ADJUSTMENT_FACTOR), 1)
            reason = f"low precision ({precision:.0%} over {field_stats.total} samples) -- consider decreasing"
        else:
            suggested = current
            reason = f"precision looks reasonable ({precision:.0%} over {field_stats.total} samples) -- no change suggested"

        suggestions.append(WeightSuggestion(field_name, current, field_stats.total, precision, suggested, reason))

    return suggestions


def render_suggested_weights_toml(suggestions: list[WeightSuggestion]) -> str:
    """A ready-to-paste [ranking_weights] block reflecting the suggestions
    (falling back to the current weight wherever nothing was suggested)."""
    lines = ["[ranking_weights]"]
    for s in suggestions:
        weight = s.suggested_weight if s.suggested_weight is not None else s.current_weight
        lines.append(f"{s.field_name} = {weight}")
    return "\n".join(lines)
