from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from projectdb_search.cli.main import cli
from projectdb_search.models import make_doc_id

EXPECTED_QUERIES_PATH = Path(__file__).parent / "fixtures" / "expected_queries.json"


def _run_index_and_search(corpus_root: Path, index_dir: Path, query: str) -> dict:
    runner = CliRunner()

    index_result = runner.invoke(cli, ["index", "--corpus-root", str(corpus_root), "--index-dir", str(index_dir)])
    assert index_result.exit_code == 0, index_result.output

    search_result = runner.invoke(
        cli, ["search", query, "--index-dir", str(index_dir), "--json"]
    )
    assert search_result.exit_code == 0, search_result.output
    return json.loads(search_result.output)


def test_top3_contains_expected_document_for_every_query(corpus_root: Path, tmp_path: Path):
    index_dir = tmp_path / "index"
    expected_queries = json.loads(EXPECTED_QUERIES_PATH.read_text())

    # Some expected queries only resolve via PDF-text/OCR fallback (see the
    # fixtures' "note" fields) -- --deep-scan runs that pass too.
    # --workers 1 keeps this deterministic/fast for a handful of test docs.
    runner = CliRunner()
    index_result = runner.invoke(
        cli,
        [
            "index", "--corpus-root", str(corpus_root), "--index-dir", str(index_dir),
            "--deep-scan", "--workers", "1",
        ],
    )
    assert index_result.exit_code == 0, index_result.output

    failures = []
    for case in expected_queries:
        expected_doc_id = make_doc_id(case["expected_relative_path"])
        search_result = runner.invoke(
            cli, ["search", case["query"], "--index-dir", str(index_dir), "--json"]
        )
        assert search_result.exit_code == 0, search_result.output
        payload = json.loads(search_result.output)
        top3_doc_ids = [r["doc_id"] for r in payload["results"]]

        if expected_doc_id not in top3_doc_ids:
            failures.append((case["query"], case["expected_relative_path"], top3_doc_ids))

    assert not failures, f"Queries whose expected document was NOT in the Top-3: {failures}"


def test_search_before_index_gives_a_clear_error(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "anything", "--index-dir", str(tmp_path / "no_index")])
    assert result.exit_code != 0
    assert "index" in result.output.lower()


def test_incremental_reindex_skips_unchanged_files(corpus_root: Path, tmp_path: Path):
    index_dir = tmp_path / "index"
    runner = CliRunner()

    total_files = sum(1 for p in corpus_root.rglob("*") if p.is_file())

    first = runner.invoke(cli, ["index", "--corpus-root", str(corpus_root), "--index-dir", str(index_dir)])
    assert first.exit_code == 0, first.output
    assert "skipped 0 unchanged" in first.output

    second = runner.invoke(cli, ["index", "--corpus-root", str(corpus_root), "--index-dir", str(index_dir)])
    assert second.exit_code == 0, second.output
    assert f"skipped {total_files} unchanged" in second.output
