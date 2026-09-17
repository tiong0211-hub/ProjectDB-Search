"""Fetches a fully self-contained Windows Python 3.11 runtime + Tesseract OCR
from conda-forge (https://conda.anaconda.org/conda-forge/win-64/), with no
manual downloads needed.

Why conda-forge instead of python.org / github.com (the "official" sources
documented as manual steps in BUILD.md): those two hosts are blocked by
some sandboxed dev environments' network egress policy, while
conda.anaconda.org is often allowed. conda-forge's `python` and `tesseract`
win-64 packages are plain redistributable builds (not conda-the-tool
requirements) -- this script just resolves their dependency closure,
downloads each .conda package, and extracts the files a normal Windows
process needs (DLLs + exes) into a flat runtime folder. No conda/mamba
installation is required to run this script or the resulting bundle.

Requires on the machine RUNNING this script (not on the target machine):
`curl`, `unzip`, `zstd`, `tar` -- all standard on Linux/macOS; on Windows,
run this under WSL.

Usage:
    python packaging/embeddable/fetch_conda_runtime.py

Produces packaging/embeddable/conda_runtime/{python,tesseract}/ -- feed
these into assemble.py via --python-dir / --tesseract-dir instead of
--python-embed-zip, to skip BUILD.md's two manual downloads entirely.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

CHANNEL_URL = "https://conda.anaconda.org/conda-forge/win-64"
WORK_DIR = Path(__file__).resolve().parent / "_conda_work"
OUT_DIR = Path(__file__).resolve().parent / "conda_runtime"

# Pinned root packages. Bump these occasionally; everything else resolves
# to "latest available" for its name (see _resolve below) -- fine for a
# leaf runtime tool where we don't need reproducible-to-the-patch builds.
PYTHON_ROOT = "python-3.11.16-hb12b558_2_cpython.conda"
TESSERACT_ROOT = "tesseract-5.5.3-he87eeb8_0.conda"

# Files/dirs from the python package we don't need at runtime.
PYTHON_EXCLUDE = {"Tools", "libs", "include", "Scripts"}


def _load_repodata() -> dict:
    cache = WORK_DIR / "repodata.json"
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        print("Downloading conda-forge win-64 repodata (~250MB, one-time)...")
        urllib.request.urlretrieve(f"{CHANNEL_URL}/repodata.json", cache)
    with open(cache) as f:
        data = json.load(f)
    pkgs = {}
    pkgs.update(data.get("packages", {}))
    pkgs.update(data.get("packages.conda", {}))
    return pkgs


def _by_name(pkgs: dict) -> dict:
    by_name: dict[str, list] = {}
    for fname, meta in pkgs.items():
        by_name.setdefault(meta["name"], []).append((fname, meta))
    return by_name


def _parse_dep(dep_str: str) -> tuple[str, list[str]]:
    parts = dep_str.split()
    return parts[0], parts[1:]


def _resolve(pkgs: dict, by_name: dict, root_fname: str) -> dict[str, tuple[str, dict]]:
    resolved: dict[str, tuple[str, dict]] = {}
    root_name = pkgs[root_fname]["name"]
    resolved[root_name] = (root_fname, pkgs[root_fname])
    queue = list(resolved[root_name][1].get("depends", []))

    def latest(name: str):
        candidates = by_name.get(name)
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1].get("timestamp", 0))
        return candidates[-1]

    def exact(name: str, version: str, build: str):
        for fname, meta in by_name.get(name, []):
            if meta.get("version") == version and meta.get("build") == build:
                return (fname, meta)
        return None

    while queue:
        dep_str = queue.pop()
        name, rest = _parse_dep(dep_str)
        if name in resolved:
            continue
        entry = None
        if len(rest) == 2 and not any(c in rest[0] for c in "<>="):
            entry = exact(name, rest[0], rest[1])
        if entry is None:
            entry = latest(name)
        if entry is None:
            print(f"  (skipping unresolvable dependency: {dep_str})")
            continue
        resolved[name] = entry
        for dep in entry[1].get("depends", []):
            if _parse_dep(dep)[0] not in resolved:
                queue.append(dep)

    return resolved


def _download_and_extract(fname: str, dest: Path) -> Path:
    conda_path = WORK_DIR / fname
    if not conda_path.exists():
        urllib.request.urlretrieve(f"{CHANNEL_URL}/{fname}", conda_path)

    pkg_entry = subprocess.run(
        ["unzip", "-Z1", str(conda_path)], check=True, capture_output=True, text=True
    ).stdout
    pkg_tar_zst = next(line for line in pkg_entry.splitlines() if line.startswith("pkg-"))

    extract_dir = WORK_DIR / f"extracted_{fname.removesuffix('.conda')}"
    if extract_dir.exists():
        return extract_dir
    subprocess.run(["unzip", "-q", "-o", str(conda_path), pkg_tar_zst, "-d", str(WORK_DIR)], check=True)
    tar_path = WORK_DIR / pkg_tar_zst.removesuffix(".zst")
    subprocess.run(["unzstd", "-q", "-f", str(WORK_DIR / pkg_tar_zst), "-o", str(tar_path)], check=True)
    extract_dir.mkdir(parents=True)
    subprocess.run(["tar", "-xf", str(tar_path), "-C", str(extract_dir)], check=True)
    return extract_dir


def _fetch_closure(pkgs: dict, by_name: dict, root_fname: str) -> dict[str, Path]:
    resolved = _resolve(pkgs, by_name, root_fname)
    print(f"Resolved {len(resolved)} package(s): {', '.join(sorted(resolved))}")
    extracted = {}
    for name, (fname, _meta) in resolved.items():
        print(f"  fetching {fname}...")
        extracted[name] = _download_and_extract(fname, WORK_DIR)
    return extracted


def build_python_runtime() -> Path:
    print("\n=== Python runtime ===")
    pkgs = _load_repodata()
    by_name = _by_name(pkgs)
    extracted = _fetch_closure(pkgs, by_name, PYTHON_ROOT)

    out = OUT_DIR / "python"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    python_dir = extracted["python"]
    for item in python_dir.iterdir():
        if item.name in PYTHON_EXCLUDE or item.suffix == ".pdb":
            continue
        dest = out / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    for name, path in extracted.items():
        if name == "python":
            continue
        bindir = path / "Library" / "bin"
        if bindir.is_dir():
            for f in bindir.iterdir():
                if f.suffix.lower() in (".dll", ".exe") and f.is_file():
                    shutil.copy2(f, out / f.name)

    print(f"Python runtime assembled at {out}")
    return out


def build_tesseract_runtime() -> Path:
    print("\n=== Tesseract runtime ===")
    pkgs = _load_repodata()
    by_name = _by_name(pkgs)
    extracted = _fetch_closure(pkgs, by_name, TESSERACT_ROOT)

    out = OUT_DIR / "tesseract"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for path in extracted.values():
        bindir = path / "Library" / "bin"
        if bindir.is_dir():
            for f in bindir.iterdir():
                if f.suffix.lower() in (".dll", ".exe") and f.is_file():
                    shutil.copy2(f, out / f.name)

    tessdata_src = extracted["tesseract"] / "share" / "tessdata"
    tessdata_out = out / "tessdata"
    tessdata_out.mkdir()
    for lang_file in ("eng.traineddata", "osd.traineddata"):
        shutil.copy2(tessdata_src / lang_file, tessdata_out / lang_file)

    print(f"Tesseract runtime assembled at {out} (eng + osd only -- copy more from")
    print(f"{tessdata_src} into {tessdata_out} if other languages are needed)")
    return out


def main() -> None:
    for tool in ("curl", "unzip", "zstd", "tar"):
        if shutil.which(tool) is None:
            raise SystemExit(f"'{tool}' is required on PATH to run this script.")

    build_python_runtime()
    build_tesseract_runtime()

    print(f"\nDone. Feed these into assemble.py:")
    print(f"  --python-dir {OUT_DIR / 'python'}")
    print(f"  --tesseract-dir {OUT_DIR / 'tesseract'}")


if __name__ == "__main__":
    main()
