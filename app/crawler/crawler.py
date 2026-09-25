"""
Crawler — walks a source directory, processes each archive, and upserts records
into the database. Runs inside a QThread to keep the UI responsive.
"""
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import CrawlerConfig
from app.crawler.archive import iter_archives, extract_archive_contents, archive_hash, list_contents, list_stl_members, IMAGE_SUFFIXES
from app.crawler.folder_metadata import (
    export_folder_metadata,
    import_folder_metadata,
)
from app.crawler.metadata import extract_metadata, ModelMetadata
from app.crawler.rules import (
    detect_presupport, detect_scale, detect_warnings,
    WARNING_LOW_COMPRESSION, WARNING_UNREADABLE,
)
from app.database.db import session_scope
from app.database.helpers import (
    get_or_create_category,
    get_or_create_collection,
    get_or_create_release,
    get_or_create_scale,
    get_or_create_sculptor,
    get_or_create_tag,
)
from app.database.models import STLModel, Image, STLFile, CrawledFolder
from app.utils.thumbnailer import (
    save_thumbnail, delete_thumbnail, get_thumbnail_base_dir,
    get_thumbnail_bytes, save_thumbnails_batch, write_raw_thumbnails_batch,
)
from app.ui.find_content_duplicates_dialog import compute_dhash_from_jpeg, format_dhash
from app.utils.path_pattern import parse_path, apply_extracted_to_model, parse_release_date


class CrawlerWorker(QThread):
    """QThread that crawls *source_dir* and populates the database.

    Signals:
        progress(current, total): Emitted after each archive is processed.
        log(message): Human-readable status line for the UI log panel.
        finished(): Emitted when the crawl completes or is stopped by the user.
    """

    progress       = pyqtSignal(int, int)
    log            = pyqtSignal(str)
    warning        = pyqtSignal(str)              # corrupted/unreadable archive
    phase          = pyqtSignal(str)              # high-level step description
    finished       = pyqtSignal()
    counts_changed = pyqtSignal(int, int, int)   # new, updated, unchanged
    archive_result = pyqtSignal(str, str)         # full archive path, "new"|"updated"
    file_moved     = pyqtSignal(str, str)         # old_path, new_path
    file_deleted   = pyqtSignal(str)              # path

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
    MAX_CONSECUTIVE_FAILURES = 50

    def run(self) -> None:
        """Main thread entry point — orchestrates the full crawl pipeline."""
        self._stop_requested = False
        self._consecutive_failures = 0
        self._stats_new = 0
        self._stats_updated = 0
        self._stats_unchanged = 0
        self._new_archive_paths: set[str] = set()
        logger.info("Crawl started — source: %s, pattern: %r, workers: %d",
                    self.source_dir, self._pattern, self._config.max_workers)
        self.phase.emit("Scanning for deleted files…")
        missing_records = self._collect_missing()

        self.phase.emit("Retrieving file list…")
        logger.debug("Scanning source directory for archives: %s (recurse=%s)", self.source_dir, self._config.recurse_subdirs)
        if self._config.recurse_subdirs:
            archives = sorted(iter_archives(self.source_dir))
        else:
            # Only list archives directly in the selected folder (no recursion)
            archives = sorted([p for p in self.source_dir.iterdir() if p.is_file() and p.suffix.lower() in ('.zip', '.7z', '.rar')])
        total    = len(archives)
        msg      = f"Found {total} archives in {self.source_dir}"
        self.log.emit(msg)
        logger.info(msg)
        self.phase.emit(msg)

        thumb_base = get_thumbnail_base_dir()
        if thumb_base is not None and self._config.metadata_sidecar_enabled:
            self._pre_crawl_import(archives, thumb_base)

        num_batches      = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        modified_folders: set[Path] = set()

        if total:
            self.phase.emit(f"Computing hashes for {total} archive(s)…")

        for batch_start in range(0, total, self.BATCH_SIZE):
            if self._stop_requested:
                break
            batch     = archives[batch_start:batch_start + self.BATCH_SIZE]
            batch_idx = batch_start // self.BATCH_SIZE + 1
            logger.debug("--- Batch %d/%d (%d archives) ---", batch_idx, num_batches, len(batch))

            hashes = self._phase_hash(batch)

            with session_scope() as session:
                if batch_start == 0:
                    self.phase.emit("Checking database for changes…")
                to_extract, stl_backfill, existing_map = self._phase_diff(batch, hashes, session)
                if to_extract and batch_start == 0:
                    self.phase.emit(f"Extracting new/changed archives…")
                contents_map, all_names_map, stl_info_map, uncompressed_map, precomp_map = self._phase_extract(to_extract, stl_backfill)
                # Fresh per-batch lookup cache so repeated sculptor/tag/scale
                # references within the same batch skip redundant SELECTs.
                self._lookup_cache: dict = {}
                stopped = self._phase_upsert(
                    batch, batch_start, total, hashes,
                    existing_map, contents_map, all_names_map, stl_info_map, uncompressed_map,
                    precomp_map, session, modified_folders,
                )
                self._lookup_cache = {}

            if stopped or self._stop_requested:
                break

        if modified_folders and thumb_base is not None and self._config.metadata_sidecar_enabled:
            self._post_export_metadata(modified_folders, thumb_base)

        if missing_records:
            self.phase.emit(f"Finding moved files ({len(missing_records)} candidate(s))…")
            self._reconcile_moved_files(missing_records)

        self._record_crawled_folder(total)

        try:
            from app.database.helpers import prune_unused_lookups
            with session_scope() as _s:
                _deleted = prune_unused_lookups(_s)
            if _deleted:
                summary = ", ".join(f"{n} {k}" for k, n in _deleted.items())
                self.log.emit(f"Pruned unused lookups: {summary}")
        except Exception as _exc:
            logger.warning("Failed to prune unused lookups: %s", _exc)

        msg = "Crawl complete."
        self.log.emit(msg)
        logger.info(msg)
        self.finished.emit()

    def _pre_crawl_import(self, archives: list[Path], thumb_base: Path) -> None:
        """Restore DB records from METADATA.7z sidecars before crawling."""
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

    def _phase_hash(self, batch: list[Path]) -> dict[Path, str]:
        """Phase 1: parallel CRC-based fingerprinting of each archive."""
        workers = self._config.max_workers
        logger.debug("Phase 1: computing archive hashes (parallel, %d workers)", workers)

        # Optimization: if the archive exists in DB and its cached size and mtime
        # match the filesystem, assume unchanged and reuse the stored hash.
        hashes: dict[Path, str] = {}
        to_hash: list[Path] = []

        # Stat all files in parallel — N filesystem calls instead of N serial.
        def _stat_one(p: Path) -> tuple[int | None, int | None]:
            try:
                st = p.stat()
                return int(st.st_size), int(st.st_mtime_ns)
            except Exception:
                return None, None

        stats: dict[Path, tuple[int | None, int | None]] = {}
        if batch:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for p, res in zip(batch, pool.map(_stat_one, batch)):
                    stats[p] = res

        # Single batched DB lookup instead of one query per archive (was N+1).
        batch_strs = [str(p) for p in batch]
        existing: dict[str, tuple[str, int | None, int | None]] = {}
        with session_scope() as session:
            for ap, ah, asize, amtime in session.execute(
                sa_select(
                    STLModel.archive_path, STLModel.archive_hash,
                    STLModel.archive_size, STLModel.archive_mtime_ns,
                ).where(STLModel.archive_path.in_(batch_strs))
            ).all():
                existing[ap] = (ah, asize, amtime)

        for p in batch:
            fs_size, fs_mtime = stats.get(p, (None, None))
            row = existing.get(str(p))
            if (not self._config.force_rescan
                    and row is not None
                    and row[1] is not None
                    and row[2] is not None
                    and fs_size == row[1]
                    and fs_mtime == row[2]):
                hashes[p] = row[0]
                continue
            to_hash.append(p)

        if to_hash:
            hash_fn = partial(archive_hash, unrar_path=self._config.unrar_path)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for p, h in zip(to_hash, pool.map(hash_fn, to_hash)):
                    hashes[p] = h
        logger.debug("Phase 1 complete — %d hashes computed", len(hashes))
        return hashes

    def _phase_diff(
        self,
        batch: list[Path],
        hashes: dict[Path, str],
        session: Session,
    ) -> tuple[list[Path], list[Path], dict[str, STLModel]]:
        """Phase 2: query the DB to decide which archives need (re-)extraction."""
        logger.debug("Phase 2: querying database for %d existing records", len(batch))
        batch_strs = [str(p) for p in batch]
        existing_map: dict[str, STLModel] = {
            m.archive_path: m
            for m in session.query(STLModel)
                .filter(STLModel.archive_path.in_(batch_strs))
                .all()
        }
        logger.debug("Phase 2 complete — %d existing record(s) found", len(existing_map))

        to_extract: list[Path] = []
        stl_backfill: list[Path] = []
        store_stl = self._config.store_stl_file_list
        force = self._config.force_rescan
        for p in batch:
            h = hashes[p]
            if h.startswith("fallback:"):
                msg = f"WARNING: Cannot open archive (possibly corrupted): {p.name}"
                self.warning.emit(msg)
                logger.warning("Corrupted or unreadable archive: %s (%s)", p, h)
                # Mark as unreadable in the DB warning field so it appears in the filter.
                ex2 = existing_map.get(str(p))
                if ex2 is not None:
                    _uw = WARNING_UNREADABLE
                    if ex2.warning:
                        if _uw not in ex2.warning:
                            ex2.warning += "\n" + _uw
                    else:
                        ex2.warning = _uw

            ex = existing_map.get(str(p))
            if ex is None:
                to_extract.append(p)
                logger.debug("  NEW: %s", p.name)
            elif force:
                to_extract.append(p)
                logger.debug("  FORCE: %s", p.name)
            elif not ex.archive_hash:
                # Hash was never stored (record pre-dates the archive_hash column).
                # Backfill the fingerprint from Phase 1 without re-extracting content;
                # the user hasn't changed anything, so don't classify this as "updated".
                ex.archive_hash = h
                try:
                    st = p.stat()
                    ex.archive_size = int(st.st_size)
                    ex.archive_mtime_ns = int(st.st_mtime_ns)
                    session.add(ex)
                    session.flush()
                except Exception:
                    pass
                logger.debug("  HASH BACKFILLED: %s", p.name)
            elif ex.archive_hash != h:
                to_extract.append(p)
                _old = ex.archive_hash or ""
                _new = h or ""
                logger.info(
                    "  CHANGED: %s  old=%s  new=%s%s",
                    p.name, _old, _new,
                    "  [fallback fingerprint]" if _new.startswith("fallback:") else "",
                )
                try:
                    st = p.stat()
                    ex.archive_size = int(st.st_size)
                    ex.archive_mtime_ns = int(st.st_mtime_ns)
                    session.add(ex)
                    session.flush()
                except Exception:
                    pass
            elif store_stl and not ex.stl_files and ex.stl_list_hash != h:
                # Archive content unchanged but STL list was never recorded.
                # Use a lightweight listing pass — don't re-extract images/metadata
                # so this doesn't show as "updated" in the UI.
                stl_backfill.append(p)
                logger.debug("  MISSING STL LIST: %s", p.name)
            else:
                logger.debug("  UNCHANGED: %s", p.name)

        logger.debug("Extraction needed: %d, stl_backfill: %d, unchanged: %d",
                     len(to_extract), len(stl_backfill),
                     len(batch) - len(to_extract) - len(stl_backfill))
        return to_extract, stl_backfill, existing_map

    def _phase_extract(
        self,
        to_extract: list[Path],
        stl_backfill: list[Path],
    ) -> tuple[
        dict[Path, dict[str, bytes]],
        dict[Path, list[str]],
        dict[Path, dict[str, int | None]],
        dict[Path, int | None],
        dict[Path, dict[str, tuple]],
    ]:
        """Phase 3: parallel archive decompression for new/changed archives.

        Archives in *stl_backfill* only need their STL member list populated —
        they are listed without content extraction and appear as unchanged in
        the UI (contents_map is not populated for them).

        Each extracted archive also has its image thumbnails pre-generated
        (Pillow resize + dHash) in the same parallel worker so that the serial
        Phase 4 only needs to write the final JPEG bytes to LMDB.
        """
        contents_map:      dict[Path, dict[str, bytes]]       = {}
        all_names_map:     dict[Path, list[str]]              = {}
        stl_info_map:      dict[Path, dict[str, int | None]]  = {}
        uncompressed_map:  dict[Path, int | None]             = {}
        precomp_map:       dict[Path, dict[str, tuple]]       = {}

        if not to_extract and not stl_backfill:
            logger.debug("Phase 3 skipped — no archives need extraction")
            return contents_map, all_names_map, stl_info_map, uncompressed_map, precomp_map

        max_thumbs     = self._config.max_thumbnails
        max_thumb_size = self._config.max_thumb_size

        def _extract_one(
            p: Path,
        ) -> tuple[dict[str, bytes], list[str], dict[str, int | None], int | None, dict[str, tuple]]:
            names:    list[str]             = []
            stl_info: dict[str, int | None] = {}
            uncompressed_out: list[int]     = []
            data = extract_archive_contents(
                p,
                unrar_path=self._config.unrar_path,
                all_names_out=names,
                stl_info_out=stl_info,
                uncompressed_size_out=uncompressed_out,
            )
            uncompressed = uncompressed_out[0] if uncompressed_out else None

            # Pre-generate thumbnail JPEG bytes and dHashes in the worker thread
            # so Phase 4 (serial) only needs a cheap LMDB write, not Pillow work.
            from app.utils.thumbnailer import make_thumbnail, pdf_first_page_to_image_bytes
            precomp: dict[str, tuple] = {}
            image_candidates = [
                n for n in data
                if Path(n.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
                or Path(n.replace("\\", "/")).suffix.lower() == ".pdf"
            ][:max_thumbs]
            for fname in image_candidates:
                raw = data.get(fname, b"")
                if not raw:
                    precomp[fname] = (None, None)
                    continue
                if Path(fname.replace("\\", "/")).suffix.lower() == ".pdf":
                    raw = pdf_first_page_to_image_bytes(raw) or b""
                jpeg = make_thumbnail(raw, max_thumb_size) if raw else None
                dhash_int = compute_dhash_from_jpeg(jpeg) if jpeg else None
                precomp[fname] = (jpeg, dhash_int)

            return data, names, stl_info, uncompressed, precomp

        workers = self._config.max_workers
        if to_extract:
            logger.debug("Phase 3: extracting %d archive(s) in parallel", len(to_extract))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_extract_one, p): p for p in to_extract}
                for future in as_completed(futures):
                    p = futures[future]
                    try:
                        data, names, stl_info, uncompressed, precomp = future.result()
                        contents_map[p]     = data
                        all_names_map[p]    = names
                        stl_info_map[p]     = stl_info
                        uncompressed_map[p] = uncompressed
                        precomp_map[p]      = precomp
                        logger.debug("  extracted %s → %d file(s), %d member(s), %d stl(s)",
                                     p.name, len(data), len(names), len(stl_info))
                    except Exception as exc:
                        warn = f"WARNING: Failed to extract {p.name} — file may be corrupted: {exc}"
                        self.warning.emit(warn)
                        logger.error("Extraction failed for %s: %s", p.name, exc, exc_info=True)
                        contents_map[p]     = {}
                        all_names_map[p]    = []
                        stl_info_map[p]     = {}
                        uncompressed_map[p] = None
                        precomp_map[p]      = {}

        if stl_backfill:
            logger.debug("Phase 3: lightweight STL listing for %d archive(s)", len(stl_backfill))
            _list_fn = partial(list_stl_members, unrar_path=self._config.unrar_path)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for p, stl_info in zip(stl_backfill, pool.map(_list_fn, stl_backfill)):
                    stl_info_map[p] = stl_info
                    logger.debug("  stl-listed %s → %d stl(s)", p.name, len(stl_info))

        logger.debug("Phase 3 complete — %d extracted, %d stl-listed",
                     len(contents_map), len(stl_backfill))
        return contents_map, all_names_map, stl_info_map, uncompressed_map, precomp_map

    def _phase_upsert(
        self,
        batch: list[Path],
        batch_start: int,
        total: int,
        hashes: dict[Path, str],
        existing_map: dict[str, STLModel],
        contents_map: dict[Path, dict[str, bytes]],
        all_names_map: dict[Path, list[str]],
        stl_info_map: dict[Path, dict],
        uncompressed_map: dict[Path, int | None],
        precomp_map: dict,
        session: Session,
        modified_folders: set[Path],
    ) -> bool:
        """Phase 4: serial DB upserts.  Returns ``True`` if stop was requested."""
        logger.debug("Phase 4: upserting %d archive(s) into database", len(batch))
        max_thumbs     = self._config.max_thumbnails
        max_thumb_size = self._config.max_thumb_size

        for idx, archive_path in enumerate(batch, start=batch_start + 1):
            if self._stop_requested:
                msg = "Crawl stopped by user."
                self.log.emit(msg)
                logger.info(msg)
                return True

            _existing  = existing_map.get(str(archive_path))
            _contents  = contents_map.get(archive_path)
            if _contents is not None or self._config.log_unchanged:
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
                    existing=_existing,
                    contents=_contents,
                    all_names=all_names_map.get(archive_path),
                    stl_info=stl_info_map.get(archive_path),
                    uncompressed_size=uncompressed_map.get(archive_path),
                    precomp=precomp_map.get(archive_path),
                )
                sp.commit()
                if _contents is not None:
                    if _existing is None:
                        self._stats_new += 1
                        self._new_archive_paths.add(str(archive_path))
                        self.archive_result.emit(str(archive_path), "new")
                    else:
                        self._stats_updated += 1
                        self.archive_result.emit(str(archive_path), "updated")
                    modified_folders.add(archive_path.parent)
                else:
                    self._stats_unchanged += 1
                self._consecutive_failures = 0
                logger.debug("  Upsert committed: %s", archive_path.name)
            except Exception as exc:
                sp.rollback()
                msg = f"  ERROR: {exc}"
                self.log.emit(msg)
                logger.error("Upsert failed for %s: %s", archive_path.name, exc, exc_info=True)
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    abort_msg = (
                        f"Crawl aborted: {self._consecutive_failures} consecutive upsert "
                        f"failures — last error: {exc}"
                    )
                    self.log.emit(abort_msg)
                    logger.error(abort_msg)
                    return True
            self.progress.emit(idx, total)

        # Emit once per batch instead of per archive to reduce UI repaints.
        self.counts_changed.emit(self._stats_new, self._stats_updated, self._stats_unchanged)
        return False

    def _post_export_metadata(self, modified_folders: set[Path], thumb_base: Path) -> None:
        """Post-crawl: write METADATA.7z sidecars for all modified folders."""
        include_thumbs = self._config.metadata_thumbnails
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_crawled_folder(self, archive_count: int) -> None:
        """Upsert a CrawledFolder row for source_dir with stats from this run."""
        path_str = str(self.source_dir)
        try:
            with session_scope() as session:
                folder = session.query(CrawledFolder).filter_by(path=path_str).first()
                if folder is None:
                    folder = CrawledFolder(path=path_str)
                    session.add(folder)
                folder.pattern         = self._pattern or None
                folder.last_crawled_at = datetime.now(timezone.utc)
                folder.archive_count   = archive_count
                folder.new_count       = self._stats_new
                folder.updated_count   = self._stats_updated
            logger.info("CrawledFolder recorded: path=%s archives=%d new=%d updated=%d",
                        path_str, archive_count, self._stats_new, self._stats_updated)
        except Exception as exc:
            logger.error("Failed to record crawled folder %s: %s", path_str, exc)

    def _collect_missing(self) -> list[dict]:
        """Return a snapshot of DB records whose archive file no longer exists on disk.

        Includes both records newly found to be absent and records already flagged
        ``is_missing=True`` from a previous crawl session, so that files moved to a
        folder not covered by the current crawl still get another reconciliation
        attempt on future crawls.

        Does NOT delete them — deletion (or metadata transfer for moved files)
        happens later in :meth:`_reconcile_moved_files`.
        """
        logger.debug("Collecting missing archive records from database…")
        missing: list[dict] = []
        seen_ids: set[int] = set()
        with session_scope() as session:
            rows = session.query(
                STLModel.id, STLModel.archive_path,
                STLModel.archive_hash, STLModel.archive_size,
                STLModel.is_missing,
            ).all()

        # Parallelise the filesystem existence check — was N serial syscalls.
        # Rows already flagged is_missing skip the syscall entirely.
        to_check = [(mid, ap) for mid, ap, _ah, _asize, im in rows if ap and not im]

        absent_ids: set[int] = set()
        if to_check:
            workers = max(4, self._config.max_workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = pool.map(lambda t: (t[0], not Path(t[1]).exists()), to_check)
                for mid, is_absent in results:
                    if is_absent:
                        absent_ids.add(mid)

        for mid, ap, ah, asize, already_missing in rows:
            if not ap:
                continue
            if (mid in absent_ids) or already_missing:
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    missing.append({
                        "id":       mid,
                        "path":     ap,
                        "hash":     ah or "",
                        "filename": Path(ap).name,
                        "size":     asize,
                    })
        if missing:
            msg = (f"Found {len(missing)} missing archive(s) — "
                   "will check for moved files after crawl.")
            self.log.emit(msg)
            logger.info(msg)
        else:
            logger.debug("No missing archives found.")
        return missing

    def _reconcile_moved_files(self, missing: list[dict]) -> None:
        """Match missing records against new archives found in this crawl.

        For each match, the old (missing) record's *archive_path* is updated to
        the new location so all user-edited metadata is preserved.  The freshly-
        created record at the new path is removed.  Unmatched missing records are
        flagged ``is_missing=True`` instead of being deleted, so a future crawl
        of a different folder can still find them.

        Matching strategy (in order):
        1. Identical content hash → definitive match.
        2. Same filename + same file size → likely moved (skipped when ambiguous).

        Collision guards:
        - If two *new* archives share the same hash, neither is used for hash
          matching (hash collisions among new files are vanishingly rare but
          theoretically possible with fallback fingerprints).
        - If two *new* archives share the same filename+size, the name+size
          fallback is skipped entirely for that key to avoid mis-attribution.
        - If two *missing* records share the same hash, hash matching is skipped
          for both (they are content duplicates; the duplicate-finder should
          handle them separately).
        """
        new_paths = self._new_archive_paths
        if not new_paths:
            self._mark_records_missing(missing)
            return

        msg = (f"Reconciling {len(missing)} missing record(s) against "
               f"{len(new_paths)} new archive(s)…")
        self.log.emit(msg)
        logger.info(msg)

        matched_ids: set[int] = set()

        with session_scope() as session:
            new_models: dict[str, STLModel] = {
                m.archive_path: m
                for m in session.query(STLModel)
                    .filter(STLModel.archive_path.in_(list(new_paths)))
                    .all()
            }

            # Build forward indexes, tracking collisions so ambiguous keys are
            # excluded from matching rather than picking arbitrarily.
            hash_to_path:       dict[str, str]   = {}
            hash_collision:     set[str]          = set()
            namesize_to_path:   dict[tuple, str]  = {}
            namesize_collision: set[tuple]        = set()

            for path, m in new_models.items():
                if m.archive_hash:
                    h = m.archive_hash
                    if h in hash_to_path:
                        hash_collision.add(h)
                    else:
                        hash_to_path[h] = path
                if m.archive_size is not None:
                    key = (Path(path).name, m.archive_size)
                    if key in namesize_to_path:
                        namesize_collision.add(key)
                    else:
                        namesize_to_path[key] = path

            # Detect hash collisions among the missing records themselves.
            missing_hash_collision: set[str] = set()
            seen_missing_hashes:    dict[str, int] = {}
            for rec in missing:
                h = rec["hash"]
                if not h:
                    continue
                if h in seen_missing_hashes:
                    missing_hash_collision.add(h)
                else:
                    seen_missing_hashes[h] = rec["id"]

            used_new: set[str] = set()

            for rec in missing:
                # --- hash match ---
                h = rec["hash"]
                new_path: str | None = None
                if h and h not in hash_collision and h not in missing_hash_collision:
                    candidate = hash_to_path.get(h)
                    if candidate and candidate not in used_new:
                        new_path = candidate

                # --- filename+size fallback ---
                if new_path is None and rec["filename"] and rec["size"] is not None:
                    key = (rec["filename"], rec["size"])
                    if key not in namesize_collision:
                        candidate = namesize_to_path.get(key)
                        if candidate and candidate not in used_new:
                            new_path = candidate
                        elif key in namesize_collision:
                            self.log.emit(
                                f"  Skipped ambiguous name+size match for "
                                f"{rec['filename']} — multiple candidates"
                            )

                if new_path is None:
                    continue

                old_model = session.get(STLModel, rec["id"])
                new_model = new_models.get(new_path)
                if old_model is None or new_model is None:
                    continue

                # Capture new model's filesystem metadata before deletion
                new_archive_path  = new_model.archive_path
                new_archive_hash  = new_model.archive_hash
                new_archive_size  = new_model.archive_size
                new_archive_mtime = new_model.archive_mtime_ns
                new_compression   = new_model.compression_rate

                # Delete new model's thumbnails; old record keeps its existing ones
                for img in list(new_model.images):
                    if img.thumbnail_path:
                        delete_thumbnail(img.thumbnail_path)
                    session.delete(img)
                session.flush()

                # Remove the freshly-created (shallow) record
                session.delete(new_model)
                session.flush()

                # Redirect old record to new location and clear missing flag
                old_model.archive_path     = new_archive_path
                old_model.archive_hash     = new_archive_hash
                old_model.archive_size     = new_archive_size
                old_model.archive_mtime_ns = new_archive_mtime
                old_model.archive_folder   = str(Path(new_archive_path).parent)
                old_model.is_missing       = False
                if new_compression is not None:
                    old_model.compression_rate = new_compression
                session.flush()

                matched_ids.add(rec["id"])
                used_new.add(new_path)

                msg = f"Moved: {rec['filename']} → {new_archive_path}"
                self.log.emit(msg)
                logger.info("File moved: %s → %s", rec["path"], new_archive_path)
                self.file_moved.emit(rec["path"], new_archive_path)

        if matched_ids:
            self.log.emit(f"Matched {len(matched_ids)} moved file(s).")

        unmatched = [r for r in missing if r["id"] not in matched_ids]
        self._mark_records_missing(unmatched)

    def _mark_records_missing(self, missing: list[dict]) -> None:
        """Handle unmatched missing records.

        Records whose archive path falls under the currently crawled folder are
        deleted immediately — the crawler is authoritative for that folder's
        contents.  Records outside the crawled scope are flagged
        ``is_missing=True`` so a future crawl of the other folder can still
        attempt reconciliation.

        When ``config.defer_missing_deletion`` is True, in-scope records are
        also only flagged ``is_missing=True`` (without emitting *file_deleted*)
        so the caller can attempt cross-folder reconciliation before committing
        to deletion.
        """
        if not missing:
            return
        source = Path(self.source_dir)
        defer = self._config.defer_missing_deletion
        deleted  = 0
        marked   = 0
        deferred = 0
        with session_scope() as session:
            for rec in missing:
                model = session.get(STLModel, rec["id"])
                if model is None:
                    continue
                try:
                    in_scope = Path(rec["path"]).is_relative_to(source)
                except ValueError:
                    in_scope = False

                if in_scope:
                    if defer:
                        # Defer: mark as missing without deleting or notifying UI.
                        # Cross-folder reconciliation will either match it as a move
                        # or delete it in the cleanup pass.
                        if not model.is_missing:
                            model.is_missing = True
                        deferred += 1
                        logger.info("Deferred deletion: %s", rec["path"])
                    else:
                        for img in list(model.images):
                            if img.thumbnail_path:
                                delete_thumbnail(img.thumbnail_path)
                            session.delete(img)
                        session.flush()
                        for stl in list(model.stl_files):
                            session.delete(stl)
                        session.flush()
                        session.delete(model)
                        deleted += 1
                        msg = f"Deleted: {rec['path']}"
                        self.log.emit(msg)
                        logger.info(msg)
                        self.file_deleted.emit(rec["path"])
                elif not model.is_missing:
                    model.is_missing = True
                    marked += 1
                    msg = f"Marked missing: {rec['path']}"
                    self.log.emit(msg)
                    logger.info(msg)
                    self.file_deleted.emit(rec["path"])

        if deleted:
            msg = f"Deleted {deleted} record(s) removed from the crawled folder."
            self.log.emit(msg)
            logger.info(msg)
        if deferred:
            logger.info("Deferred %d in-scope deletion(s) for cross-folder reconciliation.", deferred)
        if marked:
            msg = f"Marked {marked} record(s) as missing (outside crawled folder — use DB Maintenance to remove)."
            self.log.emit(msg)
            logger.info(msg)

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
        stl_info: Optional[dict] = None,
        uncompressed_size: Optional[int] = None,
        precomp: Optional[dict] = None,
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
            or Path(n.replace("\\", "/")).suffix.lower() == ".pdf"
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
        model.archive_folder = archive_path.parent.as_posix()
        # Update cached filesystem metadata
        try:
            st = archive_path.stat()
            model.archive_size = int(st.st_size)
            model.archive_mtime_ns = int(st.st_mtime_ns)
        except Exception:
            pass

        if uncompressed_size is not None and uncompressed_size > 0 and model.archive_size is not None:
            model.compression_rate = round(model.archive_size / uncompressed_size, 4)

        if all_names is None:
            logger.debug("  Listing archive contents for rule detection: %s", archive_path.name)
            all_names = list_contents(archive_path, self._config.unrar_path)
        logger.debug("  %d member(s) in archive", len(all_names))

        self._detect_rules(model, all_names, archive_path, session)

        if (self._config.warn_low_compression
                and model.compression_rate is not None
                and model.compression_rate >= self._config.low_compression_threshold):
            _warn_text = WARNING_LOW_COMPRESSION
            if model.warning:
                if _warn_text not in model.warning:
                    model.warning += "\n" + _warn_text
            else:
                model.warning = _warn_text

        if current_hash.startswith("fallback:"):
            _uw = WARNING_UNREADABLE
            if model.warning:
                if _uw not in model.warning:
                    model.warning += "\n" + _uw
            else:
                model.warning = _uw

        self._assign_metadata(model, meta, session)

        cfg = self._config
        cache = getattr(self, "_lookup_cache", None)
        if cfg.force_creator:
            model.creator = cfg.force_creator
        if cfg.force_source:
            model.source = cfg.force_source
        if cfg.force_sculptor:
            model.sculptor = get_or_create_sculptor(session, cfg.force_sculptor, cache=cache)
        if cfg.force_category:
            model.category = get_or_create_category(session, cfg.force_category, cache=cache)
        if cfg.force_collection:
            model.collection = get_or_create_collection(session, cfg.force_collection, cache=cache)
        if cfg.force_release:
            model.release = get_or_create_release(session, cfg.force_release, cache=cache)
        if cfg.force_scale:
            model.scales = [get_or_create_scale(session, cfg.force_scale, cache=cache)]
        if cfg.force_date:
            _year, _month = parse_release_date(cfg.force_date)
            if _year:
                model.release_year  = _year
                model.release_month = _month

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
                model.tags.append(get_or_create_tag(session, tag_name, cache=cache))
        logger.debug("  Tags: %d → %d (%s)",
                     prev_tag_count, len(model.tags),
                     ", ".join(t.name for t in model.tags) or "none")

        self._update_thumbnails(
            model, existing, contents, image_names,
            session, max_thumbs, max_thumb_size, archive_path, hash_changed,
            precomp_cache=precomp,
        )

        if self._config.store_stl_file_list and stl_info is not None:
            for old in list(model.stl_files):
                session.delete(old)
            session.flush()
            for fname, fsize in stl_info.items():
                session.add(STLFile(
                    model=model,
                    filename=fname,
                    file_size=fsize,
                    archive_hash=current_hash,
                ))
            model.stl_list_hash = current_hash
            logger.debug("  STL file list: %d entry/entries stored", len(stl_info))

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
                if self._config.log_unchanged:
                    msg = "  unchanged – pattern applied: " + ", ".join(f"{k}={v!r}" for k, v in extracted.items())
                    self.log.emit(msg)
                    logger.debug("  %s — %s", archive_path.name, msg.strip())
            else:
                if self._config.log_unchanged:
                    self.log.emit("  unchanged – pattern: no match")
                logger.debug("  %s — unchanged, pattern had no match", archive_path.name)
        else:
            if self._config.log_unchanged:
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

        warnings = detect_warnings(
            all_names,
            check_presupported=self._config.warn_presupported_stl,
            check_no_images=self._config.warn_no_images,
            check_nested_archive=self._config.warn_nested_archive,
            check_no_printable=self._config.warn_no_printable,
            presupport_patterns=self._config.presupport_patterns,
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
            cache = getattr(self, "_lookup_cache", None)
            model.scales = [get_or_create_scale(session, s, cache=cache) for s in scales]

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
        model.creator       = meta.creator
        model.source        = meta.distributor
        _year, _month = parse_release_date(meta.release_date or "")
        model.release_year  = _year
        model.release_month = _month
        model.description   = meta.description

        if meta.release:
            cache = getattr(self, "_lookup_cache", None)
            model.release = get_or_create_release(session, meta.release, cache=cache)

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
        precomp_cache: Optional[dict] = None,
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
            precomp_cache: ``{filename: (jpeg_bytes, dhash_int)}`` pre-computed
                in parallel Phase 3; when populated, skips serial resize/dHash.
        """
        if hash_changed:
            old_thumb_count = len([i for i in existing.images if i.thumbnail_path])
            logger.debug("  Hash changed — purging %d old thumbnail(s)", old_thumb_count)
            for old_img in list(existing.images):
                if old_img.thumbnail_path:
                    delete_thumbnail(old_img.thumbnail_path)
                session.delete(old_img)

        if not existing or hash_changed:
            # Flush Image records first so each gets a PK before LMDB writes.
            candidates = image_names[:max_thumbs]
            if len(image_names) > max_thumbs:
                logger.debug("  Thumbnail limit (%d) reached, skipping remaining images",
                             max_thumbs)
            img_records: list[Image] = []
            for filename in candidates:
                img = Image(model=model, filename=filename)
                session.add(img)
                img_records.append(img)
            session.flush()

            count = 0
            if precomp_cache:
                # Fast path: JPEG bytes and dHash were computed in parallel Phase 3.
                resolved_pre: list[tuple[Image, bytes, object]] = []
                for img in img_records:
                    jpeg, dhash_int = precomp_cache.get(img.filename, (None, None))
                    if jpeg:
                        resolved_pre.append((img, jpeg, dhash_int))
                    else:
                        session.delete(img)
                        logger.warning("Thumbnail conversion failed: %s in %s",
                                       img.filename, archive_path.name)

                write_raw_thumbnails_batch([(img.id, jpeg) for img, jpeg, _ in resolved_pre])

                embed_vecs: list = []
                if self._config.embed_during_crawl:
                    try:
                        from app.utils import image_embedding as _emb
                        embed_vecs = _emb.embed_batch([jpeg for _, jpeg, _ in resolved_pre])
                    except Exception:
                        embed_vecs = [None] * len(resolved_pre)

                for i, (img, jpeg, dhash_int) in enumerate(resolved_pre):
                    img.thumbnail_path = str(img.id)
                    if dhash_int is not None:
                        img.dhash = format_dhash(dhash_int)
                    if embed_vecs:
                        # _emb is already imported above where embed_vecs was built.
                        try:
                            vec = embed_vecs[i]
                            if vec is not None:
                                img.embedding = _emb.to_blob(vec)
                                img.embedding_model = _emb.EMBED_MODEL_ID
                        except Exception:
                            logger.debug("  Failed to store embedding for image id %s", img.id)
                    count += 1
                    logger.debug("  Thumbnail saved: %s → %s", img.filename, img.thumbnail_path)
            else:
                # Slow path (fallback): resolve raw bytes and resize serially.
                from app.utils.thumbnailer import pdf_first_page_to_image_bytes
                resolved_raw: list[tuple[Image, bytes]] = []
                for img in img_records:
                    raw = contents.get(img.filename)
                    if raw is None:
                        session.delete(img)
                        logger.warning("Image catalogued but not extracted: %s in %s",
                                       img.filename, archive_path.name)
                        continue
                    if Path(img.filename.replace("\\", "/")).suffix.lower() == ".pdf":
                        raw = pdf_first_page_to_image_bytes(raw)
                        if raw is None:
                            session.delete(img)
                            logger.warning("PDF render failed: %s in %s",
                                           img.filename, archive_path.name)
                            continue
                    resolved_raw.append((img, raw))
                img_records = [img for img, _ in resolved_raw]

                batch_items = [
                    (img.id, raw, max_thumb_size)
                    for img, raw in resolved_raw
                ]
                batch_results = save_thumbnails_batch(batch_items)

                embed_vecs = []
                if self._config.embed_during_crawl:
                    try:
                        from app.utils import image_embedding as _emb
                        jpegs = [
                            jpeg_bytes if rel_path else b""
                            for _img, (_iid, rel_path, jpeg_bytes) in zip(img_records, batch_results)
                        ]
                        embed_vecs = _emb.embed_batch(jpegs)
                    except Exception:
                        embed_vecs = [None] * len(img_records)

                for i, (img, (_iid, rel_path, jpeg_bytes)) in enumerate(zip(img_records, batch_results)):
                    if rel_path:
                        img.thumbnail_path = rel_path
                        try:
                            if jpeg_bytes:
                                h = compute_dhash_from_jpeg(jpeg_bytes)
                                if h is not None:
                                    img.dhash = format_dhash(h)
                        except Exception:
                            logger.debug("  Failed to compute dHash for image id %s", img.id)
                        if embed_vecs:
                            # _emb is already imported above where embed_vecs was built.
                            try:
                                vec = embed_vecs[i]
                                if vec is not None:
                                    img.embedding = _emb.to_blob(vec)
                                    img.embedding_model = _emb.EMBED_MODEL_ID
                            except Exception:
                                logger.debug("  Failed to store embedding for image id %s", img.id)
                        count += 1
                        logger.debug("  Thumbnail saved: %s → %s", img.filename, rel_path)
                    else:
                        session.delete(img)
                        msg = f"  WARNING: thumbnail conversion failed for {img.filename}"
                        self.log.emit(msg)
                        logger.warning("Thumbnail conversion failed: %s in %s",
                                       img.filename, archive_path.name)

            self.log.emit(f"  {count} thumbnail(s) stored")
            logger.debug("  %d thumbnail(s) stored for %s", count, archive_path.name)

            folder_count = self._store_folder_images(model, archive_path, session, max_thumb_size)
            if folder_count:
                self.log.emit(f"  {folder_count} folder image(s) stored")
                logger.debug("  %d folder image(s) for %s", folder_count, archive_path.name)

    def _store_folder_images(
        self,
        model: STLModel,
        archive_path: Path,
        session: Session,
        max_thumb_size: int,
    ) -> int:
        """Store on-disk image files from ancestor folders as extra thumbnails.

        Scans the archive's parent directory and each ancestor up to and
        including ``source_dir``.  Found images are stored without counting
        toward the per-archive thumbnail limit.

        Returns the number of images successfully stored.
        """
        count = 0
        folder = archive_path.parent
        while True:
            try:
                folder.relative_to(self.source_dir)
            except ValueError:
                break  # above source_dir — stop
            try:
                candidates = sorted(
                    p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                )
            except OSError:
                candidates = []
            for p in candidates:
                try:
                    raw = p.read_bytes()
                except OSError:
                    continue
                img = Image(model=model, filename=str(p))
                session.add(img)
                session.flush()
                rel_path = save_thumbnail(raw, img.id, max_size=max_thumb_size)
                if rel_path:
                    img.thumbnail_path = rel_path
                    try:
                        jpeg = get_thumbnail_bytes(img.id)
                        if jpeg:
                            h = compute_dhash_from_jpeg(jpeg)
                            if h is not None:
                                img.dhash = format_dhash(h)
                    except Exception:
                        pass
                    count += 1
                    logger.debug("  Folder image stored: %s", p.name)
                else:
                    session.delete(img)
                    logger.debug("  Folder image thumbnail failed: %s", p.name)
            if folder == self.source_dir:
                break
            folder = folder.parent
        return count

    def _log_metadata(self, meta: ModelMetadata) -> None:
        """Emit a log line summarising the extracted metadata fields.

        Args:
            meta: The ``ModelMetadata`` instance to summarise.
        """
        meta_parts = []
        if meta.title:        meta_parts.append(f"title={meta.title!r}")
        if meta.creator:      meta_parts.append(f"creator={meta.creator!r}")
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
