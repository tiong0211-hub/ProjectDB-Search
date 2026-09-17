"""Core data structures shared by the indexer and the search engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
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

    source_of_metadata: str = "filename"  # e.g. "filename", "filename+pdf_text", "filename+ocr"
    # "ok" | "pending_deep_scan" (filename pass alone couldn't fill the
    # required fields; PDF-text/OCR fallback hasn't run yet -- see
    # indexer/pipeline.py's run_pipeline/run_deep_scan split) | "needs_review"
    extraction_status: str = "ok"
    ocr_confidence: Optional[float] = None
    indexed_at: str = ""
    schema_version: int = 1

    @classmethod
    def from_partial(
        cls,
        corpus_root: Path,
        file_path: Path,
        meta: PartialMetadata,
        source: str = "filename",
        relative_path: str | None = None,
    ) -> "DocumentRecord":
        # `relative_path` lets the indexing loop pass in the relative path
        # it already computed, instead of pathlib recomputing it per record.
        relative = relative_path if relative_path is not None else str(file_path.relative_to(corpus_root))
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

    def merge_partial(self, meta: PartialMetadata, source: str) -> None:
        """Fill fields the first (filename) pass left empty using a
        second-pass result (PDF text or OCR). Fields already set win — the
        first pass is assumed to reflect a deliberate naming convention, so
        a fallback pass only supplements gaps rather than overwriting it.
        """
        for field_name in ("project_name", "doc_type", "year", "department", "equipment_tag"):
            if getattr(self, field_name) is None:
                value = getattr(meta, field_name)
                if value is not None:
                    setattr(self, field_name, value)

        for keyword in meta.keywords:
            if keyword not in self.keywords:
                self.keywords.append(keyword)

        self.source_of_metadata = f"{self.source_of_metadata}+{source}"

    def to_dict(self) -> dict[str, Any]:
        # Deliberately NOT dataclasses.asdict(): that deep-copies
        # recursively (21 internal calls per record here), which showed up
        # as ~20% of total indexing time at 20k documents. Every field on
        # this dataclass is a scalar or a flat list of strings, so a shallow
        # copy is equivalent -- except that `keywords` would be shared with
        # the record, so copy that one list explicitly.
        data = dict(self.__dict__)
        data["keywords"] = list(self.keywords)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentRecord":
        return cls(**data)
