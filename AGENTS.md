# CoreClip — Agent Guide

CoreClip is a fully automated, agentic pipeline for ingesting YouTube videos, analyzing them for viral moments using Google Gemini 2.5, extracting short-form vertical clips with dynamic text animations, and distributing them to major social networks (TikTok, Instagram, YouTube Shorts, and Snapchat).

## Technology Stack

### Backend
- **Python 3.11** — runtime
- **FastAPI** — HTTP API framework
- **Celery 5.x** — distributed task queue
- **Redis** — Celery broker and pub/sub backend
- **SQLAlchemy 2.x** — ORM over SQLite (WAL mode enabled)
- **Google GenAI (`google-genai`)** — Gemini 2.5 API client
- **faster-whisper** — local speech-to-text transcription
- **OpenCV (headless), MediaPipe, Ultralytics YOLOv8** — computer vision and face/speaker tracking
- **pyannote.audio** — speaker diarization
- **numpy** — audio feature analysis (decoded through FFmpeg)
- **yt-dlp** — YouTube video downloading
- **FFmpeg + ffprobe** — video/audio processing (required system binary)
- **boto3** — AWS S3 cloud storage client
- **Pydantic v2** — request/response validation

### Frontend
- **React 18** — UI library
- **TypeScript 5.x** — language
- **Vite 5.x** — build tool and dev server
- **Tailwind CSS v4** — styling
- **react-router-dom v6** — client-side routing
- **axios** — HTTP client

### Infrastructure
- **Docker + Docker Compose** — containerized deployment
- **SQLite** — single-file database at repo root (`db.sqlite3`)
- **Redis 7** — message broker and real-time pub/sub

## Project Structure

```
coreclip/
├── backend/              # Python backend
│   ├── api/              # FastAPI route modules (jobs, clips, logs, websockets)
│   ├── pipeline/         # Processing stage implementations
│   │   ├── ingestion.py         # Download, extract audio, transcribe
│   │   ├── audio_analysis.py    # Audio feature computation
│   │   ├── moment_detection.py  # Gemini-based viral moment detection
│   │   ├── clip_extraction.py   # Extract raw clips with FFmpeg
│   │   ├── reformat.py          # Vertical reformatting
│   │   ├── captioning.py        # Burn ASS subtitles into final video
│   │   ├── metadata_generation.py # Gemini-based title/description/hashtag generation
│   │   ├── distribution.py      # Platform client dispatcher
│   │   └── orchestrator.py      # Linear stage runner
│   ├── services/         # External service clients and utilities
│   │   ├── gemini_client.py     # Gemini API wrapper with retries/logging
│   │   ├── ffmpeg_client.py     # FFmpeg/ffprobe subprocess wrapper
│   │   ├── whisper_client.py    # faster-whisper wrapper
│   │   ├── redis_pubsub.py      # Redis publish/subscribe helpers
│   │   ├── token_manager.py     # OAuth token JSON store
│   │   ├── face_detector.py     # OpenCV/Mediapipe face detection
│   │   ├── speaker_tracker.py   # YOLOv8 + face speaker tracking
│   │   ├── speaker_diarizer.py  # pyannote.audio diarization
│   │   ├── video_reframer.py    # Smart crop/reframe to vertical
│   │   └── platform_clients/    # Platform upload clients (base, youtube, tiktok, instagram, snapchat)
│   ├── tasks/            # Celery tasks and app configuration
│   │   ├── celery_app.py        # Celery instance, worker init, beat schedule
│   │   └── celery_tasks.py      # process_job, post_clip, check_scheduled_posts
│   ├── models/           # SQLAlchemy ORM models and Pydantic schemas
│   │   ├── job.py               # Job table definition
│   │   ├── clip.py              # Clip table definition
│   │   └── schemas.py           # Pydantic request/response models
│   ├── utils/            # General utilities
│   │   ├── file_manager.py      # Output directory management, cleanup
│   │   ├── logger.py            # JSON logger with contextvars
│   │   ├── timestamps.py        # Time formatting helpers
│   │   └── validators.py        # Input validation (e.g., YouTube URL parsing)
│   ├── config.py         # Centralized configuration from environment
│   ├── database.py       # SQLAlchemy engine, session, WAL/foreign-key setup
│   ├── main.py           # FastAPI application entry point
│   ├── requirements.txt  # Python dependencies
│   ├── Dockerfile        # Backend container image
│   └── test_*.py         # Loose smoke-test scripts (not pytest)
├── frontend/             # React + Vite frontend
│   ├── src/
│   │   ├── api/client.ts        # Axios client (hardcoded localhost:8000/api)
│   │   ├── components/          # Reusable UI components
│   │   ├── hooks/               # Custom React hooks (useJobs, useWebSocket)
│   │   ├── pages/               # Route-level pages (Dashboard, NewJob, JobView, Logs)
│   │   ├── types/index.ts       # TypeScript interfaces
│   │   ├── App.tsx              # Router setup
│   │   ├── main.tsx             # React root mount
│   │   └── index.css            # Tailwind import + base styles
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── Dockerfile
│   └── index.html
├── fonts/                # User-provided .ttf fonts (e.g., Montserrat-ExtraBold.ttf)
├── output/               # Generated clips and intermediate files
├── logs/                 # Application logs (api.jsonl, errors.jsonl, platform.jsonl, jobs/)
├── .env                  # Environment variables (not committed)
├── .env.example          # Template for required env vars
├── docker-compose.yml    # Full stack orchestration
├── start.sh              # Canonical local launcher script
├── db.sqlite3            # SQLite database (repo root, WAL mode)
└── README.md
```

## Build, Run, and Test Commands

### Local Development (Recommended)
```bash
# One-command launcher — boots Redis, FastAPI, Celery worker, Celery beat, and Vite dev server
./start.sh
```
The script auto-creates `backend/venv`, installs Python deps, runs `npm install`, and tails logs into `logs/`.

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs
- Logs: `logs/backend.log`, `logs/celery-worker.log`, `logs/celery-beat.log`, `logs/frontend.log`

### Docker
```bash
docker-compose up --build
```
Note: the compose file does **not** mount the SQLite DB as a persistent volume. Local dev via `start.sh` is the primary development path.

### Individual Services
```bash
# Backend (from backend/)
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Celery worker — must run from backend/ so tasks.celery_app resolves
celery -A tasks.celery_app worker --loglevel=info --concurrency=1 --pool=solo

# Celery beat scheduler
celery -A tasks.celery_app beat --loglevel=info

# Frontend (from frontend/)
npm run dev      # dev server
npm run build    # production build (tsc -b && vite build)
npm run preview  # preview production build
```

### Testing
There is no formal pytest suite. Smoke tests are loose scripts in `backend/`:
```bash
cd backend
source venv/bin/activate
python test_api.py      # FastAPI endpoint smoke test
python test_celery.py   # End-to-end job flow smoke test
python test_ffmpeg.py   # FFmpeg wrapper test
python test_audio.py    # Audio analysis test
python test_face.py     # Face detection test
```

## Code Style and Conventions

- **No lint/format config exists** — match the surrounding style precisely.
- **Logging**: use the centralized `clipper` logger (`logging.getLogger("clipper")`).
  - `utils/logger.py` configures JSON formatting with rotating file handlers.
  - It uses `contextvars.ContextVar` (`current_job_id`, `current_clip_id`) to tag every log line with the active job/clip.
  - Call `get_job_logger(job_id)` at the start of any per-job work to set the contextvar.
  - Per-job logs are mirrored to `logs/jobs/{job_id}.jsonl` via `JobFileHandler`.
- **Database mutations on JSON columns**: `platform_variants`, `hashtags`, `post_results`, and `target_platforms` are JSON columns. Reassign a new dict/list rather than mutating in place, or SQLAlchemy will not detect the change. Example:
  ```python
  new_results = dict(clip.post_results)
  new_results["tiktok"] = {...}
  clip.post_results = new_results  # reassign!
  db.commit()
  ```
- **Error handling in pipeline stages**: if a single clip fails during `processing` or `captioning`, its status is set to `failed` and its `error_message` is stored, but the stage continues for remaining clips. Stage-level failures bubble up and set `Job.status = "failed"` + `Job.failed_stage`.
- **Relative paths**: `config.py` resolves `./output`, `./logs`, `./fonts`, etc. relative to the repo root (parent of `backend/`). Running the backend from `backend/` is required for these paths to resolve correctly.

## Architecture Details

### Job Lifecycle and Pipeline Orchestration
A `Job` represents one YouTube URL → many `Clip`s.

1. API creates a `Job` row and enqueues `tasks.celery_tasks.process_job`.
2. The Celery worker instantiates `pipeline.orchestrator.PipelineOrchestrator` and runs a fixed linear sequence:
   ```
   ingesting → analyzing → clipping → processing → captioning → generating_metadata
   ```
3. For each stage:
   - `Job.status` is updated to the stage name.
   - A Redis pub/sub event is published on `clipper:status:{job_id}`.
   - The corresponding `handle_<stage>` method is invoked.
   - On failure, `Job.status = "failed"`, `Job.failed_stage = <stage>`, and `Job.error_message` are stored so `POST /api/jobs/{id}/retry` can resume from that exact point via `resume_from`.
4. Terminal statuses after the final stage:
   - `completed` — zero clips found.
   - `ready_for_review` — clips found; human approval is always required before posting.

### Intermediate Artifact Paths
Each clip accumulates files under `./output/{job_id}/clip_{index}/`:
```
raw.mp4 → vertical.mp4 → captioned.mp4 (final)
```
These are tracked on the `Clip` row as `raw_clip_path`, `vertical_clip_path`, and `final_clip_path`.

Cleanup flags:
- `CLEANUP_SOURCE_AFTER_CLIPPING=true` deletes `source.mp4` + `audio.wav` after extraction.
- `CLEANUP_INTERMEDIATES=true` deletes `raw.mp4` and `vertical.mp4` after captioning.

### Posting and Scheduling
Posting is decoupled from the pipeline:
- Clips get a `scheduled_post_time` when the user schedules them after manual review.
- `check_scheduled_posts` runs every 60 seconds via Celery beat.
- When a clip is due, it dispatches one `post_clip` Celery task per platform in `Job.target_platforms`.
- Platform clients live in `services/platform_clients/` and conform to the abstract base in `base.py`.
- OAuth tokens are persisted to `TOKEN_STORE_PATH` (a JSON file) via `services/token_manager.py`.

### Realtime UI Updates
```
Orchestrator → publish_status() → Redis channel clipper:status:{job_id}
  → WebSocket route /ws/jobs/{job_id} (routes_ws.py)
    → frontend useWebSocket hook
```
Redis is required for live UI updates, but the orchestrator still runs and completes jobs without it.

### Database
- SQLite at `./db.sqlite3` (repo root, **not** under `backend/`).
- WAL mode and foreign keys are enabled via a SQLAlchemy `connect` event listener in `database.py`.
- Schema is auto-created on FastAPI startup via `init_db()` — there are no migrations.
- Two tables: `jobs` and `clips` (cascade delete via FK).
- Both use string UUID primary keys (`str(uuid.uuid4())`).

## Environment Variables

Copy `.env.example` to `.env` at the repo root and fill in values.

**Required:**
- `GEMINI_API_KEY` — Google Gemini API key.

**Application:**
- `FRONTEND_URL` — defaults to `http://localhost:5173`
- `REDIS_URL` — defaults to `redis://localhost:6379/0`
- `APP_HOST` / `APP_PORT` — FastAPI bind address

**AI/ML:**
- `GEMINI_DEFAULT_MODEL` — defaults to `gemini-2.5-flash`
- `WHISPER_MODEL_SIZE` — defaults to `medium`
- `WHISPER_DEVICE` — defaults to `cpu`
- `WHISPER_COMPUTE_TYPE` — defaults to `int8`

**Paths:**
- `OUTPUT_DIR` — defaults to `./output`
- `LOG_DIR` — defaults to `./logs`
- `FONT_DIR` — defaults to `./fonts`

**Cleanup:**
- `CLEANUP_SOURCE_AFTER_CLIPPING` — `true`/`false`
- `CLEANUP_INTERMEDIATES` — `true`/`false`

**Platform OAuth (optional):**
- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`
- `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`
- `SNAPCHAT_CLIENT_ID`, `SNAPCHAT_CLIENT_SECRET`

**Cloud Storage (optional):**
- `CLOUD_STORAGE_PROVIDER` — `s3` or `gcs`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`
- `GCS_BUCKET`, `GCS_SERVICE_ACCOUNT_JSON`

## Security Considerations

- `.env` contains sensitive API keys and OAuth credentials and is gitignored.
- OAuth tokens are stored in a local JSON file (`token_store.json` by default). Treat this file as sensitive.
- The backend enables CORS for `FRONTEND_URL` only.
- Job uniqueness is enforced by `youtube_video_id` for **non-failed jobs only** — failed jobs can be retried or re-submitted.
- Disk-space checks run before job creation (`MIN_DISK_SPACE_GB`, default 10GB).
- No authentication/authorization layer is implemented on the API — the app is intended for local or trusted-network use.

## Important Gotchas

- The Celery worker **must** be started from `backend/` (or with `PYTHONPATH=backend`). `tasks/celery_app.py` injects `backend/` onto `sys.path` defensively, but relative paths in `config.py` still depend on the working directory.
- `worker_concurrency=1` and `--pool=solo` are **intentional**: the Whisper model is loaded once per worker process in `worker_process_init`, and the pipeline holds large FFmpeg/OpenCV state. Do not raise concurrency without rethinking model loading.
- Two virtualenv directories exist under `backend/` (`.venv/` and `venv/`); `start.sh` uses `venv/`. Keep dependency installs consistent with whichever one you activate.
- `backend/.models/` ships the OpenCV face-detector weights (`res10_300x300_ssd.caffemodel`). `backend/yolov8n.pt` lives at the backend root. Both are loaded by services under `backend/services/`.
- The frontend API base URL is hardcoded to `http://localhost:8000/api` in `frontend/src/api/client.ts`.
