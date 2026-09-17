from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest

from projectdb_search.config import AppConfig
from projectdb_search.search import feedback
from projectdb_search.storage.inverted_index import InvertedIndex
from projectdb_search.webui.app import create_app


def _client(corpus_root: Path, built_index, app_config: AppConfig):
    index_dir, log_dir, _ = built_index
    app = create_app(index_dir, log_dir, corpus_root, app_config)
    app.testing = True
    return app.test_client(), log_dir


def _sequential_config(app_config: AppConfig) -> AppConfig:
    """A copy of app_config forced to single-worker deep scans -- keeps
    background-job tests deterministic and avoids spawning a process pool
    for a handful of tiny fixture documents.
    """
    return dataclasses.replace(app_config, extraction=dataclasses.replace(app_config.extraction, deep_scan_workers=1))


def _wait_until_not_running(client, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    progress = client.get("/deep-scan/progress").get_json()
    while progress["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
        progress = client.get("/deep-scan/progress").get_json()
    if progress["running"]:
        pytest.fail("deep scan did not finish in time")
    return progress


def test_healthz(corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_index_page_renders_empty_search_form(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"search" in resp.data.lower()


def test_search_page_returns_results_with_justification(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/search", data={"query": "isometric drawing for HX-203"})
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "HX-203_Isometric_Rev2.pdf" in body
    assert "equipment tag matches" in body


def test_search_page_handles_no_matches_gracefully(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/search", data={"query": ""})
    assert resp.status_code == 200


def test_feedback_endpoint_logs_entry(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, log_dir = _client(corpus_root, built_index, app_config)

    resp = client.post(
        "/feedback",
        data=json.dumps(
            {
                "query": "isometric drawing for HX-203",
                "top_doc_ids": ["abc123"],
                "scores": [64.0],
                "ambiguous": False,
                "used_llm_rerank": False,
                "doc_id": "abc123",
                "correct": True,
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200
    entries = feedback.read_feedback(log_dir)
    assert len(entries) == 1
    assert entries[0]["chosen_doc_id"] == "abc123"
    assert entries[0]["correct"] is True


def test_feedback_endpoint_rejects_missing_fields(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/feedback", data=json.dumps({"query": "x"}), content_type="application/json")
    assert resp.status_code == 400


def test_get_file_serves_the_original_document(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    search_resp = client.post("/search", data={"query": "isometric drawing for HX-203"})
    assert b"HX-203" in search_resp.data

    from projectdb_search.models import make_doc_id

    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.get(f"/file/{doc_id}")
    assert resp.status_code == 200


def test_get_file_404s_for_unknown_doc_id(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/file/does-not-exist")
    assert resp.status_code == 404


def test_reveal_file_calls_file_manager_with_the_resolved_path(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig, monkeypatch
):
    from projectdb_search.models import make_doc_id
    from projectdb_search.webui import routes as routes_module

    calls = []
    monkeypatch.setattr(routes_module, "reveal_in_file_manager", lambda path: calls.append(path) or True)

    client, _ = _client(corpus_root, built_index, app_config)
    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.post(f"/reveal/{doc_id}")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert len(calls) == 1
    assert calls[0] == corpus_root / "RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf"


def test_reveal_file_404s_for_unknown_doc_id(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/reveal/does-not-exist")
    assert resp.status_code == 404


def test_reveal_file_404s_when_the_file_no_longer_exists_on_disk(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    from projectdb_search.models import make_doc_id

    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    (corpus_root / "RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf").unlink()

    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post(f"/reveal/{doc_id}")
    assert resp.status_code == 404


def test_reveal_file_returns_501_when_unsupported_on_this_platform(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig, monkeypatch
):
    from projectdb_search.models import make_doc_id
    from projectdb_search.webui import routes as routes_module

    monkeypatch.setattr(routes_module, "reveal_in_file_manager", lambda path: False)

    client, _ = _client(corpus_root, built_index, app_config)
    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.post(f"/reveal/{doc_id}")

    assert resp.status_code == 501
    assert resp.get_json() == {"status": "unsupported"}


def test_review_queue_page_lists_low_confidence_entries(
    corpus_root: Path, deep_scanned_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    # The review queue is only populated once a deep scan has actually run
    # (Stage 1 alone never opens a file, so it never flags anything).
    client, _ = _client(corpus_root, deep_scanned_index, app_config)
    resp = client.get("/review-queue")
    assert resp.status_code == 200
    assert b"blurry_scan_noname.pdf" in resp.data


def test_index_page_shows_no_index_notice_when_index_dir_is_empty(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"No index yet" in resp.data
    assert b'href="/index-documents"' in resp.data


def test_search_before_indexing_shows_no_index_notice_instead_of_erroring(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.post("/search", data={"query": "anything"})

    assert resp.status_code == 200
    assert b"No index yet" in resp.data


def test_index_documents_page_renders_form(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.get("/index-documents")

    assert resp.status_code == 200
    assert b'name="corpus_root"' in resp.data


def test_index_documents_post_builds_index_and_updates_corpus_root(corpus_root: Path, tmp_path: Path, app_config: AppConfig):
    index_dir = tmp_path / "index"
    app = create_app(index_dir, tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.post("/index-documents", data={"corpus_root": str(corpus_root)})

    assert resp.status_code == 200
    assert b"Done." in resp.data
    assert app.config["CORPUS_ROOT"] == corpus_root.resolve()

    # The index now exists, so the homepage should show the search form, not the notice.
    home = client.get("/")
    assert b"No index yet" not in home.data


def test_index_documents_post_warns_when_corpus_root_changes(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    app = create_app(index_dir, tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    first = client.post("/index-documents", data={"corpus_root": str(corpus_root)})
    assert b"previously pointed at a different folder" not in first.data

    other_root = tmp_path / "other_corpus"
    other_root.mkdir()
    (other_root / "Doc.pdf").write_bytes(b"%PDF-1.4\n")

    second = client.post("/index-documents", data={"corpus_root": str(other_root)})
    assert b"previously pointed at a different folder" in second.data


def test_index_documents_post_rejects_nonexistent_path(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.post("/index-documents", data={"corpus_root": str(tmp_path / "does-not-exist")})

    assert resp.status_code == 200
    assert b"is not a folder that exists" in resp.data


def test_index_documents_page_shows_pending_deep_scan_count(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/index-documents")
    assert resp.status_code == 200
    assert b"couldn" in resp.data  # "couldn't be fully identified..."
    assert b"deep-scan-start" in resp.data


def test_deep_scan_endpoint_processes_all_pending_documents(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    index_dir, log_dir, _ = built_index
    app = create_app(index_dir, log_dir, corpus_root, _sequential_config(app_config))
    app.testing = True
    client = app.test_client()

    start = client.post("/deep-scan")
    assert start.status_code == 200
    assert start.get_json()["status"] == "started"

    progress = _wait_until_not_running(client)
    assert progress["summary"]["processed"] == progress["summary"]["total_pending"]
    assert progress["summary"]["processed"] > 0
    assert progress["summary"]["cancelled"] is False


def test_deep_scan_endpoint_rejects_concurrent_start(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    index_dir, log_dir, _ = built_index
    app = create_app(index_dir, log_dir, corpus_root, _sequential_config(app_config))
    app.testing = True
    client = app.test_client()

    first = client.post("/deep-scan")
    assert first.status_code == 200
    second = client.post("/deep-scan")
    assert second.status_code == 409

    _wait_until_not_running(client)  # drain so no background thread outlives the test


def test_deep_scan_cancel_without_a_running_job_returns_409(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/deep-scan/cancel")
    assert resp.status_code == 409
