"""
Cross-platform portability checks. Run from backend/:  python test_platform.py

CoreClip is supported on macOS and Windows. Everything here is written to run
identically on both and to assert the invariants that keep it that way, so a
regression shows up as a failing check rather than as a broken job on the OS
nobody happened to be developing on that week.

Covers:
  1. platform detection and temp-dir selection agree with the host
  2. INTERMEDIATE_DIR lands on the platform temp volume and is creatable
  3. ffmpeg / ffprobe resolve to real, absolute, libass-capable binaries
  4. filtergraph escaping survives Windows-shaped paths
  5. link_or_copy gives a readable duplicate whose deletion spares the source
  6. logs round-trip non-ASCII (Windows defaults these handlers to cp1252)
  7. no source file hardcodes a POSIX-only /tmp path
  8. this platform's launcher exists, and the Windows one pins Celery to solo
"""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from utils.platform_compat import (
    EXE_SUFFIX,
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    default_intermediate_dir,
    exe,
    ffmpeg_filter_path,
    find_executables,
    link_or_copy,
    no_window_kwargs,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_platform_detection():
    print("\n[1] platform detection")
    flags = [IS_WINDOWS, IS_MACOS, IS_LINUX]
    check("exactly one OS flag is set", sum(bool(f) for f in flags) == 1, str(flags))
    check("exe suffix matches OS", EXE_SUFFIX == (".exe" if IS_WINDOWS else ""))
    check("exe() applies the suffix", exe("ffmpeg") == "ffmpeg" + EXE_SUFFIX)

    # CREATE_NO_WINDOW is Windows-only; on POSIX the kwargs must stay empty or
    # every subprocess call in the codebase would raise TypeError.
    kwargs = no_window_kwargs()
    if IS_WINDOWS:
        check("no_window_kwargs sets creationflags", "creationflags" in kwargs)
    else:
        check("no_window_kwargs is empty off Windows", kwargs == {}, str(kwargs))


def test_intermediate_dir():
    print("\n[2] intermediate directory")
    default = default_intermediate_dir()
    check("default is absolute", os.path.isabs(default), default)
    check(
        "default sits on the platform temp volume",
        os.path.dirname(default.rstrip(os.sep)) == tempfile.gettempdir().rstrip(os.sep),
        f"{default} vs {tempfile.gettempdir()}",
    )
    # The literal "/tmp" would resolve to C:\tmp on Windows.
    if IS_WINDOWS:
        check("default is not a POSIX /tmp path", not default.startswith("/"), default)

    os.makedirs(config.INTERMEDIATE_DIR, exist_ok=True)
    check("configured INTERMEDIATE_DIR is creatable", os.path.isdir(config.INTERMEDIATE_DIR))


def test_ffmpeg_discovery():
    print("\n[3] ffmpeg discovery")
    from services import ffmpeg_client

    ffmpeg = ffmpeg_client._get_ffmpeg_binary()
    ffprobe = ffmpeg_client._get_ffprobe_binary()

    check("ffmpeg was found", os.path.isfile(ffmpeg), ffmpeg)
    check("ffprobe was found", os.path.isfile(ffprobe), ffprobe)
    # An absolute path is what makes the desktop app work: a GUI-launched
    # process inherits a PATH with neither Homebrew nor most Windows installs.
    check("ffmpeg path is absolute", os.path.isabs(ffmpeg), ffmpeg)
    check("ffprobe path is absolute", os.path.isabs(ffprobe), ffprobe)
    check("ffmpeg has the ass filter (captions)", ffmpeg_client._has_ass_filter(ffmpeg), ffmpeg)

    if IS_WINDOWS:
        check("resolved binaries carry .exe", ffmpeg.lower().endswith(".exe"), ffmpeg)

    encoder = ffmpeg_client.get_selected_encoder()
    check("an encoder was selected", bool(encoder), encoder)
    if IS_MACOS:
        check(
            "macOS never selects a non-Apple hardware encoder",
            encoder in ("h264_videotoolbox", "hevc_videotoolbox", "libx264"),
            encoder,
        )
    else:
        check(
            "videotoolbox is not selected off macOS",
            "videotoolbox" not in encoder,
            encoder,
        )

    check("find_executables dedupes", len(find_executables("ffmpeg")) == len(set(find_executables("ffmpeg"))))


def test_filter_escaping():
    print("\n[4] filtergraph path escaping")
    # A Windows path is made of exactly the two characters a filtergraph treats
    # as syntax: \ escapes, : separates options.
    win = ffmpeg_filter_path(r"C:\Users\me\out\clip_1\captions.ass")
    check("windows drive colon is escaped", win == "C\\:/Users/me/out/clip_1/captions.ass", win)
    check("no raw backslashes survive", "\\\\" not in win and "\\U" not in win, win)

    posix = ffmpeg_filter_path("/Users/me/out/clip_1/captions.ass")
    check("plain posix path is untouched", posix == "/Users/me/out/clip_1/captions.ass", posix)

    tricky = ffmpeg_filter_path("/tmp/a,b/c'd[e]/x.ass")
    for ch in (",", "'", "[", "]"):
        check(f"{ch!r} is escaped", f"\\{ch}" in tricky, tricky)

    # The real call site must go through the escaper, not build paths by hand.
    from services.ffmpeg_client import build_final_burn_args

    args = build_final_burn_args("in.mp4", r"C:\o\captions.ass", "out.mp4", r"C:\fonts")
    vf = args[args.index("-vf") + 1]
    check("burn filter escapes both paths", vf.count("C\\:") == 2, vf)


def test_link_or_copy():
    print("\n[5] link_or_copy")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "cached.mp4")
        dst = os.path.join(tmp, "source.mp4")
        payload = b"video-bytes" * 100
        with open(src, "wb") as f:
            f.write(payload)

        strategy = link_or_copy(src, dst)
        check("returns a known strategy", strategy in ("symlink", "hardlink", "copy"), strategy)
        check("destination exists", os.path.exists(dst))
        with open(dst, "rb") as f:
            check("destination reads back identical bytes", f.read() == payload)

        # Overwriting an existing destination must not raise — the pipeline
        # re-links source.mp4 on every retry of a cached job.
        again = link_or_copy(src, dst)
        check("relinking over an existing file works", os.path.exists(dst), again)

        # cleanup_source_files deletes the destination; the cache must survive.
        os.remove(dst)
        check("removing the link leaves the cache intact", os.path.exists(src))


def test_log_encoding():
    print("\n[6] log encoding and rotation")
    import logging
    from utils.logger import JsonFormatter, _LOG_ENCODING, _rotating_handler

    check("log encoding is utf-8", _LOG_ENCODING == "utf-8", _LOG_ENCODING)

    def record(message):
        return logging.LogRecord("clipper", logging.INFO, __file__, 1, message, None, None)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "probe.jsonl")
        handler = _rotating_handler(path, JsonFormatter())
        # Exactly the shapes the pipeline logs: status glyphs and unicode titles.
        message = "▶ Posting clip — Café Münster 日本語 ✓"
        handler.emit(record(message))
        handler.close()

        with io.open(path, encoding="utf-8") as f:
            written = json.loads(f.read().strip())
        check("non-ascii log message round-trips", written["message"] == message, written["message"])

    # A rollover blocked by another process (the norm on Windows, where the
    # worker's process pool holds the same log open) must not lose the line.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rotate.jsonl")
        handler = _rotating_handler(path, JsonFormatter(), max_bytes=1, backup_count=1)
        handler.rotate = lambda source, dest: (_ for _ in ()).throw(OSError("file in use"))
        try:
            handler.emit(record("first"))
            handler.emit(record("after blocked rollover"))
            survived = True
        except Exception as e:
            survived = False
            detail = repr(e)
        finally:
            handler.close()
        check("a blocked rollover does not raise", survived, locals().get("detail", ""))
        with io.open(path, encoding="utf-8") as f:
            body = f.read()
        check("logging continues after a blocked rollover", "after blocked rollover" in body, body[:120])


def test_no_posix_only_paths():
    print("\n[7] no hardcoded posix paths")
    offenders = []
    for dirpath, dirnames, filenames in os.walk(os.path.dirname(os.path.abspath(__file__))):
        dirnames[:] = [
            d for d in dirnames
            if d not in {"venv", ".venv", "__pycache__", "node_modules", "output", ".models"}
        ]
        for name in filenames:
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            full = os.path.join(dirpath, name)
            with io.open(full, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if '"/tmp/' in line or "'/tmp/" in line:
                        offenders.append(f"{os.path.relpath(full, ROOT)}:{lineno}")
    check("no source file hardcodes /tmp/", not offenders, ", ".join(offenders))


def test_launchers():
    print("\n[8] launchers")
    sh = os.path.join(ROOT, "start.sh")
    ps1 = os.path.join(ROOT, "start.ps1")
    cmd = os.path.join(ROOT, "start.cmd")

    check("start.sh exists (macOS/Linux)", os.path.isfile(sh))
    check("start.ps1 exists (Windows)", os.path.isfile(ps1))
    check("start.cmd exists (Windows shim)", os.path.isfile(cmd))

    with io.open(ps1, encoding="utf-8") as f:
        ps1_text = f.read()
    # Celery's prefork pool needs fork(), which Windows does not have.
    check("windows launcher pins Celery to the solo pool", "--pool=solo" in ps1_text)
    check("windows launcher forces UTF-8 for Python children", "PYTHONUTF8" in ps1_text)
    # PowerShell 5.1 reads a BOM-less .ps1 as cp1252, mangling multi-byte UTF-8.
    check("windows launcher is pure ASCII", all(ord(c) < 128 for c in ps1_text))

    with io.open(sh, encoding="utf-8") as f:
        sh_text = f.read()
    check("macOS launcher repairs PATH for GUI launch", "/opt/homebrew/bin" in sh_text)
    check("macOS launcher forces UTF-8 for Python children", "PYTHONUTF8" in sh_text)


if __name__ == "__main__":
    print(f"CoreClip portability checks - {sys.platform} / python {sys.version.split()[0]}")
    test_platform_detection()
    test_intermediate_dir()
    test_ffmpeg_discovery()
    test_filter_escaping()
    test_link_or_copy()
    test_log_encoding()
    test_no_posix_only_paths()
    test_launchers()
    print(f"\n{'=' * 48}\n  {PASS} passed, {FAIL} failed\n{'=' * 48}")
    sys.exit(1 if FAIL else 0)
