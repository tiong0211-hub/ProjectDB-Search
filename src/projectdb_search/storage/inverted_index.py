"""Inverted index: the data structure that makes search fast.

Two tiers:
- `field_index`: normalized field value -> list of doc_ids, one map per
  structured field (project_name, doc_type, department, equipment_tag).
  Lets the ranker treat an exact field match very differently from an
  incidental keyword hit.
- `keyword_index`: free-text token -> list of doc_ids.

At search time, a query only touches the handful of buckets its own parsed
fields/keywords map to (O(1) dict lookups) to build a small candidate set —
it never re-scans document text, which is what makes this faster than a
person Ctrl+F-ing through files one at a time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from projectdb_search.models import DocumentRecord

INVERTED_INDEX_FILENAME = "inverted_index.json"

INDEXED_FIELDS = ("project_name", "doc_type", "department", "equipment_tag")


def normalize(value: str) -> str:
    return value.strip().lower()


def _levenshtein_at_most_one(a: str, b: str) -> bool:
    """True if `a` and `b` differ by at most one insertion, deletion, or
    substitution. Callers already guarantee len(a) and len(b) differ by at
    most 1, so this never needs full Levenshtein DP.
    """
    if a == b:
        return True
    if len(a) == len(b):
        mismatches = sum(1 for x, y in zip(a, b) if x != y)
        return mismatches <= 1
    if len(a) > len(b):
        a, b = b, a
    i = j = skipped = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            skipped += 1
            if skipped > 1:
                return False
            j += 1
        else:
            i += 1
            j += 1
    return True


def is_loose_match(query_keyword: str, indexed_keyword: str) -> bool:
    """'Roughly the same word': one contains the other (handles a query
    like 'compressor' against a merged token like 'compressorstation', or a
    truncated/rough query), or they differ by a single likely typo. The
    typo check only applies to tokens of 4+ characters -- for shorter
    tokens, "differs by one character" is too loose to mean anything.
    """
    a, b = normalize(query_keyword), normalize(indexed_keyword)
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        # Numbers (years, revision counters, etc.) are exact identifiers --
        # "2018" must never loosely match "2019" or "2015" just because
        # they're one character apart.
        return False
    if a in b or b in a:
        return True
    if len(a) >= 4 and len(b) >= 4 and abs(len(a) - len(b)) <= 1:
        return _levenshtein_at_most_one(a, b)
    return False


@dataclass
class InvertedIndex:
    field_index: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    keyword_index: dict[str, list[str]] = field(default_factory=dict)
    built_at: str = ""

    def field_matches(self, field_name: str, value: str) -> list[str]:
        return self.field_index.get(field_name, {}).get(normalize(value), [])

    def keyword_matches(self, keyword: str) -> list[str]:
        return self.keyword_index.get(normalize(keyword), [])

    def keyword_matches_loose(self, keyword: str) -> list[str]:
        """Documents whose keyword is 'roughly' the query keyword (see
        `is_loose_match`) but not an exact match. Scans the distinct
        keyword vocabulary, not documents, so it stays cheap even at
        thousands of documents.
        """
        normalized = normalize(keyword)
        doc_ids: list[str] = []
        for kw, ids in self.keyword_index.items():
            if kw != normalized and is_loose_match(normalized, kw):
                doc_ids.extend(ids)
        return doc_ids

    def all_doc_ids(self) -> set[str]:
        ids: set[str] = set()
        for bucket in self.field_index.values():
            for doc_ids in bucket.values():
                ids.update(doc_ids)
        for doc_ids in self.keyword_index.values():
            ids.update(doc_ids)
        return ids


def build_inverted_index(records: list[DocumentRecord]) -> InvertedIndex:
    field_index: dict[str, dict[str, list[str]]] = {f: {} for f in INDEXED_FIELDS}
    keyword_index: dict[str, list[str]] = {}

    for record in records:
        for field_name in INDEXED_FIELDS:
            value = getattr(record, field_name)
            if value:
                bucket = field_index[field_name].setdefault(normalize(value), [])
                if record.doc_id not in bucket:
                    bucket.append(record.doc_id)

        for keyword in record.keywords:
            bucket = keyword_index.setdefault(normalize(keyword), [])
            if record.doc_id not in bucket:
                bucket.append(record.doc_id)

    return InvertedIndex(
        field_index=field_index,
        keyword_index=keyword_index,
        built_at=datetime.now(timezone.utc).isoformat(),
    )


def save_inverted_index(index_dir: Path, inverted: InvertedIndex) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": inverted.built_at,
        "field_index": inverted.field_index,
        "keyword_index": inverted.keyword_index,
    }
    with open(index_dir / INVERTED_INDEX_FILENAME, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_inverted_index(index_dir: Path) -> InvertedIndex | None:
    path = index_dir / INVERTED_INDEX_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return InvertedIndex(
        field_index=raw.get("field_index", {}),
        keyword_index=raw.get("keyword_index", {}),
        built_at=raw.get("built_at", ""),
    )
