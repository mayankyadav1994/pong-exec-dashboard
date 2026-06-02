@echo off
REM ============================================================
REM  refresh.bat - local refresh for the Game Pipeline dashboards
REM  1. Activate .venv if present, else use system python
REM  2. Build both projects from Jira (reads .env)
REM  3. Open both pages in the default browser
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo [refresh] Using virtual environment .venv
) else (
    echo [refresh] No .venv found - using system python
)

echo [refresh] Building V2 + iGaming data from Jira...
"%PY%" build_jira_data.py --project both %*
if errorlevel 1 (
    echo.
    echo [refresh] BUILD FAILED - see messages above.
    pause
    exit /b 1
)

echo.
echo [refresh] Build complete. Opening dashboards...
start "" "v2-game-pipeline.html"
start "" "igaming-game-pipeline.html"

echo.
echo [refresh] Done.
pause
endlocal
