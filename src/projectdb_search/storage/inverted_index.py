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

# `year` is indexed like the string fields even though it's an int: a
# year-only query ("2021 도면") otherwise matches no bucket at all, and
# search falls back to scoring every document in the corpus to find the
# ones whose year matches -- the slowest possible path for a perfectly
# ordinary query.
INDEXED_FIELDS = ("project_name", "doc_type", "department", "equipment_tag", "year")


def normalize(value: object) -> str:
    return str(value).strip().lower()


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
    # doc_id -> that document's record, as a plain dict. A read-optimized
    # snapshot of records/*.json (which stays the durable per-document
    # store): scoring a query used to open and parse one record file per
    # candidate, so a query matching a common project name meant tens of
    # thousands of file reads. Search reads this one already-loaded map
    # instead. Empty for indexes built before this field existed --
    # ranker.py falls back to reading the record file in that case.
    documents: dict[str, dict] = field(default_factory=dict)

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
    # Sets during construction -- O(1) membership/insert, vs. the O(bucket
    # size) `if doc_id not in a_list` this replaces, which made this
    # function effectively O(n^2) whenever many documents share a field
    # value or keyword (a corpus's most common project name, "photo", the
    # word "unit", etc. -- exactly the common case at real scale). Measured
    # impact: ~8.3s to build for 20,000 documents with the old list-based
    # check, vs. a fraction of that with sets. Converted to sorted lists
    # once at the end -- JSON-serializable, deterministic, and exactly the
    # shape every reader (field_matches/keyword_matches/all_doc_ids) expects.
    field_sets: dict[str, dict[str, set[str]]] = {f: {} for f in INDEXED_FIELDS}
    keyword_sets: dict[str, set[str]] = {}

    for record in records:
        for field_name in INDEXED_FIELDS:
            value = getattr(record, field_name)
            if value:
                field_sets[field_name].setdefault(normalize(value), set()).add(record.doc_id)

        for keyword in record.keywords:
            keyword_sets.setdefault(normalize(keyword), set()).add(record.doc_id)

    return InvertedIndex(
        field_index={
            field_name: {value: sorted(doc_ids) for value, doc_ids in buckets.items()}
            for field_name, buckets in field_sets.items()
        },
        keyword_index={keyword: sorted(doc_ids) for keyword, doc_ids in keyword_sets.items()},
        built_at=datetime.now(timezone.utc).isoformat(),
        documents={record.doc_id: record.to_dict() for record in records},
    )


def save_inverted_index(index_dir: Path, inverted: InvertedIndex) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": inverted.built_at,
        "field_index": inverted.field_index,
        "keyword_index": inverted.keyword_index,
        "documents": inverted.documents,
    }
    with open(index_dir / INVERTED_INDEX_FILENAME, "w", encoding="utf-8") as f:
        # No indent -- see save_manifest. This is the single largest file
        # the app writes and the one search re-reads, so its size matters
        # twice over.
        json.dump(payload, f, separators=(",", ":"))


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
        documents=raw.get("documents", {}),
    )


_cache: dict[Path, tuple[float, int, InvertedIndex]] = {}


def load_inverted_index_cached(index_dir: Path) -> InvertedIndex | None:
    """`load_inverted_index`, but reusing the previously parsed index while
    the file on disk is unchanged.

    The web UI parses the index on every request (twice, on a search), and
    at tens of thousands of documents that parse is tens of milliseconds of
    pure overhead per query. Keyed on the file's (mtime, size) so an index
    rebuilt by this process -- or by a `projectdb-search index` run in
    another terminal -- is picked up on the next call rather than served
    stale.
    """
    path = index_dir / INVERTED_INDEX_FILENAME
    try:
        stat = path.stat()
    except OSError:
        _cache.pop(index_dir, None)
        return None

    cached = _cache.get(index_dir)
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    inverted = load_inverted_index(index_dir)
    if inverted is not None:
        _cache[index_dir] = (stat.st_mtime, stat.st_size, inverted)
    return inverted
