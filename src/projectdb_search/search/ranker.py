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
from projectdb_search.storage.inverted_index import INDEXED_FIELDS, InvertedIndex, is_loose_match, normalize
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


SCORED_FIELDS = ("equipment_tag", "project_name", "doc_type", "department")


@dataclass
class QueryPlan:
    """Everything derived from the query that would otherwise be recomputed
    for every candidate document. Built once per search (see `plan_query`);
    with tens of thousands of candidates, anything done per-candidate that
    only depends on the query dominates the whole search.
    """

    loose_map: dict[str, set[str]]
    normalized_fields: dict[str, str]


def plan_query(query: ParsedQuery, inverted: InvertedIndex) -> QueryPlan:
    return QueryPlan(
        loose_map=build_loose_keyword_map(query.keywords, inverted),
        normalized_fields={
            field_name: _normalize(value)
            for field_name in SCORED_FIELDS
            if (value := getattr(query, field_name))
        },
    )


def build_loose_keyword_map(query_keywords: list[str], inverted: InvertedIndex) -> dict[str, set[str]]:
    """For each query keyword, the indexed vocabulary tokens that "roughly"
    match it (see `is_loose_match`).

    Computed once per query against the keyword vocabulary, which is small
    and grows slowly. The alternative -- what this replaces -- was running
    `is_loose_match` between every query keyword and every keyword of every
    candidate document, which at 20k documents meant ~330,000 fuzzy string
    comparisons (each with its own normalize + Levenshtein) for a single
    search. Scoring can now answer "does this document loosely match?" with
    a set lookup.
    """
    loose_map: dict[str, set[str]] = {}
    for keyword in query_keywords:
        normalized = normalize(keyword)
        loose_map[keyword] = {
            vocab_token
            for vocab_token in inverted.keyword_index
            if vocab_token != normalized and is_loose_match(normalized, vocab_token)
        }
    return loose_map


def gather_candidates(
    query: ParsedQuery, inverted: InvertedIndex, plan: QueryPlan | None = None
) -> set[str]:
    candidates: set[str] = set()

    for field_name in INDEXED_FIELDS:
        value = getattr(query, field_name)
        if value:
            candidates.update(inverted.field_matches(field_name, str(value)))

    loose_map = plan.loose_map if plan is not None else build_loose_keyword_map(query.keywords, inverted)

    for keyword in query.keywords:
        candidates.update(inverted.keyword_matches(keyword))
        for vocab_token in loose_map.get(keyword, ()):
            candidates.update(inverted.keyword_index.get(vocab_token, ()))

    return candidates


def _normalize(value: object) -> str:
    return str(value).strip().lower()


def _keyword_overlap(
    query_keywords: list[str],
    record_keywords: list[str],
    loose_map: dict[str, set[str]] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Splits query/record keyword overlap into exact hits and "rough"
    hits (substring relationship, e.g. 'compressor' vs a merged token like
    'compressorstation', or a likely single-character typo — see
    `is_loose_match`). Rough hits score lower (see `score_document`) but
    still surface the document instead of missing it entirely.

    `loose_map` (from `build_loose_keyword_map`) turns the rough-hit check
    into a set lookup per keyword. Without it this falls back to comparing
    every query keyword against every record keyword, which is correct but
    far too slow to do once per candidate document at scale.
    """
    record_set = set(record_keywords)
    exact = [k for k in query_keywords if k in record_set]
    exact_set = set(exact)

    loose: list[tuple[str, str]] = []
    for qk in query_keywords:
        if qk in exact_set:
            continue
        if loose_map is not None:
            equivalents = loose_map.get(qk)
            if not equivalents:
                continue
            for rk in record_keywords:
                if rk in equivalents:
                    loose.append((qk, rk))
                    break
        else:
            for rk in record_keywords:
                if is_loose_match(qk, rk):
                    loose.append((qk, rk))
                    break

    return exact, loose


def score_document(
    query: ParsedQuery,
    record: DocumentRecord,
    ranking: RankingConfig,
    plan: QueryPlan | None = None,
) -> ScoredMatch:
    score = 0.0
    matched_fields: list[MatchedField] = []
    normalized_query = plan.normalized_fields if plan is not None else None

    for field_name in SCORED_FIELDS:
        rval = getattr(record, field_name)
        if not rval:
            continue
        if normalized_query is not None:
            nqval = normalized_query.get(field_name)
            if nqval is None:
                continue
        else:
            qval = getattr(query, field_name)
            if not qval:
                continue
            nqval = _normalize(qval)

        weight = ranking.weights.get(field_name, 0)
        nrval = _normalize(rval)
        if nqval == nrval:
            score += weight
            matched_fields.append((field_name, rval, "exact"))
        elif nqval in nrval or nrval in nqval:
            score += weight * 0.6
            matched_fields.append((field_name, rval, "partial"))

    if query.year and record.year and query.year == record.year:
        score += ranking.weights.get("year", 0)
        matched_fields.append(("year", record.year, "exact"))

    exact_hits, loose_hits = _keyword_overlap(
        query.keywords, record.keywords, plan.loose_map if plan is not None else None
    )
    if exact_hits:
        capped = min(len(exact_hits), ranking.max_keyword_hits)
        score += capped * ranking.weights.get("keyword", 0)
        matched_fields.append(("keywords", exact_hits, "keyword"))
    if loose_hits:
        capped = min(len(loose_hits), ranking.max_keyword_hits)
        score += capped * ranking.weights.get("keyword", 0) * 0.6
        matched_fields.append(("keywords", [f"{qk}~{rk}" for qk, rk in loose_hits], "keyword_loose"))

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
            if kind == "keyword_loose":
                parts.append(f"loosely matched keywords: {', '.join(value)}")
            else:
                parts.append(f"matched keywords: {', '.join(value)}")
        elif kind == "exact":
            parts.append(f"{field_name.replace('_', ' ')} matches '{value}'")
        else:
            parts.append(f"{field_name.replace('_', ' ')} partially matches '{value}'")

    if match.record.extraction_status == "needs_review":
        parts.append("(metadata from low-confidence extraction — flagged for review)")

    return "; ".join(parts) if parts else "weak overall keyword overlap only"


def _candidate_record(index_dir: Path, inverted: InvertedIndex, doc_id: str) -> DocumentRecord:
    """The record for a candidate, from the index's in-memory snapshot.

    Scoring used to open and parse one `records/<doc_id>.json` per
    candidate, so a query matching a common project name meant tens of
    thousands of file reads for a single search. The index now carries a
    `documents` snapshot for exactly this. Indexes built before that field
    existed have an empty snapshot, so fall back to the record file rather
    than forcing a re-index.
    """
    data = inverted.documents.get(doc_id)
    if data is not None:
        return DocumentRecord.from_dict(data)
    return load_record(index_dir, doc_id)


def _top_level_folder(file_path: str) -> str:
    parts = Path(file_path).parts
    return parts[0] if parts else file_path


def search(
    query: ParsedQuery,
    index_dir: Path,
    inverted: InvertedIndex,
    ranking: RankingConfig,
    llm_backend: LLMBackend,
    top_n: int = 3,
    sort_by: str = "relevance",  # "relevance" | "newest" | "file_type"
    extensions: set[str] | None = None,       # e.g. {".pdf"} -- None = no filter
    folder_queries: list[str] | None = None,  # e.g. ["Riverside"] -- OR of case-insensitive prefixes
) -> SearchResult:
    # Built once and reused for both candidate gathering and scoring --
    # see QueryPlan for why this matters so much at scale.
    plan = plan_query(query, inverted)

    candidate_ids = gather_candidates(query, inverted, plan)
    if not candidate_ids:
        # Nothing matched any structured field or keyword — fall back to
        # scoring the whole corpus rather than returning nothing.
        candidate_ids = inverted.all_doc_ids()

    folder_prefixes = (
        [f.strip().lower() for f in folder_queries if f.strip()] if folder_queries else None
    )

    # Filters applied before scoring, not after -- narrowing the candidate
    # set here means less work below, not just a smaller displayed slice.
    scored = []
    for doc_id in candidate_ids:
        record = _candidate_record(index_dir, inverted, doc_id)
        if extensions is not None and record.file_ext not in extensions:
            continue
        if folder_prefixes is not None:
            top_folder = _top_level_folder(record.file_path).lower()
            if not any(top_folder.startswith(prefix) for prefix in folder_prefixes):
                continue
        scored.append(score_document(query, record, ranking, plan))

    # Secondary key breaks ties deterministically -- candidate_ids is a
    # set, so without this, which of several equally-scored documents
    # (e.g. hundreds sharing a generic doc_type like "datasheet") lands in
    # the visible slice was effectively random from one run to the next.
    scored.sort(key=lambda m: (-m.score, m.record.file_path))

    ambiguous = is_ambiguous(scored, ranking)
    top = scored[:top_n]
    used_llm_rerank = False

    if ambiguous and llm_backend.is_enabled():
        top = llm_backend.rerank(query.raw_query, scored[:10])[:top_n]
        used_llm_rerank = True

    # Applied last, to the already-decided top slice only -- ambiguity and
    # LLM re-ranking above still operate on relevance order regardless of
    # how the final list is displayed.
    if sort_by == "newest":
        top = sorted(top, key=lambda m: m.record.indexed_at, reverse=True)
    elif sort_by == "file_type":
        top = sorted(top, key=lambda m: (m.record.file_ext, -m.score, m.record.file_path))

    return SearchResult(
        query=query,
        top=top,
        ambiguous=ambiguous,
        candidate_count=len(scored),  # post-filter count -- was len(candidate_ids)
        used_llm_rerank=used_llm_rerank,
    )
