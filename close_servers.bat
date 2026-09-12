@echo off
:: UTF-8 console. Without it cmd.exe is codepage 437 and Python
:: raises UnicodeEncodeError on the first em dash it prints.
chcp 65001 >nul
title Closing Riko
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ===========================================================================
::  Closes everything start_servers.bat opened.
::
::  Two passes, because either one alone misses cases:
::    1. by WINDOW TITLE  - closes the three launcher windows and their
::       children (/T kills the whole tree, so python and node go with them).
::    2. by PORT          - catches anything still listening: a window closed
::       by hand that left an orphan, or a server started manually.
::
::  Pass 2 only ever kills OUR processes (python / node / cmd). If something
::  else holds a port it is reported, never killed - see :kill_pid.
:: ===========================================================================

:: The port lives in server/config.py. Read it rather than repeating it here.
:: The line it matches is:   SERVER_PORT: int = 8100
set "SERVER_PORT="
for /f "tokens=2 delims==" %%P in ('findstr /b /c:"SERVER_PORT: int =" "server\config.py" 2^>nul') do set "SERVER_PORT=%%P"
set "SERVER_PORT=%SERVER_PORT: =%"
if not defined SERVER_PORT set "SERVER_PORT=8100"

echo ======================================
echo   Closing Riko
echo ======================================
echo.

echo [1/3] Closing the launcher windows...
call :kill_window "Riko Server"
call :kill_window "Riko Web Page"
call :kill_window "GPT-SoVITS"

echo.
echo [2/3] Closing anything still on our ports...
call :free_port %SERVER_PORT% "server"
call :free_port 5173 "web page"
call :free_port 9880 "GPT-SoVITS"

:: Give the kills a moment to actually release the sockets.
timeout /t 2 /nobreak >nul 2>&1

echo.
echo [3/3] Checking the ports are free...
set "STUCK="
call :check_port %SERVER_PORT% "server"
call :check_port 5173 "web page"
call :check_port 9880 "GPT-SoVITS"

echo.
if defined STUCK (
    echo ======================================
    echo   A PORT IS STILL HELD
    echo ======================================
    echo.
    echo Whatever holds it is not one of ours, so it was left alone.
    echo The notes above each stuck port say what to do.
    echo.
    echo For the full explanation, run check_setup.bat - test 8.
    echo.
    pause
    exit /b 1
)

echo ======================================
echo   ALL CLOSED
echo ======================================
echo.
echo Riko can be started again with run.bat
echo.
pause
exit /b 0


:: ---------------------------------------------------------------------------
::  Close a launcher window and everything it started.
::
::  taskkill /FI returns 0 EVEN WHEN NOTHING MATCHED, so its exit code cannot
::  be used to tell "closed" from "was not running". Its output can: it prints
::  a SUCCESS line per process killed, and "INFO: No tasks..." otherwise.
:: ---------------------------------------------------------------------------
:kill_window
taskkill /FI "WINDOWTITLE eq %~1*" /T /F 2>&1 | findstr /i /c:"SUCCESS" >nul
if errorlevel 1 (
    echo    %~1 - was not running
) else (
    echo    %~1 - closed
)
goto :eof


:: ---------------------------------------------------------------------------
::  Kill whatever is LISTENING on a port, but ONLY if it is one of ours.
::  %1 = port   %2 = label
:: ---------------------------------------------------------------------------
:free_port
set "FP_PORT=%~1"
set "FP_LABEL=%~2"
set "FP_HIT="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%FP_PORT% "') do (
    set "FP_HIT=1"
    call :kill_pid %%A "%FP_PORT%" "%FP_LABEL%"
)
if not defined FP_HIT echo    %FP_PORT% ^(%FP_LABEL%^) - nothing there
goto :eof


:: ---------------------------------------------------------------------------
::  %1 = pid   %2 = port   %3 = label
:: ---------------------------------------------------------------------------
:kill_pid
set "KP_PID=%~1"
set "KP_PORT=%~2"
set "KP_LABEL=%~3"
set "KP_NAME="
:: /FO CSV gives  "python.exe","1234",...  and %%~N strips the quotes.
:: The .exe filter drops tasklist's "INFO: no tasks" line.
for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %KP_PID%" /NH /FO CSV 2^>nul ^| findstr /i "\.exe"') do set "KP_NAME=%%~N"
if not defined KP_NAME goto :eof

:: Whitelist. Anything not on it is left alone.
set "KP_OURS="
if /i "%KP_NAME%"=="python.exe"  set "KP_OURS=1"
if /i "%KP_NAME%"=="pythonw.exe" set "KP_OURS=1"
if /i "%KP_NAME%"=="node.exe"    set "KP_OURS=1"
if /i "%KP_NAME%"=="cmd.exe"     set "KP_OURS=1"

if not defined KP_OURS (
    echo    %KP_PORT% ^(%KP_LABEL%^) - held by %KP_NAME% ^(pid %KP_PID%^) - not ours, left alone
    goto :eof
)

taskkill /PID %KP_PID% /T /F >nul 2>&1
echo    %KP_PORT% ^(%KP_LABEL%^) - closed %KP_NAME% ^(pid %KP_PID%^)
goto :eof


:: ---------------------------------------------------------------------------
::  Is the port free now? Sets STUCK and explains it if not.
::  %1 = port   %2 = label
:: ---------------------------------------------------------------------------
:check_port
set "CP_PORT=%~1"
set "CP_LABEL=%~2"
set "CP_PID="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%CP_PORT% "') do set "CP_PID=%%A"
if not defined CP_PID (
    echo    %CP_PORT% ^(%CP_LABEL%^) - free
    goto :eof
)
set "CP_NAME=?"
for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %CP_PID%" /NH /FO CSV 2^>nul ^| findstr /i "\.exe"') do set "CP_NAME=%%~N"
echo    %CP_PORT% ^(%CP_LABEL%^) - STILL HELD by %CP_NAME% ^(pid %CP_PID%^)
set "STUCK=1"

:: By far the most common cause of a port held by svchost.exe: a leftover
:: `netsh portproxy` forwarding rule. They are invisible in Task Manager,
:: they survive reboots, and they squat the port forever. Anyone who has
:: forwarded a port into WSL has some.
netsh interface portproxy show all 2>nul | findstr /c:" %CP_PORT% " >nul
if not errorlevel 1 (
    echo       ^-^> there is a portproxy rule on this port. To remove it, in an
    echo          ADMINISTRATOR terminal:
    echo             netsh interface portproxy delete v4tov4 listenport=%CP_PORT% listenaddress=0.0.0.0
    goto :eof
)
echo       ^-^> see what it is:  tasklist /FI "PID eq %CP_PID%"
goto :eof
