"""Pluggable LLM re-ranking backend.

`get_llm_backend()` is the single place that decides which backend is
active: `NoOpBackend` when no API key/endpoint is configured (the default —
the tool must always work fully offline), or a real backend once one is
added in a later phase (e.g. an Anthropic-API-compatible backend pointed at
an internal Claude Enterprise gateway via `base_url`). Callers only depend on
the `LLMBackend` interface, so adding that backend later is a new class plus
one branch here — no changes anywhere else in the search engine.
"""

from __future__ import annotations

from projectdb_search.config import AppConfig
from projectdb_search.search.llm.base import LLMBackend
from projectdb_search.search.llm.noop_backend import NoOpBackend


def get_llm_backend(config: AppConfig) -> LLMBackend:
    if not config.llm.api_key:
        return NoOpBackend()
    # No real backend implemented yet (Phase 7 of the project plan) — an
    # api_key with no backend to use it is a config error, not a silent
    # fallback, so it's surfaced rather than swallowed here.
    raise NotImplementedError(
        "An LLM api_key is configured, but no LLM backend is implemented in this phase. "
        "Clear config/default_config.toml's [llm] api_key to use rule-based search only."
    )
