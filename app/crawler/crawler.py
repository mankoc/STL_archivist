"""
Crawler — walks a source directory, processes each archive, and upserts records
into the database. Runs inside a QThread to keep the UI responsive.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

from app.config import CrawlerConfig
from app.crawler.archive import iter_archives, extract_archive_contents, archive_hash, list_contents, IMAGE_SUFFIXES
from app.crawler.metadata import extract_metadata
from app.crawler.rules import detect_presupport, detect_scale, detect_warnings
from app.database.db import session_scope
from app.database.models import STLModel, Tag, Image, Release, Scale
from app.utils.thumbnailer import save_thumbnail, delete_thumbnail
from app.utils.path_pattern import parse_path, apply_extracted_to_model, parse_release_date



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

    def __init__(self, source_dir: str, pattern: str = "", config: CrawlerConfig | None = None, parent=None):
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self._pattern = pattern.strip()
        self._config = config or CrawlerConfig()
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    BATCH_SIZE = 50

    def run(self):
        self._stop_requested = False
        logger.info("Crawl started — source: %s, pattern: %r, workers: %d",
                    self.source_dir, self._pattern, self._config.max_workers)
        self._remove_missing()

        logger.debug("Scanning source directory for archives: %s", self.source_dir)
        archives = sorted(iter_archives(self.source_dir))
        total = len(archives)
        msg = f"Found {total} archives in {self.source_dir}"
        self.log.emit(msg)
        logger.info(msg)

        max_thumbs     = self._config.max_thumbnails
        max_thumb_size = self._config.max_thumb_size
        workers        = self._config.max_workers
        num_batches    = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        logger.debug("Processing %d archive(s) in %d batch(es) of up to %d",
                     total, num_batches, self.BATCH_SIZE)

        for batch_start in range(0, total, self.BATCH_SIZE):
            if self._stop_requested:
                break
            batch       = archives[batch_start:batch_start + self.BATCH_SIZE]
            batch_idx   = batch_start // self.BATCH_SIZE + 1
            logger.debug("--- Batch %d/%d (%d archives) ---",
                         batch_idx, num_batches, len(batch))

            # Phase 1: read per-member CRCs from each archive's catalog (no decompression)
            logger.debug("Phase 1: computing archive hashes (parallel, %d workers)", workers)
            hash_fn = partial(archive_hash, unrar_path=self._config.unrar_path)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                hashes: dict[Path, str] = dict(zip(batch, pool.map(hash_fn, batch)))
            logger.debug("Phase 1 complete — %d hashes computed", len(hashes))

            with session_scope() as session:
                # Phase 2: batch-query existing records — one round-trip for the whole batch
                logger.debug("Phase 2: querying database for %d existing records", len(batch))
                batch_strs = [str(p) for p in batch]
                existing_map: dict[str, STLModel] = {
                    m.archive_path: m
                    for m in session.query(STLModel)
                        .filter(STLModel.archive_path.in_(batch_strs))
                        .all()
                }
                logger.debug("Phase 2 complete — %d existing record(s) found", len(existing_map))

                # Decide which archives need extraction (new or hash-changed)
                to_extract: list[Path] = []
                unchanged: list[Path]  = []
                for p in batch:
                    ex = existing_map.get(str(p))
                    if ex is None:
                        to_extract.append(p)
                        logger.debug("  NEW: %s", p.name)
                    elif ex.archive_hash != hashes[p]:
                        to_extract.append(p)
                        logger.debug("  CHANGED: %s (old=%s… new=%s…)",
                                     p.name, ex.archive_hash[:8], hashes[p][:8])
                    else:
                        unchanged.append(p)
                logger.debug("Extraction needed: %d new/changed, %d unchanged",
                             len(to_extract), len(unchanged))

                # Phase 3: extract archive contents in parallel (IO/CPU-bound)
                contents_map: dict[Path, dict[str, bytes]] = {}
                if to_extract:
                    logger.debug("Phase 3: extracting %d archive(s) in parallel", len(to_extract))
                    extract_fn = partial(
                        extract_archive_contents, unrar_path=self._config.unrar_path
                    )
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {pool.submit(extract_fn, p): p for p in to_extract}
                        for future in as_completed(futures):
                            p = futures[future]
                            try:
                                result = future.result()
                                contents_map[p] = result
                                logger.debug("  extracted %s → %d file(s)",
                                             p.name, len(result))
                            except Exception as exc:
                                msg = f"  ERROR extracting {p.name}: {exc}"
                                self.log.emit(msg)
                                logger.error("Extraction failed for %s: %s",
                                             p.name, exc, exc_info=True)
                                contents_map[p] = {}
                    logger.debug("Phase 3 complete — %d archive(s) extracted", len(contents_map))
                else:
                    logger.debug("Phase 3 skipped — no archives need extraction")

                # Phase 4: serial DB upserts with all expensive work already done
                logger.debug("Phase 4: upserting %d archive(s) into database", len(batch))
                for idx, archive_path in enumerate(batch, start=batch_start + 1):
                    if self._stop_requested:
                        msg = "Crawl stopped by user."
                        self.log.emit(msg)
                        logger.info(msg)
                        break

                    self.log.emit(f"[{idx}/{total}] {archive_path.name}")
                    logger.debug("[%d/%d] Upserting: %s", idx, total, archive_path)
                    sp = session.begin_nested()
                    try:
                        self._upsert_archive(
                            archive_path,
                            session,
                            max_thumbs,
                            max_thumb_size,
                            current_hash=hashes[archive_path],
                            existing=existing_map.get(str(archive_path)),
                            # None signals "hash unchanged — skip extraction"
                            contents=contents_map.get(archive_path),
                        )
                        sp.commit()
                        logger.debug("  Upsert committed: %s", archive_path.name)
                    except Exception as exc:
                        sp.rollback()
                        msg = f"  ERROR: {exc}"
                        self.log.emit(msg)
                        logger.error("Upsert failed for %s: %s",
                                     archive_path.name, exc, exc_info=True)
                    self.progress.emit(idx, total)

            if self._stop_requested:
                break

        msg = "Crawl complete."
        self.log.emit(msg)
        logger.info(msg)
        self.finished.emit()

    # ------------------------------------------------------------------
    def _remove_missing(self):
        """Delete DB records whose archive file no longer exists on disk,
        including any thumbnail files stored on disk."""
        logger.debug("Checking for missing archives in database…")
        with session_scope() as session:
            all_models = session.query(STLModel).all()
            logger.debug("  %d record(s) in database", len(all_models))
            removed = 0
            for model in all_models:
                if not Path(model.archive_path).exists():
                    msg = f"Removing missing: {model.archive_path}"
                    self.log.emit(msg)
                    logger.info(msg)
                    for image in model.images:
                        if image.thumbnail_path:
                            logger.debug("  Deleting thumbnail: %s", image.thumbnail_path)
                            delete_thumbnail(image.thumbnail_path)
                    session.delete(model)
                    removed += 1
        if removed:
            msg = f"Removed {removed} missing record(s) from database."
            self.log.emit(msg)
            logger.info(msg)
        else:
            logger.debug("No missing archives found.")

    def _upsert_archive(
        self,
        archive_path: Path,
        session,
        max_thumbs: int,
        max_thumb_size: int,
        current_hash: str,
        existing: Optional[STLModel],
        contents: Optional[dict[str, bytes]],
    ):
        """
        Write one archive to the database using pre-computed *current_hash* and
        *contents*.  When *contents* is None the hash is unchanged and only the
        path pattern is re-applied.
        """
        archive_str  = str(archive_path)
        hash_changed = existing is not None and existing.archive_hash != current_hash

        # Unchanged: only re-apply the path pattern, skip everything else
        if contents is None:
            if self._pattern:
                try:
                    rel_path = "/" + str(archive_path.relative_to(self.source_dir)).replace("\\", "/")
                except ValueError:
                    rel_path = archive_str
                extracted = parse_path(self._pattern, rel_path)
                if extracted:
                    apply_extracted_to_model(session, model=existing, extracted=extracted, overwrite=False)
                    msg = "  unchanged – pattern applied: " + ", ".join(f"{k}={v!r}" for k, v in extracted.items())
                    self.log.emit(msg)
                    logger.debug("  %s — %s", archive_path.name, msg.strip())
                else:
                    self.log.emit("  unchanged – pattern: no match")
                    logger.debug("  %s — unchanged, pattern had no match", archive_path.name)
            else:
                self.log.emit("  unchanged – skipped")
                logger.debug("  %s — unchanged, skipped (no pattern)", archive_path.name)
            return

        image_names = [
            n for n in contents
            if Path(n.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
        ]
        msg = (f"  {len(contents)} relevant file(s) extracted "
               f"({len(image_names)} image(s))")
        self.log.emit(msg)
        logger.debug("  %s — %s", archive_path.name, msg.strip())

        logger.debug("  Extracting metadata for: %s", archive_path.name)
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
            logger.debug("  metadata: %s", ", ".join(meta_parts))
        else:
            self.log.emit("  metadata: (none found)")
            logger.debug("  %s — no metadata extracted", archive_path.name)

        if existing:
            model = existing
            self.log.emit("  updated")
            logger.debug("  %s — updating existing record (id=%s)", archive_path.name, existing.id)
        else:
            model = STLModel(archive_path=archive_str)
            session.add(model)
            self.log.emit("  new")
            logger.debug("  %s — inserting new record", archive_path.name)

        model.archive_hash  = current_hash
        logger.debug("  Listing archive contents for rule detection: %s", archive_path.name)
        all_names           = list_contents(archive_path, self._config.unrar_path)
        logger.debug("  %d member(s) in archive", len(all_names))

        presupport = detect_presupport(all_names)
        model.presupports   = presupport
        logger.debug("  Presupport detected: %s", presupport)

        from app.ui.config_dialog import get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive
        warnings = detect_warnings(
            all_names,
            check_presupported=get_warn_presupported_stl(),
            check_no_images=get_warn_no_images(),
            check_nested_archive=get_warn_nested_archive(),
        )
        model.warning = warnings or None
        if warnings:
            logger.warning("  Warnings for %s: %s", archive_path.name,
                           warnings.replace("\n", "; "))
        else:
            logger.debug("  No warnings for %s", archive_path.name)

        scales = detect_scale([archive_path.name] + all_names)
        logger.debug("  Scales detected: %s", scales or "none")
        if scales:
            sc = session.query(Scale).filter_by(name=scales[0]).first()
            if sc is None:
                sc = Scale(name=scales[0])
                session.add(sc)
                session.flush()
                logger.debug("  Created new Scale record: %r", scales[0])
            model.scale = sc

        model.title         = meta.title
        model.author        = meta.author
        model.source        = meta.distributor
        _year, _month = parse_release_date(meta.release_date or "")
        model.release_year  = _year
        model.release_month = _month
        model.url           = meta.url
        model.description   = meta.description

        if meta.release:
            rel = session.query(Release).filter_by(name=meta.release).first()
            if rel is None:
                rel = Release(name=meta.release)
                session.add(rel)
                session.flush()
                logger.debug("  Created new Release record: %r", meta.release)
            model.release = rel

        # Apply path pattern — fills in fields left empty by metadata
        if self._pattern:
            try:
                rel_path = "/" + str(archive_path.relative_to(self.source_dir)).replace("\\", "/")
            except ValueError:
                rel_path = archive_str
            extracted = parse_path(self._pattern, rel_path)
            if extracted:
                apply_extracted_to_model(session, model, extracted, overwrite=False)
                self.log.emit("  pattern: " + ", ".join(f"{k}={v!r}" for k, v in extracted.items()))
                logger.debug("  Pattern matched: %s",
                             ", ".join(f"{k}={v!r}" for k, v in extracted.items()))
            else:
                self.log.emit("  pattern: no match")
                logger.debug("  Pattern had no match for: %s", rel_path)

        # Tags
        prev_tag_count = len(model.tags)
        model.tags.clear()
        for tag_name in meta.tags:
            if tag_name.strip():
                model.tags.append(_get_or_create_tag(session, tag_name))
        logger.debug("  Tags: %d → %d (%s)",
                     prev_tag_count, len(model.tags),
                     ", ".join(t.name for t in model.tags) or "none")

        # Thumbnails – for new archives and archives whose file content changed
        if hash_changed:
            old_thumb_count = len([i for i in existing.images if i.thumbnail_path])
            logger.debug("  Hash changed — purging %d old thumbnail(s)", old_thumb_count)
            for old_img in list(existing.images):
                if old_img.thumbnail_path:
                    delete_thumbnail(old_img.thumbnail_path)
                session.delete(old_img)
        if not existing or hash_changed:
            count = 0
            for filename in image_names:
                if count >= max_thumbs:
                    logger.debug("  Thumbnail limit (%d) reached, skipping remaining images",
                                 max_thumbs)
                    break
                raw_bytes = contents[filename]
                img = Image(model=model, filename=filename)
                session.add(img)
                session.flush()  # get img.id before writing the file
                rel_path = save_thumbnail(raw_bytes, img.id, max_size=max_thumb_size)
                if rel_path:
                    img.thumbnail_path = rel_path
                    count += 1
                    logger.debug("  Thumbnail saved: %s → %s", filename, rel_path)
                else:
                    session.delete(img)
                    msg = f"  WARNING: thumbnail conversion failed for {filename}"
                    self.log.emit(msg)
                    logger.warning("Thumbnail conversion failed: %s in %s",
                                   filename, archive_path.name)
            self.log.emit(f"  {count} thumbnail(s) stored")
            logger.debug("  %d thumbnail(s) stored for %s", count, archive_path.name)
