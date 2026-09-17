"""Core data structures shared by the indexer and the search engine."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def make_doc_id(relative_path: str) -> str:
    """Stable id derived from the document's path relative to the corpus root.

    Deterministic across re-index runs, and short enough to use as a filename.
    """
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]


@dataclass
class PartialMetadata:
    """Output of a single parsing pass (filename, or later PDF text/OCR)."""

    project_name: Optional[str] = None
    doc_type: Optional[str] = None
    year: Optional[int] = None
    department: Optional[str] = None
    equipment_tag: Optional[str] = None
    keywords: list[str] = field(default_factory=list)


@dataclass
class DocumentRecord:
    doc_id: str
    file_path: str  # relative to corpus root
    file_name: str
    file_ext: str

    project_name: Optional[str] = None
    doc_type: Optional[str] = None
    year: Optional[int] = None
    department: Optional[str] = None
    equipment_tag: Optional[str] = None
    keywords: list[str] = field(default_factory=list)

    source_of_metadata: str = "filename"  # "filename" | "pdf_text" | "ocr" (future)
    extraction_status: str = "ok"  # "ok" | "partial" | "needs_review" (future)
    indexed_at: str = ""
    schema_version: int = 1

    @classmethod
    def from_partial(
        cls, corpus_root: Path, file_path: Path, meta: PartialMetadata, source: str = "filename"
    ) -> "DocumentRecord":
        relative = str(file_path.relative_to(corpus_root))
        return cls(
            doc_id=make_doc_id(relative),
            file_path=relative,
            file_name=file_path.name,
            file_ext=file_path.suffix.lower(),
            project_name=meta.project_name,
            doc_type=meta.doc_type,
            year=meta.year,
            department=meta.department,
            equipment_tag=meta.equipment_tag,
            keywords=meta.keywords,
            source_of_metadata=source,
            extraction_status="ok",
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentRecord":
        return cls(**data)
