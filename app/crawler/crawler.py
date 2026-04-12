"""
Crawler — walks a source directory, processes each archive, and upserts records
into the database. Runs inside a QThread to keep the UI responsive.
"""
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.crawler.archive import is_archive, extract_archive_contents, IMAGE_SUFFIXES
from app.crawler.metadata import extract_metadata
from app.database.db import session_scope
from app.database.models import STLModel, Tag, Image, Release
from app.utils.thumbnailer import save_thumbnail, delete_thumbnail
from app.utils.path_pattern import parse_path, apply_extracted_to_model


def _hash_file(path: Path, chunk: int = 1 << 20) -> str:
    """xxHash-64 hash of a file, reading in chunks to stay memory-efficient."""
    import xxhash
    h = xxhash.xxh64()
    with open(path, "rb") as fh:
        for chunk_data in iter(lambda: fh.read(chunk), b""):
            h.update(chunk_data)
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

    def __init__(self, source_dir: str, pattern: str = "", parent=None):
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self._pattern = pattern.strip()
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    BATCH_SIZE = 50

    def run(self):
        self._stop_requested = False
        self._remove_missing()

        archives = sorted(
            p for p in self.source_dir.rglob("*") if is_archive(p)
        )
        total = len(archives)
        self.log.emit(f"Found {total} archives in {self.source_dir}")

        from app.ui.config_dialog import get_max_thumbnails, get_max_thumb_size
        max_thumbs = get_max_thumbnails()
        max_thumb_size = get_max_thumb_size()

        for batch_start in range(0, total, self.BATCH_SIZE):
            batch = archives[batch_start:batch_start + self.BATCH_SIZE]
            with session_scope() as session:
                for idx, archive_path in enumerate(batch, start=batch_start + 1):
                    if self._stop_requested:
                        self.log.emit("Crawl stopped by user.")
                        break

                    self.log.emit(f"[{idx}/{total}] {archive_path.name}")
                    sp = session.begin_nested()
                    try:
                        self._process_archive(archive_path, session, max_thumbs, max_thumb_size)
                        sp.commit()
                    except Exception as exc:
                        sp.rollback()
                        self.log.emit(f"  ERROR: {exc}")
                    self.progress.emit(idx, total)

            if self._stop_requested:
                break

        self.log.emit("Crawl complete.")
        self.finished.emit()

    # ------------------------------------------------------------------
    def _remove_missing(self):
        """Delete DB records whose archive file no longer exists on disk,
        including any thumbnail files stored on disk."""
        with session_scope() as session:
            all_models = session.query(STLModel).all()
            removed = 0
            for model in all_models:
                if not Path(model.archive_path).exists():
                    self.log.emit(f"Removing missing: {model.archive_path}")
                    for image in model.images:
                        if image.thumbnail_path:
                            delete_thumbnail(image.thumbnail_path)
                    session.delete(model)
                    removed += 1
        if removed:
            self.log.emit(f"Removed {removed} missing record(s) from database.")

    def _process_archive(self, archive_path: Path, session, max_thumbs: int, max_thumb_size: int):
        archive_str = str(archive_path)
        current_hash = _hash_file(archive_path)

        existing: Optional[STLModel] = (
            session.query(STLModel).filter_by(archive_path=archive_str).first()
        )
        hash_changed = existing is not None and existing.archive_hash != current_hash

        # Existing + unchanged: only re-apply the path pattern, skip everything else
        if existing and existing.archive_hash == current_hash:
            if self._pattern:
                extracted = parse_path(self._pattern, archive_str)
                if extracted:
                    apply_extracted_to_model(session, model=existing, extracted=extracted, overwrite=True)
                    self.log.emit("  unchanged – pattern updated: " + ", ".join(f"{k}={v!r}" for k, v in extracted.items()))
                else:
                    self.log.emit("  unchanged – pattern: no match")
            else:
                self.log.emit("  unchanged – skipped")
            return

        # Extract archive contents for metadata + (new files only) thumbnails
        contents = extract_archive_contents(archive_path)
        image_names = [
            n for n in contents
            if Path(n.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
        ]
        self.log.emit(f"  {len(contents)} relevant file(s) extracted "
                      f"({len(image_names)} image(s))")

        meta = extract_metadata(archive_path, contents=contents,
                                source_dir=self.source_dir)

        meta_parts = []
        if meta.title:        meta_parts.append(f"title={meta.title!r}")
        if meta.author:       meta_parts.append(f"author={meta.author!r}")
        if meta.distributor:  meta_parts.append(f"source={meta.distributor!r}")
        if meta.release_date: meta_parts.append(f"date={meta.release_date!r}")
        if meta.url:          meta_parts.append(f"url={meta.url!r}")
        if meta.description:  meta_parts.append(f"description={meta.description!r}")
        if meta.tags:         meta_parts.append(f"tags={meta.tags}")
        if meta.release:      meta_parts.append(f"release={meta.release!r}")
        if meta_parts:
            self.log.emit("  metadata: " + ", ".join(meta_parts))
        else:
            self.log.emit("  metadata: (none found)")

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
        model.source       = meta.distributor
        model.release_date = meta.release_date
        model.url          = meta.url
        model.description  = meta.description

        if meta.release:
            rel = session.query(Release).filter_by(name=meta.release).first()
            if rel is None:
                rel = Release(name=meta.release)
                session.add(rel)
                session.flush()
            model.release = rel

        # Apply path pattern — fills in fields left empty by metadata
        if self._pattern:
            extracted = parse_path(self._pattern, archive_str)
            if extracted:
                apply_extracted_to_model(session, model, extracted, overwrite=False)
                self.log.emit("  pattern: " + ", ".join(f"{k}={v!r}" for k, v in extracted.items()))
            else:
                self.log.emit("  pattern: no match")

        # Tags
        model.tags.clear()
        for tag_name in meta.tags:
            if tag_name.strip():
                model.tags.append(_get_or_create_tag(session, tag_name))

        # Thumbnails – for new archives and archives whose file content changed
        if hash_changed:
            for old_img in list(existing.images):
                if old_img.thumbnail_path:
                    delete_thumbnail(old_img.thumbnail_path)
                session.delete(old_img)
        if not existing or hash_changed:
            count = 0
            for filename in image_names:
                if count >= max_thumbs:
                    break
                raw_bytes = contents[filename]
                img = Image(model=model, filename=filename)
                session.add(img)
                session.flush()  # get img.id before writing the file
                rel_path = save_thumbnail(raw_bytes, img.id, max_size=max_thumb_size)
                if rel_path:
                    img.thumbnail_path = rel_path
                    count += 1
                else:
                    session.delete(img)
                    self.log.emit(f"  WARNING: thumbnail conversion failed for {filename}")
            self.log.emit(f"  {count} thumbnail(s) stored")
