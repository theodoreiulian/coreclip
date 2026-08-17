# CoreClip

CoreClip is a fully automated, agentic pipeline for ingesting YouTube videos, analyzing them for viral moments using Google Gemini 2.5, extracting short-form vertical clips with robust dynamic text-animations, and automatically distributing them to major social networks (TikTok, Instagram, YouTube Shorts, and Snapchat).

## Architecture
```
[ YouTube ] -> [ yt-dlp / FFmpeg ] -> [ Whisper ] -> [ Gemini ] -> [ OpenCV / FFmpeg ] -> [ API / Web UI ]
```

CoreClip runs on **macOS** and **Windows**. Each platform has its own launcher
that boots the identical five-process stack (Redis, FastAPI, a Celery worker,
Celery beat, Vite); everything above the launcher is shared.

## Prerequisites

Python 3.11+, Node 20+, Redis and an FFmpeg build **compiled with libass**
(caption burning needs the `ass` filter). The launcher checks for all of these
before starting anything and tells you what is missing.

### macOS

```bash
brew install ffmpeg-full redis node python@3.12
```

`ffmpeg-full` is the Homebrew build that includes libass. It is keg-only, so it
does not land on your `PATH` automatically — CoreClip finds it anyway, in
`/opt/homebrew/opt/ffmpeg-full/bin` (Apple Silicon) or
`/usr/local/opt/ffmpeg-full/bin` (Intel).

### Windows

```powershell
winget install Gyan.FFmpeg
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12
winget install Memurai.MemuraiDeveloper   # Redis-compatible Windows service
```

Notes specific to Windows:

- **Redis** has no official Windows build. Memurai is a native Redis-compatible
  Windows service and is what the launcher looks for first; a `redis-server.exe`
  from Chocolatey/Scoop, or Redis in Docker (`docker run -d -p 6379:6379
  redis:7-alpine`), work equally well. The launcher accepts whichever is
  present, and simply attaches if something already answers on port 6379.
- **FFmpeg** installed via winget/Chocolatey/Scoop is found automatically even
  when it is not on your `PATH`.
- The Celery worker runs with `--pool=solo`, because Celery's default prefork
  pool needs `fork()`, which Windows does not have.
- **TikTok posting** drives your real Google Chrome, so Chrome must be
  installed. A per-user install (under `%LOCALAPPDATA%`) is detected as well as
  a machine-wide one.

## Quick Start

```bash
# macOS
./start.sh
```

```powershell
# Windows (or just double-click start.cmd)
.\start.cmd
```

The launcher creates `backend/venv`, installs Python and npm dependencies on
first run, starts every service, and opens `http://localhost:5173`. Press
Ctrl+C to stop everything. Logs stream into `./logs/`.

Before the first run, copy `.env.example` to `.env` (the launcher does this for
you) and add a `.ttf` font such as Montserrat-ExtraBold.ttf to `./fonts/`. The
Gemini API key can be set in `.env` or from the in-app Settings page.

## Quick Start (Docker)
1. Clone this repository.
2. Copy `.env.example` to `.env` and add your `GEMINI_API_KEY`.
3. Add a `.ttf` font (e.g. Montserrat-ExtraBold.ttf) to `./fonts/`.
4. Run `docker-compose up --build`.
5. Open your browser to `http://localhost:5173`.

## Desktop App (Electron)

CoreClip can run as a native desktop application instead of a browser tab. The
Electron shell launches the entire stack for you (Redis, FastAPI, Celery, and
the Vite frontend) and opens CoreClip in its own window — no browser required.

```bash
# from the repo root (coreclip/)
npm install        # one-time: installs Electron
npm start          # launches the stack + opens the CoreClip window
```

A loading screen is shown while services start; the window navigates to the app
once the frontend is ready. Quitting the app (Cmd+Q / closing the window) tears
the whole stack back down. The first launch can take a few minutes while Python
dependencies and models are installed.

If you already have the stack running via `./start.sh` (macOS) or `.\start.cmd`
(Windows), `npm start` detects it and simply attaches a window to the existing
instance instead of starting a second copy.

To build a distributable bundle of the shell, run `npm run dist:mac` or
`npm run dist:win`. Note this packages only the Electron launcher — the backend
still requires the local prerequisites above (Python, Redis, FFmpeg) to be
installed on the machine.

## Environment Variables
- `GEMINI_API_KEY`: Required. Your Google Gemini API Key.
- `CLEANUP_SOURCE_AFTER_CLIPPING`: (true/false) Whether to delete the large source video file after clips have been successfully extracted.
- `CLEANUP_INTERMEDIATES`: (true/false) Whether to delete intermediate vertical and raw clip renders to save disk space.
- `FRONTEND_URL`: URL of the frontend (defaults to localhost:5173).

## Platform Setup
- **YouTube**: Use Google Cloud Console to set up OAuth for YouTube Data API v3.
- **TikTok**: Sign up for TikTok For Developers.
- **Instagram**: Set up a Meta App with the Instagram Graph API.

## Troubleshooting
- **Clips not extracting**: Ensure `ffmpeg` is installed and `.env` paths are correct. Run `python backend/test_platform.py` — it reports which ffmpeg binary CoreClip resolved and whether it has libass.
- **Captions not burning in**: Your FFmpeg lacks libass. macOS: `brew install ffmpeg-full`. Windows: use the Gyan.FFmpeg (winget) or `ffmpeg-full` (Chocolatey) build.
- **Gemini failing**: Ensure your API key has quota available.
- **Windows: "cannot be loaded because running scripts is disabled"**: Launch via `start.cmd` rather than `start.ps1` — it bypasses the execution policy for that one invocation without changing your machine settings.
- **Windows: Celery worker exits immediately**: It must run with `--pool=solo`; the bundled launcher already does this.

## Verifying a platform

`backend/test_platform.py` asserts the cross-platform invariants — binary
discovery, filtergraph escaping, link fallbacks, log encoding, launcher
integrity — and is the fastest way to confirm a machine is set up correctly:

```bash
cd backend && python test_platform.py
```

The Windows support was written and reviewed on macOS, so `start.ps1` has not
yet been run on a real Windows host. `WINDOWS_TESTING.md` is a P0–P3 punchlist
for closing that gap — what to run, why each item matters, and what a failure
indicates.
