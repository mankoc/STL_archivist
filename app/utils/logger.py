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

LOG_FILENAME = "stl_database.log"
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


def get_log_path() -> Path:
    """Return the path to the active (uncompressed) log file."""
    from app.ui.config_dialog import get_data_dir
    return Path(get_data_dir()) / LOG_FILENAME


def setup_logging() -> None:
    """Configure the root logger with a rotating compressed file handler.

    Must be called after QApplication and QSettings are initialised so that
    ``get_data_dir()`` can resolve the configured data directory.
    """
    log_path = get_log_path()
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

    logging.getLogger(__name__).info(
        "Logging initialised → %s (max %d MB, %d backups)",
        log_path, MAX_LOG_SIZE // (1024 * 1024), BACKUP_COUNT,
    )


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
