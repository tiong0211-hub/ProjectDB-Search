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

from pathlib import Path

from flask import Flask, abort, current_app, jsonify, render_template, request, send_file

from projectdb_search.indexer import logging_utils
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.search import feedback as feedback_module
from projectdb_search.search.llm import get_llm_backend
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import SearchResult, build_justification
from projectdb_search.search.ranker import search as run_search
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import load_inverted_index


def register_routes(app: Flask) -> None:
    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/")
    def index_page():
        return render_template("search.html", query=None, results=None)

    @app.post("/search")
    def do_search():
        query_text = request.form.get("query", "").strip()
        if not query_text:
            return render_template("search.html", query="", results=None)

        result = _run_search(query_text)
        return render_template(
            "search.html",
            query=query_text,
            results=_build_results_view(result),
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

    @app.get("/review-queue")
    def review_queue_page():
        entries = logging_utils.read_review_queue(current_app.config["LOG_DIR"])
        return render_template("review_queue.html", entries=entries)


def _run_search(query_text: str) -> SearchResult:
    index_dir = current_app.config["INDEX_DIR"]
    config = current_app.config["APP_CONFIG"]
    inverted = load_inverted_index(index_dir)
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
