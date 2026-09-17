# Building the offline bundle

This produces a folder (`ProjectDB-Search/`) that runs on any Windows PC
with **no Python, no pip, no admin rights, and no internet access** —
suitable for carrying into an internal network that has none of those.

Unlike a PyInstaller/exe build, this does **not** need a Windows machine to
assemble — `assemble.py` is plain-stdlib Python and runs on Linux/macOS too.
Only running the *result* (`run.bat`) needs Windows.

## 1. Three manual downloads (once)

These three hosts are commonly blocked by sandboxed dev environments'
network policy, so grab them yourself on any regular internet-connected
machine:

| What | Link | Notes |
|---|---|---|
| Python 3.11 embeddable package | https://www.python.org/downloads/windows/ → "Windows embeddable package (64-bit)" for 3.11.x | A `.zip`, ~10MB. Do **not** use the installer — the embeddable package specifically. |
| Tesseract OCR (Windows build) | https://github.com/UB-Mannheim/tesseract/wiki | Run the installer on any Windows machine, then copy its install folder (e.g. `C:\Program Files\Tesseract-OCR`) — must include `tesseract.exe` and a `tessdata\` folder with at least `eng.traineddata`. |
| Poppler (Windows build) | https://github.com/oschwartz10612/poppler-windows/releases | A `.zip`; the binaries you need are under `Library\bin\` (contains `pdftoppm.exe`, `pdftocairo.exe`, and supporting DLLs). |

Everything else (the app's Python dependencies) is already vendored in
`packaging/embeddable/wheels/` — see `download_wheels.sh` if you need to
refresh them after a `pyproject.toml` dependency change.

## 2. Assemble

From this repo, with the three items above extracted/available somewhere:

```bash
python packaging/embeddable/assemble.py \
  --python-embed-zip /path/to/python-3.11.9-embed-amd64.zip \
  --tesseract-dir "/path/to/Tesseract-OCR" \
  --poppler-dir "/path/to/poppler-.../Library/bin"
```

This writes `packaging/embeddable/dist/ProjectDB-Search/`:

```
ProjectDB-Search/
├── run.bat            <- double-click this (no console window)
├── run_console.bat    <- same, but shows a console window (for troubleshooting)
├── python/            <- embeddable Python 3.11 + all pip dependencies
├── app/projectdb_search/
├── config/default_config.toml
├── tesseract/          <- tesseract.exe + tessdata/
└── poppler/            <- pdftoppm.exe etc.
```

## 3. Test before shipping

On a Windows machine (a clean VM is ideal — no Python installed):

1. Zip the `ProjectDB-Search/` folder, copy it over, unzip.
2. Double-click `run.bat`. A browser tab should open to the search UI
   within a couple seconds.
3. Use "Index documents" to point at a small test folder, confirm it
   indexes without errors, then search and open a result.
4. If a PDF needs OCR fallback, confirm it completes (this is the step
   most likely to fail if `tessdata/` or Poppler's DLLs weren't copied
   correctly — check `run_console.bat`'s output for the actual error).

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
