"""
STL Database — application entry point.
"""
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


def _resource(relative: str) -> Path:
    """Return the path to a bundled resource.

    Works both in development (relative to this file) and when packaged by
    PyInstaller (resources land in sys._MEIPASS at runtime).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative

from app import __version__
from app.database.db import init_db
from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # flat base style — QSS overrides work fully on all platforms
    app.setApplicationName("STL Database")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MrIxolite")

    icon_path = _resource("resources/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Read configured database path (falls back to default if not set)
    from app.ui.config_dialog import get_db_path, get_color_scheme, apply_color_scheme

    # Initialise logging before anything else that might log.
    from app.utils.logger import setup_logging
    setup_logging()

    import logging
    logging.getLogger(__name__).info(
        "STL Database %s starting up", __version__
    )

    init_db(get_db_path())

    apply_color_scheme(get_color_scheme())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
