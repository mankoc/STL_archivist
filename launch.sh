#!/bin/bash
# stlLibrary Launcher for Linux/macOS
# This script launches the stlLibrary application with proper error handling

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo ""
    echo "ERROR: Python 3 is not installed or not in PATH"
    echo ""
    echo "Please install Python 3.11 or later:"
    echo "  - Ubuntu/Debian: sudo apt-get install python3 python3-pip"
    echo "  - macOS: brew install python3"
    echo "  - Fedora: sudo dnf install python3"
    echo ""
    exit 1
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    echo ""
    echo "ERROR: main.py not found in $SCRIPT_DIR"
    echo ""
    echo "Make sure you are running this script from the stlLibrary root directory."
    echo ""
    exit 1
fi

# Check if required packages are installed
echo "Checking dependencies..."
python3 -c "import PyQt6" 2>/dev/null || {
    echo ""
    echo "ERROR: Required Python packages not found."
    echo ""
    echo "Please install dependencies using:"
    echo "  pip install -r requirements.txt"
    echo ""
    exit 1
}

# Run the application
echo "Launching stlLibrary..."
python3 main.py

# Capture exit code
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "ERROR: The application exited with error code $EXIT_CODE"
    echo ""
    exit $EXIT_CODE
fi
