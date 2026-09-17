"""CLI entry point.

    projectdb-search index --corpus-root <path> [--rebuild]
    projectdb-search search "<query>" [--top 3] [--json]
    projectdb-search review-queue [--index-dir ...]
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from projectdb_search.config import load_config
from projectdb_search.indexer import logging_utils
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.indexer.pipeline import run_pipeline
from projectdb_search.search.llm import get_llm_backend
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import build_justification, search
from projectdb_search.storage.inverted_index import load_inverted_index

DEFAULT_INDEX_DIR = Path("data/index")


@click.group()
def cli() -> None:
    """ProjectDB-Search: find which document (and why) matches a query."""


@cli.command("index")
@click.option("--corpus-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option("--rebuild", is_flag=True, help="Force full re-index instead of incremental.")
def index_cmd(corpus_root: Path, index_dir: Path, log_dir: Path | None, rebuild: bool) -> None:
    config = load_config()
    summary = run_pipeline(corpus_root, index_dir, config, force_rebuild=rebuild, log_dir=log_dir)
    click.echo(
        f"Indexed {summary.processed} file(s), skipped {summary.skipped_unchanged} unchanged, "
        f"{summary.total_files} total files found under {corpus_root}."
    )


@cli.command("search")
@click.argument("query_text")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--top", "top_n", default=3, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of text.")
def search_cmd(query_text: str, index_dir: Path, top_n: int, as_json: bool) -> None:
    config = load_config()
    inverted = load_inverted_index(index_dir)
    if inverted is None:
        raise click.ClickException(f"No index found at {index_dir}. Run `projectdb-search index` first.")

    parser = FilenameParser(config)
    parsed_query = parse_query(query_text, parser)
    llm_backend = get_llm_backend(config)

    result = search(parsed_query, index_dir, inverted, config.ranking, llm_backend, top_n=top_n)

    if as_json:
        payload = {
            "query": query_text,
            "ambiguous": result.ambiguous,
            "used_llm_rerank": result.used_llm_rerank,
            "candidate_count": result.candidate_count,
            "results": [
                {
                    "doc_id": m.record.doc_id,
                    "file_path": m.record.file_path,
                    "score": m.score,
                    "justification": build_justification(m),
                }
                for m in result.top
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    if not result.top:
        click.echo("No matching documents found.")
        return

    click.echo(f'Top {len(result.top)} result(s) for "{query_text}"' + (" [ambiguous]" if result.ambiguous else ""))
    for rank, match in enumerate(result.top, start=1):
        click.echo(f"\n{rank}. {match.record.file_path}  (score: {match.score:.1f})")
        click.echo(f"   why: {build_justification(match)}")


@cli.command("review-queue")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
def review_queue_cmd(index_dir: Path, log_dir: Path | None) -> None:
    """List documents flagged for manual review (low-confidence OCR)."""
    log_dir = log_dir or (index_dir.parent / "logs")
    entries = logging_utils.read_review_queue(log_dir)

    if not entries:
        click.echo("Review queue is empty.")
        return

    for entry in entries:
        confidence = entry["confidence"]
        confidence_str = f"{confidence:.2f}" if confidence is not None else "n/a"
        click.echo(f"{entry['file_path']}  reason={entry['reason']}  confidence={confidence_str}  ({entry['timestamp']})")


if __name__ == "__main__":
    cli()
