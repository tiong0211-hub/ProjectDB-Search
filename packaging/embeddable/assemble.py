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
    # Non-developer end users only ever see this dist/ folder, never the
    # repo -- docs/USER_GUIDE.ko.md previously never made it into the
    # actual package, so every first-time user got run.bat with zero
    # instructions. Named in Korean, at the top level next to run.bat, so
    # it's the obvious thing to open right after unzipping.
    shutil.copy2(REPO_ROOT / "docs" / "USER_GUIDE.ko.md", out_dir / "사용설명서.md")


def _copy_binaries(src_dir: Path, dest_dir: Path, label: str) -> None:
    if not src_dir.is_dir():
        raise SystemExit(f"{label} directory not found: {src_dir}")
    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)


def _write_launchers(out_dir: Path) -> None:
    (out_dir / "run.bat").write_text(RUN_BAT)
    (out_dir / "run_console.bat").write_text(RUN_CONSOLE_BAT)


# --- Bundle trimming --------------------------------------------------------
# Everything below is verified unused by this app's actual dependency
# closure (grepped for `import distutils`/`lib2to3`/`tkinter` across every
# vendored wheel -- the only hits were cffi's optional runtime-compile path
# and Pillow's optional ImageTk, neither ever touched by this app) or is
# flatly dead weight (CPython's own test suite, __pycache__, standalone CLI
# tools from Tesseract's transitive C-library dependencies that this app
# never invokes -- only their *libraries* are needed). If a future
# dependency needs tkinter/distutils/etc., pass --no-trim while
# investigating rather than assuming this list is still safe.

PYTHON_LIB_TRIM = ["test", "idlelib", "ensurepip", "distutils", "tkinter", "lib2to3", "turtledemo"]
PYTHON_DLL_TRIM_PREFIXES = ["_test", "_ctypes_test", "_tkinter"]
PYTHON_TOP_LEVEL_TRIM = ["tcl86t.dll", "tk86t.dll"]
PYTHON_EXE_TRIM_GLOBS = ["tclsh*.exe", "wish*.exe"]

TESSERACT_EXE_KEEP = {"tesseract.exe"}
# Some conda packages ship both a versioned DLL (e.g. icuuc78.dll) and a
# byte-identical unversioned alias (icuuc.dll). Verified via md5sum (both
# names are byte-identical) and `objdump -p` on every DLL in the bundle
# (only the versioned name is ever referenced from an import table) -- the
# alias is pure dead weight, not a fallback anything loads.
TESSERACT_DUPLICATE_DLL_ALIASES = ["icuuc.dll", "icuin.dll", "icuio.dll", "icutu.dll", "icutest.dll"]


def _trim_python_runtime(python_dir: Path) -> None:
    lib = python_dir / "Lib"
    for name in PYTHON_LIB_TRIM:
        shutil.rmtree(lib / name, ignore_errors=True)
    for cache_dir in lib.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)

    dlls_dir = python_dir / "DLLs"
    if dlls_dir.is_dir():
        for f in list(dlls_dir.iterdir()):
            if any(f.name.startswith(prefix) for prefix in PYTHON_DLL_TRIM_PREFIXES):
                f.unlink()

    for name in PYTHON_TOP_LEVEL_TRIM:
        (python_dir / name).unlink(missing_ok=True)
    for pattern in PYTHON_EXE_TRIM_GLOBS:
        for f in python_dir.glob(pattern):
            f.unlink()


def _trim_tesseract_runtime(tesseract_dir: Path) -> None:
    for f in list(tesseract_dir.glob("*.exe")):
        if f.name not in TESSERACT_EXE_KEEP:
            f.unlink()
    for name in TESSERACT_DUPLICATE_DLL_ALIASES:
        (tesseract_dir / name).unlink(missing_ok=True)


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


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
    parser.add_argument(
        "--no-trim",
        action="store_true",
        help="Skip removing unused stdlib modules / duplicate DLLs / unrelated CLI tools (see _trim_* for what's normally removed).",
    )
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

    if not args.no_trim:
        before = _dir_size(out_dir)
        print("\nTrimming unused stdlib modules / duplicate DLLs / unrelated CLI tools...")
        _trim_python_runtime(python_dir)
        _trim_tesseract_runtime(out_dir / "tesseract")
        after = _dir_size(out_dir)
        print(f"  {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")

    print(f"\nDone: {out_dir}")
    print("Zip this folder and carry it into the internal network. Double-click run.bat there.")


if __name__ == "__main__":
    main()
