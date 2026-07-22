@echo off
REM stlLibrary Launcher for Windows
REM This script launches the stlLibrary application with proper error handling

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Check if virtual environment exists
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo Using virtual environment
) else (
    REM Fall back to system Python
    set "PYTHON_EXE=python"
    echo Note: No virtual environment found. Using system Python.
)

REM Check if Python is installed
%PYTHON_EXE% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not in PATH
    echo.
    echo Please install Python 3.11 or later from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Check if main.py exists
if not exist "main.py" (
    echo.
    echo ERROR: main.py not found in %SCRIPT_DIR%
    echo.
    echo Make sure you are running this script from the stlLibrary root directory.
    echo.
    pause
    exit /b 1
)

REM Check if required packages are installed
echo Checking dependencies...
%PYTHON_EXE% -c "import PyQt6, sqlalchemy, lmdb" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Required Python packages are not installed
    echo.
    if exist ".venv" (
        echo Please install dependencies using:
        echo   .venv\Scripts\python -m pip install -r requirements.txt
    ) else (
        echo Please install dependencies using one of these methods:
        echo.
        echo   Option 1 - Using virtual environment (recommended):
        echo   -----
        echo   python -m venv .venv
        echo   .venv\Scripts\python -m pip install -r requirements.txt
        echo.
        echo   Option 2 - Direct installation:
        echo   -----
        echo   python -m pip install -r requirements.txt
    )
    echo.
    echo After installing dependencies, run this launcher again.
    echo.
    pause
    exit /b 1
)

REM Run the application
echo Launching stlLibrary...
%PYTHON_EXE% main.py

REM If the app exits with an error, show the error message
if errorlevel 1 (
    echo.
    echo ERROR: The application exited with an error.
    echo Please check the error message above for details.
    echo.
    pause
)

endlocal
