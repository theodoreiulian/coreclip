# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CoreClip is an end-to-end pipeline that ingests a YouTube URL, finds viral moments with Gemini, extracts vertical short-form clips with burned-in captions, and distributes them to TikTok / Instagram / YouTube Shorts / Snapchat. It's a FastAPI + Celery backend, a React/Vite frontend, Redis for queue + pub/sub, and SQLite (WAL mode) as the system of record.

## Running it

`./start.sh` from the repo root is the canonical local launcher — it boots Redis, FastAPI (`:8000`), a Celery worker, Celery beat, and the Vite dev server (`:5173`) and tails logs into `./logs/`. It also auto-creates `backend/venv` and runs `pip install` / `npm install` on first run. There is also `docker-compose up --build`, but note the compose file does *not* mount the SQLite DB volume — local dev via `start.sh` is the path most things have been built around.

### Desktop app

CoreClip also ships an Electron shell so it runs as a native window instead of a browser tab. From the repo root: `npm install` (installs Electron) then `npm start`. The root `package.json` (`main: electron/main.js`) is the desktop launcher — distinct from `frontend/package.json` (the Vite app). `electron/main.js` spawns `start.sh` in its own process group (with `CORECLIP_NO_BROWSER=1` so the script skips its `open` call), shows `electron/loading.html` while polling `http://localhost:5173`, then loads the frontend into the window; on quit it `SIGTERM`s the process group so `start.sh`'s `cleanup` trap tears the stack down. If the stack is already up, it attaches a window without spawning a second copy. No frontend code changed — the app's absolute `localhost:8000`/WebSocket URLs work unchanged inside Electron.

Individual services (when you need to run one piece without the full stack):

```bash
# Backend (from backend/)
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Celery worker — must run from backend/ so `tasks.celery_app` resolves
celery -A tasks.celery_app worker --loglevel=info --concurrency=1 --pool=solo
celery -A tasks.celery_app beat --loglevel=info

# Frontend (from frontend/)
npm run dev
npm run build   # tsc -b && vite build
```

Tests are loose script-style smoke checks in `backend/test_*.py` (run with `python test_ffmpeg.py` etc., not pytest-orchestrated). There is no lint/format config — match the surrounding style.

Required system deps: `ffmpeg`, `redis-server`, Node 20+, Python 3.11. `GEMINI_API_KEY` must be set in `.env` at the repo root (loaded by `backend/config.py`, which looks at project-root `.env` first, then CWD).

## Architecture

### Job lifecycle and the orchestrator

A job is the unit of work — one YouTube URL → many clips. The API creates a `Job` row, enqueues `tasks.celery_tasks.process_job`, and the worker hands it to `pipeline.orchestrator.PipelineOrchestrator`.

The orchestrator runs a fixed linear sequence of stages from `STAGES` in `pipeline/orchestrator.py`:

```
ingesting → analyzing → clipping → processing → captioning → generating_metadata
```

For each stage it (1) writes the stage name into `Job.status`, (2) publishes a Redis pub/sub event on `clipper:status:{job_id}`, (3) dispatches to `handle_<stage>`, (4) on failure stores `failed_stage` + `error_message` so `POST /api/jobs/{id}/retry` can resume from that exact point via `resume_from`. After the last stage, terminal status is either `completed` (zero clips found) or `ready_for_review` (clips found — manual review is always required before posting).

Each stage module under `backend/pipeline/` is a thin function that mutates the `Job`/`Clip` rows and writes files into `./output/{job_id}/clip_{index}/`. Intermediate artifacts are tracked as paths on the `Clip` row: `raw_clip_path` → `vertical_clip_path` → `final_clip_path` (captioned). `CLEANUP_INTERMEDIATES=true` deletes the first two after captioning; `CLEANUP_SOURCE_AFTER_CLIPPING=true` deletes `source.mp4` + `audio.wav` after extraction.

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

## Gotchas

- The Celery worker must be started from `backend/` (or with `PYTHONPATH=backend`) — `tasks/celery_app.py` injects `backend/` onto `sys.path` defensively, but the working directory still matters for relative paths in `config.py` (`./output`, `./logs`, `./fonts`).
- `worker_concurrency=1` and `--pool=solo` are intentional: the Whisper model is loaded once per worker process in `worker_process_init`, and the pipeline holds large ffmpeg/OpenCV state. Don't raise concurrency without rethinking model loading.
- Two virtualenvs exist under `backend/` (`.venv/` and `venv/`); `start.sh` uses `venv/`. Keep dependency installs consistent with whichever one you activate.
- `backend/.models/` ships the OpenCV face-detector weights (`res10_300x300_ssd.caffemodel`). `yolov8n.pt` lives at `backend/yolov8n.pt`. Both are loaded by services under `backend/services/`.
- Job uniqueness is enforced by `youtube_video_id` *for non-failed jobs only* — failed jobs are allowed to co-exist with a new attempt on the same URL.
