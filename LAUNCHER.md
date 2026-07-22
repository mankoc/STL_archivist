# stlLibrary Launcher Scripts

This directory includes launcher scripts to run the stlLibrary application with proper error handling and dependency checking.

## Quick Start

### Windows
Double-click `launch.bat` or run from command prompt:
```cmd
launch.bat
```

### Linux / macOS
Run from terminal:
```bash
./launch.sh
```

Or using Python directly:
```bash
python3 launch.py
```

## Available Launchers

### `launch.bat` (Windows)
- Checks if Python is installed and in PATH
- Verifies `main.py` exists
- Shows helpful error messages if something is wrong
- Pauses on error to allow reading error messages

### `launch.sh` (Linux/macOS)
- Checks if Python 3 is installed
- Verifies `main.py` exists
- Checks if required dependencies are installed
- Provides installation instructions if needed
- Requires execute permission (normally set automatically)

### `launch.py` (Cross-platform)
- Pure Python launcher that works on all platforms
- Checks Python version (3.11+)
- Verifies all required packages are installed
- Works with both `python` and `python3` commands
- Useful for development and testing

## Requirements

### Python
- Python 3.11 or later
- Must be installed and available in system PATH

### Python Packages
- PyQt6
- SQLAlchemy
- LMDB

Install with:
```bash
pip install -r requirements.txt
```

## Troubleshooting

### "Python is not installed"
- Install Python 3.11+ from https://www.python.org/
- On Windows: Check "Add Python to PATH" during installation
- On macOS: Use `brew install python3`
- On Linux: Use your package manager (`apt`, `dnf`, `pacman`, etc.)

### "Required packages not found"
- Run: `pip install -r requirements.txt`
- On some systems: `pip3 install -r requirements.txt`

### Script won't run (Linux/macOS)
- Make executable: `chmod +x launch.sh`
- Or use Python launcher: `python3 launch.py`

### Application crashes on startup
- Check logs in the configured data directory (usually `~/.stlLibrary/`)
- Ensure the database file exists or let the app create one
- Try starting with fresh config: delete `~/.stlLibrary/settings.json`

## Development

For development, you can also run directly:
```bash
python main.py
```

Or use the Python launcher:
```bash
python launch.py
```

## Notes

- The launchers will display the application window after startup
- If the application crashes, error messages will be shown
- On Windows, the command prompt window will remain open until you close it
- On Linux/macOS, you can use `nohup ./launch.sh &` to run in the background
