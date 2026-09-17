from __future__ import annotations

from pathlib import Path

from projectdb_search.webui import reveal


def test_windows_uses_explorer_select_with_a_single_quoted_string(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda arg: calls.append(arg))

    result = reveal.reveal_in_file_manager(Path(r"C:\Shared\Project Docs\HX-203.pdf"))

    assert result is True
    assert len(calls) == 1
    # A single pre-quoted string, not an argv list -- Explorer's /select
    # parsing only works reliably this way, and it must survive spaces.
    assert calls[0] == 'explorer /select,"C:\\Shared\\Project Docs\\HX-203.pdf"'


def test_macos_uses_open_dash_r(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(reveal.subprocess, "Popen", lambda argv: calls.append(argv))

    result = reveal.reveal_in_file_manager(Path("/Users/eng/docs/HX-203.pdf"))

    assert result is True
    assert calls == [["open", "-R", "/Users/eng/docs/HX-203.pdf"]]


def test_unsupported_platform_returns_false_without_raising(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "linux")
    result = reveal.reveal_in_file_manager(Path("/mnt/docs/HX-203.pdf"))
    assert result is False


def test_launch_failure_is_swallowed_and_returns_false(monkeypatch):
    monkeypatch.setattr(reveal.sys, "platform", "win32")

    def _boom(arg):
        raise OSError("explorer.exe not found")

    monkeypatch.setattr(reveal.subprocess, "Popen", _boom)

    result = reveal.reveal_in_file_manager(Path(r"C:\docs\HX-203.pdf"))
    assert result is False
