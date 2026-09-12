@echo off
:: UTF-8 console. Without it cmd.exe is codepage 437 and Python
:: raises UnicodeEncodeError on the first em dash it prints.
chcp 65001 >nul
title Riko - transparent desktop window
setlocal
cd /d "%~dp0"

:: The frameless, transparent window: just the avatar over your desktop.
:: START THE STACK FIRST (start_servers.bat) - this is only the window.
::
:: ctrl+shift+Q quits. Drag the top 22 pixels to move it.

if not exist "desktop\node_modules\electron" (
    echo Installing Electron. One time, about 200 MB...
    pushd desktop
    call npm install
    popd
    if not exist "desktop\node_modules\electron" (
        echo.
        echo   Electron install failed. Is Node.js installed? https://nodejs.org/
        pause
        exit /b 1
    )
)

echo Opening the transparent window...
echo   ctrl+shift+Q quits  -  drag the top 22px to move it
echo.
pushd desktop
call npx electron . %1
popd
