"""LLM backend interface for the optional ambiguous-query re-rank step."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from projectdb_search.search.ranker import ScoredMatch


class LLMBackend(ABC):
    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def rerank(self, query_text: str, top_candidates: list["ScoredMatch"]) -> list["ScoredMatch"]: ...
