@echo off
setlocal
REM WebSR backend only (port 8000). Use this when the frontend is already running.
REM Relative path: run from server/ so ..\.venvs resolves to the project-root venv.
cd /d "%~dp0server"

echo ============================================================
echo  WebSR backend  (port 8000)
echo ============================================================

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if %errorlevel%==0 (
  echo [SKIP] port 8000 is already in use - backend is probably running.
  for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do echo         PID %%p
  echo Check: http://127.0.0.1:8000/api/health
  pause
  endlocal
  exit /b 0
)

echo [OK] starting with ..\.venvs\sr-app\Scripts\python.exe
echo      URL: http://127.0.0.1:8000
echo.
..\.venvs\sr-app\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
endlocal
