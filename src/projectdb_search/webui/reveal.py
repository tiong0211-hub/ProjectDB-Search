"""Opens the host machine's file manager at a document's location.

SAFE ONLY because the web server always binds to 127.0.0.1 (see
cli/main.py's `serve` command) -- this is a single-user, localhost-only
tool, so "the server's machine" and "the person clicking the button" are
always the same machine. Never call this from a server that could be
reached by anyone other than its own operator.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Explorer's own GUI remains unreliable selecting a file whose full path is
# near/over the historical MAX_PATH (260-char) limit, even with Windows'
# OS-level long-path support enabled -- a well-known limitation of
# Explorer itself (its selection/navigation code, not just file I/O), not
# something this app can fully work around. Below this length, opening the
# parent folder is far more likely to actually succeed than trying (and
# silently failing) to select the exact file.
WINDOWS_PATH_WARN_LENGTH = 250


def reveal_in_file_manager(path: Path) -> tuple[bool, str | None]:
    """Best-effort: opens the OS file manager with `path` selected/shown.

    Returns (ok, warning). `warning="path_too_long"` means we fell back to
    opening the parent folder instead of selecting the exact file. Returns
    (False, None) (never raises) when unsupported on this platform or the
    launch fails -- callers treat that as "not available here", not an
    error worth surfacing as a 500.
    """
    try:
        if sys.platform == "win32":
            if len(str(path)) >= WINDOWS_PATH_WARN_LENGTH:
                subprocess.Popen(f'explorer "{path.parent}"')
                return True, "path_too_long"
            # Explorer's /select switch is notoriously picky about
            # quoting; passing one pre-quoted string (not a list) is what
            # reliably works on Windows, including paths with spaces --
            # subprocess passes a string argument straight through to
            # CreateProcess there rather than re-quoting it.
            subprocess.Popen(f'explorer /select,"{path}"')
            return True, None
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return True, None
        return False, None  # no single standard "reveal" command across Linux file managers
    except OSError:
        return False, None
