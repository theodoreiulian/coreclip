# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CoreClip is an end-to-end pipeline that ingests a YouTube URL, finds viral moments with Gemini, extracts vertical short-form clips with burned-in captions, and distributes them to TikTok / Instagram / YouTube Shorts / Snapchat. It's a FastAPI + Celery backend, a React/Vite frontend, Redis for queue + pub/sub, and SQLite (WAL mode) as the system of record.

## Running it

CoreClip is supported on **macOS and Windows**. The canonical local launcher is `./start.sh` on macOS and `.\start.cmd` (a shim over `start.ps1`) on Windows — both boot the same stack: Redis, FastAPI (`:8000`), a Celery worker, Celery beat, and the Vite dev server (`:5173`), tailing logs into `./logs/`. Both auto-create `backend/venv` and run `pip install` / `npm install` on first run. There is also `docker-compose up --build`, but note the compose file does *not* mount the SQLite DB volume — local dev via the launchers is the path most things have been built around.

Keep the two launchers in lockstep: a change to the process list, ports, queues or env setup in one belongs in the other. `backend/test_platform.py` asserts a few of these invariants (Celery's solo pool on Windows, UTF-8 env, PATH repair on macOS) and is the fastest check that a machine is set up correctly.

### Desktop app

CoreClip also ships an Electron shell so it runs as a native window instead of a browser tab. From the repo root: `npm install` (installs Electron) then `npm start`. The root `package.json` (`main: electron/main.js`) is the desktop launcher — distinct from `frontend/package.json` (the Vite app). `electron/main.js` spawns `start.sh` in its own process group (with `CORECLIP_NO_BROWSER=1` so the script skips its `open` call), shows `electron/loading.html` while polling `http://localhost:5173`, then loads the frontend into the window; on quit it `SIGTERM`s the process group so `start.sh`'s `cleanup` trap tears the stack down. If the stack is already up, it attaches a window without spawning a second copy. No frontend code changed — the app's absolute `localhost:8000`/WebSocket URLs work unchanged inside Electron.

Individual services (when you need to run one piece without the full stack):

```bash
# Backend (from backend/)
source venv/bin/activate          # Windows: venv\Scripts\activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Celery worker — must run from backend/ so `tasks.celery_app` resolves.
# --pool=solo is mandatory on Windows (prefork needs fork()).
celery -A tasks.celery_app worker --loglevel=info --concurrency=1 --pool=solo
celery -A tasks.celery_app beat --loglevel=info

# Frontend (from frontend/)
npm run dev
npm run build   # tsc -b && vite build
```

Tests are loose script-style smoke checks in `backend/test_*.py` (run with `python test_ffmpeg.py` etc., not pytest-orchestrated). `backend/tests/` holds a small pytest suite, but pytest is not in `requirements.txt` — install it separately to run those. There is no lint/format config — match the surrounding style.

Required system deps: FFmpeg **built with libass** (macOS: `ffmpeg-full`; Windows: any winget/Choco/Scoop build), a Redis-compatible server (Windows: Memurai, `redis-server.exe`, or Docker), Node 20+, Python 3.11+. `GEMINI_API_KEY` can be set in `.env` at the repo root (loaded by `backend/config.py`, which looks at project-root `.env` first, then CWD) or through the Settings UI, which writes `user_settings.json` and takes precedence.

`backend/requirements-optional.txt` holds dependencies nothing in the active pipeline imports — `pyannote.audio` (speaker diarization, used only by the currently-uncalled `services/speaker_diarizer.py`) and `boto3` (the unimplemented cloud-storage handoff). Keeping them out of `requirements.txt` drops ~19 transitive packages from every install; both consumers already degrade gracefully when absent, so don't "fix" an ImportError by moving one back.

The install footprint is kept small on purpose, so don't reintroduce these: `librosa` (`pipeline/audio_analysis.py` reproduces the three features it used in numpy, fed by `ffmpeg_client.decode_audio_mono` — librosa costs 200+ MB of numba/llvmlite/scikit-learn), `playwright install chromium` (TikTok posting only ever launches the installed Google Chrome via `channel="chrome"`), and `electron-builder` as a devDependency (the `dist` scripts run it through `npx`). The launchers install with `pip --no-cache-dir`, and `main.py` drops successful requests from uvicorn's access log, which the frontend's polling otherwise grows without bound in `logs/backend.log`.

## Architecture

### Job lifecycle and the orchestrator

A job is the unit of work — one YouTube URL → many clips. The API creates a `Job` row, enqueues `tasks.celery_tasks.process_job`, and the worker hands it to `pipeline.orchestrator.PipelineOrchestrator`.

The orchestrator runs a fixed linear sequence of stages from `STAGES` in `pipeline/orchestrator.py`:

```
ingesting → analyzing → clipping → processing → captioning → generating_metadata
```

For each stage it (1) writes the stage name into `Job.status`, (2) publishes a Redis pub/sub event on `clipper:status:{job_id}`, (3) dispatches to `handle_<stage>`, (4) on failure stores `failed_stage` + `error_message` so `POST /api/jobs/{id}/retry` can resume from that exact point via `resume_from`. After the last stage, terminal status is either `completed` (zero clips found) or `ready_for_review` (clips found — manual review is always required before posting).

Each stage module under `backend/pipeline/` is a thin function that mutates the `Job`/`Clip` rows and writes files into `./output/{job_id}/clip_{index}/`. Intermediate artifacts are tracked as paths on the `Clip` row: `raw_clip_path` → `vertical_clip_path` → `final_clip_path` (captioned). `CLEANUP_INTERMEDIATES=true` deletes the first two after captioning; `CLEANUP_SOURCE_AFTER_CLIPPING=true` deletes `source.mp4` + `audio.wav` after extraction.

### Disk guardrails

`ensure_disk_space` runs before each disk-heavy stage and `check_disk_space` before accepting a new job. Both now *reclaim* before refusing, via `file_manager.reclaim_disk_space`, which only acts when already under `MIN_DISK_SPACE_GB` and stops the moment it is satisfied — a healthy install never loses anything. It frees in cheapest-to-lose order: first `output/.cache` source downloads (pure cache; ingestion re-downloads), then the output of terminal jobs oldest-first.

**Only `_RECLAIMABLE_STATUSES` (`completed`, `rejected`, `failed`) are eligible.** `ready_for_review` is excluded because that is the user's unreviewed work, and `posting` because its posts are still pending — which is also what makes the function safe to call from inside a running job, since every in-flight status is ineligible by the same rule. Reclaimed clips leave dangling paths on their rows; the download routes already `os.path.exists`-check and 404, so it degrades to "no longer available" rather than an error.

### Posting and the beat scheduler

Posting is decoupled from the pipeline. Clips get `scheduled_post_time` stamped (either by user action or by `schedule_all_clips`), and `check_scheduled_posts` runs every 60s via Celery beat. When a clip becomes due, it dispatches one `post_clip` task per platform in `Job.target_platforms`. Platform clients live in `services/platform_clients/` (one module per platform, all conforming to `base.py`). OAuth tokens are persisted to `TOKEN_STORE_PATH` (a JSON file) via `services/token_manager.py`.

### Realtime UI updates

Status flow: orchestrator → `publish_status()` → Redis channel `clipper:status:{job_id}` → `routes_ws.py` WebSocket at `/ws/jobs/{job_id}` → frontend `useWebSocket` hook. Redis is required for live UI updates; the orchestrator still runs without it.

### Database

SQLite at `./db.sqlite3` (repo root, *not* under `backend/`). WAL + foreign keys are enabled in `database.py` via a SQLAlchemy connect listener. Schema is created on FastAPI startup via `init_db()` — there are no migrations. Two tables: `jobs` and `clips` (cascade-on-delete via FK). Both use string UUID primary keys. `Clip.platform_variants`, `Clip.hashtags`, `Clip.post_results`, and `Job.target_platforms` are JSON columns — when mutating them in Python, reassign a new dict/list rather than mutating in place, or SQLAlchemy won't notice the change (existing code follows this pattern in `celery_tasks.post_clip`).

### Logging

`utils/logger.py` sets up a single `clipper` logger with a JSON formatter. It uses `contextvars` (`current_job_id`, `current_clip_id`) to tag each log line with the job that's currently being processed, and `JobFileHandler` mirrors per-job logs into `logs/jobs/{job_id}.jsonl`. Call `get_job_logger(job_id)` at the start of any per-job work to set the contextvar — the orchestrator does this in `run()`.

### Frontend

Vite + React 18 + Tailwind v4 + react-router. API base URL is hardcoded to `http://localhost:8000/api` in `src/api/client.ts`. Four pages (`Dashboard`, `NewJob`, `JobView`, `Logs`) and a small set of components — keep it that way unless a feature genuinely needs more.

## Cross-platform rules

`backend/utils/platform_compat.py` is the single place that knows about OS differences. Use it rather than branching on `sys.platform` at a call site:

- **Never shell out to a bare `"ffmpeg"`/`"ffprobe"`.** Go through `services.ffmpeg_client._get_ffmpeg_binary()`, which resolves an absolute path to a libass-capable build. A GUI-launched process (the Electron shell from Finder or Explorer) inherits a minimal `PATH` containing neither Homebrew nor most Windows install dirs, so a bare name resolves in a terminal and fails in the app.
- **Never build an FFmpeg filter option value by hand.** Pass the path through `ffmpeg_filter_path()`. Filtergraphs are parsed before the filter sees its options, and `\` (escape) plus `:` (option separator) is exactly what a Windows path is made of.
- **Never call `os.symlink`.** Use `link_or_copy()` — Windows symlinks need Developer Mode or elevation, so it degrades to a hard link and then a copy. Use `os.replace` (not `os.rename`) when a destination may already exist, and `replace_dir()` for directories, which retries past Windows' open-handle locks.
- **Pass `**no_window_kwargs()` to every `subprocess` call.** Otherwise each ffmpeg invocation flashes a console window on Windows.
- **Open files with an explicit `encoding`,** and keep log handlers on UTF-8. Windows defaults to cp1252, which cannot represent the video titles, hashtags and status glyphs the pipeline logs.
- **Default scratch paths come from `default_intermediate_dir()`,** never a literal `/tmp`.

`backend/test_platform.py` checks all of the above; run it after touching any of these areas.

## Gotchas

- The Celery worker must be started from `backend/` (or with `PYTHONPATH=backend`) — `tasks/celery_app.py` injects `backend/` onto `sys.path` defensively, but the working directory still matters for relative paths in `config.py` (`./output`, `./logs`, `./fonts`).
- `worker_concurrency=1` and `--pool=solo` are intentional: the Whisper model is loaded once per worker process in `worker_process_init`, and the pipeline holds large ffmpeg/OpenCV state. Don't raise concurrency without rethinking model loading.
- Two virtualenvs exist under `backend/` (`.venv/` and `venv/`); `start.sh` uses `venv/`. Keep dependency installs consistent with whichever one you activate.
- `backend/.models/` ships the OpenCV face-detector weights (`res10_300x300_ssd.caffemodel`). `yolov8n.pt` lives at `backend/yolov8n.pt`. Both are loaded by services under `backend/services/`.
- Job uniqueness is enforced by `youtube_video_id` *for non-failed jobs only* — failed jobs are allowed to co-exist with a new attempt on the same URL.
