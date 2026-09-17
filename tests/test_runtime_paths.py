from __future__ import annotations

from pathlib import Path

from projectdb_search import runtime_paths


def test_bundle_root_is_none_on_a_normal_dev_machine():
    # sys.executable is a real system/venv python with no sibling tesseract/
    # folder two levels up, so this must not misdetect a bundle.
    assert runtime_paths._bundle_root() is None


def test_configure_tesseract_is_a_no_op_outside_a_bundle():
    runtime_paths.configure_tesseract()  # must not raise


def test_bundle_root_detected_when_layout_matches(tmp_path: Path, monkeypatch):
    bundle_root = tmp_path / "ProjectDB-Search"
    python_dir = bundle_root / "python"
    tesseract_dir = bundle_root / "tesseract"
    python_dir.mkdir(parents=True)
    tesseract_dir.mkdir(parents=True)
    fake_python_exe = python_dir / "python.exe"
    fake_python_exe.touch()
    (tesseract_dir / "tesseract.exe").touch()

    monkeypatch.setattr(runtime_paths.sys, "executable", str(fake_python_exe))

    assert runtime_paths._bundle_root() == bundle_root
