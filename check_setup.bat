@echo off
:: UTF-8 console. Without it cmd.exe is codepage 437 and Python
:: raises UnicodeEncodeError on the first em dash it prints.
chcp 65001 >nul
title Riko - system check
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   No virtual environment found. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe check_setup.py %*
pause
