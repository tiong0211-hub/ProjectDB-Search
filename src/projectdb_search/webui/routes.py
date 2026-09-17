"""Routes for the local web UI.

`/feedback` is deliberately stateless: the browser sends back the exact
top_doc_ids/scores/ambiguous/used_llm_rerank it rendered (embedded in the
results page as a hidden JSON blob, see templates/results.html +
static/app.js) rather than the server re-running the search — this avoids
ever logging feedback against a different result set than what the user
actually saw (which re-running search could produce if LLM re-ranking is
involved).
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, current_app, jsonify, render_template, request, send_file

from projectdb_search.indexer import logging_utils
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.indexer.pipeline import run_deep_scan, run_pipeline
from projectdb_search.search import feedback as feedback_module
from projectdb_search.search.llm import get_llm_backend
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import SearchResult, build_justification
from projectdb_search.search.ranker import search as run_search
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import load_inverted_index_cached
from projectdb_search.webui.reveal import reveal_in_file_manager

# Deep-scan (PDF-text/OCR fallback) runs in a background thread so the
# request that starts it returns immediately -- the browser polls
# /deep-scan/progress instead of blocking on a request for however long the
# scan takes. One job at a time is enough for this single-user local tool;
# state lives in this plain dict rather than a database since it's only
# ever read/written by this one process.
_deep_scan_lock = threading.Lock()
_deep_scan_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "current_file": "",
    "cancel_requested": False,
    "summary": None,
    "error": None,
}

# Stage 1 (filename indexing) runs in the background for the same reason:
# it's usually quick, but over a large folder on a network/OneDrive drive
# it is slow enough that a synchronous request just looks like a hung
# browser. Same shape as the deep-scan state above, plus `phase` -- see
# indexer/pipeline.py's PHASE_* constants.
_index_lock = threading.Lock()
_index_state: dict = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "current_file": "",
    "summary": None,
    "error": None,
}


def _format_built_at(built_at: str) -> str | None:
    """`InvertedIndex.built_at` is stored as an ISO 8601 UTC timestamp (see
    storage/inverted_index.py) -- convert it to the viewer's local time for
    display, since "마지막 인덱싱: ...UTC" would just confuse a Korean office
    user checking how fresh the index is. Returns None (hide the line
    entirely) for a missing/unparseable value rather than showing garbage.
    """
    if not built_at:
        return None
    try:
        return datetime.fromisoformat(built_at).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _count_pending_deep_scan(index_dir: Path) -> int:
    inverted = load_inverted_index_cached(index_dir)
    if inverted is None:
        return 0
    if inverted.documents:
        return sum(
            1 for d in inverted.documents.values() if d.get("extraction_status") == "pending_deep_scan"
        )
    # Index predates the documents snapshot -- fall back to reading the
    # record files (slower, but correct until the next re-index).
    return sum(1 for r in index_store.load_all_records(index_dir) if r.extraction_status == "pending_deep_scan")


def _run_index_job(corpus_path: Path, index_dir: Path, config, log_dir: Path, rebuild: bool) -> None:
    def on_progress(phase: str, done: int, total: int, current_file: str) -> None:
        with _index_lock:
            _index_state["phase"] = phase
            _index_state["done"] = done
            _index_state["total"] = total
            _index_state["current_file"] = current_file

    try:
        summary = run_pipeline(
            corpus_path, index_dir, config, force_rebuild=rebuild, log_dir=log_dir, progress_callback=on_progress
        )
        with _index_lock:
            _index_state["summary"] = {
                "total_files": summary.total_files,
                "processed": summary.processed,
                "skipped_unchanged": summary.skipped_unchanged,
                "corpus_root_changed": summary.corpus_root_changed,
                "pending_deep_scan": summary.pending_deep_scan,
            }
    except Exception as exc:
        with _index_lock:
            _index_state["error"] = str(exc)
    finally:
        with _index_lock:
            _index_state["running"] = False


def _run_deep_scan_job(index_dir: Path, config, corpus_root: Path | None, log_dir: Path) -> None:
    def on_progress(done: int, total: int, current_file: str) -> None:
        with _deep_scan_lock:
            _deep_scan_state["done"] = done
            _deep_scan_state["total"] = total
            _deep_scan_state["current_file"] = current_file

    def should_cancel() -> bool:
        with _deep_scan_lock:
            return _deep_scan_state["cancel_requested"]

    try:
        summary = run_deep_scan(
            index_dir,
            config,
            corpus_root=corpus_root,
            log_dir=log_dir,
            progress_callback=on_progress,
            should_cancel=should_cancel,
        )
        with _deep_scan_lock:
            _deep_scan_state["summary"] = {
                "total_pending": summary.total_pending,
                "processed": summary.processed,
                "cancelled": summary.cancelled,
            }
    except Exception as exc:
        with _deep_scan_lock:
            _deep_scan_state["error"] = str(exc)
    finally:
        with _deep_scan_lock:
            _deep_scan_state["running"] = False


def register_routes(app: Flask) -> None:
    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/")
    def index_page():
        index_dir = current_app.config["INDEX_DIR"]
        inverted = load_inverted_index_cached(index_dir)
        corpus_root = current_app.config["CORPUS_ROOT"] or index_store.load_corpus_root(index_dir)
        return render_template(
            "search.html",
            query=None,
            results=None,
            has_index=inverted is not None,
            last_indexed=_format_built_at(inverted.built_at) if inverted else None,
            indexed_root=str(corpus_root) if corpus_root else None,
        )

    @app.post("/search")
    def do_search():
        index_dir = current_app.config["INDEX_DIR"]
        inverted = load_inverted_index_cached(index_dir)
        if inverted is None:
            return render_template("search.html", query=None, results=None, has_index=False)
        last_indexed = _format_built_at(inverted.built_at)
        corpus_root = current_app.config["CORPUS_ROOT"] or index_store.load_corpus_root(index_dir)
        indexed_root = str(corpus_root) if corpus_root else None

        query_text = request.form.get("query", "").strip()
        if not query_text:
            return render_template(
                "search.html", query="", results=None, has_index=True,
                last_indexed=last_indexed, indexed_root=indexed_root,
            )

        result = _run_search(query_text)
        return render_template(
            "search.html",
            query=query_text,
            results=_build_results_view(result),
            has_index=True,
            last_indexed=last_indexed,
            indexed_root=indexed_root,
            ambiguous=result.ambiguous,
            used_llm_rerank=result.used_llm_rerank,
            search_context={
                "query": query_text,
                "top_doc_ids": [m.record.doc_id for m in result.top],
                "scores": [m.score for m in result.top],
                "ambiguous": result.ambiguous,
                "used_llm_rerank": result.used_llm_rerank,
            },
        )

    @app.get("/index-documents")
    def index_documents_page():
        index_dir = current_app.config["INDEX_DIR"]
        default_corpus_root = current_app.config["CORPUS_ROOT"] or index_store.load_corpus_root(index_dir)
        inverted = load_inverted_index_cached(index_dir)
        # A background run reloads this page when it finishes, so hand its
        # summary over once -- otherwise the run the user just watched would
        # end with no result shown. Consumed here so a later visit doesn't
        # show a stale "Done." from an old run.
        summary = error = None
        with _index_lock:
            if not _index_state["running"]:
                summary, _index_state["summary"] = _index_state["summary"], None
                error, _index_state["error"] = _index_state["error"], None
        return render_template(
            "index_documents.html",
            default_corpus_root=default_corpus_root,
            summary=summary,
            error=error,
            pending_deep_scan_count=_count_pending_deep_scan(index_dir),
            last_indexed=_format_built_at(inverted.built_at) if inverted else None,
        )

    @app.post("/index-documents")
    def do_index_documents():
        index_dir = current_app.config["INDEX_DIR"]
        log_dir = current_app.config["LOG_DIR"]
        config = current_app.config["APP_CONFIG"]

        corpus_root_input = request.form.get("corpus_root", "").strip()
        rebuild = request.form.get("rebuild") == "on"
        corpus_path = Path(corpus_root_input).expanduser() if corpus_root_input else None

        if not corpus_path or not corpus_path.is_dir():
            inverted = load_inverted_index_cached(index_dir)
            return render_template(
                "index_documents.html",
                default_corpus_root=corpus_root_input,
                summary=None,
                error=f"'{corpus_root_input}' is not a folder that exists on this machine.",
                pending_deep_scan_count=_count_pending_deep_scan(index_dir),
                last_indexed=_format_built_at(inverted.built_at) if inverted else None,
            )

        # Stage 1 only -- filename/folder parsing. Fast even over a large
        # corpus, so it stays a normal (blocking) request; the slow part
        # (deep scan) is a separate, backgrounded step below.
        summary = run_pipeline(corpus_path, index_dir, config, force_rebuild=rebuild, log_dir=log_dir)
        current_app.config["CORPUS_ROOT"] = corpus_path.resolve()

        inverted = load_inverted_index_cached(index_dir)
        return render_template(
            "index_documents.html",
            default_corpus_root=str(corpus_path),
            summary=summary,
            error=None,
            pending_deep_scan_count=summary.pending_deep_scan,
            last_indexed=_format_built_at(inverted.built_at) if inverted else None,
        )

    @app.post("/index-documents/start")
    def start_index_documents():
        """Kicks off Stage 1 in the background and returns immediately; the
        browser polls /index-documents/progress. The plain form POST above
        still works (and still blocks) as a no-JavaScript fallback.
        """
        index_dir = current_app.config["INDEX_DIR"]
        log_dir = current_app.config["LOG_DIR"]
        config = current_app.config["APP_CONFIG"]

        corpus_root_input = (request.form.get("corpus_root") or "").strip()
        rebuild = request.form.get("rebuild") == "on"
        corpus_path = Path(corpus_root_input).expanduser() if corpus_root_input else None

        if not corpus_path or not corpus_path.is_dir():
            return jsonify({"error": f"'{corpus_root_input}' is not a folder that exists on this machine."}), 400

        with _index_lock:
            if _index_state["running"]:
                return jsonify({"status": "already_running"}), 409
            _index_state.update(
                running=True, phase="", done=0, total=0, current_file="", summary=None, error=None
            )

        current_app.config["CORPUS_ROOT"] = corpus_path.resolve()
        threading.Thread(
            target=_run_index_job, args=(corpus_path, index_dir, config, log_dir, rebuild), daemon=True
        ).start()
        return jsonify({"status": "started"})

    @app.get("/index-documents/progress")
    def index_documents_progress():
        with _index_lock:
            return jsonify(dict(_index_state))

    @app.post("/deep-scan")
    def start_deep_scan():
        index_dir = current_app.config["INDEX_DIR"]
        log_dir = current_app.config["LOG_DIR"]
        config = current_app.config["APP_CONFIG"]
        corpus_root = current_app.config["CORPUS_ROOT"] or index_store.load_corpus_root(index_dir)

        if corpus_root is None:
            abort(400, "No corpus folder on record -- index a folder first.")

        with _deep_scan_lock:
            if _deep_scan_state["running"]:
                return jsonify({"status": "already_running"}), 409
            _deep_scan_state.update(
                running=True,
                done=0,
                total=0,
                current_file="",
                cancel_requested=False,
                summary=None,
                error=None,
            )

        thread = threading.Thread(
            target=_run_deep_scan_job, args=(index_dir, config, corpus_root, log_dir), daemon=True
        )
        thread.start()
        return jsonify({"status": "started"})

    @app.get("/deep-scan/progress")
    def deep_scan_progress():
        with _deep_scan_lock:
            return jsonify(dict(_deep_scan_state))

    @app.post("/deep-scan/cancel")
    def cancel_deep_scan():
        with _deep_scan_lock:
            if not _deep_scan_state["running"]:
                return jsonify({"status": "not_running"}), 409
            _deep_scan_state["cancel_requested"] = True
        return jsonify({"status": "cancel_requested"})

    @app.post("/feedback")
    def do_feedback():
        payload = request.get_json(force=True) or {}
        try:
            feedback_module.log_feedback(
                current_app.config["LOG_DIR"],
                query_text=payload["query"],
                top_doc_ids=payload["top_doc_ids"],
                scores=payload["scores"],
                ambiguous=payload["ambiguous"],
                used_llm_rerank=payload["used_llm_rerank"],
                chosen_doc_id=payload.get("doc_id"),
                correct=bool(payload.get("correct")),
            )
        except KeyError as exc:
            abort(400, f"missing field: {exc}")
        return jsonify({"status": "ok"})

    @app.get("/file/<doc_id>")
    def get_file(doc_id: str):
        index_dir = current_app.config["INDEX_DIR"]
        corpus_root = current_app.config["CORPUS_ROOT"]
        try:
            record = index_store.load_record(index_dir, doc_id)
        except FileNotFoundError:
            abort(404)

        if corpus_root is None:
            abort(500, "Corpus root is unknown. Re-run `projectdb-search index` to record it.")

        full_path = Path(corpus_root) / record.file_path
        if not full_path.exists():
            abort(404)
        return send_file(full_path)

    @app.post("/reveal/<doc_id>")
    def reveal_file(doc_id: str):
        """Opens the host machine's file manager at this document's folder,
        with the file selected -- see webui/reveal.py for why this is only
        safe on a localhost-only, single-user server like this one.
        """
        index_dir = current_app.config["INDEX_DIR"]
        corpus_root = current_app.config["CORPUS_ROOT"]
        try:
            record = index_store.load_record(index_dir, doc_id)
        except FileNotFoundError:
            abort(404)

        if corpus_root is None:
            abort(500, "Corpus root is unknown. Re-run `projectdb-search index` to record it.")

        full_path = Path(corpus_root) / record.file_path
        if not full_path.exists():
            abort(404)

        if not reveal_in_file_manager(full_path):
            return jsonify({"status": "unsupported"}), 501
        return jsonify({"status": "ok"})

    @app.get("/review-queue")
    def review_queue_page():
        entries = logging_utils.read_review_queue(current_app.config["LOG_DIR"])
        return render_template("review_queue.html", entries=entries)


def _run_search(query_text: str) -> SearchResult:
    index_dir = current_app.config["INDEX_DIR"]
    config = current_app.config["APP_CONFIG"]
    inverted = load_inverted_index_cached(index_dir)
    if inverted is None:
        abort(500, f"No index found at {index_dir}. Run `projectdb-search index` first.")

    parser = FilenameParser(config)
    parsed_query = parse_query(query_text, parser)
    llm_backend = get_llm_backend(config)
    return run_search(parsed_query, index_dir, inverted, config.ranking, llm_backend, top_n=3)


def _build_results_view(result: SearchResult) -> list[dict]:
    return [
        {
            "doc_id": m.record.doc_id,
            "file_path": m.record.file_path,
            "score": m.score,
            "justification": build_justification(m),
        }
        for m in result.top
    ]
