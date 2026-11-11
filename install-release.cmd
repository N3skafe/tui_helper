@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-release.ps1"
if errorlevel 1 exit /b %ERRORLEVEL%
echo %PATH% | find /I "%LOCALAPPDATA%\tui_helper" >nul
if errorlevel 1 set "PATH=%PATH%;%LOCALAPPDATA%\tui_helper"
exit /b 0
