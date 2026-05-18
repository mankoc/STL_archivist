"""
Logging setup — writes to a rotating, gzip-compressed log file in the same
directory as the database.  Compression is applied automatically when the active
log file exceeds MAX_LOG_SIZE bytes; up to BACKUP_COUNT compressed copies are
kept.
"""
import gzip
import logging
import logging.handlers
import shutil
from pathlib import Path

LOG_FILENAME = "stlLibrary.log"
MAX_LOG_SIZE = 10 * 1024 * 1024   # 10 MB before rotation
BACKUP_COUNT = 3                   # number of compressed backups to keep


def _namer(name: str) -> str:
    """Append .gz so rotated logs are stored compressed."""
    return name + ".gz"


def _rotator(source: str, dest: str) -> None:
    """Gzip-compress *source* into *dest* then remove the uncompressed file."""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    Path(source).unlink()


def get_log_path(data_dir: Path | str | None = None) -> Path:
    """Return the path to the active (uncompressed) log file.

    When *data_dir* is ``None`` the path is looked up from ``QSettings``.
    Passing *data_dir* explicitly avoids importing the UI layer and makes
    the function testable without a running ``QApplication``.
    """
    if data_dir is None:
        from app.ui.config_dialog import get_data_dir
        data_dir = get_data_dir()
    return Path(data_dir) / LOG_FILENAME


def setup_logging(data_dir: Path | str | None = None) -> None:
    """Configure the root logger with a rotating compressed file handler.

    Args:
        data_dir: Directory where the log file is written.  When ``None``
            the value is read from ``QSettings`` via ``get_data_dir()``;
            callers that already have the path should pass it explicitly to
            avoid a dependency on the UI layer.
    """
    log_path = get_log_path(data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(log_path),
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.rotator = _rotator
    handler.namer   = _namer

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)-30s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)

    # Keep a minimal stderr handler at WARNING level so errors still surface
    # when running from a terminal.
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    # urllib3 is chatty at DEBUG; dku.utils re-emits those as INFO
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised → %s (max %d MB, %d backups)",
        log_path, MAX_LOG_SIZE // (1024 * 1024), BACKUP_COUNT,
    )


def setup_excepthook() -> None:
    """Install sys.excepthook so uncaught exceptions are logged as CRITICAL
    and shown in an error dialog.

    KeyboardInterrupt is forwarded to the default handler so Ctrl-C still works.
    """
    import sys
    import traceback
    _log = logging.getLogger("uncaught")

    def _handle(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        _log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QVBoxLayout, \
                QLabel, QTextEdit, QDialogButtonBox
            app = QApplication.instance()
            if app is None:
                return
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            dlg = QDialog()
            dlg.setWindowTitle("Uncaught Exception")
            dlg.resize(700, 400)
            layout = QVBoxLayout(dlg)
            label = QLabel(
                f"<b style='color:#d32f2f;'>An unexpected error occurred:</b><br>"
                f"{exc_type.__name__}: {exc_value}"
            )
            label.setWordWrap(True)
            layout.addWidget(label)
            tb_edit = QTextEdit()
            tb_edit.setReadOnly(True)
            tb_edit.setFontFamily("monospace")
            tb_edit.setPlainText(tb_text)
            layout.addWidget(tb_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dlg.reject)
            layout.addWidget(buttons)
            dlg.exec()
        except Exception:
            pass

    sys.excepthook = _handle


def disable_logging() -> None:
    """Remove all handlers from the root logger, silencing all log output."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        root.removeHandler(handler)


def delete_logs() -> int:
    """Delete the active log file and all compressed backups.

    Returns the number of files deleted.  The handler is *not* closed here;
    a new log file will be created automatically on the next log write.
    """
    log_path = get_log_path()
    deleted = 0

    if log_path.exists():
        try:
            log_path.unlink()
            deleted += 1
        except OSError:
            pass

    for i in range(1, BACKUP_COUNT + 2):
        backup = Path(str(log_path) + f".{i}.gz")
        if backup.exists():
            try:
                backup.unlink()
                deleted += 1
            except OSError:
                pass

    return deleted
