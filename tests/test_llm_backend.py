from __future__ import annotations

from types import SimpleNamespace

from projectdb_search.config import load_config
from projectdb_search.models import DocumentRecord
from projectdb_search.search.llm import get_llm_backend
from projectdb_search.search.llm.anthropic_backend import AnthropicAPIBackend
from projectdb_search.search.llm.noop_backend import NoOpBackend
from projectdb_search.search.ranker import ScoredMatch


def _match(doc_id: str, score: float) -> ScoredMatch:
    record = DocumentRecord(doc_id=doc_id, file_path=f"{doc_id}.pdf", file_name=f"{doc_id}.pdf", file_ext=".pdf")
    return ScoredMatch(record=record, score=score, matched_fields=[("keywords", ["x"], "keyword")])


def _fake_response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_factory_returns_noop_when_no_api_key():
    config = load_config()  # fresh instance -- config.llm.api_key is "" by default
    assert isinstance(get_llm_backend(config), NoOpBackend)


def test_factory_returns_anthropic_backend_when_api_key_set():
    config = load_config()
    config.llm.api_key = "sk-test-key"
    backend = get_llm_backend(config)
    assert isinstance(backend, AnthropicAPIBackend)
    assert backend.is_enabled() is True


def test_noop_backend_is_pass_through():
    backend = NoOpBackend()
    candidates = [_match("a", 10), _match("b", 5)]
    assert backend.rerank("query", candidates) == candidates
    assert backend.is_enabled() is False


def test_rerank_reorders_candidates_from_model_response():
    backend = AnthropicAPIBackend(api_key="sk-test")
    backend._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: _fake_response("[1, 0]")))
    candidates = [_match("a", 10), _match("b", 5)]

    result = backend.rerank("query", candidates)

    assert [m.record.doc_id for m in result] == ["b", "a"]


def test_rerank_falls_back_to_original_order_on_malformed_response():
    backend = AnthropicAPIBackend(api_key="sk-test")
    backend._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: _fake_response("not json")))
    candidates = [_match("a", 10), _match("b", 5)]

    result = backend.rerank("query", candidates)

    assert result == candidates


def test_rerank_falls_back_when_api_call_raises():
    backend = AnthropicAPIBackend(api_key="sk-test")

    def _raise(**kwargs):
        raise RuntimeError("network error")

    backend._client = SimpleNamespace(messages=SimpleNamespace(create=_raise))
    candidates = [_match("a", 10), _match("b", 5)]

    result = backend.rerank("query", candidates)

    assert result == candidates


def test_rerank_skips_api_call_for_a_single_candidate():
    backend = AnthropicAPIBackend(api_key="sk-test")

    def _should_not_be_called(**kwargs):
        raise AssertionError("should not call the API for a single candidate")

    backend._client = SimpleNamespace(messages=SimpleNamespace(create=_should_not_be_called))
    candidates = [_match("a", 10)]

    result = backend.rerank("query", candidates)

    assert result == candidates
