"""
STL Database — application entry point.
"""
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app.database.db import init_db
from app.ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    qss_path = Path(__file__).parent / "resources" / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))


def main() -> None:
    # Initialise the database (creates tables if first run)
    init_db("stl_database.db")

    app = QApplication(sys.argv)
    app.setApplicationName("STL Database")
    app.setOrganizationName("STLBrowser")

    load_stylesheet(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
