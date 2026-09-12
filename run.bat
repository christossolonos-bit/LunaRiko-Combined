@echo off
:: One launcher. Starts the server + web page, opens the browser, then
:: listens for you. Ctrl-C stops the chat; servers close when this window ends.
chcp 65001 >nul
title Riko
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: Keep character/voice if the caller already set them (start_luna_*.bat).
if not defined RIKO_CHARACTER set "RIKO_CHARACTER=riko"
if not defined RIKO_VOICE_SOURCE set "RIKO_VOICE_SOURCE=mic"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   First run: double-click setup.bat, wait for it to finish, then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" copy /y ".env.example" ".env" >nul
)

:: Port from server/config.py  (line: SERVER_PORT: int = 8100)
set "SERVER_PORT="
for /f "tokens=2 delims==" %%P in ('findstr /b /c:"SERVER_PORT: int =" "server\config.py"') do set "SERVER_PORT=%%P"
set "SERVER_PORT=%SERVER_PORT: =%"
if not defined SERVER_PORT set "SERVER_PORT=8100"

echo.
echo   Starting %RIKO_CHARACTER% (voice=%RIKO_VOICE_SOURCE%)...
echo.

:: Quiet helper windows — you only need this one.
start "Riko Server" /MIN /D "%~dp0" cmd /c "set RIKO_CHARACTER=%RIKO_CHARACTER%&& set RIKO_VOICE_SOURCE=%RIKO_VOICE_SOURCE%&& .venv\Scripts\python.exe -m server.server"
start "Riko Web Page" /MIN /D "%~dp0client" cmd /c "npx vite --host 127.0.0.1 --port 5173 --strictPort"

echo   Waiting for the web page...
set "READY="
for /l %%I in (1,1,40) do (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173 -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set "READY=1"
        goto :ready
    )
    timeout /t 1 /nobreak >nul
)
:ready
if not defined READY (
    echo   Web page did not come up. Check the minimized "Riko Web Page" window.
    goto :cleanup
)

start "" "http://127.0.0.1:5173"

echo.
echo   Browser opened. Click the page once so audio can play.
echo   Speak when it says listening.  Ctrl-C to stop.
echo.

.venv\Scripts\python.exe -m server.chat_terminal

:cleanup
echo.
echo   Closing servers...
taskkill /FI "WINDOWTITLE eq Riko Server*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Riko Web Page*" /T /F >nul 2>&1
:: Catch orphans by port if the window title already went away.
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%SERVER_PORT% "') do taskkill /PID %%A /T /F >nul 2>&1
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do taskkill /PID %%A /T /F >nul 2>&1
echo   Done.
pause
