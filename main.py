"""
stlLibrary — application entry point.
"""
import shutil
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import QCoreApplication, Qt


def _resource(relative: str) -> Path:
    """Return the path to a bundled resource.

    Works both in development (relative to this file) and when packaged by
    PyInstaller (resources land in sys._MEIPASS at runtime).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative

from app import __version__
from app.database.db import init_db, backfill_archive_folder
from app.ui.main_window import MainWindow


def _show_splash():
    from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
    from PyQt6.QtCore import QRect

    ICON_SIZE = 96
    W, H = 400, 240
    pix = QPixmap(W, H)
    pix.fill(QColor("#1e1e2e"))

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    icon_path = _resource("resources/icon.ico")
    if icon_path.exists():
        from PyQt6.QtGui import QIcon
        icon_pix = QIcon(str(icon_path)).pixmap(ICON_SIZE, ICON_SIZE)
        p.drawPixmap((W - ICON_SIZE) // 2, 16, icon_pix)

    title_y = 16 + ICON_SIZE + 10
    title_font = QFont()
    title_font.setPointSize(18)
    title_font.setBold(True)
    p.setFont(title_font)
    p.setPen(QColor("#cdd6f4"))
    p.drawText(QRect(0, title_y, W, 36), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "stlLibrary")

    ver_font = QFont()
    ver_font.setPointSize(10)
    p.setFont(ver_font)
    p.setPen(QColor("#888ba8"))
    p.drawText(QRect(0, title_y + 34, W, 26), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, f"v{__version__}")

    p.end()

    splash = QSplashScreen(pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.showMessage(
        "Loading…",
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor("#6c7086"),
    )
    splash.show()
    return splash


def _update_splash(splash, message: str) -> None:
    if splash is None or not splash.isVisible():
        return
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    splash.showMessage(
        message,
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor("#6c7086"),
    )
    QApplication.processEvents()


def _check_db_on_startup(db_path: str, splash=None) -> str:
    """If no database file exists at *db_path*, show an alert and optionally open
    Configuration so the user can point to an existing library.

    Returns the db_path that should be used for init_db() — which may differ from
    the argument if the user reconfigured the data directory.
    """
    if Path(db_path).exists():
        return db_path

    if splash is not None:
        splash.close()

    from PyQt6.QtWidgets import QMessageBox
    msg = QMessageBox()
    msg.setWindowTitle(QCoreApplication.translate("main", "No Database Found"))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(QCoreApplication.translate("main", "No database was found at the configured location."))
    msg.setInformativeText(
        f"<b>{db_path}</b><br><br>"
        "A new empty database will be created there when you continue, "
        "or configure a different data directory to open an existing library."
    )
    ok_btn     = msg.addButton(QCoreApplication.translate("main", "Continue"),             QMessageBox.ButtonRole.AcceptRole)
    config_btn = msg.addButton(QCoreApplication.translate("main", "Configure Library…"),   QMessageBox.ButtonRole.ActionRole)
    msg.setDefaultButton(ok_btn)
    msg.exec()

    if msg.clickedButton() is config_btn:
        from app.ui.config_dialog import ConfigDialog, get_db_path
        ConfigDialog().exec()
        return get_db_path()

    return db_path


def _migrate_db_filename(new_db_path: str) -> None:
    """Rename stl_database.db → stlLibrary.db if the old file exists and the new one doesn't."""
    new = Path(new_db_path)
    old = new.parent / "stl_database.db"
    if old.exists() and not new.exists():
        shutil.move(str(old), str(new))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # flat base style — QSS overrides work fully on all platforms
    app.setApplicationName("stlLibrary")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MrIxolite")

    icon_path = _resource("resources/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash = _show_splash()
    app.processEvents()

    _update_splash(splash, "Loading configuration…")
    from app.ui.config_dialog import get_db_path, get_color_scheme, apply_color_scheme, get_language

    from app.utils.ai_runtime import activate_ai_packages
    activate_ai_packages()

    from app.utils import i18n
    i18n.install_language(get_language())

    _update_splash(splash, "Initialising logging…")
    from app.utils.logger import setup_logging, setup_excepthook
    from app.ui.config_dialog import get_logging_enabled
    if get_logging_enabled():
        setup_logging()
    setup_excepthook()

    import logging
    logging.getLogger(__name__).info(
        "stlLibrary %s starting up", __version__
    )

    _update_splash(splash, "Opening database…")
    db_path = get_db_path()
    _migrate_db_filename(db_path)
    db_path = _check_db_on_startup(db_path, splash)
    init_db(db_path)
    backfill_archive_folder()

    _update_splash(splash, "Applying theme…")
    apply_color_scheme(get_color_scheme())

    hl = app.palette().color(QPalette.ColorRole.Highlight).name()
    splitter_h_svg = _resource("resources/icons/splitter_h.svg").as_posix()
    splitter_v_svg = _resource("resources/icons/splitter_v.svg").as_posix()
    app.setStyleSheet(
        (app.styleSheet() or "")
        + (
            f"\nQSplitter::handle:horizontal {{"
            f" width: 16px; background-color: {hl};"
            f" image: url({splitter_h_svg}); }}"
        )
        + (
            f"\nQSplitter::handle:vertical {{"
            f" height: 16px; background-color: {hl};"
            f" image: url({splitter_v_svg}); }}"
        )
    )

    _update_splash(splash, "Starting up…")
    window = MainWindow()
    window.show()
    splash.finish(window)
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
