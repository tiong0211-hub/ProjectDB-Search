"""Append-only JSONL logs written by the PDF/OCR fallback pass.

Two logs, kept separate from the searchable index on purpose:

- `pdf_extraction_log.jsonl`: every PDF-text/OCR extraction attempt, with
  the full extracted text, regardless of whether it ended up merged into
  the index. Kept so future re-parsing (better regex, different ranking)
  can reprocess raw extracted text without re-running OCR.
- `review_queue.jsonl`: documents whose OCR confidence was too low to
  trust. Never blocks indexing — the document is still searchable on
  whatever filename metadata it has — but flags it for a human to look at.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PDF_EXTRACTION_LOG_FILENAME = "pdf_extraction_log.jsonl"
REVIEW_QUEUE_FILENAME = "review_queue.jsonl"
INDEX_RUN_LOG_FILENAME = "index_runs.jsonl"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_extraction(
    log_dir: Path,
    file_path: str,
    source: str,
    text: str,
    confidence: float | None = None,
) -> None:
    _append_jsonl(
        log_dir / PDF_EXTRACTION_LOG_FILENAME,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": file_path,
            "source": source,  # "pdf_text" | "ocr"
            "confidence": confidence,
            "text": text,
        },
    )


def append_review_queue(
    log_dir: Path,
    file_path: str,
    reason: str,
    confidence: float | None = None,
) -> None:
    _append_jsonl(
        log_dir / REVIEW_QUEUE_FILENAME,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": file_path,
            "reason": reason,
            "confidence": confidence,
        },
    )


def read_review_queue(log_dir: Path) -> list[dict[str, Any]]:
    path = log_dir / REVIEW_QUEUE_FILENAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_index_run(
    log_dir: Path,
    corpus_root: str,
    total_files: int,
    processed: int,
    skipped_unchanged: int,
    corpus_root_changed: bool,
) -> None:
    """One entry per `run_pipeline()` call, including no-op runs where
    nothing had changed -- "when did I last even check this folder" is
    useful on its own, so people don't re-index just to find out.
    """
    _append_jsonl(
        log_dir / INDEX_RUN_LOG_FILENAME,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "corpus_root": corpus_root,
            "total_files": total_files,
            "processed": processed,
            "skipped_unchanged": skipped_unchanged,
            "corpus_root_changed": corpus_root_changed,
        },
    )


def read_index_runs(log_dir: Path) -> list[dict[str, Any]]:
    path = log_dir / INDEX_RUN_LOG_FILENAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
