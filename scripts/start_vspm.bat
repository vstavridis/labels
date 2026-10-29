@echo off
REM VS Production Manager - start the application (double-click this file).
chcp 65001 >nul
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo The application is not installed yet. Run scripts\setup_windows.ps1 first.
    pause
    exit /b 1
)
echo Starting VS Production Manager... (close this window to stop it)
start "" http://localhost:8501
".venv\Scripts\python.exe" -m streamlit run src\vspm\ui\app.py --server.port 8501 --server.address localhost --server.headless true --browser.gatherUsageStats false
pause
