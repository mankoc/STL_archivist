"""
Crawler — walks a source directory, processes each archive, and upserts records
into the database. Runs inside a QThread to keep the UI responsive.
"""
import hashlib
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.crawler.archive import is_archive, iter_images, list_contents, IMAGE_SUFFIXES
from app.crawler.metadata import extract_metadata
from app.database.db import session_scope
from app.database.models import STLModel, Tag, Image
from app.utils.thumbnailer import make_thumbnail


def _hash_file(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 hash of a file, reading in chunks to stay memory-efficient."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _get_or_create_tag(session, name: str) -> Tag:
    name = name.strip().lower()
    tag = session.query(Tag).filter_by(name=name).first()
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


class CrawlerWorker(QThread):
    """
    QThread that crawls *source_dir* and populates the database.

    Signals:
        progress(current, total)  – emitted after each archive is processed
        log(message)              – human-readable status line
        finished()                – emitted when crawl is complete or stopped
    """
    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, source_dir: str, parent=None):
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        self._stop_requested = False
        self._remove_missing()

        archives = sorted(
            p for p in self.source_dir.rglob("*") if is_archive(p)
        )
        total = len(archives)
        self.log.emit(f"Found {total} archives in {self.source_dir}")

        for idx, archive_path in enumerate(archives, start=1):
            if self._stop_requested:
                self.log.emit("Crawl stopped by user.")
                break

            self.log.emit(f"[{idx}/{total}] {archive_path.name}")
            try:
                self._process_archive(archive_path)
            except Exception as exc:
                self.log.emit(f"  ERROR: {exc}")
            self.progress.emit(idx, total)

        self.log.emit("Crawl complete.")
        self.finished.emit()

    # ------------------------------------------------------------------
    def _remove_missing(self):
        """Delete DB records whose archive file no longer exists on disk."""
        with session_scope() as session:
            all_models = session.query(STLModel).all()
            removed = 0
            for model in all_models:
                if not Path(model.archive_path).exists():
                    self.log.emit(f"Removing missing: {model.archive_path}")
                    session.delete(model)
                    removed += 1
        if removed:
            self.log.emit(f"Removed {removed} missing record(s) from database.")

    def _process_archive(self, archive_path: Path):
        archive_str = str(archive_path)
        current_hash = _hash_file(archive_path)

        with session_scope() as session:
            existing: Optional[STLModel] = (
                session.query(STLModel).filter_by(archive_path=archive_str).first()
            )

            if existing and existing.archive_hash == current_hash:
                self.log.emit("  unchanged – skipped")
                return

            meta = extract_metadata(archive_path)

            if existing:
                model = existing
                self.log.emit("  updated")
            else:
                model = STLModel(archive_path=archive_str)
                session.add(model)
                self.log.emit("  new")

            model.archive_hash = current_hash
            model.title        = meta.title
            model.author       = meta.author
            model.type         = meta.distributor
            model.release_date = meta.release_date
            model.url          = meta.url
            model.description  = meta.description

            # Tags
            model.tags.clear()
            for tag_name in meta.tags:
                if tag_name.strip():
                    model.tags.append(_get_or_create_tag(session, tag_name))

            # Thumbnails – replace existing images for updated archives
            if existing:
                for img in list(model.images):
                    session.delete(img)
                session.flush()

            # Extract thumbnails up to the configured limit
            from app.ui.config_dialog import get_max_thumbnails, get_max_thumb_size
            max_thumbs = get_max_thumbnails()
            max_thumb_size = get_max_thumb_size()
            count = 0
            try:
                all_names = list_contents(archive_path)
                self.log.emit(f"  archive has {len(all_names)} file(s) total")
                image_names = [n for n in all_names
                               if Path(n.replace("\\", "/")).suffix.lower()
                               in IMAGE_SUFFIXES]
                self.log.emit(f"  {len(image_names)} image candidate(s): "
                              f"{[Path(n).name for n in image_names[:8]]}")

                for filename, raw_bytes in iter_images(archive_path):
                    if count >= max_thumbs:
                        break
                    thumb = make_thumbnail(raw_bytes, max_size=max_thumb_size)
                    if thumb:
                        session.add(Image(
                            model=model,
                            filename=filename,
                            thumbnail=thumb,
                        ))
                        count += 1
                    else:
                        self.log.emit(f"  WARNING: thumbnail conversion failed for {filename}")
            except Exception as exc:
                self.log.emit(f"  WARNING: image extraction failed — {exc}")
            self.log.emit(f"  {count} image(s) extracted")
