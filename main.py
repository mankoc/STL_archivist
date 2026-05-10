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


def _show_splash():
    from PyQt6.QtWidgets import QSplashScreen
    from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

    W, H = 400, 180
    pix = QPixmap(W, H)
    pix.fill(QColor("#1e1e2e"))

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    title_font = QFont()
    title_font.setPointSize(20)
    title_font.setBold(True)
    p.setFont(title_font)
    p.setPen(QColor("#cdd6f4"))
    p.drawText(0, 20, W, 80, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "STL Database")

    ver_font = QFont()
    ver_font.setPointSize(10)
    p.setFont(ver_font)
    p.setPen(QColor("#888ba8"))
    p.drawText(0, 90, W, 30, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, f"v{__version__}")

    p.end()

    splash = QSplashScreen(pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.showMessage(
        "Loading…",
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor("#6c7086"),
    )
    splash.show()
    return splash


def _check_db_on_startup(db_path: str) -> str:
    """If no database file exists at *db_path*, show an alert and optionally open
    Configuration so the user can point to an existing library.

    Returns the db_path that should be used for init_db() — which may differ from
    the argument if the user reconfigured the data directory.
    """
    if Path(db_path).exists():
        return db_path

    from PyQt6.QtWidgets import QMessageBox
    msg = QMessageBox()
    msg.setWindowTitle("No Database Found")
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText("No database was found at the configured location.")
    msg.setInformativeText(
        f"<b>{db_path}</b><br><br>"
        "A new empty database will be created there when you continue, "
        "or configure a different data directory to open an existing library."
    )
    ok_btn     = msg.addButton("Continue",             QMessageBox.ButtonRole.AcceptRole)
    config_btn = msg.addButton("Configure Library…",   QMessageBox.ButtonRole.ActionRole)
    msg.setDefaultButton(ok_btn)
    msg.exec()

    if msg.clickedButton() is config_btn:
        from app.ui.config_dialog import ConfigDialog, get_db_path
        ConfigDialog().exec()
        return get_db_path()

    return db_path


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # flat base style — QSS overrides work fully on all platforms
    app.setApplicationName("STL Database")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MrIxolite")

    icon_path = _resource("resources/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash = _show_splash()
    app.processEvents()

    from app.ui.config_dialog import get_db_path, get_color_scheme, apply_color_scheme

    # Initialise logging before anything else that might log.
    from app.utils.logger import setup_logging, setup_excepthook
    from app.ui.config_dialog import get_logging_enabled
    if get_logging_enabled():
        setup_logging()
    setup_excepthook()

    import logging
    logging.getLogger(__name__).info(
        "STL Database %s starting up", __version__
    )

    db_path = _check_db_on_startup(get_db_path())
    init_db(db_path)

    apply_color_scheme(get_color_scheme())

    window = MainWindow()
    window.show()
    splash.finish(window)
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
