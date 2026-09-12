@echo off
:: UTF-8 console. Without it cmd.exe is codepage 437 and Python
:: raises UnicodeEncodeError on the first em dash it prints.
chcp 65001 >nul
title Riko Setup
setlocal

echo ============================================
echo   Riko - one-time setup
echo ============================================
echo.

cd /d "%~dp0"

:: ---------------------------------------------------------------------------
:: 1) Python virtual environment
:: ---------------------------------------------------------------------------
:: uv is used because plain pip takes several minutes on this dependency list.
:: It is always called as `python -m uv`: a freshly pip-installed uv.exe lands
:: in a Scripts folder that this session's PATH may not know about yet.
python --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Could not run python. Is Python 3.10 installed and on PATH?
    echo   Get it here: https://www.python.org/downloads/release/python-31011/
    echo   During install, TICK "Add python.exe to PATH", then reopen this window.
    goto :fail
)

echo [1/4] Installing uv ^(fast Python installer^)...
python -m pip install --quiet --upgrade uv
if errorlevel 1 goto :fail

echo [2/4] Creating the virtual environment ^(.venv^)...
if not exist ".venv" (
    python -m uv venv --python 3.10 .venv
    if errorlevel 1 (
        echo.
        echo   Could not create the environment with Python 3.10.
        echo   uv normally downloads it for you, so this usually means no
        echo   internet, or a firewall blocking the download.
        goto :fail
    )
) else (
    echo       .venv already exists - reusing it.
)

echo [3/4] Installing Python packages. This takes a few minutes...
python -m uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto :fail

:: ---------------------------------------------------------------------------
:: 2) Browser side
:: ---------------------------------------------------------------------------
echo [4/4] Installing browser packages...
where npm >nul 2>nul
if errorlevel 1 (
    echo.
    echo   npm was not found. Install Node.js, then run this again:
    echo   https://nodejs.org/
    goto :fail
)
:: `popd` resets errorlevel, so npm's exit code has to be captured before it.
pushd client
call npm install
set NPM_RESULT=%errorlevel%
popd
if not "%NPM_RESULT%"=="0" goto :fail

:: ---------------------------------------------------------------------------
:: 3) .env
:: ---------------------------------------------------------------------------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo   Created .env from the template. OPEN IT and paste in your API keys.
)

echo.
echo ============================================
echo   Setup finished.
echo ============================================
echo.
echo   Next:
echo     1. Put your API keys in  .env
echo     2. Run  check_setup.bat   ^(tests everything, tells you what is wrong^)
echo     3. Run  start_servers.bat
echo.
pause
exit /b 0

:fail
echo.
echo ============================================
echo   Setup FAILED - see the error above.
echo ============================================
pause
exit /b 1
