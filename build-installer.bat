@echo off
rem Build the StlArchivist Windows installer (the desktop app with the server and
rem the web client) without any questions: double-click it, or run it from a
rem scheduled task with --no-pause.
rem
rem   build-installer.bat             build, then wait for a key
rem   build-installer.bat --no-pause  build and exit (exit code 0 on success)
rem
rem Needs Rust (rustup) and Node.js 24. Node.js is taken from PATH, else from
rem NODE_HOME, else from its usual install folders. The Tauri CLI is installed
rem (pinned version) when missing. The installer lands in
rem rust\target\release\bundle\nsis.

setlocal EnableExtensions
set "TAURI_CLI_VERSION=2.12.3"
set "PAUSE_AT_END=1"
if /i "%~1"=="--no-pause" set "PAUSE_AT_END="
cd /d "%~dp0"

rem -- Rust
where cargo >nul 2>&1 || if exist "%USERPROFILE%\.cargo\bin\cargo.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
where cargo >nul 2>&1 || (echo Rust was not found: install it from https://rustup.rs & goto :failed)

rem -- Node.js
where npm >nul 2>&1 && goto :node_found
for %%D in ("%NODE_HOME%" "%ProgramFiles%\nodejs" "%LOCALAPPDATA%\Programs\nodejs") do (
    if exist "%%~D\npm.cmd" (
        set "PATH=%%~D;%PATH%"
        goto :node_found
    )
)
echo Node.js was not found: install Node.js 24, or set NODE_HOME to its folder.
goto :failed
:node_found

rem -- Tauri CLI (cargo tauri), the version the build is made with
cargo tauri --version 2>nul | findstr /c:"%TAURI_CLI_VERSION%" >nul
if errorlevel 1 (
    echo == Installing the Tauri CLI %TAURI_CLI_VERSION% (once^)
    cargo install tauri-cli --version %TAURI_CLI_VERSION% --locked || goto :failed
)

rem -- Build: web client, server, desktop app, installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -Installer || goto :failed

echo.
echo Installer ready (newest first):
for /f "delims=" %%F in ('dir /b /o-d "rust\target\release\bundle\nsis\*.exe" 2^>nul') do (
    echo   %~dp0rust\target\release\bundle\nsis\%%F
    goto :done
)
:done
if defined PAUSE_AT_END pause
exit /b 0

:failed
echo.
echo The build FAILED (see the messages above).
if defined PAUSE_AT_END pause
exit /b 1
