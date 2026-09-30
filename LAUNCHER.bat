@echo off
setlocal EnableExtensions
title Project Hub
cd /d "%~dp0"

if exist "Project Hub.exe" (
  start "" "Project Hub.exe"
  exit /b 0
)

where python >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw hub.py
  exit /b 0
)

for %%P in (python3.exe py.exe) do (
  where %%P >nul 2>&1 && (
    start "" %%P hub.py
    exit /b 0
  )
)

echo   Python was not found. Install it, or run the packaged Project Hub.exe.
pause
exit /b 1
