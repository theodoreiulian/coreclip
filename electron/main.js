'use strict';

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

// ── Config ──
const ROOT_DIR = path.resolve(__dirname, '..'); // the coreclip/ repo root
const START_SCRIPT = path.join(ROOT_DIR, 'start.sh');
const FRONTEND_URL = 'http://localhost:5173';
const READY_TIMEOUT_MS = 10 * 60 * 1000; // first run installs deps + downloads models
const POLL_INTERVAL_MS = 800;

let mainWindow = null;
let stackProc = null;
let weStartedTheStack = false;
let isQuitting = false;

// ── Helpers ──

// Resolve to true if the Vite frontend answers an HTTP request, false otherwise.
function pingFrontend() {
  return new Promise((resolve) => {
    const req = http.get(FRONTEND_URL, { timeout: 1500 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

// Push a status line into the loading screen (no-op if the window is gone or
// has already navigated to the app).
function setLoadingStatus(text) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const safe = JSON.stringify(String(text));
  mainWindow.webContents
    .executeJavaScript(`window.setStatus && window.setStatus(${safe});`)
    .catch(() => {});
}

// Spawn start.sh as its own process group so we can tear the whole stack
// (Redis, FastAPI, Celery worker/beat, Vite) down with one signal on quit.
function startStack() {
  stackProc = spawn('bash', [START_SCRIPT], {
    cwd: ROOT_DIR,
    detached: true, // new process group => killable as -pid
    env: { ...process.env, CORECLIP_NO_BROWSER: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  weStartedTheStack = true;

  const onLine = (buf) => {
    const text = buf.toString();
    process.stdout.write(`[stack] ${text}`);
    // Surface the most recent non-empty line on the loading screen.
    const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.length) setLoadingStatus(lines[lines.length - 1].replace(/\x1b\[[0-9;]*m/g, ''));
  };
  stackProc.stdout.on('data', onLine);
  stackProc.stderr.on('data', onLine);

  stackProc.on('exit', (code, signal) => {
    process.stdout.write(`[stack] exited (code=${code}, signal=${signal})\n`);
    stackProc = null;
    // If the stack dies on its own before the window loaded the app, surface it.
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed() &&
        mainWindow.webContents.getURL().startsWith('http') === false) {
      setLoadingStatus(`Backend exited unexpectedly (code ${code}). Check logs/ for details.`);
    }
  });
}

// Tear down the whole process group started by startStack(). start.sh traps
// SIGTERM and runs its own cleanup (kills celery/uvicorn, shuts down redis if
// it started it).
function stopStack() {
  if (!stackProc || !weStartedTheStack) return;
  try {
    process.kill(-stackProc.pid, 'SIGTERM');
  } catch (_) {
    /* group already gone */
  }
}

// Poll until the frontend responds (or we time out).
async function waitForFrontend() {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await pingFrontend()) return true;
    if (isQuitting) return false;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  return false;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#0b0b0f',
    title: 'CoreClip',
    show: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // Open target="_blank" / external links in the system browser, not new
  // Electron windows (e.g. OAuth/help links).
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://localhost')) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
}

async function boot() {
  createWindow();

  // If the stack is already up (user ran ./start.sh manually), just attach.
  const alreadyUp = await pingFrontend();
  if (alreadyUp) {
    setLoadingStatus('CoreClip is already running — connecting…');
  } else {
    setLoadingStatus('Starting CoreClip services…');
    startStack();
  }

  const ready = await waitForFrontend();
  if (isQuitting || !mainWindow || mainWindow.isDestroyed()) return;

  if (ready) {
    mainWindow.loadURL(FRONTEND_URL);
  } else {
    setLoadingStatus('Timed out waiting for the backend. Check logs/ for details.');
    dialog.showErrorBox(
      'CoreClip failed to start',
      'The CoreClip backend did not become ready in time.\n\n' +
        'Check the log files under the logs/ directory for details, or run ./start.sh ' +
        'in a terminal to see the full output.'
    );
  }
}

// ── App lifecycle ──

app.whenReady().then(boot);

app.on('window-all-closed', () => {
  // Quitting tears down the stack in before-quit; here we just trigger quit.
  app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) boot();
});

// Ensure the backend stack is stopped before the process exits. We delay the
// final quit briefly so start.sh's SIGTERM cleanup can run.
app.on('before-quit', (e) => {
  if (isQuitting || !weStartedTheStack || !stackProc) return;
  e.preventDefault();
  isQuitting = true;
  stopStack();
  setTimeout(() => app.exit(0), 2500);
});
