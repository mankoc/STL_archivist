"""
Command-line crawler for STL Database.

Usage:
    python crawl.py <folder> [options]

The crawler scans <folder> for .zip/.7z/.rar archives and upserts records into
the configured (or explicitly specified) SQLite database.
"""
import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="crawl",
        description="Crawl a folder of STL archives and populate the database.",
    )
    parser.add_argument(
        "folder",
        help="Root folder to scan for archives (.zip, .7z, .rar).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=(
            "Path to the SQLite database file. "
            "Defaults to the path configured in the GUI settings."
        ),
    )
    parser.add_argument(
        "--pattern",
        metavar="PATTERN",
        default="",
        help="Path pattern used to extract metadata from archive paths.",
    )
    parser.add_argument(
        "--workers",
        metavar="N",
        type=int,
        default=None,
        help="Number of parallel worker threads (default: CPUs/2, clamped to 2-8).",
    )
    parser.add_argument(
        "--max-thumbnails",
        metavar="N",
        type=int,
        default=None,
        help="Maximum number of thumbnails to store per archive (default: 5).",
    )
    parser.add_argument(
        "--max-thumb-size",
        metavar="PX",
        type=int,
        default=None,
        help="Maximum thumbnail dimension in pixels (default: 512).",
    )
    parser.add_argument(
        "--unrar",
        metavar="PATH",
        default=None,
        help="Path to the unrar/rar executable for RAR archives.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"ERROR: Not a directory: {folder}", file=sys.stderr)
        sys.exit(1)

    # QCoreApplication is required for QSettings and QThread.
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication(sys.argv)
    app.setApplicationName("STL Database")
    app.setOrganizationName("MrIxolite")

    from app.utils.logger import setup_logging
    setup_logging()

    from app.ui.config_dialog import get_db_path
    from app.database.db import init_db
    from app.crawler.crawler import CrawlerWorker
    from app.ui.settings_loader import build_crawler_config

    db_path = args.db or get_db_path()
    init_db(db_path)
    print(f"Database: {db_path}")
    print(f"Scanning: {folder}")

    config = build_crawler_config(
        max_thumbnails=args.max_thumbnails,
    )
    # CLI overrides — explicit args win over stored settings
    if args.max_thumb_size is not None:
        config.max_thumb_size = args.max_thumb_size
    if args.unrar is not None:
        config.unrar_path = args.unrar
    if args.workers is not None:
        config.max_workers = args.workers

    worker = CrawlerWorker(
        source_dir=str(folder),
        pattern=args.pattern,
        config=config,
    )

    worker.log.connect(print)
    worker.progress.connect(lambda cur, tot: None)  # progress already printed via log
    worker.finished.connect(app.quit)

    worker.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
