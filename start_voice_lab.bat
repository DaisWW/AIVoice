@echo off
setlocal
cd /d "%~dp0"
title Voice Lab - Port 18082

if not exist "%~dp0run_web.ps1" (
    echo [ERROR] run_web.ps1 was not found.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -Command "$used = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port -contains 18082; if ($used) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Port 18082 is already in use. Voice Lab may already be running.
    echo Local: http://127.0.0.1:18082/
    pause
    exit /b 1
)

echo ==================================================
echo Voice Lab is starting on port 18082
echo Local: http://127.0.0.1:18082/
echo LAN IPv4 addresses (append :18082):
ipconfig | findstr /R /C:"IPv4" /C:"IPv4 Address"
echo Close this window or press Ctrl+C to stop.
echo ==================================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_web.ps1"
set "VOICE_LAB_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%VOICE_LAB_EXIT_CODE%"=="0" echo [ERROR] Voice Lab exited with code %VOICE_LAB_EXIT_CODE%.
echo Voice Lab stopped.
pause
exit /b %VOICE_LAB_EXIT_CODE%
