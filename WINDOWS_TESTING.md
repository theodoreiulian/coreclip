# CoreClip Windows Verification Punchlist

Actionable checks to confirm CoreClip runs correctly on Windows. Written for a
coding agent working on a Windows machine — **read `CLAUDE.md` first** for the
architecture and the cross-platform rules this punchlist verifies.

**Why this exists:** the cross-platform work was written and tested entirely on
macOS, where 41 portability checks, the FFmpeg smoke test and the pytest suite
pass. The machine that wrote the Windows support had no Windows and no
PowerShell on it, so **`start.ps1` has never been executed — not once.**
Python-side behavior was verified by faking the OS (`IS_WINDOWS`, `ntpath`) and
asserting the resulting path shapes, which proves the *logic* but not the
*runtime*. The launcher was checked statically only: ASCII-only encoding,
balanced blocks, hand-derived `cmd.exe` quoting. Your job is to turn "should
work" into "does work".

**Legend:**
- **P0** — Blocks everything else / do first
- **P1** — Must pass before calling Windows supported
- **P2** — Should pass / fix before release
- **P3** — Optional, environment-dependent

**How to use this:** work top to bottom — each section assumes the previous
passed. Run the command, compare against **Pass**, tick the box. For every box
you cannot tick, record the actual output and see **Reporting back**.

---

## 0. Setup

- [ ] **P0 — Install prerequisites**
  - Run (skip any already present):
    ```powershell
    winget install Gyan.FFmpeg
    winget install OpenJS.NodeJS.LTS
    winget install Python.Python.3.12
    winget install Memurai.MemuraiDeveloper   # Redis-compatible Windows service
    ```
  - Redis has no official Windows build. Memurai is a native service; a
    `redis-server.exe` from Chocolatey/Scoop or
    `docker run -d -p 6379:6379 redis:7-alpine` work equally well.

- [ ] **P0 — Build the environment**
  - Run from the repo root: `.\start.cmd`
  - First run creates `backend\venv`, pip-installs everything and downloads
    Playwright's Chromium. Several minutes.
  - A failure here is a section 2 finding — record it and read section 2 for the
    diagnostics before retrying.
  - Afterwards, always invoke Python as `.\venv\Scripts\python.exe` from
    `backend\`, never whatever bare `python` resolves to.

- [ ] **P0 — Know which `logs` directory is which**
  - Confusing these wastes time:

    | Path | Written by |
    |---|---|
    | `logs\backend.log`, `celery-worker.log`, `celery-beat.log`, `frontend.log` | The **launcher**, redirecting each service's stdout+stderr |
    | `backend\logs\jobs\<job_id>.jsonl`, `errors.jsonl`, `api.jsonl` | The **application** logger |
    | `backend\output\<job_id>\clip_N\` | Pipeline artifacts |

  - The latter two sit under `backend\` because the launcher starts every service
    with `backend\` as its working directory, and `config.py` resolves `./output`
    and `./logs` relative to that.

---

## 1. Static & Offline Checks

No running services needed. Run from `backend\` — several tests resolve paths
relative to the working directory.

- [ ] **P0 — Portability invariants pass**
  - Where: `backend/test_platform.py`
  - Run: `cd backend; .\venv\Scripts\python.exe test_platform.py`
  - Why: the regression net for every cross-platform rule in `CLAUDE.md` —
    binary discovery, filtergraph escaping, the symlink fallback, log encoding,
    launcher integrity. Fastest signal that machine and code agree.
  - Pass: `0 failed`. Expect **43** passed on Windows vs 41 on macOS, since four
    assertions only activate here and one macOS-only assertion drops out. *That
    count was derived by reading the branches, not observed — treat `0 failed`
    as the real criterion and report the number you actually see.*
  - Windows-only checks worth knowing by name:

    | Check | A failure means |
    |---|---|
    | `no_window_kwargs sets creationflags` | Every ffmpeg call flashes a console window |
    | `resolved binaries carry .exe` | `find_executables` isn't applying `EXE_SUFFIX` |
    | `windows drive colon is escaped` | Caption burning fails on every clip |
    | `default is not a POSIX /tmp path` | Intermediates are landing in `C:\tmp` |
    | `windows launcher is pure ASCII` | PowerShell 5.1 will mis-decode `start.ps1` |

- [ ] **P0 — PowerShell parses `start.ps1`**
  - Where: `start.ps1`
  - Run:
    ```powershell
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path .\start.ps1), [ref]$null, [ref]$errors) | Out-Null
    $errors
    ```
  - Why: this file has never been parsed by a real PowerShell. Catches syntax
    errors in seconds instead of halfway through a launch.
  - Pass: `$errors` is empty.
  - Optional: `Invoke-ScriptAnalyzer .\start.ps1` if PSScriptAnalyzer is
    installed — warnings fine, errors not.

- [ ] **P1 — FFmpeg resolves and encodes**
  - Where: `backend/test_ffmpeg.py`, `backend/services/ffmpeg_client.py`
  - Run: `.\venv\Scripts\python.exe test_ffmpeg.py`
  - Why: confirms CoreClip finds a *libass-capable* `ffmpeg.exe` — including a
    winget install that is not on `PATH` — and that the encoder picked for this
    GPU emits correct rate-control flags. Without libass, captions fail silently
    later in the pipeline.
  - Pass: absolute `.exe` paths for ffmpeg **and** ffprobe; an encoder is
    selected (`h264_nvenc` / `h264_qsv` / `h264_amf` / `libx264` depending on
    GPU — **never** `h264_videotoolbox`); ends with `Success! test.wav created.`

- [ ] **P1 — Existing pytest suite shows no new failures**
  - Where: `backend/tests/`
  - Run:
    ```powershell
    .\venv\Scripts\python.exe -m pip install pytest
    .\venv\Scripts\python.exe -m pytest tests\ -q
    ```
  - Why: covers `config`, `database`, `file_manager`, `logger` — the modules the
    cross-platform work touched most.
  - Pass: `25 passed, 3 failed`. **These three are expected and pre-existing on
    every platform — do not fix them:**
    - `test_config.py::test_gemini_api_key_validation_raises_when_missing`
    - `test_config.py::test_gemini_api_key_validation_passes_when_set`
    - `test_file_manager.py::test_cleanup_source_files_deletes_source_and_audio`
  - They assert behavior earlier work deliberately changed (the API key moved to
    `user_settings.json`; `cleanup_source_files` no longer deletes `audio.wav`).
    **A fourth failure is a real regression** — that is the signal.

---

## 2. Launcher - highest risk, zero prior runtime evidence

- [ ] **P0 — Cold start reaches `CORECLIP_READY`**
  - Where: `start.ps1`, `start.cmd`
  - Run from the repo root, in a terminal you can watch: `.\start.cmd`
  - Why: exercises prerequisite discovery, venv handling, Redis detection, and
    four `Start-Process` calls whose `cmd.exe` quoting was derived by hand and
    never executed.
  - Pass: these appear in order with no red `[x]` line:
    ```
    [ok] ffmpeg found (...)
    [ok] node vXX.X.X found
    [ok] Python venv ready
    [ok] Frontend dependencies ready
    [ok] .env loaded
    [ok] Redis already running        (or: [ok] Redis started)
    [ok] Backend is live
    CORECLIP_READY
    ```

- [ ] **P0 — Service logs are non-empty — the single most important check**
  - Where: `start.ps1` → `Start-Service-Process`
  - Run, in a second terminal: `Get-ChildItem logs\*.log | Select-Object Name, Length`
  - Why: the direct test of the hand-derived quoting. The launcher builds
    `cmd.exe /c ""C:\...\python.exe" -m uvicorn ... > "C:\...\backend.log" 2>&1"`,
    relying on `cmd.exe` stripping exactly one outer quote pair. If that
    derivation is wrong the services either never start or their output goes
    nowhere — an empty log file is how you find out.
  - Pass: all four of `backend.log`, `celery-worker.log`, `celery-beat.log`,
    `frontend.log` exist with **non-zero length**.
  - If it fails, isolate the quoting from everything else:
    ```powershell
    $py = (Resolve-Path .\backend\venv\Scripts\python.exe).Path
    $log = (Resolve-Path .\logs).Path + "\probe.log"
    $inner = '"{0}" -c "print(1)" > "{1}" 2>&1' -f $py, $log
    Start-Process -FilePath $env:ComSpec -ArgumentList ('/c "' + $inner + '"') -Wait -NoNewWindow
    Get-Content $log
    ```
    Report whether that prints `1`.

- [ ] **P0 — Celery worker started with the solo pool**
  - Where: `logs\celery-worker.log`
  - Run: `Select-String -Path logs\celery-worker.log -Pattern "celery@|ready|pool" | Select-Object -First 10`
  - Why: Celery's default prefork pool needs `fork()`, which Windows lacks. The
    launcher passes `--pool=solo`. If that were ever dropped the worker would die
    instantly and jobs would queue forever with no visible error in the UI.
  - Pass: banner shows the worker connected to Redis, listing the `pipeline`,
    `posting`, `celery` queues. No `PermissionError`, no
    `AttributeError: module 'os' has no attribute 'fork'`.

- [ ] **P1 — All three ports serve**
  - Run:
    ```powershell
    Invoke-WebRequest http://localhost:8000/api/health -UseBasicParsing | Select-Object -Expand Content
    Invoke-WebRequest http://localhost:5173 -UseBasicParsing | Select-Object -Expand StatusCode
    Get-NetTCPConnection -LocalPort 6379,8000,5173 -State Listen | Select-Object LocalPort, OwningProcess
    ```
  - Pass: health returns `"status":"ok"` with a plausible `disk_space_gb`; the
    frontend returns `200`; all three ports listening.

- [ ] **P1 — The `--` passthrough reaches Vite**
  - Where: `start.ps1` frontend `Start-Service-Process` call
  - Run: `Select-String -Path logs\frontend.log -Pattern "Local:|Network:"`
  - Why: the frontend launches as `npm.cmd run dev -- --host 0.0.0.0` through
    `cmd.exe`. If the `--` separator is eaten, Vite binds localhost only.
  - Pass: **both** a `Local:` and a `Network:` line appear.

- [ ] **P1 — Ctrl+C leaves no orphans**
  - Where: `start.ps1` → `Stop-Tree` / `Invoke-Cleanup`
  - Run: press **Ctrl+C**, wait for `[ok] All services stopped.`, then:
    ```powershell
    Get-Process python,node,redis-server,memurai -ErrorAction SilentlyContinue |
        Select-Object Id, ProcessName, Path
    Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue
    ```
  - Why: POSIX signals a process group; Windows has none, so teardown relies on
    `taskkill /T` walking the tree from each `cmd.exe` wrapper down to the
    python/node process beneath it. A wrong tree walk leaves orphans holding
    port 8000 and every later run fails confusingly.
  - Pass: no CoreClip python/node processes survive (check `Path` — a python
    from another project is fine); ports 8000/5173 free. A surviving Memurai
    **service** is expected and correct — the launcher only shuts down a Redis
    it started itself.

---

## 3. Pipeline End-to-End

Restart the stack and submit one short YouTube video (2-5 min) at
`http://localhost:5173`. This is the only test that drives the Windows code
paths under real load; each stage probes a different fix.

- [ ] **P0 — A job runs to `ready_for_review`**
  - Why, stage by stage:

    | Stage | What it proves |
    |---|---|
    | `ingesting` | `link_or_copy` replaced `os.symlink` (needs elevation on Windows); `os.replace` tolerates an existing cache entry |
    | `ingesting` | Whisper runs; UTF-8 video titles reach the logs without `UnicodeEncodeError` |
    | `processing` | `ProcessPoolExecutor` works under Celery's solo pool with the `spawn` start method |
    | `processing` | The reframer's `sendcmd=` temp path survives the filtergraph parser |
    | `captioning` | The `ass=` burn with a `C:\` path — the highest-risk escaping fix |

  - Pass: job reaches `ready_for_review` with no `failed_stage`.

- [ ] **P0 — Captions are actually burned in**
  - Where: `backend\output\<job_id>\clip_0\captioned.mp4`
  - Why: the filtergraph escaping fix is the one most likely to fail silently —
    the job can "succeed" while producing uncaptioned video.
  - Pass: the file plays at 1080x1920 **with visible burned-in captions**.
  - If absent, grep `backend\logs\jobs\<job_id>.jsonl` for an `ass=` filter error.

- [ ] **P1 — No console windows flash during processing**
  - Where: `**no_window_kwargs()` at every `subprocess` call site
  - Why: without `CREATE_NO_WINDOW`, each ffmpeg invocation pops a console —
    once per clip, very visible under the desktop shell.
  - Pass: watch the screen through the `processing` stage; no flicker. A flicker
    per clip means some call site is missing the kwargs — find which.

- [ ] **P1 — `link_or_copy` chose a hardlink**
  - Run:
    ```powershell
    Select-String -Path backend\logs\jobs\<job_id>.jsonl -Pattern "linked \("
    fsutil hardlink list backend\output\<job_id>\source.mp4
    ```
  - Why: Windows symlinks need elevation, so the helper degrades hardlink →
    copy. `copy` works but doubles disk use, and signals the cache and output
    are on different volumes.
  - Pass: log says `linked (hardlink)`. `linked (copy)` is acceptable — note it.

- [ ] **P1 — Non-ASCII survives the logs**
  - Run: `Get-Content backend\logs\jobs\<job_id>.jsonl -Encoding UTF8 | Select-String "Downloaded"`
  - Why: Windows defaults log handlers to cp1252, which cannot represent the
    video titles, hashtags and status glyphs the pipeline logs.
  - Pass: the title reads back intact, no mojibake, and no `UnicodeEncodeError`
    anywhere in `backend\logs\errors.jsonl`.

- [ ] **P2 — Re-submitting a cached URL relinks cleanly**
  - Why: the second run takes the cache branch, where `link_or_copy` must
    overwrite an existing `source.mp4` rather than raise.
  - Pass: the job runs through again with no `FileExistsError`.

---

## 4. Electron Desktop Shell

- [ ] **P2 — The shell launches, attaches and tears down**
  - Where: `electron/main.js` → `launcherCommand()`, `stopStack()`
  - Run: `npm install` then `npm start`
  - Why: the shell spawns `powershell.exe -File start.ps1` instead of
    `bash start.sh`, and tears it down with `taskkill /PID <pid> /T /F` instead
    of a process-group signal. Neither branch has ever run.
  - Pass, all five:
    1. Loading window appears and streams launcher status lines.
    2. It navigates to the app once the frontend is ready.
    3. **No PowerShell console window appears** (`windowsHide: true`).
    4. Quitting tears everything down — re-run the orphan check from section 2.
    5. With the stack already up, `npm start` attaches instead of starting a
       second copy (no port conflicts in `logs\backend.log`).

---

## 5. TikTok Automation

Only if Google Chrome is installed and a throwaway TikTok account is available.

- [ ] **P3 — Chrome is discovered and the UA matches the host**
  - Where: `backend/services/tiktok_browser.py`
  - Run: `cd backend; .\venv\Scripts\python.exe test_tiktok.py`
  - Why: Chrome paths now come from `%ProgramFiles%` / `%LOCALAPPDATA%` rather
    than hardcoded `C:\` literals, so a **per-user** install (the default
    without admin rights) is found. The UA also switched to a Windows platform
    token, because Chrome sends `Sec-CH-UA-Platform` client hints that a
    "Macintosh" UA would contradict — a self-inflicted bot signal.
  - Pass: `availability_label()` returns `ready`; the UA contains
    `Windows NT 10.0`, not `Macintosh`.

- [ ] **P3 — A real connect + post adopts the profile directory**
  - Why: exercises `replace_dir`, which retries past Windows' refusal to rename
    a directory whose handles Chrome has only just released.
  - Pass: on success the profile under `backend\output\.tiktok_sessions\` is
    renamed from `_login` to the account handle.

---

## Reporting back

For every unticked box, report: **section, item, command, and the actual
output.** Also include:

- The exact `test_platform.py` pass/fail counts, plus the name of any failing check.
- Whether pytest showed exactly the three known failures, or more.
- For any launcher failure: the relevant `logs\*.log` contents and the result of
  the isolated quoting probe in section 2.
- Windows version, Python version, and GPU vendor (this determines which
  hardware encoder is selected).

## Fix guidance

- Fixes for OS differences belong in `backend\utils\platform_compat.py` — that
  module is the single place allowed to know about them. Read the
  "Cross-platform rules" section of `CLAUDE.md` before changing a call site.
- **Add a check to `backend\test_platform.py` for anything you fix**, so the
  next regression is caught by section 1 instead of by a user.
- If you edit `start.ps1`: keep it **pure ASCII** (PowerShell 5.1 reads a
  BOM-less `.ps1` as cp1252 and mangles multi-byte characters) and keep it in
  lockstep with `start.sh` — same processes, ports, queues, environment.

## Suggested Order of Attack

1. **Section 0** — prerequisites and the first `.\start.cmd`.
2. **Section 1 P0s** — portability invariants and the PowerShell parse check.
   Cheap, and they gate everything below.
3. **Section 2 P0s** — cold start, non-empty logs, solo-pool worker. The riskiest
   surface; if the quoting is wrong, nothing else can be tested.
4. **Section 1 P1s, then section 2 P1s** — encode, pytest, ports, teardown.
5. **Section 3** — one real job end-to-end. The captions check is the payoff.
6. **Sections 4-5** — desktop shell, then TikTok if the environment allows.
