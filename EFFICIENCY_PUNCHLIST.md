# CoreClip Efficiency Punchlist

Actionable improvements to reduce CPU, memory, disk, and API usage across the pipeline while keeping all current functionality intact.

**Legend:**
- **P0** — Critical impact / do first
- **P1** — High impact / do next
- **P2** — Medium impact / backlog
- **P3** — Nice to have / polish

---

## 1. FFmpeg / Video Encoding

- [x] **P0 — Tune intermediate encode settings for speed**
  - Where: `backend/services/ffmpeg_client.py`
  - Change `extract_clip`/`extract_clip_accurate` from `-preset fast -crf 18` to `-preset veryfast -crf 23` for intermediates.
  - Keep final `captioning.py` output at higher quality (`-preset medium -crf 20`).
  - Add env vars: `FFMPEG_INTERMEDIATE_PRESET`, `FFMPEG_INTERMEDIATE_CRF`, `FFMPEG_FINAL_PRESET`, `FFMPEG_FINAL_CRF`.

- [x] **P0 — Auto-select hardware video encoder**
  - Where: `backend/services/ffmpeg_client.py`, `backend/config.py`
  - Probe available encoders at startup.
  - Prefer `h264_videotoolbox` / `hevc_videotoolbox` on macOS, `h264_nvenc` on NVIDIA, `h264_qsv` on Intel.
  - Fall back to `libx264`.
  - Note: hardware encoders use `-q:v` or `-b:v`, not `-crf`.

- [x] **P1 — Combine or skip re-encode passes**
  - Where: `backend/pipeline/clip_extraction.py`, `backend/pipeline/reformat.py`, `backend/pipeline/captioning.py`
  - When no smart crop needed, extract vertical directly from source in one pass.
  - Burn captions during final render instead of a separate `captioned.mp4` pass.
  - Use stream-copy for rough cuts when start aligns to a keyframe.

- [x] **P1 — Replace OpenCV `VideoWriter` with FFmpeg-based render**
  - Where: `backend/services/reframer/render.py`
  - Current `mp4v` output is large and slow.
  - Pipe raw frames to FFmpeg or write raw frames and encode once with chosen codec/preset.

- [x] **P1 — Avoid duration-mismatch retry by using accurate extraction first when needed**
  - Where: `backend/pipeline/clip_extraction.py` lines 40-48
  - Every clip is first extracted with fast seek, then ffprobed, and re-extracted with accurate seek if the duration is off by >1s.
  - Use accurate seek by default for short clips (the dominant case) where the speed difference is negligible, or skip the fast-path entirely.

- [x] **P1 — Memoize FFmpeg binary discovery**
  - Where: `backend/services/ffmpeg_client.py` lines 33-65
  - `_get_ffmpeg_binary()` and `_get_ffprobe_binary()` run a subprocess (`ffmpeg -filters`) on every call.
  - Cache the discovered binary path in a module-level variable after first call.

- [x] **P2 — Explicitly set FFmpeg threads**
  - Where: `backend/services/ffmpeg_client.py`
  - Add `-threads 0` (auto) or `-threads N` from config.

- [x] **P2 — Request only needed streams from ffprobe**
  - Where: `backend/services/ffmpeg_client.py` lines 132-138
  - `get_video_info` calls `-show_format -show_streams` and parses the full JSON.
  - Use `-show_entries format=duration -show_entries stream=width,height,codec_name` and a selective parser.

---

## 2. Reframer / Computer Vision

- [x] **P0 — Add simple FFmpeg reframe fallback for non-complex clips**
  - Where: `backend/pipeline/reformat.py`
  - Detect when ML reframe is not needed (single speaker, moderate motion, simple crop).
  - Use FFmpeg `crop`/`scale`/`pad` directly instead of YOLO + tracking + ASD.
  - Add `REFRAMER_MODE=auto|ml|simple`.

- [x] **P0 — Use FFmpeg for vertical reframing instead of Python frame-by-frame rendering**
  - Where: `backend/services/reframer/render.py`, `backend/services/reframer/reframer.py`
  - The reframer decodes every frame with OpenCV, crops/resizes in NumPy, and writes with `cv2.VideoWriter` (`mp4v`), then remuxes audio.
  - Export the computed crop trajectory as a sequence of crop rectangles and use `ffmpeg` with `crop`/`scale` filters to render in one pass. This removes OpenCV from the render path entirely.

- [x] **P1 — Reduce detection cadence or make it adaptive**
  - Where: `backend/services/reframer/config.py`, `backend/services/reframer/detect.py` line 249
  - Default `detect_every=2` runs face + person detection on every other frame even for static talking-head shots.
  - Default to `detect_every=3` or `4`; only increase cadence around shot boundaries or fast motion.

- [x] **P1 — Downscale frames before detection**
  - Where: `backend/services/reframer/detect.py` lines 111-147
  - Face and YOLO person detection run on full-resolution frames.
  - Resize frames to a fixed max dimension (e.g., 720p) before detection; scale boxes back to original coordinates.

- [x] **P1 — Avoid decoding the clip multiple times**
  - Where: `backend/services/reframer/reframer.py`, `backend/services/reframer/detect.py`, `backend/services/reframer/asd.py`, `backend/services/reframer/audio_features.py`
  - The clip is decoded separately for shot detection, detection, ASD crop extraction, and rendering.
  - Combine detection + ASD crop extraction + frame timestamp collection into a single decode pass. Reuse frames in memory where feasible.

- [x] **P1 — Use lighter face/person models**
  - Where: `backend/services/reframer/detect.py`
  - Evaluate `yunet` / MediaPipe face detection.
  - Skip person detection when `allow_group_framing=False`.

- [x] **P2 — Limit deterministic mode to single-threaded OpenCV/Torch**
  - Where: `backend/services/reframer/reframer.py` lines 27-42
  - Determinism forces `cv2.setNumThreads(1)` and `torch.set_num_threads(1)` globally.
  - Make `deterministic=False` the default, or allow a per-job override. Document that deterministic mode costs ~2-3x speed.

- [x] **P2 — Cache Silero VAD model load per worker**
  - Where: `backend/services/reframer/audio_features.py` lines 108-118
  - `load_silero_vad()` is called per clip inside `_compute_vad`.
  - Cache the VAD model as a module-level singleton (similar to Whisper/face net).

- [x] **P2 — Reuse job-level audio instead of per-clip extraction**
  - Where: `backend/services/reframer/audio_features.py`
  - Use existing `audio.wav` with time offsets where timeline alignment allows.

- [x] **P3 — Avoid extracting audio for clips when the reframer already extracts it internally**
  - Where: `backend/pipeline/reformat.py` lines 37-41
  - `audio_path` is resolved from the job directory and passed to `reframe_video`, but the docstring says it is ignored and the clip’s own audio is used.
  - Remove the `audio_path` lookup and parameter to clarify the code.

---

## 3. Whisper / Transcription

- [x] **P0 — Default to smaller model for English content**
  - Where: `backend/config.py`, `backend/services/whisper_client.py`
  - Use `small.en` or `base.en` by default for English.
  - Add `WHISPER_FAST_PREVIEW_MODEL` and upgrade path for low-confidence jobs.

- [x] **P0 — Allow GPU/MPS by default when available** *(partial: model size auto-adapts to device and `small.en` is the CPU default, but `WHISPER_DEVICE` still defaults to `cpu` with no GPU/MPS auto-detection)*
  - Where: `backend/config.py` lines 27-29, `backend/tasks/celery_app.py` lines 44-48
  - Default is `medium` on `cpu` with `int8`. For English content, `small` or `base` is often sufficient.
  - Default to `small` (or `medium` only when `WHISPER_DEVICE=cuda/mps`). Auto-detect GPU/MPS and default to it.

- [x] **P1 — Add batched Whisper inference**
  - Where: `backend/services/whisper_client.py`
  - Use `BatchedInferencePipeline` with `batch_size=4–8`.

- [x] **P1 — Tune Whisper CPU settings and VAD**
  - Where: `backend/services/whisper_client.py`, `backend/tasks/celery_app.py`
  - Pass `cpu_threads=4` (configurable).
  - Enable `vad_filter=True`.
  - Add `beam_size=1` fast-preview mode.

- [x] **P2 — Evaluate Apple Silicon Whisper acceleration**
  - Where: `backend/services/whisper_client.py` (new abstraction)
  - Consider `whisper.cpp` + CoreML/ANE or WhisperKit for macOS deployments.

- [x] **P2 — Keep Whisper model warm in workers**
  - Where: `backend/tasks/celery_app.py`
  - Avoid frequent worker restarts; set `worker_max_tasks_per_child` high unless memory leaks appear.

---

## 4. Gemini / LLM

- [x] **P1 — Use cheaper/faster models where sufficient**
  - Where: `backend/config.py`, `backend/pipeline/metadata_generation.py`
  - Use `gemini-2.5-flash` or `gemini-2.0-flash` for metadata generation.
  - Disable Search Grounding hashtag fetch by default (`TRENDING_HASHTAG=false`).

- [x] **P1 — Cache and batch LLM work**
  - Where: `backend/services/gemini_client.py`, `backend/pipeline/metadata_generation.py`
  - Cache system prompts / transcripts when possible.
  - Increase metadata batch size to 12–16 if context allows.
  - Keep `extract_person_names` cached per job.

- [x] **P1 — Parallelize metadata generation batches**
  - Where: `backend/services/gemini_client.py` lines 95-99, `backend/pipeline/metadata_generation.py` lines 75-189
  - `gemini_client.call_batch` is sequential.
  - Issue independent batch calls concurrently with `asyncio.gather` or a thread pool, respecting Gemini rate limits.

- [x] **P1 — Cache Gemini responses per transcript hash**
  - Where: `backend/services/gemini_client.py`, `backend/pipeline/moment_detection.py`, `backend/pipeline/metadata_generation.py`
  - Moment detection and metadata generation are not cached; re-running the same job or retrying a stage repeats identical API calls.
  - Cache JSON responses on disk keyed by a hash of the prompt (and model). Use the cache transparently in `GeminiClient.call()`.

- [x] **P1 — Reduce moment-detection prompt size**
  - Where: `backend/pipeline/moment_detection.py` lines 74-103
  - The full word-level transcript is sent to Gemini for moment detection.
  - Send only segment-level text with start/end timestamps; word timestamps are only needed for boundary snapping, which can be done locally after Gemini returns coarse candidates.

- [x] **P2 — Make metadata generation optional / lazy**
  - Where: `backend/pipeline/metadata_generation.py`, frontend review screen
  - Only generate metadata for selected clips.

- [x] **P2 — Batch person-name extraction with metadata generation**
  - Where: `backend/services/gemini_client.py` lines 152-181, `backend/pipeline/metadata_generation.py` line 71
  - `extract_person_names` is a separate Gemini call per job, and metadata generation already asks for `people` per clip.
  - Remove `extract_person_names` from the title and rely on the per-clip `people` field already returned by metadata generation.

- [x] **P2 — Skip audio analysis summary when Gemini doesn’t use it** *(implemented as an `ENABLE_AUDIO_ANALYSIS` toggle, default `true`)*
  - Where: `backend/pipeline/audio_analysis.py`, `backend/pipeline/moment_detection.py` line 90
  - Librosa features and RMS peak events are computed for the whole source and included in the prompt.
  - Evaluate whether the audio summary improves results. If not, remove it and skip the analysis stage entirely.

---

## 5. Pipeline Orchestration

- [x] **P1 — Process clips in parallel within a stage**
  - Where: `backend/pipeline/orchestrator.py` (`handle_processing`, `handle_captioning`)
  - Clips are reformatted and captioned one at a time in a `for clip in clips` loop.
  - Run independent clips through a process pool (e.g., `concurrent.futures.ProcessPoolExecutor` with `max_workers` capped by CPU) or spawn per-clip Celery tasks. Keep Celery worker concurrency at 1 for Whisper, but use a local pool for CPU-heavy reformat/caption work.

- [x] **P1 — Avoid re-fetching the Job row on every stage iteration**
  - Where: `backend/pipeline/orchestrator.py` lines 43, 52, 72
  - `db.query(Job).filter(Job.id == self.job_id).first()` is issued three times per stage.
  - Fetch once at the top of `run()`, refresh only when needed (e.g., before terminal status check).

- [x] **P1 — Don’t keep all clip rows in memory at once**
  - Where: `backend/pipeline/orchestrator.py` lines 105, 116
  - `db.query(Clip).filter(...).all()` loads every non-failed clip into memory before starting work.
  - Use `yield_per()` or iterate with a streaming query for processing/captioning.

- [x] **P2 — Batch Redis status publishes**
  - Where: `backend/pipeline/orchestrator.py` `_publish()`
  - Every clip emits a separate Redis publish during processing/captioning.
  - Throttle publishes to at most one every N seconds or batch them in memory and flush.

- [x] **P2 — Stop early on cancellation between stages**
  - Where: `backend/pipeline/orchestrator.py` line 53
  - The pipeline only checks `failed/rejected/cancelled` at the start of each stage; a cancel request between stages is ignored until the next stage.
  - Add a lightweight cancellation check inside long-running clip loops and between sub-stages.

- [x] **P2 — Batch commits in pipeline stages**
  - Where: `backend/pipeline/orchestrator.py`
  - Commit once per stage instead of per clip where correctness allows.

- [x] **P3 — Avoid writing JSON transcripts with indentation** *(partial: `metadata.json` is now compact; ingestion/clip transcripts still use `indent=2`)*
  - Where: `backend/pipeline/ingestion.py` line 101, `backend/pipeline/clip_extraction.py` line 53, `backend/pipeline/metadata_generation.py` line 185
  - `json.dump(..., indent=2)` is used for every internal transcript/metadata file.
  - Write internal JSON compact (`indent=None`) and only pretty-print when exporting for display.

---

## 6. Video Download & Audio Extraction

- [x] **P0 — Download only the quality actually needed** *(partial: `YTDLP_MAX_HEIGHT`/`YTDLP_MAX_AUDIO_BITRATE` exist in config but `ingestion.py` doesn't use them yet)*
  - Where: `backend/pipeline/ingestion.py` lines 22-42
  - yt-dlp format is `bestvideo[height<=1080]+bestaudio`, which often pulls a high-bitrate 1080p stream even if the final output is only 1080x1920 vertical.
  - Add an env override (e.g., `YTDLP_MAX_HEIGHT`, `YTDLP_MAX_AUDIO_BITRATE`) and prefer a lower bitrate audio track. For short-form output, 720p video and 128k audio are usually sufficient.

- [x] **P0 — Avoid re-encoding the downloaded source with FFmpegVideoConvertor**
  - Where: `backend/pipeline/ingestion.py` lines 36-39
  - The postprocessor re-encodes the whole downloaded video to MP4 even when yt-dlp already muxes to mp4.
  - Remove the convertor when `merge_output_format='mp4'` already produces MP4; only convert if the source is webm/mkv.

- [x] **P1 — Extract audio only when needed and at the smallest viable quality**
  - Where: `backend/pipeline/ingestion.py` line 72-83, `backend/services/ffmpeg_client.py` lines 159-169
  - Full source audio is extracted as 16 kHz mono WAV for every job, then the reframer extracts the clip’s own audio again later.
  - Pipe audio directly to Whisper when possible, or extract a compressed FLAC/OGG and decode on the fly. Delete `audio.wav` immediately after transcription/analysis if cleanup is enabled.

- [x] **P1 — Run download, audio extraction, and transcription in a single FFmpeg/yt-dlp pass where possible**
  - Where: `backend/pipeline/ingestion.py`
  - Download → extract audio → transcribe are three separate steps with intermediate files.
  - For Whisper, stream audio directly from the downloaded source without writing `audio.wav` to disk (faster-whisper accepts file paths; consider a temporary pipe).

- [x] **P2 — Cache downloaded source videos across jobs**
  - Where: `backend/pipeline/ingestion.py`
  - The same YouTube video is re-downloaded on every retry/re-submission.
  - Cache source files keyed by `youtube_video_id` in a shared `./output/.cache` directory and symlink/copy into the job directory.

---

## 7. Captioning

- [x] **P0 — Avoid re-encoding the whole video just to burn captions** *(partial: `BURN_CAPTIONS` config knob and `ffmpeg_client.build_final_burn_args`/`copy_video_stream` helpers exist, but `captioning.py` still burns with inline libx264 args)*
  - Where: `backend/pipeline/captioning.py` lines 135-145
  - Captions are burned with `libx264 -preset fast -crf 20`, re-encoding the already-rendered vertical clip.
  - If the platform/player supports it, serve the `.ass` subtitle file alongside the MP4 and skip burning. For burn-in, use `h264_nvenc`/`videotoolbox` when available, or at least copy the video stream and only re-encode when the container requires it.

- [x] **P1 — Generate fewer ASS dialogue lines**
  - Where: `backend/pipeline/captioning.py` lines 33-46
  - One ASS `Dialogue` line is emitted per word, each containing the full sentence with one word highlighted. A 60-second clip can generate thousands of lines.
  - Render one line per phrase/chunk and use ASS `\t` tags or the `{\rStyle}` approach once per line, not once per word. Only the highlighted word needs styling.

---

## 8. Celery / Workers

- [x] **P1 — Split worker queues by workload**
  - Where: `backend/tasks/celery_app.py`, `docker-compose.yml`, `start.sh`
  - `process_job` → dedicated pipeline queue (concurrency=1).
  - `post_clip` / `check_scheduled_posts` → posting queue (concurrency=2–4).
  - Optional GPU queue for Whisper/CV if hardware available.

- [x] **P1 — Set Celery task time limits** *(partial: `PIPELINE_*`/`POSTING_*` limit knobs exist in config but `celery_tasks.py` doesn't apply them yet)*
  - Where: `backend/tasks/celery_tasks.py`
  - No `soft_time_limit` or `time_limit` is set on `process_job` or `post_clip`.
  - A hung task (e.g., stuck FFmpeg, browser automation) can block the worker forever.
  - Add reasonable `soft_time_limit`/`time_limit` (e.g., 30 min for processing, 10 min for posting) and handle `SoftTimeLimitExceeded`.

- [x] **P2 — Configure `worker_max_tasks_per_child`**
  - Where: `backend/tasks/celery_app.py`
  - Set based on observed memory leaks; balance against model reload cost.

- [x] **P2 — Add result backend TTL or disable results**
  - Where: `backend/tasks/celery_app.py` line 16
  - Celery is configured without `result_backend`; results may be silently lost. If a result backend is added later, tasks would accumulate.
  - Explicitly set `result_backend=None` or `task_ignore_result=True` if results are not needed. If results are needed, set `result_expires=3600`.

- [x] **P2 — Close database sessions explicitly in error paths**
  - Where: `backend/tasks/celery_tasks.py` lines 25-35
  - The `except` block uses a nested `try/except: pass` around a DB update; if the update fails, the session is still closed in `finally`.
  - Log the DB update failure and rollback explicitly.

---

## 9. SQLite / Database

- [x] **P1 — Add database indexes for hot queries**
  - Where: `backend/models/job.py`, `backend/models/clip.py`
  - `Job.status`, `Clip.status`, `ScheduledPost.status`, and `ScheduledPost.scheduled_time` are queried frequently without indexes.
  - Add indexes on `jobs.status`, `clips.job_id + status`, `scheduled_posts.status + scheduled_time`.

- [x] **P1 — Avoid N+1 clip count queries in job listing**
  - Where: `backend/api/routes_jobs.py` lines 95-96, 111
  - For each job in a list, a separate `COUNT(*)` query is issued for clips.
  - Use a single joined subquery or SQLAlchemy `selectinload` with `func.count`.

- [x] **P1 — Use SQLAlchemy connection pooling**
  - Where: `backend/database.py` lines 13-15
  - `create_engine` uses default pooling for SQLite, which is a `NullPool` equivalent for file-based SQLite.
  - For SQLite, `connect_args={"check_same_thread": False}` is already set; explicitly set `poolclass=StaticPool` or `QueuePool` with `pool_size=5`.

- [x] **P2 — Add SQLite performance pragmas**
  - Where: `backend/database.py`
  - Add:
    ```sql
    PRAGMA synchronous = NORMAL;
    PRAGMA cache_size = -64000;
    PRAGMA temp_store = MEMORY;
    PRAGMA mmap_size = 268435456;
    ```

- [x] **P2 — Paginate scheduled-post completion check**
  - Where: `backend/tasks/celery_tasks.py` lines 148-164
  - `check_scheduled_posts` loads all pending `ScheduledPost` rows and all posting `Job` rows on every 60-second tick.
  - Query only rows with `scheduled_time <= now` and process completion in batches.

- [x] **P2 — Avoid loading the full clip video into memory during validation**
  - Where: `backend/tasks/celery_tasks.py` line 80
  - Platform clients likely read the full file for validation (depending on implementation).
  - Validate via ffprobe metadata (duration, resolution, bitrate) instead of reading the file content.

---

## 10. Disk / I/O

- [x] **P0 — Enable aggressive cleanup by default** *(note: `CLEANUP_TRANSCRIPTS` flag and `cleanup_transcripts()` exist but nothing calls it yet — transcripts are needed by the new lazy metadata endpoint, so wire it after metadata generation, not captioning)*
  - Where: `backend/config.py`, `backend/utils/file_manager.py`, `.env.example`
  - `CLEANUP_SOURCE_AFTER_CLIPPING=false` and `CLEANUP_INTERMEDIATES=false` by default.
  - Default both to `true`; users can opt into keeping intermediates for debugging.
  - Add `CLEANUP_TRANSCRIPTS=true` to remove per-clip `transcript.json` after metadata generation.

- [x] **P1 — Use RAM disk / tmp for short intermediates**
  - Where: `backend/pipeline/*`
  - Write short clip intermediates to `/tmp` or RAM disk; move only final outputs.
  - Avoid large per-clip JSON files; keep transcript data in memory or DB JSON column.

- [x] **P1 — Cache downloaded source videos across jobs**
  - Where: `backend/pipeline/ingestion.py`
  - Re-downloading the same YouTube video on retry/re-submission wastes bandwidth and disk.
  - Cache source files keyed by `youtube_video_id` in a shared `./output/.cache` directory.

- [x] **P2 — Re-check disk space during pipeline** *(partial: `file_manager.ensure_disk_space()` exists but no pipeline stage calls it yet)*
  - Where: `backend/pipeline/orchestrator.py`, `backend/utils/file_manager.py`
  - Check free space before heavy stages and fail fast with clear error.

---

## 11. Frontend / API

- [x] **P1 — Stop polling the jobs list every 10 seconds when the dashboard is not visible**
  - Where: `frontend/src/hooks/useJobs.ts` line 22
  - `setInterval(fetchJobs, 10000)` runs even when the tab is backgrounded.
  - Use `document.visibilityState` to pause polling when hidden, or switch to the WebSocket for live updates.

- [x] **P1 — Throttle LogViewer polling**
  - Where: `frontend/src/components/LogViewer.tsx` line 33
  - Logs are fetched every 3 seconds per viewer.
  - Increase interval to 5-10 seconds when logs are not actively growing; use the WebSocket for real-time log pushes instead.

- [x] **P1 — Avoid refetching the entire job/clips list on every WebSocket message**
  - Where: `frontend/src/pages/JobView.tsx` lines 44-48
  - Each WebSocket status message triggers `fetchAll()`, which reloads job + all clips.
  - Only refetch on stage transitions (e.g., `status` changed) or at most once every few seconds.

- [x] **P1 — Use environment-aware API base URL**
  - Where: `frontend/src/api/client.ts` line 5-6, `frontend/src/components/LogViewer.tsx` line 20
  - API URL is hardcoded to `http://localhost:8000/api`.
  - Use `import.meta.env.VITE_API_URL` with a fallback.

- [x] **P2 — Paginate and filter jobs/clips endpoints**
  - Where: `backend/api/routes_jobs.py`, `backend/api/routes_clips.py`, `frontend/src/hooks/useJobs.ts`
  - Add limit/offset/status query params.

- [x] **P2 — Memoize derived dashboard stats**
  - Where: `frontend/src/pages/Dashboard.tsx` lines 58-61
  - `jobs.filter(...)` and `reduce(...)` run on every render.
  - Wrap in `useMemo`.

- [ ] **P2 — Memoize clip card list rendering** *(not done: keys are stable but neither `ClipCard` nor the dashboard job cards are wrapped in `React.memo`)*
  - Where: `frontend/src/pages/JobView.tsx` lines 104-106, `frontend/src/pages/Dashboard.tsx` lines 103-160
  - Mapping over arrays without `key`-stable memoization can cause unnecessary re-renders.
  - Wrap `ClipCard` and job cards in `React.memo` and ensure stable keys.

- [x] **P3 — Debounce WebSocket reconnections**
  - Where: `frontend/src/hooks/useWebSocket.ts`
  - Add exponential backoff and clean unmount handling.

---

## 12. Infrastructure & Deployment

- [ ] **P1 — Add resource limits to Docker Compose services**
  - Where: `docker-compose.yml`
  - No `mem_limit`, `cpus`, or `deploy.resources` constraints on backend, worker, beat, or frontend.
  - Add limits (e.g., worker 4 CPU / 8 GB, backend 0.5 CPU / 512 MB).

- [ ] **P1 — Avoid building the backend image twice**
  - Where: `docker-compose.yml` lines 12-14, 28-30, 43-45
  - `backend`, `celery-worker`, and `celery-beat` each build `./backend` independently.
  - Define a single `x-backend-build` anchor or a base image and extend it.

- [ ] **P1 — Replace fixed sleep-based health checks in `start.sh`**
  - Where: `start.sh` lines 135, 163
  - `sleep 2` and `sleep 3` are used to wait for backend/frontend readiness.
  - Loop with `curl` health checks and a timeout.

- [ ] **P2 — Use a smaller Redis memory policy**
  - Where: `docker-compose.yml` lines 4-9
  - Redis has no `maxmemory` or eviction policy configured.
  - Add `command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru`.

- [ ] **P2 — Clean up leaked child processes in `start.sh`**
  - Where: `start.sh` lines 23-40
  - `cleanup()` kills PIDs and then uses `pkill -f` for Celery/uvicorn. Child processes spawned by subprocesses (e.g., FFmpeg, Playwright) may survive.
  - Run services in process groups and kill the process groups; or use a process supervisor like `supervisord`/`honcho`/`overmind`.

- [ ] **P2 — Make frontend Vite dev server optional in production**
  - Where: `docker-compose.yml` lines 53-60
  - `npm run dev` runs in a container in production compose.
  - Build the frontend and serve static files from FastAPI or Nginx in production.

- [ ] **P3 — Mount only required volumes in Celery beat**
  - Where: `docker-compose.yml` lines 45-46
  - Beat only needs `.env` and Redis, but it could inherit the same service definition.
  - Keep minimal mounts for beat.

---

## 13. Logging, Cleanup & Configuration

- [x] **P0 — Default cleanup flags to `true`**
  - Where: `backend/config.py` lines 69-70, `.env.example` lines 21-22
  - Disk usage explodes after a few jobs.
  - Default both to `true`; users can opt into keeping intermediates for debugging.

- [ ] **P1 — Add automatic disk-usage guardrails**
  - Where: `backend/utils/file_manager.py`, `backend/config.py` line 68
  - Only `MIN_DISK_SPACE_GB` is checked at job creation; no cleanup happens if disk runs low during processing.
  - Check free space before each disk-heavy stage and trigger cleanup of old completed jobs if below a threshold.

- [x] **P2 — Rotate per-job log files**
  - Where: `backend/utils/logger.py` lines 31-42
  - `JobFileHandler` appends indefinitely to `logs/jobs/{job_id}.jsonl`.
  - Use `RotatingFileHandler` for job logs as well.

- [x] **P2 — Lower default log level for production**
  - Where: `backend/utils/logger.py` line 46
  - Logger is set to `DEBUG` globally.
  - Default to `INFO` and read `LOG_LEVEL` from env.

- [x] **P2 — Validate required env vars at startup**
  - Where: `backend/config.py` line 20
  - `GEMINI_API_KEY` defaults to an empty string; failures happen later on first API call.
  - Raise a clear error during FastAPI startup if `GEMINI_API_KEY` is missing.

- [ ] **P2 — Add timeout to Redis client creation**
  - Where: `backend/services/redis_pubsub.py` line 9
  - `redis.from_url()` can hang indefinitely if Redis is unreachable.
  - Add `socket_connect_timeout=5` and `socket_timeout=5`.

- [ ] **P2 — Cache `.models` downloads locally and avoid re-downloading**
  - Where: `backend/services/face_detector.py` lines 35-41
  - Downloads from GitHub on first use; no integrity check besides file existence.
  - Ship the model files in the repo or Docker image; verify checksums.

---

## 14. Dependencies / Environment

- [ ] **P1 — Remove or make optional unused heavy dependencies**
  - Where: `backend/requirements.txt`
  - Verify and remove/move to extras:
    - `pyannote.audio` (unused by active pipeline)
    - `facenet-pytorch` (verify usage)
    - `mediapipe` if not actively used
  - Keep `scenedetect` as optional if pixel-diff fallback is acceptable.

- [ ] **P2 — Pin CPU-only torch builds for non-GPU deployments**
  - Where: `backend/requirements.txt`
  - Use CPU/mac index URLs to avoid CUDA bloat when no GPU is present.

---

## Suggested Order of Attack

1. Default cleanup flags to `true`.
2. Tune FFmpeg intermediate preset/CRF.
3. Auto-select hardware encoder.
4. Default to smaller English Whisper model / auto-detect GPU.
5. Add simple reframe fallback.
6. Cache FFmpeg binary discovery.
7. Add DB indexes on status fields.
8. Add Celery task time limits.
9. Pause dashboard polling when tab is hidden.
10. Cache Gemini responses per prompt hash.

---

*Generated from a full codebase audit. Earlier research from 2026-07-01 merged and expanded.*
*Checkboxes updated 2026-07-03 after verifying the interrupted 2026-07-02 agent run against the actual code — only items confirmed working in code are ticked; "(partial)" notes flag items whose config knobs exist but whose consumers are unwired.*
