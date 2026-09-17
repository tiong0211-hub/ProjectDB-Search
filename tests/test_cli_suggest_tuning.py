from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from projectdb_search.cli.main import cli
from projectdb_search.models import make_doc_id
from projectdb_search.search import feedback
from projectdb_search.storage.inverted_index import InvertedIndex


def test_suggest_tuning_reports_no_feedback_yet(tmp_path: Path):
    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True)
    runner = CliRunner()

    result = runner.invoke(cli, ["suggest-tuning", "--index-dir", str(index_dir)])

    assert result.exit_code == 0
    assert "No feedback logged yet" in result.output


def test_suggest_tuning_reports_and_never_writes_to_disk(
    built_index: tuple[Path, Path, InvertedIndex]
):
    index_dir, log_dir, _ = built_index
    runner = CliRunner()

    doc_id = make_doc_id("RiversidePlant/Isometric/HX-203_Isometric_Rev2.pdf")
    for _ in range(6):
        feedback.log_feedback(
            log_dir, "isometric HX-203", [doc_id], [64.0], False, False, chosen_doc_id=doc_id, correct=True
        )

    result = runner.invoke(cli, ["suggest-tuning", "--index-dir", str(index_dir), "--log-dir", str(log_dir)])

    assert result.exit_code == 0
    assert "Analyzed 6 feedback entries" in result.output
    assert "[ranking_weights]" in result.output
    assert "Nothing has been changed automatically" in result.output
    assert "equipment_tag" in result.output
