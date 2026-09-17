"""Assembles the offline, embeddable-Python bundle for internal (no-internet)
deployment. This is the alternative to a PyInstaller/exe build: no Windows
machine is needed to build it (this script is plain-stdlib Python and runs
anywhere), and there's no self-extracting-exe pattern to trip antivirus.

Needs a Python runtime and a Tesseract build for Windows, supplied either
way:
  - Recommended, zero manual downloads: run `fetch_conda_runtime.py` first
    (pulls both from conda-forge, which is reachable even where
    python.org/github.com are not), then pass its output directories via
    `--python-dir` / `--tesseract-dir`.
  - Manual: download the official Python embeddable .zip and a Tesseract
    build yourself (see BUILD.md for links), and pass
    `--python-embed-zip` / `--tesseract-dir`.

(PDF page rendering uses pypdfium2, a compiled wheel already in
wheels/ -- no separate Poppler binary is needed either way.)

Usage:
    python packaging/embeddable/assemble.py \
        --python-dir packaging/embeddable/conda_runtime/python \
        --tesseract-dir packaging/embeddable/conda_runtime/tesseract

    # or, with the manual python.org download:
    python packaging/embeddable/assemble.py \
        --python-embed-zip /path/to/python-3.11.9-embed-amd64.zip \
        --tesseract-dir /path/to/extracted/tesseract

Produces packaging/embeddable/dist/ProjectDB-Search/ -- zip that folder and
carry it into the internal network as-is. `run.bat` there needs nothing
else installed: no Python, no pip, no admin rights.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WHEELS_DIR = Path(__file__).resolve().parent / "wheels"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "dist" / "ProjectDB-Search"

RUN_BAT = """@echo off
setlocal
set HERE=%~dp0
set PYTHONPATH=%HERE%app
"%HERE%python\\pythonw.exe" -m projectdb_search.cli.main serve
"""

RUN_CONSOLE_BAT = """@echo off
setlocal
set HERE=%~dp0
set PYTHONPATH=%HERE%app
"%HERE%python\\python.exe" -m projectdb_search.cli.main serve
pause
"""


def _unzip(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dest)


def _enable_site_packages(python_dir: Path) -> None:
    """The embeddable distribution ships with `site` (and hence
    site-packages) disabled by default via its `._pth` file. Re-enable it
    and point it at Lib/site-packages -- we install packages by extracting
    wheels directly (see `_install_wheels`) rather than running pip inside
    the bundle, since the embeddable distribution has no pip either.
    """
    pth_files = list(python_dir.glob("python3*._pth"))
    if not pth_files:
        raise SystemExit(f"Could not find a python3*._pth file in {python_dir} -- is this really the embeddable zip?")
    pth_file = pth_files[0]
    zip_name = f"{pth_file.stem}.zip"
    pth_file.write_text(f"{zip_name}\n.\nLib\\site-packages\n\nimport site\n")


def _install_wheels(wheels_dir: Path, site_packages: Path) -> None:
    wheel_files = sorted(wheels_dir.glob("*.whl"))
    if not wheel_files:
        raise SystemExit(f"No .whl files found in {wheels_dir} -- run download_wheels.sh first")
    site_packages.mkdir(parents=True, exist_ok=True)
    for wheel in wheel_files:
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(site_packages)
    print(f"  extracted {len(wheel_files)} wheel(s) into {site_packages}")


def _copy_app(out_dir: Path) -> None:
    app_dir = out_dir / "app"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(
        REPO_ROOT / "src" / "projectdb_search", app_dir / "projectdb_search", dirs_exist_ok=True, ignore=ignore
    )
    shutil.copytree(REPO_ROOT / "config", out_dir / "config", dirs_exist_ok=True)


def _copy_binaries(src_dir: Path, dest_dir: Path, label: str) -> None:
    if not src_dir.is_dir():
        raise SystemExit(f"{label} directory not found: {src_dir}")
    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)


def _write_launchers(out_dir: Path) -> None:
    (out_dir / "run.bat").write_text(RUN_BAT)
    (out_dir / "run_console.bat").write_text(RUN_CONSOLE_BAT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    python_source = parser.add_mutually_exclusive_group(required=True)
    python_source.add_argument(
        "--python-dir", type=Path, help="Pre-extracted Python runtime dir (e.g. from fetch_conda_runtime.py)"
    )
    python_source.add_argument(
        "--python-embed-zip", type=Path, help="The official python.org Windows embeddable package .zip"
    )
    parser.add_argument(
        "--tesseract-dir", required=True, type=Path, help="Folder containing tesseract.exe + tessdata/"
    )
    parser.add_argument("--wheels-dir", default=DEFAULT_WHEELS_DIR, type=Path)
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, type=Path)
    args = parser.parse_args()

    out_dir = args.out
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    python_dir = out_dir / "python"
    if args.python_dir:
        print("1/5 Copying pre-extracted Python runtime...")
        _copy_binaries(args.python_dir, python_dir, "Python runtime")
        # A full conda-forge Python already supports site-packages natively
        # (no ._pth stripping to undo), so nothing else to do here.
    else:
        print("1/5 Unpacking Python embeddable distribution...")
        _unzip(args.python_embed_zip, python_dir)
        _enable_site_packages(python_dir)

    print("2/5 Installing dependencies from vendored wheels...")
    _install_wheels(args.wheels_dir, python_dir / "Lib" / "site-packages")

    print("3/5 Copying app source + config...")
    _copy_app(out_dir)

    print("4/5 Copying Tesseract...")
    _copy_binaries(args.tesseract_dir, out_dir / "tesseract", "Tesseract")
    if not (out_dir / "tesseract" / "tessdata").is_dir():
        print(
            "WARNING: no tessdata/ folder found under the tesseract dir -- OCR will fail without it.",
            file=sys.stderr,
        )

    print("5/5 Writing launchers...")
    _write_launchers(out_dir)

    print(f"\nDone: {out_dir}")
    print("Zip this folder and carry it into the internal network. Double-click run.bat there.")


if __name__ == "__main__":
    main()
