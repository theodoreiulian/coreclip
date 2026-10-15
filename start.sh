#!/bin/bash
set -e

# ── CoreClip — One-command launcher ──
# Usage: ./start.sh
# Stop:  Ctrl+C (kills everything cleanly)

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs"
PIDS=()

mkdir -p "$LOG_DIR"

# ── PATH repair ──
# Launched from a terminal this script inherits the user's full PATH. Launched
# by the Electron shell from Finder it inherits only /usr/bin:/bin:/usr/sbin:/sbin
# — no Homebrew, no nvm — so ffmpeg, redis-server and node all vanish. Put the
# usual prefixes back so both entry points behave identically.
# ffmpeg-full comes first on purpose: it is the Homebrew build with libass,
# which caption burning requires.
prepend_path() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) [ -d "$1" ] && PATH="$1:$PATH" ;;
    esac
}
for d in /opt/local/bin /usr/local/bin /opt/homebrew/bin \
         /usr/local/opt/ffmpeg-full/bin /opt/homebrew/opt/ffmpeg-full/bin \
         "$HOME/.volta/bin" "$HOME/.local/bin"; do
    prepend_path "$d"
done
# nvm installs node under a versioned directory and is activated by a shell
# profile this non-interactive script never sources; take the newest install.
if ! command -v node &>/dev/null && [ -d "$HOME/.nvm/versions/node" ]; then
    NVM_LATEST="$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)"
    [ -n "$NVM_LATEST" ] && prepend_path "$HOME/.nvm/versions/node/$NVM_LATEST/bin"
fi
export PATH

# Force UTF-8 for every Python child. Their stdout is redirected into logs/,
# and a redirected stream falls back to the locale encoding — which drops or
# crashes on the non-ASCII video titles and hashtags the pipeline logs.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Kill a process and everything it spawned, depth-first so children die before
# the parent can reap or re-spawn them.
#
# Signalling only the PIDs we launched is not enough: each service is a subshell
# whose real work happens two levels down (uvicorn, celery, node), and those in
# turn spawn ffmpeg and Playwright/Chrome. Killing the subshell alone orphans
# the whole subtree — an ffmpeg encode keeps a core pinned, and a stray Chrome
# keeps a TikTok profile locked. This walks the tree the way `taskkill /T` does
# on Windows, which is what start.ps1 relies on.
kill_tree() {
    local pid=$1
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child"
    done
    kill -TERM "$pid" 2>/dev/null || true
}

cleanup() {
    echo ""
    echo -e "${YELLOW}⏹  Shutting down all services...${NC}"
    for pid in "${PIDS[@]}"; do
        kill_tree "$pid"
    done

    # Give the trees a moment to exit on their own, then insist.
    sleep 1
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    # Last-resort sweep for anything a previous run orphaned, matched on this
    # checkout's own interpreter paths. Two properties matter here: it cannot
    # touch another project (the old `pkill -f vite` would kill any Vite on the
    # machine), and it cannot match this script — `bash .../start.sh` contains
    # the repo root, so a root-wide pattern would kill the cleanup mid-run.
    pkill -f "$BACKEND_DIR/venv/bin/celery" 2>/dev/null || true
    pkill -f "$BACKEND_DIR/venv/bin/uvicorn" 2>/dev/null || true
    pkill -f "$FRONTEND_DIR/node_modules" 2>/dev/null || true

    # Stop redis if we started it
    if [ "$REDIS_STARTED_BY_US" = "true" ]; then
        redis-cli shutdown nosave 2>/dev/null || true
    fi
    echo -e "${GREEN}✓  All services stopped.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# ── 1. Check prerequisites ──
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  CoreClip — Starting all services${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo -e "${RED}✗  ffmpeg not found. Install with: brew install ffmpeg${NC}"
    exit 1
fi
# Caption burning requires the libass-enabled 'ass' filter
if ! ffmpeg -filters 2>/dev/null | awk '$2 == "ass" {found=1} END {exit !found}'; then
    echo -e "${YELLOW}⚠  ffmpeg is missing libass support (required for captions).${NC}"
    echo -e "${YELLOW}   Install the full build:  brew install ffmpeg-full${NC}"
    echo -e "${YELLOW}   Then add it to your PATH, e.g.:${NC}"
    echo -e "${YELLOW}   export PATH=\"/opt/homebrew/opt/ffmpeg-full/bin:\$PATH\"   # Apple Silicon${NC}"
    echo -e "${YELLOW}   export PATH=\"/usr/local/opt/ffmpeg-full/bin:\$PATH\"     # Intel Mac${NC}"
fi
echo -e "${GREEN}✓${NC}  ffmpeg found"

# Check redis
if ! command -v redis-server &>/dev/null; then
    echo -e "${RED}✗  redis-server not found. Install with: brew install redis${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC}  redis-server found"

# Check node
if ! command -v node &>/dev/null; then
    echo -e "${RED}✗  node not found. Install Node.js 20+${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC}  node $(node --version) found"

# Check python venv
if [ ! -d "$BACKEND_DIR/venv" ]; then
    echo -e "${YELLOW}⚠  No Python venv found. Creating one...${NC}"
    python3 -m venv "$BACKEND_DIR/venv"
    source "$BACKEND_DIR/venv/bin/activate"
    # --no-cache-dir: pip would otherwise keep a second copy of every wheel
    # (torch alone is hundreds of MB) in the user cache. No `playwright install`
    # either — TikTok posting drives the installed Google Chrome, never the
    # bundled Chromium.
    pip install --no-cache-dir -r "$BACKEND_DIR/requirements.txt"
else
    source "$BACKEND_DIR/venv/bin/activate"
fi
echo -e "${GREEN}✓${NC}  Python venv activated"

# Check frontend node_modules
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo -e "${YELLOW}⚠  No node_modules found. Running npm install...${NC}"
    (cd "$FRONTEND_DIR" && npm install)
fi
echo -e "${GREEN}✓${NC}  Frontend dependencies ready"

# Check .env
if [ ! -f "$ROOT_DIR/.env" ]; then
    echo -e "${YELLOW}⚠  No .env file found. Copying from .env.example...${NC}"
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo -e "${YELLOW}   → Edit $ROOT_DIR/.env and add your GEMINI_API_KEY${NC}"
fi
echo -e "${GREEN}✓${NC}  .env loaded"

echo ""

# Set PYTHONPATH so Celery worker can find all backend modules
export PYTHONPATH="$BACKEND_DIR:$PYTHONPATH"

# ── 2. Start Redis ──
REDIS_STARTED_BY_US="false"
if redis-cli ping &>/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}  Redis already running"
else
    echo -e "${CYAN}▶${NC}  Starting Redis..."
    redis-server --daemonize yes --loglevel warning
    REDIS_STARTED_BY_US="true"
    sleep 1
    if redis-cli ping &>/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}  Redis started"
    else
        echo -e "${RED}✗  Failed to start Redis${NC}"
        exit 1
    fi
fi

# ── 3. Start Backend (FastAPI) ──
echo -e "${CYAN}▶${NC}  Starting FastAPI backend on :8000..."
(cd "$BACKEND_DIR" && source venv/bin/activate && \
    uvicorn main:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/backend.log" 2>&1) &
PIDS+=($!)

echo -e "${YELLOW}⏳${NC}  Waiting for Backend to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
while ! curl -s http://localhost:8000/api/health > /dev/null 2>&1; do
    sleep 1
    RETRY_COUNT=$((RETRY_COUNT+1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "${RED}✗  Backend failed to start within ${MAX_RETRIES}s${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓${NC}  Backend is live"

# ── 4. Start Celery Worker ──
echo -e "${CYAN}▶${NC}  Starting Celery worker..."
(cd "$BACKEND_DIR" && source venv/bin/activate && \
    celery -A tasks.celery_app worker --loglevel=info --concurrency=1 --pool=solo \
    -Q pipeline,posting,celery \
    > "$LOG_DIR/celery-worker.log" 2>&1) &
PIDS+=($!)

# ── 5. Start Celery Beat ──
echo -e "${CYAN}▶${NC}  Starting Celery beat scheduler..."
(cd "$BACKEND_DIR" && source venv/bin/activate && \
    celery -A tasks.celery_app beat --loglevel=info \
    > "$LOG_DIR/celery-beat.log" 2>&1) &
PIDS+=($!)

# ── 6. Start Frontend ──
echo -e "${CYAN}▶${NC}  Starting frontend on :5173..."
(cd "$FRONTEND_DIR" && npm run dev -- --host 0.0.0.0 \
    > "$LOG_DIR/frontend.log" 2>&1) &
PIDS+=($!)

echo -e "${YELLOW}⏳${NC}  Waiting for Frontend to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
while ! curl -s http://localhost:5173 > /dev/null 2>&1; do
    sleep 1
    RETRY_COUNT=$((RETRY_COUNT+1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "${RED}✗  Frontend failed to start within ${MAX_RETRIES}s${NC}"
        exit 1
    fi
done

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✓  All services running!${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${CYAN}Frontend:${NC}  http://localhost:5173"
echo -e "  ${CYAN}Backend:${NC}   http://localhost:8000"
echo -e "  ${CYAN}API docs:${NC}  http://localhost:8000/docs"
echo ""
echo -e "  ${CYAN}Logs:${NC}      $LOG_DIR/"
echo -e "             backend.log | celery-worker.log | celery-beat.log | frontend.log"
echo ""
echo -e "  ${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""

# Open browser — skipped when launched by the Electron desktop shell, which
# opens its own native window pointing at the same frontend.
if [ -z "$CORECLIP_NO_BROWSER" ] && command -v open &>/dev/null; then
    open "http://localhost:5173"
fi

# Signal readiness to a parent launcher (e.g. Electron) that watches stdout.
echo "CORECLIP_READY"

# Wait forever (until Ctrl+C)
wait
