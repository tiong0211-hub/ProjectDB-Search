from __future__ import annotations

import dataclasses
import json
import threading
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


def test_index_page_shows_last_indexed_timestamp(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/")
    assert b"Last indexed:" in resp.data


def test_index_documents_page_shows_last_indexed_timestamp(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.get("/index-documents")
    assert b"Last indexed:" in resp.data


def test_no_last_indexed_line_before_any_index_exists(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    assert b"Last indexed:" not in client.get("/").data
    assert b"Last indexed:" not in client.get("/index-documents").data


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


def _client_with_many_tied_documents(tmp_path: Path, app_config: AppConfig, count: int = 60):
    """A synthetic index where every document ties on the same score for
    the query "datasheet" -- the scenario a generic query (a user's real
    report: "Data sheet" matching hundreds of documents) needs to exercise
    top_n clamping and the "N matched -- showing M" hint, which the small
    fixture corpus (a handful of documents) never has enough candidates
    to trigger.
    """
    from projectdb_search.models import DocumentRecord
    from projectdb_search.storage import index_store
    from projectdb_search.storage.inverted_index import build_inverted_index, save_inverted_index

    index_dir = tmp_path / "index"
    log_dir = tmp_path / "logs"
    corpus_root = tmp_path / "raw_docs"
    corpus_root.mkdir()

    records = [
        DocumentRecord(
            doc_id=f"doc{i:03d}",
            file_path=f"{i:05d}.pdf",
            file_name=f"{i:05d}.pdf",
            file_ext=".pdf",
            project_name="Riverside Plant",
            doc_type="datasheet",
            year=2020,
            department=None,
            equipment_tag=None,
            keywords=["data", "sheet"],
        )
        for i in range(count)
    ]
    for record in records:
        index_store.write_record(index_dir, record)
    index_store.save_corpus_root(index_dir, corpus_root)
    save_inverted_index(index_dir, build_inverted_index(records))

    app = create_app(index_dir, log_dir, corpus_root, app_config)
    app.testing = True
    return app.test_client()


def _visible_and_hidden_result_counts(body: str) -> tuple[int, int]:
    """`results.html` renders every fetched candidate (up to MAX_TOP_N) so
    "show more" never needs another request -- items past the selected
    top_n are still present in the HTML, just marked `hidden`. Total minus
    hidden is what a browser would actually display.
    """
    total = body.count('class="result-item"')
    hidden = body.count("hidden data-batch=")
    return total - hidden, hidden


def test_search_defaults_to_25_visible_results(tmp_path: Path, app_config: AppConfig):
    client = _client_with_many_tied_documents(tmp_path, app_config, count=60)
    resp = client.post("/search", data={"query": "Data sheet"})
    body = resp.data.decode()
    visible, hidden = _visible_and_hidden_result_counts(body)

    assert visible == 25
    assert hidden == 35
    assert 'id="show-more-results"' in body
    assert "Show 25 more (of 35)" in body


def test_search_top_n_is_respected_and_clamped_to_200(tmp_path: Path, app_config: AppConfig):
    client = _client_with_many_tied_documents(tmp_path, app_config, count=60)

    resp = client.post("/search", data={"query": "Data sheet", "top_n": "10"})
    visible, _ = _visible_and_hidden_result_counts(resp.data.decode())
    assert visible == 10

    # Above the 200 cap: clamps down rather than erroring or ignoring it.
    resp = client.post("/search", data={"query": "Data sheet", "top_n": "9999"})
    assert resp.status_code == 200
    assert 'value="200" selected>200 (max)' in resp.data.decode()


def test_search_top_n_falls_back_to_default_on_garbage_input(tmp_path: Path, app_config: AppConfig):
    client = _client_with_many_tied_documents(tmp_path, app_config, count=60)
    resp = client.post("/search", data={"query": "Data sheet", "top_n": "not-a-number"})
    assert resp.status_code == 200
    visible, _ = _visible_and_hidden_result_counts(resp.data.decode())
    assert visible == 25


def test_search_shows_candidate_count_hint_when_pool_exceeds_200(tmp_path: Path, app_config: AppConfig):
    client = _client_with_many_tied_documents(tmp_path, app_config, count=250)
    resp = client.post("/search", data={"query": "Data sheet", "top_n": "200"})
    body = resp.data.decode()

    assert "250 document(s) matched" in body
    assert "showing 200" in body


def test_search_hides_no_hint_when_the_full_pool_fits(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    client, _ = _client(corpus_root, built_index, app_config)
    resp = client.post("/search", data={"query": "isometric drawing for HX-203"})
    assert b"document(s) matched" not in resp.data


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

    def fake_reveal(path):
        calls.append(path)
        return True, None

    monkeypatch.setattr(routes_module, "reveal_in_file_manager", fake_reveal)

    client, _ = _client(corpus_root, built_index, app_config)
    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.post(f"/reveal/{doc_id}")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "warning": None}
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

    monkeypatch.setattr(routes_module, "reveal_in_file_manager", lambda path: (False, None))

    client, _ = _client(corpus_root, built_index, app_config)
    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.post(f"/reveal/{doc_id}")

    assert resp.status_code == 501
    assert resp.get_json() == {"status": "unsupported"}


def test_reveal_file_surfaces_the_path_too_long_warning(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig, monkeypatch
):
    from projectdb_search.models import make_doc_id
    from projectdb_search.webui import routes as routes_module

    monkeypatch.setattr(routes_module, "reveal_in_file_manager", lambda path: (True, "path_too_long"))

    client, _ = _client(corpus_root, built_index, app_config)
    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    resp = client.post(f"/reveal/{doc_id}")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "warning": "path_too_long"}


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


def _wait_until_index_done(client, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    progress = client.get("/index-documents/progress").get_json()
    while progress["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
        progress = client.get("/index-documents/progress").get_json()
    if progress["running"]:
        pytest.fail("indexing did not finish in time")
    return progress


def test_index_documents_start_runs_in_background_and_reports_progress(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    app = create_app(index_dir, tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    start = client.post("/index-documents/start", data={"corpus_root": str(corpus_root)})
    assert start.status_code == 200
    assert start.get_json()["status"] == "started"

    progress = _wait_until_index_done(client)
    assert progress["error"] is None
    assert progress["summary"]["total_files"] > 0
    assert progress["summary"]["processed"] == progress["summary"]["total_files"]
    assert app.config["CORPUS_ROOT"] == corpus_root.resolve()

    # The finished run's summary is handed to the next page load once.
    page = client.get("/index-documents")
    assert b"Done." in page.data
    assert b"Done." not in client.get("/index-documents").data


def test_index_documents_start_rejects_a_nonexistent_folder(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.post("/index-documents/start", data={"corpus_root": str(tmp_path / "nope")})

    assert resp.status_code == 400
    assert "is not a folder that exists" in resp.get_json()["error"]
    assert client.get("/index-documents/progress").get_json()["running"] is False


def test_index_documents_start_rejects_a_concurrent_run(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig
):
    index_dir = tmp_path / "index"
    app = create_app(index_dir, tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    assert client.post("/index-documents/start", data={"corpus_root": str(corpus_root)}).status_code == 200
    second = client.post("/index-documents/start", data={"corpus_root": str(corpus_root)})
    assert second.status_code == 409

    _wait_until_index_done(client)  # don't leave a thread running past the test


def test_index_documents_cancel_without_a_running_job_returns_409(tmp_path: Path, app_config: AppConfig):
    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    resp = client.post("/index-documents/cancel")
    assert resp.status_code == 409


def test_index_documents_cancel_stops_a_running_job(
    corpus_root: Path, tmp_path: Path, app_config: AppConfig, monkeypatch
):
    """Guards the wiring end-to-end: the cancel route sets the flag,
    _run_index_job's should_cancel reads it, and the finished summary
    reflects the cancellation -- independent of how many files the fixture
    corpus actually has (too few to race a real cancel against reliably).
    """
    from projectdb_search.indexer.pipeline import IndexRunSummary
    from projectdb_search.webui import routes as routes_module

    entered = threading.Event()

    def fake_run_pipeline(
        corpus_path, index_dir, config, force_rebuild=False, log_dir=None, progress_callback=None,
        should_cancel=None,
    ):
        entered.set()
        deadline = time.monotonic() + 5
        while not (should_cancel and should_cancel()) and time.monotonic() < deadline:
            time.sleep(0.01)
        return IndexRunSummary(
            total_files=10, processed=1, skipped_unchanged=0,
            cancelled=bool(should_cancel and should_cancel()),
        )

    monkeypatch.setattr(routes_module, "run_pipeline", fake_run_pipeline)

    app = create_app(tmp_path / "index", tmp_path / "logs", None, app_config)
    app.testing = True
    client = app.test_client()

    start = client.post("/index-documents/start", data={"corpus_root": str(corpus_root)})
    assert start.status_code == 200
    assert entered.wait(timeout=5), "background job never started"

    cancel = client.post("/index-documents/cancel")
    assert cancel.status_code == 200
    assert cancel.get_json()["status"] == "cancel_requested"

    progress = _wait_until_index_done(client)
    assert progress["summary"]["cancelled"] is True


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


def test_cancel_buttons_share_one_class_not_the_global_feedback_handler(
    corpus_root: Path, built_index: tuple[Path, Path, InvertedIndex], app_config: AppConfig
):
    """Both cancel buttons must use their own class -- .feedback-btn is
    also what the search results' "Correct"/"Not this one" buttons use,
    and a global click handler for that class assumes a #search-context
    element exists on the page (see app.js), which this page never has.
    """
    client, _ = _client(corpus_root, built_index, app_config)
    body = client.get("/index-documents").data.decode()

    assert 'id="deep-scan-cancel" class="cancel-btn"' in body
    assert 'id="index-cancel" class="cancel-btn"' in body
    assert "feedback-btn" not in body
