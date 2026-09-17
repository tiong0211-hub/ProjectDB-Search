"""Default LLM backend: disabled, pass-through. Never called since
`is_enabled()` is False, but implements the interface fully so callers never
need to special-case "no backend configured".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from projectdb_search.search.llm.base import LLMBackend

if TYPE_CHECKING:
    from projectdb_search.search.ranker import ScoredMatch


class NoOpBackend(LLMBackend):
    def is_enabled(self) -> bool:
        return False

    def rerank(self, query_text: str, top_candidates: list["ScoredMatch"]) -> list["ScoredMatch"]:
        return top_candidates
