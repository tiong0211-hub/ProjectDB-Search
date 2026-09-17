"""Anthropic-API-compatible backend for the optional ambiguous-query re-rank
step.

`base_url` is deliberately configurable (see config/default_config.toml's
[llm] section) so this exact backend can later be pointed at an internal
Claude Enterprise gateway instead of the public Anthropic API -- swapping
environments is a config change (api_key + base_url), never a code change.

Only candidate metadata (file path, score, which fields matched) is ever
sent to the model -- never raw document content -- since this project's
job is pointing at documents, not reading them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anthropic

from projectdb_search.search.llm.base import LLMBackend

if TYPE_CHECKING:
    from projectdb_search.search.ranker import ScoredMatch

DEFAULT_MODEL = "claude-sonnet-4-5"


class AnthropicAPIBackend(LLMBackend):
    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self._model = model or DEFAULT_MODEL

    def is_enabled(self) -> bool:
        return True

    def rerank(self, query_text: str, top_candidates: list["ScoredMatch"]) -> list["ScoredMatch"]:
        if len(top_candidates) < 2:
            return top_candidates

        candidates_payload = [
            {
                "index": i,
                "file_path": m.record.file_path,
                "score": m.score,
                "matched_fields": [f"{field}={value}" for field, value, _ in m.matched_fields],
            }
            for i, m in enumerate(top_candidates)
        ]
        prompt = (
            "A user is searching an engineering document archive for a specific "
            "document. Given their query and a list of candidate documents "
            "(each with the metadata fields that matched the query), return "
            "the candidate indices ordered from most to least likely to be "
            "what the user wants. Only reorder the given indices -- never "
            "invent new ones, and include every index exactly once.\n\n"
            f"Query: {query_text}\n\n"
            f"Candidates (JSON): {json.dumps(candidates_payload)}\n\n"
            "Respond with ONLY a JSON array of integers, e.g. [2, 0, 1]. No "
            "other text."
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            order = json.loads(response.content[0].text.strip())
            reranked = [top_candidates[i] for i in order if isinstance(i, int) and 0 <= i < len(top_candidates)]
            if len(reranked) == len(top_candidates):
                return reranked
        except Exception:
            pass  # Any API/parsing failure falls back to the rule-based order rather than breaking search.

        return top_candidates
