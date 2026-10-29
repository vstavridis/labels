@echo off
REM Only on the ONE designated Excel computer when VSPM_EXCEL_MODE=registry_queue.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0\.."
".venv\Scripts\vspm.exe" excel-worker --interval 30
pause
