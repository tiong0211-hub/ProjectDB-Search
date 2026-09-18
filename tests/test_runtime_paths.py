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


def test_to_extended_path_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    long_drive_path = Path("C:\\" + "\\".join(["a_long_folder_name_segment"] * 10) + "\\file.pdf")
    assert runtime_paths.to_extended_path(long_drive_path) == long_drive_path


def test_to_extended_path_is_a_no_op_for_short_paths(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    short_path = Path(r"C:\Shared\Docs\HX-203.pdf")
    assert runtime_paths.to_extended_path(short_path) == short_path


def test_to_extended_path_prefixes_a_long_drive_letter_path(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    long_path = Path("C:\\" + "\\".join(["a_long_folder_name_segment"] * 10) + "\\file.pdf")
    assert len(str(long_path)) >= runtime_paths._LONG_PATH_THRESHOLD

    result = runtime_paths.to_extended_path(long_path)

    assert str(result) == "\\\\?\\" + str(long_path)


def test_to_extended_path_prefixes_a_long_unc_path(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    long_unc = Path("\\\\" + "\\".join(["a_long_folder_name_segment"] * 10) + "\\file.pdf")
    assert len(str(long_unc)) >= runtime_paths._LONG_PATH_THRESHOLD

    result = runtime_paths.to_extended_path(long_unc)

    assert str(result) == "\\\\?\\UNC\\" + str(long_unc)[2:]


def test_to_extended_path_leaves_an_already_prefixed_path_alone(monkeypatch):
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    already_prefixed = Path("\\\\?\\C:\\short.pdf")
    assert runtime_paths.to_extended_path(already_prefixed) == already_prefixed


def test_strip_extended_path_reverses_the_drive_letter_prefix():
    assert runtime_paths.strip_extended_path("\\\\?\\C:\\a\\b.pdf") == "C:\\a\\b.pdf"


def test_strip_extended_path_reverses_the_unc_prefix():
    assert runtime_paths.strip_extended_path("\\\\?\\UNC\\server\\share\\b.pdf") == "\\\\server\\share\\b.pdf"


def test_strip_extended_path_is_a_no_op_for_an_unprefixed_path():
    assert runtime_paths.strip_extended_path("C:\\a\\b.pdf") == "C:\\a\\b.pdf"
