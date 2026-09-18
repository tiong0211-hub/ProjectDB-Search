"""Smoke tests for packaging/embeddable/assemble.py's app-copy step -- the
part of the offline package build that decides what actually ends up in
the zip handed to a non-technical end user (as opposed to the
python/tesseract runtime steps, which need real downloaded binaries and
aren't exercised in CI).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ASSEMBLE_MODULE_PATH = Path(__file__).parent.parent / "packaging" / "embeddable" / "assemble.py"
_spec = importlib.util.spec_from_file_location("assemble", _ASSEMBLE_MODULE_PATH)
assemble = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(assemble)


def test_copy_app_includes_the_user_guide_at_the_top_level(tmp_path: Path):
    """docs/USER_GUIDE.ko.md previously never made it into dist/ at all --
    _copy_app() only copied src/projectdb_search and config/, so every
    zip handed to the internal network shipped with zero instructions.
    """
    out_dir = tmp_path / "dist"
    assemble._copy_app(out_dir)

    guide = out_dir / "사용설명서.md"
    assert guide.is_file()
    assert guide.read_text(encoding="utf-8") == (
        assemble.REPO_ROOT / "docs" / "USER_GUIDE.ko.md"
    ).read_text(encoding="utf-8")


def test_copy_app_still_copies_the_app_and_config(tmp_path: Path):
    out_dir = tmp_path / "dist"
    assemble._copy_app(out_dir)

    assert (out_dir / "app" / "projectdb_search" / "cli" / "main.py").is_file()
    assert (out_dir / "config" / "default_config.toml").is_file()
