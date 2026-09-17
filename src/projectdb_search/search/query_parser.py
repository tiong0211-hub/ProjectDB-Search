"""Parses a natural-language query into the same structured fields used at
index time, by reusing FilenameParser.parse_freetext() — the single shared
rule table described in indexer/filename_parser.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from projectdb_search.indexer.filename_parser import FilenameParser


@dataclass
class ParsedQuery:
    raw_query: str
    project_name: str | None = None
    doc_type: str | None = None
    year: int | None = None
    department: str | None = None
    equipment_tag: str | None = None
    keywords: list[str] = field(default_factory=list)


def parse_query(query_text: str, parser: FilenameParser) -> ParsedQuery:
    meta = parser.parse_freetext(query_text)
    return ParsedQuery(
        raw_query=query_text,
        project_name=meta.project_name,
        doc_type=meta.doc_type,
        year=meta.year,
        department=meta.department,
        equipment_tag=meta.equipment_tag,
        keywords=meta.keywords,
    )
