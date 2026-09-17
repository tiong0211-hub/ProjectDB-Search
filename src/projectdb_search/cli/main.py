"""CLI entry point.

    projectdb-search index --corpus-root <path> [--rebuild] [--deep-scan]
    projectdb-search deep-scan [--index-dir ...]
    projectdb-search search "<query>" [--top 3] [--json] [--interactive]
    projectdb-search review-queue [--index-dir ...]
    projectdb-search serve [--port 8765] [--no-browser]
    projectdb-search suggest-tuning [--min-samples 5]
    projectdb-search suggest-projects --corpus-root <path> [--depth 1]
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import click

from projectdb_search import runtime_paths
from projectdb_search.config import load_config
from projectdb_search.indexer import logging_utils
from projectdb_search.indexer.filename_parser import FilenameParser
from projectdb_search.indexer.pipeline import run_deep_scan, run_pipeline
from projectdb_search.search import feedback as feedback_module
from projectdb_search.search import tuning
from projectdb_search.search.llm import get_llm_backend
from projectdb_search.search.query_parser import parse_query
from projectdb_search.search.ranker import build_justification, search
from projectdb_search.storage import index_store
from projectdb_search.storage.inverted_index import load_inverted_index

DEFAULT_INDEX_DIR = Path("data/index")


@click.group()
def cli() -> None:
    """ProjectDB-Search: find which document (and why) matches a query."""
    runtime_paths.configure_tesseract()


@cli.command("index")
@click.option("--corpus-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option("--rebuild", is_flag=True, help="Force full re-index instead of incremental.")
@click.option(
    "--deep-scan",
    is_flag=True,
    help="Also run PDF-text/OCR fallback (slow) for documents filename parsing couldn't fully "
    "identify, right after indexing. Can also be run/resumed later via `projectdb-search deep-scan`.",
)
@click.option(
    "--workers",
    "workers",
    default=None,
    type=int,
    help="Parallel workers for --deep-scan. Default: config's deep_scan_workers (0 = auto).",
)
def index_cmd(
    corpus_root: Path, index_dir: Path, log_dir: Path | None, rebuild: bool, deep_scan: bool, workers: int | None
) -> None:
    config = load_config()
    log_dir = log_dir or (index_dir.parent / "logs")

    summary = run_pipeline(corpus_root, index_dir, config, force_rebuild=rebuild, log_dir=log_dir)
    if summary.corpus_root_changed:
        click.echo(
            f"Note: this index previously pointed at a different folder. Cleared it and rebuilt "
            f"from scratch under {corpus_root}.",
            err=True,
        )
    click.echo(
        f"Indexed {summary.processed} file(s), skipped {summary.skipped_unchanged} unchanged, "
        f"{summary.total_files} total files found under {corpus_root}. Search is ready to use now."
    )

    if summary.pending_deep_scan:
        click.echo(
            f"{summary.pending_deep_scan} file(s) couldn't be fully identified from filename/folder "
            "name alone and are searchable by filename only for now."
        )
        if deep_scan:
            _run_deep_scan(index_dir, config, corpus_root=corpus_root, log_dir=log_dir, max_workers=workers)
        else:
            click.echo(
                "Run `projectdb-search deep-scan` (or re-run with --deep-scan) to read their contents "
                "via PDF text/OCR -- this is the slow step and can be run separately, any time, and "
                "resumed if interrupted."
            )


@cli.command("deep-scan")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option(
    "--corpus-root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Override the corpus root recorded at index time.",
)
@click.option(
    "--workers", default=None, type=int, help="Parallel workers. Default: config's deep_scan_workers (0 = auto)."
)
def deep_scan_cmd(index_dir: Path, log_dir: Path | None, corpus_root: Path | None, workers: int | None) -> None:
    """Run (or resume) PDF-text/OCR fallback for documents `index` flagged as
    needing it. This is the slow step -- safe to interrupt with Ctrl+C and
    re-run later; already-processed documents are never redone.
    """
    config = load_config()
    log_dir = log_dir or (index_dir.parent / "logs")
    _run_deep_scan(index_dir, config, corpus_root=corpus_root, log_dir=log_dir, max_workers=workers)


def _run_deep_scan(
    index_dir: Path, config, corpus_root: Path | None, log_dir: Path, max_workers: int | None = None
) -> None:
    start = time.monotonic()

    def on_progress(done: int, total: int, current_file: str) -> None:
        elapsed = time.monotonic() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta_str = f", ~{(total - done) / rate:.0f}s left" if rate > 0 else ""
        click.echo(f"\r  {done}/{total}{eta_str}: {current_file}" + " " * 20, nl=False, err=True)

    try:
        summary = run_deep_scan(
            index_dir,
            config,
            corpus_root=corpus_root,
            log_dir=log_dir,
            progress_callback=on_progress,
            max_workers=max_workers,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    click.echo("", err=True)
    if summary.total_pending == 0:
        click.echo("Nothing pending -- run `projectdb-search index` first, or everything is already scanned.")
        return

    click.echo(
        f"Deep-scanned {summary.processed}/{summary.total_pending} document(s)"
        + (" -- interrupted, re-run to finish the rest." if summary.cancelled else ".")
    )


@cli.command("search")
@click.argument("query_text")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option("--top", "top_n", default=3, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of text.")
@click.option(
    "--interactive", is_flag=True, help="After showing results, ask which one was correct and log the feedback."
)
def search_cmd(
    query_text: str, index_dir: Path, log_dir: Path | None, top_n: int, as_json: bool, interactive: bool
) -> None:
    config = load_config()
    inverted = load_inverted_index(index_dir)
    if inverted is None:
        raise click.ClickException(f"No index found at {index_dir}. Run `projectdb-search index` first.")
    log_dir = log_dir or (index_dir.parent / "logs")

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

    if interactive:
        choices = [str(i) for i in range(1, len(result.top) + 1)] + ["n"]
        choice = click.prompt(
            "\nWhich one is correct? (number, or 'n' for none)", type=click.Choice(choices), show_choices=False
        )
        if choice == "n":
            feedback_module.log_feedback_for_result(log_dir, result, chosen_doc_id=None, correct=False)
        else:
            chosen = result.top[int(choice) - 1]
            feedback_module.log_feedback_for_result(log_dir, result, chosen.record.doc_id, correct=True)
        click.echo("Thanks — feedback logged.")


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


@cli.command("serve")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option(
    "--corpus-root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Override the corpus root recorded at index time (needed to open files from search results).",
)
@click.option("--port", default=8765, show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't automatically open a browser tab.")
def serve_cmd(index_dir: Path, log_dir: Path | None, corpus_root: Path | None, port: int, no_browser: bool) -> None:
    """Start the local web UI (same search engine as `search`, browser front-end).

    No index needs to exist yet -- if one doesn't, the web UI's own
    "Index documents" page lets a non-technical user point it at a folder
    and build it, with no CLI use required at all.
    """
    config = load_config()
    log_dir = log_dir or (index_dir.parent / "logs")
    resolved_corpus_root = corpus_root or index_store.load_corpus_root(index_dir)

    if load_inverted_index(index_dir) is None:
        click.echo("No index found yet -- use the web UI's \"Index documents\" page to build one.", err=True)
    elif resolved_corpus_root is None:
        click.echo(
            "Warning: corpus root is unknown (index was built before this feature, or --corpus-root wasn't "
            "given). Search will work, but 'Open file' links won't.",
            err=True,
        )

    from projectdb_search.webui.app import create_app

    app = create_app(index_dir, log_dir, resolved_corpus_root, config)

    if not no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    click.echo(f"Serving at http://127.0.0.1:{port}  (index: {index_dir})")
    app.run(host="127.0.0.1", port=port)


@cli.command("suggest-tuning")
@click.option("--index-dir", default=DEFAULT_INDEX_DIR, type=click.Path(path_type=Path))
@click.option("--log-dir", default=None, type=click.Path(path_type=Path), help="Default: <index-dir>/../logs")
@click.option(
    "--min-samples",
    default=tuning.DEFAULT_MIN_SAMPLES,
    show_default=True,
    help="Minimum feedback samples for a field before suggesting a weight change.",
)
def suggest_tuning_cmd(index_dir: Path, log_dir: Path | None, min_samples: int) -> None:
    """Analyze logged feedback and SUGGEST ranking_weights changes.

    This never edits config/default_config.toml -- it only prints a report
    and a ready-to-paste [ranking_weights] block for a human to review and
    apply by hand.
    """
    config = load_config()
    log_dir = log_dir or (index_dir.parent / "logs")
    entries = feedback_module.read_feedback(log_dir)

    if not entries:
        click.echo("No feedback logged yet -- nothing to analyze. Use `search --interactive` or the web UI first.")
        return

    unattributed = sum(1 for e in entries if not e.get("chosen_doc_id"))
    click.echo(
        f"Analyzed {len(entries)} feedback entries "
        f"({len(entries) - unattributed} attributable to a specific document, {unattributed} 'none of these').\n"
    )

    stats = tuning.analyze_feedback(entries, index_dir, config)
    suggestions = tuning.suggest_weight_changes(stats, config.ranking.weights, min_samples=min_samples)

    for s in suggestions:
        precision_str = f"{s.precision:.0%}" if s.precision is not None else "n/a"
        change_str = (
            f"{s.current_weight} -> {s.suggested_weight}"
            if s.suggested_weight is not None and s.suggested_weight != s.current_weight
            else f"{s.current_weight} (no change)"
        )
        click.echo(f"{s.field_name:<15} n={s.samples:<4} precision={precision_str:<6} {change_str}")
        click.echo(f"{'':<15} {s.reason}\n")

    click.echo("Nothing has been changed automatically. To apply, paste this into config/default_config.toml:\n")
    click.echo(tuning.render_suggested_weights_toml(suggestions))


@cli.command("suggest-projects")
@click.option("--corpus-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--depth", default=1, show_default=True, help="Folder depth under corpus-root to scan for candidate names."
)
def suggest_projects_cmd(corpus_root: Path, depth: int) -> None:
    """Suggest project_name_patterns entries from your corpus's own folder names.

    Registering real project names in config/default_config.toml is the
    single biggest lever on indexing speed: a document only skips the slow
    PDF-text/OCR fallback pass if its project name AND doc type are both
    identifiable from the filename/folder alone (see
    indexer/pipeline.py:needs_fallback). This never edits the config file
    itself -- it only prints a ready-to-paste block for a human to review.
    """
    counts: Counter[str] = Counter()
    for dirpath, dirnames, _filenames in os.walk(corpus_root):
        rel = Path(dirpath).relative_to(corpus_root)
        current_depth = 0 if rel == Path(".") else len(rel.parts)
        if current_depth >= depth:
            dirnames[:] = []  # don't descend past the requested depth
            continue
        for name in dirnames:
            if name.strip():
                counts[name] += 1

    if not counts:
        click.echo(f"No subfolders found under {corpus_root} at depth <= {depth}.")
        return

    click.echo(f"Folder names found under {corpus_root} (depth <= {depth}), most common first:\n")
    for name, count in counts.most_common():
        click.echo(f"  {count:>4}  {name}")

    click.echo(
        "\nPaste the ones that are real project names into config/default_config.toml's "
        "[project_name_patterns] (remove ones that aren't project names, like 'Unsorted' or 'Legacy'):\n"
    )
    click.echo("[project_name_patterns]")
    for name, _count in counts.most_common():
        pattern = re.escape(name).replace(r"\ ", r"\s*")
        click.echo(f'"{name}" = ["{pattern}"]')


if __name__ == "__main__":
    cli()
