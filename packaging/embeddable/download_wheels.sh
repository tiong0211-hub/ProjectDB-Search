#!/usr/bin/env bash
# Downloads the full Windows (win_amd64, CPython 3.11) wheel closure for
# this app's runtime dependencies from PyPI, for offline assembly into the
# embeddable-Python bundle (see BUILD.md). Re-run this whenever
# pyproject.toml's [project.dependencies] changes.
#
# Deliberately NOT run against pyproject.toml's install_requires via `pip
# download .` -- that would also need this package's own metadata resolved,
# and it has no Windows-specific requirements of its own. Listing the
# top-level runtime deps directly is simpler and matches pyproject.toml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/wheels"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

pip download \
  --platform win_amd64 --python-version 3.11 --implementation cp --abi cp311 \
  --only-binary=:all: \
  -d "$OUT_DIR" \
  click pytesseract pypdfium2 Pillow Flask anthropic

ls "$OUT_DIR" > "$SCRIPT_DIR/wheels-manifest.txt"
echo "Downloaded $(wc -l < "$SCRIPT_DIR/wheels-manifest.txt") wheel(s) to $OUT_DIR"
