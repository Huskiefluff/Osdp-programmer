@echo off
rem Run the GUI straight from source (installs dependencies on first run).
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Python 3 was not found. Install it from https://www.python.org/downloads/windows/
        pause
        exit /b 1
    )
    .venv\Scripts\python -m pip install -r requirements.txt
)
set PYTHONPATH=%~dp0src
start "" .venv\Scripts\pythonw.exe -m readerprog
