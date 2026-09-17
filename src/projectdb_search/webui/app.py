"""Flask app factory for the local web UI.

Thin layer only: every route delegates to the exact same search engine
(`search.ranker.search`) and feedback logger (`search.feedback`) the CLI
uses, so results are identical between `projectdb-search search` and the
browser UI.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from projectdb_search.config import AppConfig

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(index_dir: Path, log_dir: Path, corpus_root: Path | None, config: AppConfig) -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
    app.config["INDEX_DIR"] = index_dir
    app.config["LOG_DIR"] = log_dir
    app.config["CORPUS_ROOT"] = corpus_root
    app.config["APP_CONFIG"] = config

    from projectdb_search.webui.routes import register_routes

    register_routes(app)
    return app
