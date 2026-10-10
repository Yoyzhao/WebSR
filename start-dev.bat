@echo off
setlocal
REM WebSR dev launcher - backend (8000) + frontend (5173) in separate windows.
REM Uses relative paths only; checks port availability before starting each one.
cd /d "%~dp0"

echo ============================================================
echo  WebSR dev launcher
echo ============================================================

set BACKEND_BUSY=0
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if %errorlevel%==0 set BACKEND_BUSY=1

set FRONTEND_BUSY=0
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul
if %errorlevel%==0 set FRONTEND_BUSY=1

if "%BACKEND_BUSY%"=="1" (
  echo [SKIP] backend: port 8000 already in use
  for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do echo         PID %%p
) else (
  start "WebSR Backend" cmd /k "cd /d server && ..\.venvs\sr-app\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
  echo [OK]   backend: starting on http://127.0.0.1:8000
)

if "%FRONTEND_BUSY%"=="1" (
  echo [SKIP] frontend: port 5173 already in use
  for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do echo         PID %%p
) else (
  start "WebSR Frontend" cmd /k "cd /d web && npm run dev"
  echo [OK]   frontend: starting on http://127.0.0.1:5173
)

echo.
echo Keep the spawned windows open while using the app.
timeout /t 8 >nul
endlocal
