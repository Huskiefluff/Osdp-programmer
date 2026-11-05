@echo off
rem Build OSDPReaderProgrammer.exe (GUI) and readerprog.exe (command line) into dist\
rem Requires Python 3.9+ from python.org (tick "Add python.exe to PATH").
setlocal
cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :nopython
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :fail

echo Running tests...
python -m pytest -q
if errorlevel 1 goto :fail

pyinstaller --noconfirm --clean --onefile --windowed --name OSDPReaderProgrammer --paths src --collect-data netaddr launch_gui.py
if errorlevel 1 goto :fail
pyinstaller --noconfirm --clean --onefile --console --name readerprog --paths src --collect-data netaddr launch_cli.py
if errorlevel 1 goto :fail

echo.
echo Done. Programs are in %CD%\dist
goto :eof

:nopython
echo Python 3 was not found. Install it from https://www.python.org/downloads/windows/
exit /b 1

:fail
echo Build failed.
exit /b 1
