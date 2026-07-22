@echo off
REM stlLibrary Launcher for Windows
REM This script launches the stlLibrary application with proper error handling

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Check if Python is installed
python --version >nul 2>&1
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

REM Run the application
echo Launching stlLibrary...
python main.py

REM If the app exits with an error, show the error message
if errorlevel 1 (
    echo.
    echo ERROR: The application exited with an error.
    echo.
    pause
)

endlocal
