'use strict';

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn, execFile } = require('child_process');
const http = require('http');
const path = require('path');

// ── Config ──
const IS_WINDOWS = process.platform === 'win32';
const ROOT_DIR = path.resolve(__dirname, '..'); // the coreclip/ repo root
const FRONTEND_URL = 'http://localhost:5173';
const READY_TIMEOUT_MS = 10 * 60 * 1000; // first run installs deps + downloads models
const POLL_INTERVAL_MS = 800;

// Each platform has its own launcher for the same five-process stack. Neither
// is invoked through a shell — we name the interpreter explicitly so the app
// does not depend on file associations or on the .sh bit being executable.
function launcherCommand() {
  if (IS_WINDOWS) {
    return {
      command: 'powershell.exe',
      args: [
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', path.join(ROOT_DIR, 'start.ps1'),
      ],
    };
  }
  return { command: 'bash', args: [path.join(ROOT_DIR, 'start.sh')] };
}

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

// Spawn the launcher so that the whole stack (Redis, FastAPI, Celery
// worker/beat, Vite) can be torn down as a unit on quit. On POSIX that means a
// new process group, killable as -pid; Windows has no process groups to signal,
// so there we keep the default parent/child link and let taskkill /T walk it.
function startStack() {
  const { command, args } = launcherCommand();
  stackProc = spawn(command, args, {
    cwd: ROOT_DIR,
    detached: !IS_WINDOWS,
    windowsHide: true, // no console flash from the PowerShell launcher
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

// Tear down everything startStack() brought up.
//
// POSIX: signal the process group. start.sh traps SIGTERM and runs its own
// cleanup (kills celery/uvicorn, shuts down redis if it started it).
//
// Windows: there is no SIGTERM, and killing powershell.exe alone would orphan
// every service it launched. taskkill /T walks the process tree instead, which
// reaches the cmd.exe wrappers and the python/node processes underneath them.
function stopStack() {
  if (!stackProc || !weStartedTheStack) return;
  const pid = stackProc.pid;
  try {
    if (IS_WINDOWS) {
      execFile('taskkill.exe', ['/PID', String(pid), '/T', '/F'], () => {});
    } else {
      process.kill(-pid, 'SIGTERM');
    }
  } catch (_) {
    /* already gone */
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
    const manualCommand = IS_WINDOWS ? '.\\start.cmd' : './start.sh';
    setLoadingStatus('Timed out waiting for the backend. Check logs/ for details.');
    dialog.showErrorBox(
      'CoreClip failed to start',
      'The CoreClip backend did not become ready in time.\n\n' +
        'Check the log files under the logs/ directory for details, or run ' +
        `${manualCommand} in a terminal to see the full output.`
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
