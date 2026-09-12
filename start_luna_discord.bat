@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Luna Discord: Riko VRM core as Luna + Discord bridge for text/VC out.
REM Prerequisites: setup.bat once, Ollama running, discord-bridge\.env filled.

set "RIKO_CHARACTER=luna"
set "RIKO_VOICE_SOURCE=browser"

echo [luna] character=%RIKO_CHARACTER% voice_source=%RIKO_VOICE_SOURCE%

if not exist "discord-bridge\.env" (
  echo.
  echo   Copy discord-bridge\.env.example to discord-bridge\.env
  echo   and set DISCORD_BOT_TOKEN + DISCORD_CLIENT_ID
  echo.
  pause
  exit /b 1
)

if not exist "discord-bridge\node_modules\" (
  echo [luna] installing discord-bridge deps...
  pushd discord-bridge
  call npm install
  if errorlevel 1 (
    echo npm install failed
    pause
    exit /b 1
  )
  popd
)

REM Start Discord bridge in a second window; this window runs Riko (Luna).
start "Luna Discord Bridge" /D "%~dp0discord-bridge" cmd /k "npm start"

call "%~dp0run.bat"
