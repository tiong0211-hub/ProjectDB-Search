from __future__ import annotations

from pathlib import Path

from projectdb_search.webui import reveal


def test_windows_uses_explorer_select_with_a_single_quoted_string(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda arg: calls.append(arg))

    result = reveal.reveal_in_file_manager(Path(r"C:\Shared\Project Docs\HX-203.pdf"))

    assert result == (True, None)
    assert len(calls) == 1
    # A single pre-quoted string, not an argv list -- Explorer's /select
    # parsing only works reliably this way, and it must survive spaces.
    assert calls[0] == 'explorer /select,"C:\\Shared\\Project Docs\\HX-203.pdf"'


def test_macos_uses_open_dash_r(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda argv: calls.append(argv))

    result = reveal.reveal_in_file_manager(Path("/Users/eng/docs/HX-203.pdf"))

    assert result == (True, None)
    assert calls == [["open", "-R", "/Users/eng/docs/HX-203.pdf"]]


def test_unsupported_platform_returns_false_without_raising(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "linux")
    result = reveal.reveal_in_file_manager(Path("/mnt/docs/HX-203.pdf"))
    assert result == (False, None)


def test_launch_failure_is_swallowed_and_returns_false(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "win32")

    def _boom(arg):
        raise OSError("explorer.exe not found")

    monkeypatch.setattr(reveal.subprocess, "Popen", _boom)

    result = reveal.reveal_in_file_manager(Path(r"C:\docs\HX-203.pdf"))
    assert result == (False, None)


def test_windows_falls_back_to_the_parent_folder_when_the_path_is_too_long(monkeypatch):
    # Explorer's own GUI is unreliable selecting a file at/near the
    # historical MAX_PATH limit even with Windows' long-path support
    # enabled -- opening just the parent folder is far more likely to work.
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda arg: calls.append(arg))

    long_path = Path("C:\\" + "\\".join(["a_very_long_folder_name_segment"] * 8) + "\\file.pdf")
    assert len(str(long_path)) >= reveal.WINDOWS_PATH_WARN_LENGTH

    result = reveal.reveal_in_file_manager(long_path)

    assert result == (True, "path_too_long")
    assert len(calls) == 1
    assert calls[0] == f'explorer "{long_path.parent}"'
    assert "/select," not in calls[0]


def test_windows_short_path_does_not_trigger_the_long_path_fallback(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda arg: calls.append(arg))

    short_path = Path(r"C:\Shared\Docs\HX-203.pdf")
    assert len(str(short_path)) < reveal.WINDOWS_PATH_WARN_LENGTH

    result = reveal.reveal_in_file_manager(short_path)

    assert result == (True, None)
    assert "/select," in calls[0]
