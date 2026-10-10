@echo off
REM Stop WebSR dev servers by port (8000 backend / 5173 frontend).
setlocal
echo Stopping WebSR dev servers...

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo   killing backend PID %%p
  taskkill /F /PID %%p >nul 2>&1
  set FOUND=1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
  echo   killing frontend PID %%p
  taskkill /F /PID %%p >nul 2>&1
  set FOUND=1
)

if "%FOUND%"=="0" echo   no listening process on 8000 / 5173 (already stopped)
echo Done.
timeout /t 4 >nul
endlocal
