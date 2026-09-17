"""Append-only feedback log for search results a user marks correct/incorrect.

v1 scope is logging only — no automatic re-ranking or index correction from
this data yet. It exists so a future pass (tuning ranking weights, fixing a
regex rule, re-indexing a specific document) has real signal to work from,
per the project's "log now, learn later" feedback-loop design.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from projectdb_search.search.ranker import SearchResult

FEEDBACK_LOG_FILENAME = "feedback.jsonl"


def log_feedback(
    log_dir: Path,
    query_text: str,
    top_doc_ids: list[str],
    scores: list[float],
    ambiguous: bool,
    used_llm_rerank: bool,
    chosen_doc_id: str | None,
    correct: bool,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query_text,
        "top_doc_ids": top_doc_ids,
        "scores": scores,
        "ambiguous": ambiguous,
        "used_llm_rerank": used_llm_rerank,
        "chosen_doc_id": chosen_doc_id,
        "correct": correct,
    }
    with open(log_dir / FEEDBACK_LOG_FILENAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_feedback_for_result(
    log_dir: Path, result: "SearchResult", chosen_doc_id: str | None, correct: bool
) -> None:
    """Convenience wrapper so callers (CLI, web UI) don't have to unpack a
    SearchResult by hand before logging."""
    log_feedback(
        log_dir,
        query_text=result.query.raw_query,
        top_doc_ids=[m.record.doc_id for m in result.top],
        scores=[m.score for m in result.top],
        ambiguous=result.ambiguous,
        used_llm_rerank=result.used_llm_rerank,
        chosen_doc_id=chosen_doc_id,
        correct=correct,
    )


def read_feedback(log_dir: Path) -> list[dict[str, Any]]:
    path = log_dir / FEEDBACK_LOG_FILENAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
