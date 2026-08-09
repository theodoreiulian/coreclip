# CoreClip

CoreClip is a fully automated, agentic pipeline for ingesting YouTube videos, analyzing them for viral moments using Google Gemini 2.5, extracting short-form vertical clips with robust dynamic text-animations, and automatically distributing them to major social networks (TikTok, Instagram, YouTube Shorts, and Snapchat).

## Architecture
```
[ YouTube ] -> [ yt-dlp / FFmpeg ] -> [ Whisper ] -> [ Gemini ] -> [ OpenCV / FFmpeg ] -> [ API / Web UI ]
```

## Prerequisites
- Docker and Docker Compose
- Or for local dev: Python 3.11, Node 20, Redis, FFmpeg

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

If you already have the stack running via `./start.sh`, `npm start` detects it
and simply attaches a window to the existing instance instead of starting a
second copy.

To build a distributable `.app` bundle of the shell, run `npm run dist:mac`.
Note this packages only the Electron launcher — the backend still requires the
local prerequisites below (Python, Redis, FFmpeg) to be installed on the
machine.

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
- **Clips not extracting**: Ensure `ffmpeg` is available on the path and `.env` paths are correct.
- **Gemini failing**: Ensure your API key has quota available.
