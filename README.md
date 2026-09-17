# ProjectDB-Search

CLI tool that helps engineers find old plant/manufacturing/construction
documents (drawings, scanned PDFs, photos, technical memos) via natural
language, by matching a query against a metadata index — pointing to
**which document, and why**, rather than summarizing content.

## Status: Phase 0~3 (rule-based search MVP)

Implemented in this phase:
- Filename/folder-based metadata indexer (regex rules, config-tunable)
- A JSON-file metadata index + inverted keyword index (no external DB/search
  engine — this is what keeps search fast without needing anything heavier)
- Rule-based query parsing + scoring/ranking with human-readable
  justification for each result
- A CLI (`projectdb-search index` / `projectdb-search search`)
- Test fixtures + an automated Top-3 correctness check + a search-speed
  benchmark

Not yet implemented (see `docs` / project plan for the full roadmap): PDF
text extraction, OCR fallback for scanned documents, a local web UI,
optional LLM-assisted re-ranking for ambiguous queries, and exe packaging.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Build (or incrementally update) the metadata index over a document folder
projectdb-search index --corpus-root /path/to/documents

# Search
projectdb-search search "isometric drawing for HX-203"
projectdb-search search "P&ID unit 3 riverside plant 2021" --json
```

## Why this is faster than opening files and using Ctrl+F

Indexing builds a `keyword/field -> document` inverted index once. Each
search only looks up the handful of buckets its own query terms map to
(hash lookups), scores that small candidate set, and returns the Top-3 with
which fields matched and why — it never re-scans full document text per
query, so performance doesn't degrade as the corpus grows into the
thousands. See `tests/test_search_performance.py` for a benchmark that
demonstrates this directly.

## Tests

```bash
pytest
```

`tests/fixtures/generate_fixtures.py` builds a small synthetic document
corpus (varying filename conventions, including deliberately
non-conforming/"legacy" names) used by the test suite; run it directly if
you want to poke at the fixtures with the CLI by hand:

```bash
python tests/fixtures/generate_fixtures.py
projectdb-search index --corpus-root tests/fixtures/raw_docs
projectdb-search search "isometric drawing for HX-203"
```

## Configuration

`config/default_config.toml` holds the regex rules for extracting
project/doc-type/department/equipment-tag/year metadata, the ranking
weights, and the (currently disabled-by-default) LLM re-rank settings.
Tune these as real document naming conventions are discovered — no code
change needed.
