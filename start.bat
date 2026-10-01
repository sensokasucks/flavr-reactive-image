@echo off
REM Fridge Reactive Image – run from source (no exe needed)
setlocal
cd /d "%~dp0"
if not exist "reactive_image.py" (
  echo [ERROR] reactive_image.py not found.
  pause
  exit /b 1
)
where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not on PATH.
  pause
  exit /b 1
)
echo Starting Reactive Image. HTTP control: http://127.0.0.1:3851/
echo.
python reactive_image.py
if errorlevel 1 pause
endlocal
