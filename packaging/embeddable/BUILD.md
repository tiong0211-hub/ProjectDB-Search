# Building the offline bundle

This produces a folder (`ProjectDB-Search/`) that runs on any Windows PC
with **no Python, no pip, no admin rights, and no internet access** —
suitable for carrying into an internal network that has none of those.

Unlike a PyInstaller/exe build, this does **not** need a Windows machine to
assemble — `assemble.py` is plain-stdlib Python and runs on Linux/macOS too.
Only running the *result* (`run.bat`) needs Windows.

## 1. Get a Python runtime + Tesseract build for Windows

**Option A — zero manual downloads (recommended):**

```bash
python packaging/embeddable/fetch_conda_runtime.py
```

This pulls a complete, self-contained Python 3.11 runtime and a Tesseract
OCR build (with its full DLL dependency closure) from
`conda.anaconda.org/conda-forge` — a host that's reachable even in sandboxed
dev environments where python.org and github.com are blocked. Needs `curl`,
`unzip`, `zstd`, `tar` on the machine running it (standard on Linux/macOS;
use WSL on Windows). Takes a few minutes and ~250MB of downloads, cached
under `packaging/embeddable/_conda_work/` so re-runs are fast. Output:
`packaging/embeddable/conda_runtime/{python,tesseract}/`.

Only `eng` + `osd` tessdata are copied by default; if you need other OCR
languages, copy more `*.traineddata` files from
`_conda_work/extracted_tesseract-*/share/tessdata/` into
`conda_runtime/tesseract/tessdata/` before assembling.

**Option B — manual downloads**, if conda-forge is also blocked in your
environment:

| What | Link | Notes |
|---|---|---|
| Python 3.11 embeddable package | https://www.python.org/downloads/windows/ → "Windows embeddable package (64-bit)" for 3.11.x | A `.zip`, ~10MB. Do **not** use the installer — the embeddable package specifically. |
| Tesseract OCR (Windows build) | https://github.com/UB-Mannheim/tesseract/wiki | Run the installer on any Windows machine, then copy its install folder (e.g. `C:\Program Files\Tesseract-OCR`) — must include `tesseract.exe` and a `tessdata\` folder with at least `eng.traineddata`. |

Either way, the app's Python dependencies (including PDF-page rendering
via `pypdfium2` — no separate Poppler install needed) are already vendored
in `packaging/embeddable/wheels/` — see `download_wheels.sh` if you need to
refresh them after a `pyproject.toml` dependency change.

## 2. Assemble

With Option A's output:

```bash
python packaging/embeddable/assemble.py \
  --python-dir packaging/embeddable/conda_runtime/python \
  --tesseract-dir packaging/embeddable/conda_runtime/tesseract
```

Or with Option B's manual downloads:

```bash
python packaging/embeddable/assemble.py \
  --python-embed-zip /path/to/python-3.11.9-embed-amd64.zip \
  --tesseract-dir "/path/to/Tesseract-OCR"
```

This writes `packaging/embeddable/dist/ProjectDB-Search/`:

```
ProjectDB-Search/
├── run.bat            <- double-click this (no console window)
├── run_console.bat    <- same, but shows a console window (for troubleshooting)
├── python/            <- embeddable Python 3.11 + all pip dependencies
├── app/projectdb_search/
├── config/default_config.toml
└── tesseract/          <- tesseract.exe + tessdata/
```

## 3. Test before shipping

On a Windows machine (a clean VM is ideal — no Python installed):

1. Zip the `ProjectDB-Search/` folder, copy it over, unzip.
2. Double-click `run.bat`. A browser tab should open to the search UI
   within a couple seconds.
3. Use "Index documents" to point at a small test folder, confirm it
   indexes without errors (this filename-only pass should finish in
   seconds), then search and open a result.
4. If it reports documents pending a deep scan, click "Start deep scan" and
   confirm it completes (this is the step most likely to fail if
   `tessdata/` wasn't copied correctly — check `run_console.bat`'s output,
   or the review queue page, for the actual error).

## 4. Ship it

Copy the *tested* zip into the internal network via your organization's
approved transfer method. No installation step is needed there — unzip and
run `run.bat`.

## Known limitations of this approach vs. a PyInstaller exe

- It's a folder + `.bat`, not a single polished `.exe`. Functionally
  identical; a desktop shortcut to `run.bat` closes most of the UX gap.
- Antivirus should have less reason to flag this than a PyInstaller exe
  (no self-extracting stub), but a brand-new unsigned `.bat`/`python.exe`
  pair can still occasionally trip a corporate EDR's heuristics on first
  run — test this from the security team's default policy before a wide
  rollout, and get it explicitly allow-listed if needed.
