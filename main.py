"""
STL Database — application entry point.
"""
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app import __version__
from app.database.db import init_db
from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # flat base style — QSS overrides work fully on all platforms
    app.setApplicationName("STL Database")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MrIxolite")

    # Read configured database path (falls back to default if not set)
    from app.ui.config_dialog import get_db_path, get_color_scheme, apply_color_scheme
    init_db(get_db_path())

    apply_color_scheme(get_color_scheme())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
