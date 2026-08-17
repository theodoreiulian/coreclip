@echo off
REM -- CoreClip launcher shim (Windows) --
REM Double-clickable entry point: runs start.ps1 without requiring the user to
REM change their PowerShell execution policy machine-wide (-ExecutionPolicy
REM Bypass applies to this one invocation only).

setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

REM Keep the window open on failure so the error is readable when launched by
REM double-click rather than from an existing terminal.
if not "%EXIT_CODE%"=="0" (
    echo.
    echo CoreClip exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
