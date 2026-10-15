# -- CoreClip - One-command launcher (Windows) --
# Usage: .\start.ps1     (or double-click start.cmd)
# Stop:  Ctrl+C (kills everything cleanly)
#
# This is the Windows counterpart of start.sh and boots the same five
# processes: Redis, FastAPI (:8000), a Celery worker, Celery beat, and the Vite
# dev server (:5173), tailing each one's output into .\logs\.
#
# Written for Windows PowerShell 5.1 (shipped with Windows 10/11) so it runs
# without installing PowerShell 7.

$ErrorActionPreference = 'Stop'

$RootDir     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BackendDir  = Join-Path $RootDir 'backend'
$FrontendDir = Join-Path $RootDir 'frontend'
$LogDir      = Join-Path $RootDir 'logs'
$VenvDir     = Join-Path $BackendDir 'venv'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'

$script:Children = @()
$script:RedisStartedByUs = $false
$script:CleanupDone = $false

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Celery, uvicorn and yt-dlp all emit non-ASCII (video titles, hashtags, status
# glyphs). Their stdout is redirected to a file here, and a redirected stream
# defaults to the ANSI codepage on Windows, which raises UnicodeEncodeError
# mid-job. Force UTF-8 for every Python child and for this console.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# -- Output helpers -------------------------------------------------------

function Write-Ok    ($msg) { Write-Host "[ok] " -ForegroundColor Green  -NoNewline; Write-Host $msg }
function Write-Step  ($msg) { Write-Host "[>]  " -ForegroundColor Cyan   -NoNewline; Write-Host $msg }
function Write-Warn2 ($msg) { Write-Host "[!]  " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Err   ($msg) { Write-Host "[x]  " -ForegroundColor Red    -NoNewline; Write-Host $msg }
function Write-Rule  ()     { Write-Host ('-' * 44) -ForegroundColor Cyan }

function Fail ($msg) {
    Write-Err $msg
    Invoke-Cleanup
    exit 1
}

# -- Process management ---------------------------------------------------

# Start a long-running service, combining stdout+stderr into one log file for
# parity with start.sh. Everything is routed through cmd.exe because
# Start-Process cannot merge the two streams into a single file on its own.
function Start-Service-Process {
    param(
        [string]   $Name,
        [string]   $WorkingDirectory,
        [string]   $Executable,
        [string[]] $Arguments,
        [string]   $LogFile
    )
    # Both the executable and the log path can contain spaces, so both stay
    # quoted. cmd.exe strips exactly one outer quote pair after /c when the
    # string holds more than two quotes, which is what makes the inner quoting
    # survive intact.
    $inner   = '"{0}" {1} > "{2}" 2>&1' -f $Executable, ($Arguments -join ' '), $LogFile
    $argLine = '/c "' + $inner + '"'

    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $argLine `
                          -WorkingDirectory $WorkingDirectory `
                          -PassThru -WindowStyle Hidden
    $script:Children += [pscustomobject]@{ Name = $Name; Process = $proc }
    return $proc
}

function Stop-Tree ($processId) {
    # /T takes the child processes with it - the cmd.exe wrapper is the parent
    # of the actual python/node process, so killing it alone would orphan them.
    & taskkill.exe /PID $processId /T /F > $null 2>$null
}

function Invoke-Cleanup {
    if ($script:CleanupDone) { return }
    $script:CleanupDone = $true

    Write-Host ''
    Write-Warn2 'Shutting down all services...'
    foreach ($child in $script:Children) {
        if ($child.Process -and -not $child.Process.HasExited) {
            Stop-Tree $child.Process.Id
        }
    }
    if ($script:RedisStartedByUs) {
        $redisCli = Find-Tool 'redis-cli'
        if ($redisCli) { & $redisCli shutdown nosave > $null 2>$null }
    }
    Write-Ok 'All services stopped.'
}

# -- Discovery helpers ----------------------------------------------------

# Look for a CLI on PATH, then in the locations winget / Chocolatey / Scoop and
# the common manual installs use. This mirrors backend/utils/platform_compat.py:
# PATH alone is not enough, because a desktop-launched process does not inherit
# the user's PATH customisations.
function Find-Tool ([string] $Name) {
    $onPath = Get-Command $Name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $exe = "$Name.exe"
    $patterns = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\$exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\*\*\bin\$exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\*\*\$exe"),
        (Join-Path $env:ProgramData  "chocolatey\bin\$exe"),
        (Join-Path $env:USERPROFILE  "scoop\shims\$exe"),
        (Join-Path $env:ProgramData  "scoop\shims\$exe"),
        "C:\$Name\bin\$exe",
        (Join-Path $env:ProgramFiles "$Name\bin\$exe"),
        (Join-Path $env:ProgramFiles "$Name\$exe")
    )
    foreach ($pattern in $patterns) {
        $hit = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Test-TcpPort ([int] $Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(1000, $false)) { return $false }
        $client.EndConnect($connect)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-HttpUp ([string] $Url) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        # A 4xx/5xx still proves something is listening and serving HTTP.
        return ($null -ne $_.Exception.Response)
    }
}

function Wait-For ([string] $Label, [scriptblock] $Probe, [int] $MaxSeconds = 60) {
    Write-Host "[..] " -ForegroundColor Yellow -NoNewline
    Write-Host "Waiting for $Label to be ready..."
    for ($i = 0; $i -lt $MaxSeconds; $i++) {
        if (& $Probe) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# -- 1. Prerequisites -----------------------------------------------------

try {

Write-Rule
Write-Host '  CoreClip - Starting all services' -ForegroundColor Cyan
Write-Rule
Write-Host ''

# FFmpeg. The backend resolves the binary itself (including builds that are not
# on PATH), so this is an early, friendly check rather than the source of truth.
$ffmpeg = Find-Tool 'ffmpeg'
if (-not $ffmpeg) {
    Write-Err 'ffmpeg not found. Install it with one of:'
    Write-Host '     winget install Gyan.FFmpeg'
    Write-Host '     choco install ffmpeg-full'
    Write-Host '     scoop install ffmpeg'
    Write-Host '   Then open a new terminal and re-run this script.'
    exit 1
}
Write-Ok "ffmpeg found ($ffmpeg)"

# Node
$node = Find-Tool 'node'
if (-not $node) {
    Write-Err 'node not found. Install Node.js 20+ from https://nodejs.org'
    Write-Host '     winget install OpenJS.NodeJS.LTS'
    exit 1
}
Write-Ok "node $(& $node --version) found"

# Python venv
if (-not (Test-Path $VenvPython)) {
    Write-Warn2 'No Python venv found. Creating one...'

    # The py launcher is the reliable way to pick a specific Python on Windows;
    # a bare `python` is often the Microsoft Store shim, which cannot create a
    # usable venv. Prefer the versions with the widest wheel coverage for this
    # dependency set (mediapipe, torch, faster-whisper).
    $pythonExe  = $null
    $pythonArgs = @()
    $versionProbe = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'

    if (Get-Command 'py' -ErrorAction SilentlyContinue) {
        foreach ($ver in @('-3.12', '-3.11', '-3.13', '-3')) {
            & py $ver -c $versionProbe > $null 2>$null
            if ($LASTEXITCODE -eq 0) {
                $pythonExe  = 'py'
                $pythonArgs = @($ver)
                break
            }
        }
    }
    if (-not $pythonExe) {
        $candidate = Find-Tool 'python'
        if ($candidate) {
            & $candidate -c $versionProbe > $null 2>$null
            if ($LASTEXITCODE -eq 0) { $pythonExe = $candidate }
        }
    }
    if (-not $pythonExe) {
        Fail 'Python 3.11+ not found. Install it: winget install Python.Python.3.12'
    }

    & $pythonExe @pythonArgs -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { Fail 'Failed to create the Python venv.' }

    Write-Step 'Installing Python dependencies (this takes a few minutes)...'
    # --no-cache-dir: pip would otherwise keep a second copy of every wheel
    # (torch alone is hundreds of MB) in the user cache. No `playwright install`
    # either - TikTok posting drives the installed Google Chrome, never the
    # bundled Chromium.
    & $VenvPython -m pip install --no-cache-dir --upgrade pip
    & $VenvPython -m pip install --no-cache-dir -r (Join-Path $BackendDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed. See the output above.' }
}
Write-Ok 'Python venv ready'

# Frontend dependencies
if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    Write-Warn2 'No node_modules found. Running npm install...'
    Push-Location $FrontendDir
    try {
        & cmd.exe /c 'npm install'
        if ($LASTEXITCODE -ne 0) { Fail 'npm install failed. See the output above.' }
    } finally { Pop-Location }
}
Write-Ok 'Frontend dependencies ready'

# .env
$envFile = Join-Path $RootDir '.env'
if (-not (Test-Path $envFile)) {
    Write-Warn2 'No .env file found. Copying from .env.example...'
    Copy-Item (Join-Path $RootDir '.env.example') $envFile
    Write-Host "     -> Edit $envFile and add your GEMINI_API_KEY"
}
Write-Ok '.env loaded'
Write-Host ''

# Celery resolves tasks.celery_app relative to backend/
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$BackendDir;$env:PYTHONPATH" } else { $BackendDir }

# -- 2. Redis -------------------------------------------------------------

if (Test-TcpPort 6379) {
    Write-Ok 'Redis already running'
} else {
    # Native Windows Redis builds, Memurai (a Windows-native Redis-compatible
    # server) and a Docker container are all equally good brokers here; take
    # whichever is installed.
    $redisServer = Find-Tool 'redis-server'
    $memurai = $null
    if (-not $redisServer) {
        $memurai = Find-Tool 'memurai'
        if (-not $memurai) {
            $memuraiPath = Join-Path $env:ProgramFiles 'Memurai\memurai.exe'
            if (Test-Path $memuraiPath) { $memurai = $memuraiPath }
        }
    }

    $launched = $false
    if ($redisServer -or $memurai) {
        $binary = if ($redisServer) { $redisServer } else { $memurai }
        Write-Step "Starting Redis ($binary)..."
        $proc = Start-Process -FilePath $binary -ArgumentList '--port 6379 --loglevel warning' `
                              -PassThru -WindowStyle Hidden
        $script:Children += [pscustomobject]@{ Name = 'redis'; Process = $proc }
        $script:RedisStartedByUs = $true
        $launched = $true
    } elseif ($env:CORECLIP_REDIS_DOCKER -and (Find-Tool 'docker')) {
        Write-Step 'Starting Redis in Docker...'
        & docker run -d --rm --name coreclip-redis -p 6379:6379 redis:7-alpine | Out-Null
        $launched = $true
    }

    # Nothing to wait for if we found nothing to launch - go straight to the
    # install instructions rather than burning 15s polling a dead port.
    if (-not $launched -or -not (Wait-For 'Redis' { Test-TcpPort 6379 } 15)) {
        Write-Err 'Redis is not running and could not be started. Install one of:'
        Write-Host '     winget install Memurai.MemuraiDeveloper   (native Windows service)'
        Write-Host '     choco install redis-64'
        Write-Host '     scoop install redis'
        Write-Host '   Or run it in Docker and re-run this script:'
        Write-Host '     docker run -d -p 6379:6379 redis:7-alpine'
        Fail 'Cannot continue without Redis.'
    }
    Write-Ok 'Redis started'
}

# -- 3. Backend (FastAPI) -------------------------------------------------

Write-Step 'Starting FastAPI backend on :8000...'
Start-Service-Process -Name 'backend' -WorkingDirectory $BackendDir `
    -Executable $VenvPython `
    -Arguments @('-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000') `
    -LogFile (Join-Path $LogDir 'backend.log') | Out-Null

if (-not (Wait-For 'Backend' { Test-HttpUp 'http://localhost:8000/api/health' } 60)) {
    Fail 'Backend failed to start within 60s. See logs\backend.log'
}
Write-Ok 'Backend is live'

# -- 4. Celery worker -----------------------------------------------------
# --pool=solo is mandatory on Windows: Celery's default prefork pool relies on
# fork(), which Windows does not have. It also matches the macOS launcher,
# where solo is chosen so the Whisper model loads once per worker process.

Write-Step 'Starting Celery worker...'
Start-Service-Process -Name 'celery-worker' -WorkingDirectory $BackendDir `
    -Executable $VenvPython `
    -Arguments @('-m', 'celery', '-A', 'tasks.celery_app', 'worker', '--loglevel=info',
                 '--concurrency=1', '--pool=solo', '-Q', 'pipeline,posting,celery') `
    -LogFile (Join-Path $LogDir 'celery-worker.log') | Out-Null

# -- 5. Celery beat -------------------------------------------------------

Write-Step 'Starting Celery beat scheduler...'
Start-Service-Process -Name 'celery-beat' -WorkingDirectory $BackendDir `
    -Executable $VenvPython `
    -Arguments @('-m', 'celery', '-A', 'tasks.celery_app', 'beat', '--loglevel=info') `
    -LogFile (Join-Path $LogDir 'celery-beat.log') | Out-Null

# -- 6. Frontend ----------------------------------------------------------

Write-Step 'Starting frontend on :5173...'
Start-Service-Process -Name 'frontend' -WorkingDirectory $FrontendDir `
    -Executable 'npm.cmd' `
    -Arguments @('run', 'dev', '--', '--host', '0.0.0.0') `
    -LogFile (Join-Path $LogDir 'frontend.log') | Out-Null

if (-not (Wait-For 'Frontend' { Test-HttpUp 'http://localhost:5173' } 60)) {
    Fail 'Frontend failed to start within 60s. See logs\frontend.log'
}

# -- 7. Ready -------------------------------------------------------------

Write-Host ''
Write-Rule
Write-Host '  All services running!' -ForegroundColor Green
Write-Rule
Write-Host ''
Write-Host '  Frontend:  http://localhost:5173'
Write-Host '  Backend:   http://localhost:8000'
Write-Host '  API docs:  http://localhost:8000/docs'
Write-Host ''
Write-Host "  Logs:      $LogDir\"
Write-Host '             backend.log | celery-worker.log | celery-beat.log | frontend.log'
Write-Host ''
Write-Host '  Press Ctrl+C to stop all services' -ForegroundColor Yellow
Write-Host ''

# Skipped when launched by the Electron desktop shell, which opens its own
# native window pointing at the same frontend.
if (-not $env:CORECLIP_NO_BROWSER) {
    Start-Process 'http://localhost:5173' | Out-Null
}

# Signal readiness to a parent launcher (e.g. Electron) that watches stdout.
Write-Host 'CORECLIP_READY'

# -- 8. Supervise until interrupted ---------------------------------------

while ($true) {
    Start-Sleep -Seconds 2
    foreach ($child in $script:Children) {
        if ($child.Process -and $child.Process.HasExited) {
            Write-Err "$($child.Name) exited unexpectedly (code $($child.Process.ExitCode)). See logs\"
            Fail 'A service stopped; shutting the rest down.'
        }
    }
}

} finally {
    Invoke-Cleanup
}
