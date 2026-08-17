"""Cross-platform primitives shared by the backend.

CoreClip runs on macOS and Windows. The differences that actually bite are few
but sharp, and they all live here so no caller has to special-case an OS:

  * executables carry a ``.exe`` suffix on Windows, and PATH alone is not a
    reliable way to find them (a GUI-launched app inherits a minimal PATH on
    both platforms);
  * ``os.symlink`` needs Developer Mode or elevation on Windows;
  * FFmpeg filtergraphs treat ``\\`` as an escape character and ``:`` as an
    option separator, which is exactly what a Windows path is made of;
  * spawning a child process on Windows flashes a console window unless it is
    explicitly suppressed — very visible under the Electron shell.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Optional

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Appended to every bare executable name we look up ourselves. shutil.which()
# already applies PATHEXT, so this is only for explicit candidate paths.
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""


def exe(stem: str) -> str:
    """``exe("ffmpeg")`` → ``ffmpeg.exe`` on Windows, ``ffmpeg`` elsewhere."""
    return stem + EXE_SUFFIX


# ── Subprocess ───────────────────────────────────────────────────────────


def no_window_kwargs() -> dict:
    """Extra ``subprocess`` kwargs that keep child processes windowless.

    Without CREATE_NO_WINDOW every ffmpeg/ffprobe invocation pops a console
    window on Windows. Harmless from a terminal, unbearable from the desktop
    app, which shells out once per clip.
    """
    if not IS_WINDOWS:
        return {}
    # CREATE_NO_WINDOW exists on Python 3.7+/Windows; the literal is the
    # documented value and keeps type checkers happy on POSIX.
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


# ── Filesystem locations ─────────────────────────────────────────────────


def default_intermediate_dir() -> str:
    """Scratch space for per-clip intermediates, on the platform's temp volume.

    Windows has no ``/tmp``; ``gettempdir()`` resolves to ``%TEMP%`` there and
    ``/tmp`` (or ``$TMPDIR``) on macOS.
    """
    return os.path.join(tempfile.gettempdir(), "coreclip_intermediates")


def link_or_copy(src: str, dst: str) -> str:
    """Make ``dst`` resolve to ``src``'s bytes as cheaply as the OS allows.

    Callers only ever read through ``dst``, so a symlink, a hard link and a
    plain copy are all correct — they differ only in cost. POSIX gets a
    symlink. On Windows symlink creation requires Developer Mode or an elevated
    process, so we try a hard link first (no privileges needed, same volume
    only) and degrade to a copy. Returns which strategy was used, for logging.
    """
    src = os.path.abspath(src)
    if os.path.lexists(dst):
        os.remove(dst)

    if not IS_WINDOWS:
        os.symlink(src, dst)
        return "symlink"

    for strategy, fn in (("hardlink", os.link), ("symlink", os.symlink)):
        try:
            fn(src, dst)
            return strategy
        except OSError:
            continue

    shutil.copy2(src, dst)
    return "copy"


def replace_dir(src: str, dst: str, attempts: int = 5, delay: float = 0.4) -> bool:
    """Move directory ``src`` onto ``dst``, replacing whatever is there.

    Windows refuses to rename a directory while any process still holds a
    handle inside it — a browser profile that Chrome has only just released is
    the common case — so retry briefly before giving up. Returns success.
    """
    for attempt in range(attempts):
        try:
            if os.path.exists(dst):
                shutil.rmtree(dst, ignore_errors=True)
            os.rename(src, dst)
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(delay)
    return False


# ── Executable discovery ─────────────────────────────────────────────────


def find_executables(stem: str, extra_candidates: Iterable[str] = ()) -> list[str]:
    """Every place this executable can be found, in order of preference.

    PATH comes first, then ``extra_candidates`` (absolute paths, which may
    contain glob wildcards), then the standard package-manager locations for
    the current OS. Duplicates are dropped and the order is stable, so a caller
    that needs a *specific* build (e.g. an ffmpeg compiled with libass) can walk
    the list and probe each one.

    Searching beyond PATH matters on both platforms: a GUI-launched app gets a
    minimal environment — no Homebrew on macOS, no user PATH entries on Windows
    — so relying on a bare ``"ffmpeg"`` works from a terminal and fails from
    the desktop app.
    """
    results: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        resolved = os.path.abspath(path)
        key = resolved.lower() if IS_WINDOWS else resolved
        if key not in seen:
            seen.add(key)
            results.append(resolved)

    on_path = shutil.which(stem)
    if on_path:
        add(on_path)

    for candidate in list(extra_candidates) + _standard_bin_dirs(stem):
        # Newest-first for versioned winget package dirs.
        for path in sorted(glob.glob(candidate), reverse=True):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                add(path)

    return results


def find_executable(stem: str, extra_candidates: Iterable[str] = ()) -> Optional[str]:
    """The preferred location of an executable, or None. See find_executables."""
    found = find_executables(stem, extra_candidates)
    return found[0] if found else None


def _standard_bin_dirs(stem: str) -> list[str]:
    """Per-OS install locations for a CLI tool, as glob patterns."""
    name = exe(stem)
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        user_profile = os.environ.get("USERPROFILE", "")
        return [
            # winget puts shims in Links/ and the real binary under Packages/.
            os.path.join(local, "Microsoft", "WinGet", "Links", name),
            os.path.join(local, "Microsoft", "WinGet", "Packages", "*", "*", "bin", name),
            os.path.join(local, "Microsoft", "WinGet", "Packages", "*", "*", name),
            # Chocolatey
            os.path.join(program_data, "chocolatey", "bin", name),
            # Scoop (per-user and global)
            os.path.join(user_profile, "scoop", "shims", name),
            os.path.join(program_data, "scoop", "shims", name),
            # Manual unzip-to-C:\ffmpeg, the most common non-package install.
            os.path.join("C:\\", stem, "bin", name),
            os.path.join(program_files, stem, "bin", name),
            os.path.join(program_files, stem, name),
            os.path.join(program_files_x86, stem, "bin", name),
        ]
    if IS_MACOS:
        return [
            # Homebrew keg-only formulae come first: ffmpeg-full is the build
            # that ships libass, which caption burning requires.
            f"/opt/homebrew/opt/{stem}-full/bin/{name}",
            f"/usr/local/opt/{stem}-full/bin/{name}",
            f"/opt/homebrew/bin/{name}",          # Apple Silicon Homebrew
            f"/usr/local/bin/{name}",             # Intel Homebrew
            f"/opt/local/bin/{name}",             # MacPorts
            f"/usr/bin/{name}",
        ]
    return [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/snap/bin/{name}"]


# ── FFmpeg filtergraph escaping ──────────────────────────────────────────

# Characters that terminate or nest a filtergraph description, and so must be
# escaped when they appear inside a filter *option value*.
_FILTER_METACHARS = (":", ",", ";", "'", "[", "]", "=")


def ffmpeg_filter_path(path: str) -> str:
    """Escape a filesystem path for use as an FFmpeg filter option value.

    A filtergraph is parsed before any filter sees its options, so the path is
    read by that parser first: ``\\`` escapes, ``:`` separates options, and
    ``,`` ``;`` ``'`` ``[`` ``]`` delimit or nest the graph. A Windows path
    (``C:\\Users\\me\\captions.ass``) contains the two worst offenders.

    FFmpeg accepts forward slashes on Windows, so we normalise separators —
    which disposes of the backslash-as-escape problem entirely — and then
    escape the drive-letter colon and any remaining metacharacter. The result
    is the documented Windows form: ``C\\:/Users/me/captions.ass``.
    """
    escaped = path.replace("\\", "/")
    for char in _FILTER_METACHARS:
        escaped = escaped.replace(char, "\\" + char)
    return escaped
