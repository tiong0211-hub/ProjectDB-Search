# ProjectDB-Search

CLI tool that helps engineers find old plant/manufacturing/construction
documents (drawings, scanned PDFs, photos, technical memos) via natural
language, by matching a query against a metadata index — pointing to
**which document, and why**, rather than summarizing content.

## Status: Phase 0~4 (rule-based search + PDF/OCR fallback)

Implemented so far:
- Filename/folder-based metadata indexer (regex rules, config-tunable)
- A JSON-file metadata index + inverted keyword index (no external DB/search
  engine — this is what keeps search fast without needing anything heavier)
- Rule-based query parsing + scoring/ranking with human-readable
  justification for each result
- A CLI (`projectdb-search index` / `projectdb-search search` / `projectdb-search review-queue`)
- PDF text-layer extraction and Tesseract OCR fallback, run only for
  documents the filename/folder pass couldn't fully identify (never
  indiscriminately across the whole corpus)
- Every fallback extraction attempt is logged (`data/logs/pdf_extraction_log.jsonl`)
  independent of whether it got merged, and low-confidence OCR is flagged
  to a manual review queue (`data/logs/review_queue.jsonl`) instead of
  blocking indexing
- Test fixtures + an automated Top-3 correctness check + a search-speed
  benchmark

**Scope note**: only PDFs and plain image files (jpg/png/tif) are ever
opened for text/OCR extraction. Office formats (Word/Excel/PowerPoint) are
intentionally never parsed — many real copies of those are internal
security-restricted files — and aren't even walked by the indexer.

Not yet implemented (see the project plan for the full roadmap): a local
web UI, a feedback-logging loop for ambiguous search results,
optional LLM-assisted re-ranking, and exe packaging.

## Install

```bash
pip install -e ".[dev]"
```

PDF/OCR fallback also needs two system binaries on PATH (not installed via
pip): [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) and
[Poppler](https://poppler.freedesktop.org/) (for `pdf2image`). On Debian/Ubuntu:

```bash
apt-get install -y tesseract-ocr poppler-utils
```

Without them, filename/folder-only indexing still works — the fallback
pass simply can't run.

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
