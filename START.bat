@echo off
REM START.bat - one click: Luna VRM + chat + Discord if configured
chcp 65001 >nul
title Luna
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "RIKO_CHARACTER=luna"
set "RIKO_VOICE_SOURCE=both"

echo.
echo   ============================================
echo     LUNA - starting everything
echo   ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   First run: double-click setup.bat, wait for it to finish, then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" copy /y ".env.example" ".env" >nul
)

echo   Clearing old server / web page if still running...
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8100 "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do taskkill /PID %%A /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Riko Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Riko Web Page*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Discord Bridge*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Web Page*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Desktop*" /T /F >nul 2>&1
REM Kill orphaned chat agents / API servers from earlier runs (they talk over each other).
powershell -NoProfile -Command "$root=(Resolve-Path '%~dp0').Path; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) -and ($_.CommandLine -match 'server\.chat_terminal|server\.server|transcribe_worker|electron\.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
REM ping-sleep works when stdin is redirected (timeout does not)
ping -n 2 127.0.0.1 >nul

powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 2).StatusCode | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo   WARNING: Ollama is not responding on :11434
    echo            Start Ollama, then talk to her. Continuing anyway...
    echo.
) else (
    echo   Ollama: OK
)

set "DISCORD_ON=0"
if exist "discord-bridge\.env" (
    if not exist "discord-bridge\node_modules\" (
        echo   Installing discord-bridge packages...
        pushd discord-bridge
        call npm install
        popd
    )
    echo   Discord bridge: starting...
    start "Luna Discord Bridge" /MIN /D "%~dp0discord-bridge" cmd /c "npm start"
    set "DISCORD_ON=1"
) else (
    echo   Discord bridge: skipped - no discord-bridge\.env
)

set "SERVER_PORT="
for /f "tokens=2 delims==" %%P in ('findstr /b /c:"SERVER_PORT: int =" "server\config.py"') do set "SERVER_PORT=%%P"
set "SERVER_PORT=%SERVER_PORT: =%"
if not defined SERVER_PORT set "SERVER_PORT=8100"

echo   Character: %RIKO_CHARACTER%
echo   Voice in:  %RIKO_VOICE_SOURCE%
echo.

if not exist "client\node_modules\vite\package.json" (
    echo   Installing web page packages ^(first time^)...
    pushd client
    call npm install
    if errorlevel 1 (
        echo   ERROR: npm install failed in the client folder.
        popd
        pause
        exit /b 1
    )
    popd
)

start "Luna Server" /MIN /D "%~dp0" cmd /c "set RIKO_CHARACTER=%RIKO_CHARACTER%&& set RIKO_VOICE_SOURCE=%RIKO_VOICE_SOURCE%&& .venv\Scripts\python.exe -m server.server"
start "Luna Web Page" /MIN /D "%~dp0client" cmd /c "npm run dev"

echo   Waiting for the web page...
set "READY="
for /l %%I in (1,1,60) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173 -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set "READY=1"
        goto ready
    )
    if %%I==15 echo   still waiting for Vite on :5173 ...
    if %%I==30 echo   still waiting - check the minimized Luna Web Page window for errors
    ping -n 2 127.0.0.1 >nul
)
:ready
if not defined READY (
    echo   Web page did not come up. Check the minimized Luna Web Page window.
    echo   Tip: open a terminal in the client folder and run: npm run dev
    goto cleanup
)

for /l %%I in (1,1,20) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%SERVER_PORT%/health -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 goto api_ok
    ping -n 2 127.0.0.1 >nul
)
:api_ok

if not exist "desktop\node_modules\electron" (
    echo   Installing Electron desktop window ^(one time, ~200 MB^)...
    pushd desktop
    call npm install
    popd
    if not exist "desktop\node_modules\electron" (
        echo   WARNING: Electron install failed - opening the browser instead.
        start "" "http://127.0.0.1:5173"
        goto after_window
    )
)

echo   Desktop window: starting...
start "Luna Desktop" /MIN /D "%~dp0desktop" cmd /c "npx electron ."

:after_window
echo.
echo   Desktop avatar opened. Click her once so audio can play.
echo   Drag the top edge to move. Ctrl+Shift+Q closes the avatar window.
echo   Speak into the mic, or type at http://127.0.0.1:5173 if you want the full page.
if "%DISCORD_ON%"=="1" (
    echo   Discord: /luna join  (hears VC + replies with TTS^)  or  /luna say / luna, hey
)
echo   Ctrl-C in this window stops everything.
echo   IMPORTANT: only run ONE START.bat - extra copies make her talk to herself.
echo.

.venv\Scripts\python.exe -m server.chat_terminal

:cleanup
echo.
echo   Closing everything...
taskkill /FI "WINDOWTITLE eq Luna Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Web Page*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Desktop*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Riko Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Riko Web Page*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Luna Discord Bridge*" /T /F >nul 2>&1
powershell -NoProfile -Command "$root=(Resolve-Path '%~dp0').Path; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) -and ($_.CommandLine -match 'server\.chat_terminal|server\.server|transcribe_worker|electron\.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%SERVER_PORT% "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do taskkill /PID %%A /T /F >nul 2>&1
echo   Done.
REM pause fails under redirected stdin; only wait when interactive
echo %CMDCMDLINE% | findstr /i /c:"/c" >nul
if errorlevel 1 pause
