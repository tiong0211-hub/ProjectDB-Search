"""Rule-based candidate generation, scoring, and justification.

This is where the "faster than opening every file and Ctrl+F-ing" property
actually comes from: `gather_candidates()` uses the inverted index to get a
small candidate set via O(1) dict lookups per query field/keyword, and only
that small set is scored — the full corpus is never re-scanned per query.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from projectdb_search.config import RankingConfig
from projectdb_search.models import DocumentRecord
from projectdb_search.search.llm.base import LLMBackend
from projectdb_search.search.query_parser import ParsedQuery
from projectdb_search.storage.index_store import load_record
from projectdb_search.storage.inverted_index import INDEXED_FIELDS, InvertedIndex
from pathlib import Path

MatchedField = tuple[str, object, str]  # (field_name, value, "exact" | "partial" | "keyword")


@dataclass
class ScoredMatch:
    record: DocumentRecord
    score: float
    matched_fields: list[MatchedField] = field(default_factory=list)


@dataclass
class SearchResult:
    query: ParsedQuery
    top: list[ScoredMatch]
    ambiguous: bool
    candidate_count: int
    used_llm_rerank: bool = False


def gather_candidates(query: ParsedQuery, inverted: InvertedIndex) -> set[str]:
    candidates: set[str] = set()

    for field_name in INDEXED_FIELDS:
        value = getattr(query, field_name)
        if value:
            candidates.update(inverted.field_matches(field_name, str(value)))

    for keyword in query.keywords:
        candidates.update(inverted.keyword_matches(keyword))

    return candidates


def _normalize(value: object) -> str:
    return str(value).strip().lower()


def score_document(query: ParsedQuery, record: DocumentRecord, ranking: RankingConfig) -> ScoredMatch:
    score = 0.0
    matched_fields: list[MatchedField] = []

    for field_name in ("equipment_tag", "project_name", "doc_type", "department"):
        qval = getattr(query, field_name)
        rval = getattr(record, field_name)
        weight = ranking.weights.get(field_name, 0)
        if not qval or not rval:
            continue
        if _normalize(qval) == _normalize(rval):
            score += weight
            matched_fields.append((field_name, rval, "exact"))
        elif _normalize(qval) in _normalize(rval) or _normalize(rval) in _normalize(qval):
            score += weight * 0.6
            matched_fields.append((field_name, rval, "partial"))

    if query.year and record.year and query.year == record.year:
        score += ranking.weights.get("year", 0)
        matched_fields.append(("year", record.year, "exact"))

    keyword_hits = [k for k in query.keywords if k in record.keywords]
    if keyword_hits:
        capped = min(len(keyword_hits), ranking.max_keyword_hits)
        score += capped * ranking.weights.get("keyword", 0)
        matched_fields.append(("keywords", keyword_hits, "keyword"))

    if record.extraction_status == "needs_review":
        score *= 0.85

    return ScoredMatch(record=record, score=score, matched_fields=matched_fields)


def is_ambiguous(scored: list[ScoredMatch], ranking: RankingConfig) -> bool:
    if not scored:
        return True
    if scored[0].score < ranking.ambiguous_absolute_threshold:
        return True
    if len(scored) >= 2 and (scored[0].score - scored[1].score) < ranking.ambiguous_margin:
        return True
    return False


def build_justification(match: ScoredMatch) -> str:
    parts = []
    for field_name, value, kind in match.matched_fields:
        if field_name == "keywords":
            parts.append(f"matched keywords: {', '.join(value)}")
        elif kind == "exact":
            parts.append(f"{field_name.replace('_', ' ')} matches '{value}'")
        else:
            parts.append(f"{field_name.replace('_', ' ')} partially matches '{value}'")

    if match.record.extraction_status == "needs_review":
        parts.append("(metadata from low-confidence extraction — flagged for review)")

    return "; ".join(parts) if parts else "weak overall keyword overlap only"


def search(
    query: ParsedQuery,
    index_dir: Path,
    inverted: InvertedIndex,
    ranking: RankingConfig,
    llm_backend: LLMBackend,
    top_n: int = 3,
) -> SearchResult:
    candidate_ids = gather_candidates(query, inverted)
    if not candidate_ids:
        # Nothing matched any structured field or keyword — fall back to
        # scoring the whole (small, thousands-scale) corpus rather than
        # returning nothing.
        candidate_ids = inverted.all_doc_ids()

    scored = [score_document(query, load_record(index_dir, doc_id), ranking) for doc_id in candidate_ids]
    scored.sort(key=lambda m: m.score, reverse=True)

    ambiguous = is_ambiguous(scored, ranking)
    top = scored[:top_n]
    used_llm_rerank = False

    if ambiguous and llm_backend.is_enabled():
        top = llm_backend.rerank(query.raw_query, scored[:10])[:top_n]
        used_llm_rerank = True

    return SearchResult(
        query=query,
        top=top,
        ambiguous=ambiguous,
        candidate_count=len(candidate_ids),
        used_llm_rerank=used_llm_rerank,
    )
