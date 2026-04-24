"""
Crawler — walks a source directory, processes each archive, and upserts records
into the database. Runs inside a QThread to keep the UI responsive.
"""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import CrawlerConfig
from app.crawler.archive import iter_archives, extract_archive_contents, archive_hash, list_contents, IMAGE_SUFFIXES
from app.crawler.folder_metadata import (
    export_folder_metadata,
    import_folder_metadata,
)
from app.crawler.metadata import extract_metadata, ModelMetadata
from app.crawler.rules import detect_presupport, detect_scale, detect_warnings
from app.database.db import session_scope
from app.database.models import STLModel, Tag, Image, Release, Scale
from app.utils.thumbnailer import save_thumbnail, delete_thumbnail, get_thumbnail_base_dir
from app.utils.path_pattern import parse_path, apply_extracted_to_model, parse_release_date


def _get_or_create_tag(session: Session, name: str) -> Tag:
    """Return an existing Tag with the given name, or create and flush a new one.

    Args:
        session: Active SQLAlchemy session.
        name: Raw tag string — will be stripped and lower-cased before lookup.

    Returns:
        The existing or newly created ``Tag`` instance.
    """
    name = name.strip().lower()
    tag = session.query(Tag).filter_by(name=name).first()
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


class CrawlerWorker(QThread):
    """QThread that crawls *source_dir* and populates the database.

    Signals:
        progress(current, total): Emitted after each archive is processed.
        log(message): Human-readable status line for the UI log panel.
        finished(): Emitted when the crawl completes or is stopped by the user.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        source_dir: str,
        pattern: str = "",
        config: Optional[CrawlerConfig] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self._pattern = pattern.strip()
        self._config = config or CrawlerConfig()
        self._stop_requested = False

    def stop(self) -> None:
        """Request the crawl to stop after the current archive finishes."""
        self._stop_requested = True

    BATCH_SIZE = 50

    def run(self) -> None:
        """Main thread entry point — orchestrates the full crawl pipeline."""
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

        # Pre-crawl: restore from METADATA.7z for any archive not yet in DB.
        # Deferred import avoids a circular dependency (crawler → ui → crawler).
        from app.ui.config_dialog import get_metadata_sidecar_enabled
        thumb_base = get_thumbnail_base_dir()
        if thumb_base is not None and get_metadata_sidecar_enabled():
            folder_archives: dict[Path, list[Path]] = defaultdict(list)
            for p in archives:
                folder_archives[p.parent].append(p)

            with session_scope() as session:
                for folder in folder_archives:
                    if self._stop_requested:
                        break
                    metadata_path = folder / "METADATA.7z"
                    if metadata_path.exists():
                        logger.info("Pre-crawl import from %s", metadata_path)
                        imported = import_folder_metadata(folder, session, thumb_base)
                        if imported:
                            msg = (f"Imported {len(imported)} record(s) "
                                   f"from METADATA.7z in {folder.name}")
                            self.log.emit(msg)
                            logger.info(msg)

        max_thumbs     = self._config.max_thumbnails
        max_thumb_size = self._config.max_thumb_size
        workers        = self._config.max_workers
        num_batches    = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        logger.debug("Processing %d archive(s) in %d batch(es) of up to %d",
                     total, num_batches, self.BATCH_SIZE)

        modified_folders: set[Path] = set()

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

                # Phase 3: extract archive contents in parallel (IO/CPU-bound).
                # We also capture the full member name list from each archive's
                # catalog so Phase 4 can run rule/warning detection without
                # reopening the archive.
                contents_map:  dict[Path, dict[str, bytes]] = {}
                all_names_map: dict[Path, list[str]]       = {}

                def _extract_and_list(p: Path) -> tuple[dict[str, bytes], list[str]]:
                    names: list[str] = []
                    data = extract_archive_contents(
                        p,
                        unrar_path=self._config.unrar_path,
                        all_names_out=names,
                    )
                    return data, names

                if to_extract:
                    logger.debug("Phase 3: extracting %d archive(s) in parallel", len(to_extract))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {pool.submit(_extract_and_list, p): p for p in to_extract}
                        for future in as_completed(futures):
                            p = futures[future]
                            try:
                                data, names = future.result()
                                contents_map[p]  = data
                                all_names_map[p] = names
                                logger.debug("  extracted %s → %d file(s), %d member(s)",
                                             p.name, len(data), len(names))
                            except Exception as exc:
                                msg = f"  ERROR extracting {p.name}: {exc}"
                                self.log.emit(msg)
                                logger.error("Extraction failed for %s: %s",
                                             p.name, exc, exc_info=True)
                                contents_map[p]  = {}
                                all_names_map[p] = []
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
                            all_names=all_names_map.get(archive_path),
                        )
                        sp.commit()
                        modified_folders.add(archive_path.parent)
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

        # Post-crawl: update METADATA.7z for every folder that was modified.
        # Deferred import avoids a circular dependency (crawler → ui → crawler).
        if modified_folders and thumb_base is not None and get_metadata_sidecar_enabled():
            from app.ui.config_dialog import get_metadata_thumbnails_enabled
            include_thumbs = get_metadata_thumbnails_enabled()
            self.log.emit(f"Updating METADATA.7z in {len(modified_folders)} folder(s)…")
            logger.info("Post-crawl: updating METADATA.7z in %d folder(s) (thumbnails=%s)",
                        len(modified_folders), include_thumbs)
            with session_scope() as session:
                for folder in sorted(modified_folders):
                    if self._stop_requested:
                        break
                    try:
                        export_folder_metadata(folder, session, thumb_base,
                                               include_thumbnails=include_thumbs)
                        self.log.emit(f"  Updated METADATA.7z in {folder.name}")
                    except Exception as exc:
                        logger.error("Failed to update METADATA.7z in %s: %s",
                                     folder, exc, exc_info=True)

        msg = "Crawl complete."
        self.log.emit(msg)
        logger.info(msg)
        self.finished.emit()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _remove_missing(self) -> None:
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
        session: Session,
        max_thumbs: int,
        max_thumb_size: int,
        current_hash: str,
        existing: Optional[STLModel],
        contents: Optional[dict[str, bytes]],
        all_names: Optional[list[str]] = None,
    ) -> None:
        """Write one archive to the database using pre-computed hash and contents.

        When *contents* is ``None`` the hash is unchanged; only the path pattern
        is re-applied and the function returns early.  When *contents* is provided
        (new archive or hash changed) the full metadata extraction, rule detection,
        and thumbnail pipeline runs.

        Args:
            archive_path: Absolute path to the archive file on disk.
            session: Active SQLAlchemy session (write access required).
            max_thumbs: Maximum number of thumbnails to store per archive.
            max_thumb_size: Maximum thumbnail dimension in pixels.
            current_hash: Pre-computed archive fingerprint from Phase 1.
            existing: Existing ``STLModel`` record if the archive is already in
                the DB, or ``None`` for a new archive.
            contents: Dict of ``{member_name: bytes}`` from Phase 3, or ``None``
                when the archive is unchanged and extraction was skipped.
            all_names: Full member list from the archive catalog.  If ``None``
                and rule detection is needed, ``list_contents`` is called to
                fetch it (one extra archive open).
        """
        archive_str  = str(archive_path)
        hash_changed = existing is not None and existing.archive_hash != current_hash

        if contents is None:
            self._apply_pattern_to_unchanged(archive_path, archive_str, existing, session)
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
        self._log_metadata(meta)

        if existing:
            model = existing
            self.log.emit("  updated")
            logger.debug("  %s — updating existing record (id=%s)", archive_path.name, existing.id)
        else:
            model = STLModel(archive_path=archive_str)
            session.add(model)
            self.log.emit("  new")
            logger.debug("  %s — inserting new record", archive_path.name)

        model.archive_hash = current_hash

        if all_names is None:
            logger.debug("  Listing archive contents for rule detection: %s", archive_path.name)
            all_names = list_contents(archive_path, self._config.unrar_path)
        logger.debug("  %d member(s) in archive", len(all_names))

        self._detect_rules(model, all_names, archive_path, session)
        self._assign_metadata(model, meta, session)

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

        self._update_thumbnails(
            model, existing, contents, image_names,
            session, max_thumbs, max_thumb_size, archive_path, hash_changed,
        )

    def _apply_pattern_to_unchanged(
        self,
        archive_path: Path,
        archive_str: str,
        existing: Optional[STLModel],
        session: Session,
    ) -> None:
        """Re-apply the path pattern to an unchanged archive.

        Called when the archive hash has not changed since the last crawl, so
        there is nothing to re-extract.  If a pattern is configured, fields
        that can be inferred from the path are updated without overwriting
        values the user may have edited manually (``overwrite=False``).

        Args:
            archive_path: Absolute path to the archive.
            archive_str: String form of *archive_path* (pre-computed by caller).
            existing: The current ``STLModel`` DB record for this archive.
            session: Active SQLAlchemy session.
        """
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

    def _detect_rules(
        self,
        model: STLModel,
        all_names: list[str],
        archive_path: Path,
        session: Session,
    ) -> None:
        """Run presupport, warning, and scale detection and update *model* in place.

        Args:
            model: The ``STLModel`` record being populated.
            all_names: Full list of member names from the archive catalog.
            archive_path: Path to the archive (used for scale detection on the filename).
            session: Active session used to look up or create ``Scale`` records.
        """
        presupport = detect_presupport(all_names)
        model.presupports = presupport
        logger.debug("  Presupport detected: %s", presupport)

        # Deferred import avoids circular dependency (crawler → ui → crawler).
        from app.ui.config_dialog import (
            get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive,
            get_warn_supports, get_supports_patterns,
        )
        warnings = detect_warnings(
            all_names,
            check_presupported=get_warn_presupported_stl(),
            check_no_images=get_warn_no_images(),
            check_nested_archive=get_warn_nested_archive(),
            check_supports=get_warn_supports(),
            supports_patterns=get_supports_patterns(),
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

    def _assign_metadata(
        self,
        model: STLModel,
        meta: ModelMetadata,
        session: Session,
    ) -> None:
        """Copy scalar fields from *meta* onto *model*, creating FK records as needed.

        Args:
            model: The ``STLModel`` record being populated.
            meta: Extracted metadata from ``extract_metadata()``.
            session: Active session used to look up or create ``Release`` records.
        """
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

    def _update_thumbnails(
        self,
        model: STLModel,
        existing: Optional[STLModel],
        contents: dict[str, bytes],
        image_names: list[str],
        session: Session,
        max_thumbs: int,
        max_thumb_size: int,
        archive_path: Path,
        hash_changed: bool,
    ) -> None:
        """Purge stale thumbnails and store new ones for a new or changed archive.

        Thumbnails are only updated when the archive is new (``existing`` is
        ``None``) or its content hash changed since the last crawl.

        Args:
            model: The ``STLModel`` record being populated.
            existing: The previous DB record, or ``None`` for a new archive.
            contents: Extracted file contents keyed by member name.
            image_names: Member names that are recognised image files.
            session: Active session for creating ``Image`` records.
            max_thumbs: Upper limit on stored thumbnails per archive.
            max_thumb_size: Maximum thumbnail dimension in pixels.
            archive_path: Path used in log messages.
            hash_changed: ``True`` when the archive existed before but its
                content hash differs — old thumbnails must be purged first.
        """
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
                session.flush()  # get img.id before writing to LMDB
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

    def _log_metadata(self, meta: ModelMetadata) -> None:
        """Emit a log line summarising the extracted metadata fields.

        Args:
            meta: The ``ModelMetadata`` instance to summarise.
        """
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
