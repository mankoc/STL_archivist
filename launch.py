#!/usr/bin/env python3
"""
stlLibrary Launcher
Cross-platform launcher script with dependency checking and error handling.
"""
import sys
import subprocess
from pathlib import Path


def check_python_version():
    """Verify Python version is 3.11 or later."""
    if sys.version_info < (3, 11):
        print()
        print("ERROR: Python 3.11 or later is required")
        print(f"Current version: {sys.version}")
        print()
        sys.exit(1)


def check_dependencies():
    """Verify that required packages are installed."""
    required = ["PyQt6", "sqlalchemy", "lmdb"]
    missing = []

    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    if missing:
        print()
        print("ERROR: Required Python packages not found:")
        for pkg in missing:
            print(f"  - {pkg}")
        print()
        print("Please install dependencies using:")
        print("  pip install -r requirements.txt")
        print()
        sys.exit(1)


def launch_app():
    """Launch the main application."""
    script_dir = Path(__file__).parent
    main_py = script_dir / "main.py"

    if not main_py.exists():
        print()
        print(f"ERROR: main.py not found in {script_dir}")
        print()
        print("Make sure you are running this script from the stlLibrary root directory.")
        print()
        sys.exit(1)

    print("Launching stlLibrary...")
    try:
        result = subprocess.run(
            [sys.executable, str(main_py)],
            cwd=str(script_dir),
        )
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        sys.exit(130)
    except Exception as e:
        print()
        print(f"ERROR: Failed to launch application: {e}")
        print()
        sys.exit(1)


def main():
    """Main launcher entry point."""
    print("stlLibrary Launcher")
    print("=" * 50)
    print()

    check_python_version()
    check_dependencies()

    launch_app()


if __name__ == "__main__":
    main()
