"""Pluggable LLM re-ranking backend.

`get_llm_backend()` is the single place that decides which backend is
active: `NoOpBackend` when no API key is configured (the default — the tool
must always work fully offline), or `AnthropicAPIBackend` once one is set.
`base_url` lets that same backend point at an internal Claude Enterprise
gateway later instead of the public API — a config change, not a code
change. Callers only depend on the `LLMBackend` interface, so a future
alternative backend is a new class plus one branch here.
"""

from __future__ import annotations

from projectdb_search.config import AppConfig
from projectdb_search.search.llm.anthropic_backend import AnthropicAPIBackend
from projectdb_search.search.llm.base import LLMBackend
from projectdb_search.search.llm.noop_backend import NoOpBackend


def get_llm_backend(config: AppConfig) -> LLMBackend:
    if not config.llm.api_key:
        return NoOpBackend()
    return AnthropicAPIBackend(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url or None,
        model=config.llm.model or None,
    )
