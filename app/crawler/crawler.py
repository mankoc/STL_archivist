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
from app.crawler.archive import extract_archive_contents, archive_hash, list_contents, list_stl_members, IMAGE_SUFFIXES
from app.crawler.folder_metadata import (
    export_folder_metadata,
    import_folder_metadata,
)
from app.crawler.metadata import extract_metadata, ModelMetadata
from app.crawler.rules import (
    detect_presupport, detect_scale, detect_warnings,
)
from app.utils.thumbnailer import (
    get_thumbnail_base_dir,
)
from app.utils.image_hash import compute_dhash_from_jpeg, format_dhash
from app.utils.path_pattern import parse_path, parse_release_date


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
    crawl_finished       = pyqtSignal()
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
        """Main thread entry point: the crawl, in the native core (:meth:`_run_native`)."""
        self._reset_state()
        self._run_native()

    def _run_native(self) -> None:
        """The whole crawl in the native core (``stl_core::crawler``).

        What the core leaves to Python comes back as calls on
        :class:`_NativeCrawlServices`; its events become this worker's signals.
        """
        from app.crawler.archive import _unrar_tool
        from app.utils.thumbnailer import native_store
        cfg = self._config
        logger.info("Native crawl started — source: %s, pattern: %r, workers: %d",
                    self.source_dir, self._pattern, cfg.max_workers)
        config = dict(self._native_config())
        config.update({
            "source_dir": str(self.source_dir), "pattern": self._pattern,
            "recurse": bool(cfg.recurse_subdirs), "force_rescan": bool(cfg.force_rescan),
            "max_thumbnails": int(cfg.max_thumbnails), "max_thumb_size": int(cfg.max_thumb_size),
            "max_workers": int(cfg.max_workers), "unrar": _unrar_tool(cfg.unrar_path),
            "sidecars": bool(cfg.metadata_sidecar_enabled and get_thumbnail_base_dir() is not None),
            "sidecar_thumbnails": bool(cfg.metadata_thumbnails),
            "warn_presupported": bool(cfg.warn_presupported_stl), "warn_no_images": bool(cfg.warn_no_images),
            "warn_nested_archive": bool(cfg.warn_nested_archive),
            "warn_no_printable": bool(cfg.warn_no_printable),
            "presupport_patterns": list(cfg.presupport_patterns),
            "embed": bool(cfg.embed_during_crawl), "log_unchanged": bool(cfg.log_unchanged),
            "defer_missing_deletion": bool(cfg.defer_missing_deletion),
        })

        def sink(kind, *args):
            if kind == "log":
                self.log.emit(args[0])
            elif kind == "phase":
                self.phase.emit(args[0])
            elif kind == "warning":
                self.warning.emit(args[0])
            elif kind == "progress":
                self.progress.emit(*args)
            elif kind == "counts":
                self._stats_new, self._stats_updated, self._stats_unchanged = args
                self.counts_changed.emit(*args)
            elif kind == "result":
                self.archive_result.emit(*args)
            elif kind == "moved":
                self.file_moved.emit(*args)
            elif kind == "deleted":
                self.file_deleted.emit(args[0])

        summary = self._lib.crawl(config, _NativeCrawlServices(self), sink, native_store(),
                                  lambda: self._stop_requested)
        self._stats_new = summary["new"]
        self._stats_updated = summary["updated"]
        self._stats_unchanged = summary["unchanged"]
        self._new_archive_paths = set(summary["new_paths"])
        self.crawl_finished.emit()

    def _reset_state(self) -> None:
        """Per-run state, before :meth:`run` or :meth:`recrawl`."""
        self._stop_requested = False
        self._consecutive_failures = 0
        self._stats_new = 0
        self._stats_updated = 0
        self._stats_unchanged = 0
        self._new_archive_paths: set[str] = set()
        self._lib = self._native_library()
        self._existing_hashes: dict[str, str] = {}
        self._move_candidates: dict = {}
        self._early_moved_ids: set[int] = set()
        self._moved_new_paths: set[str] = set()

    def _process_batch(self, batch: list[Path], batch_start: int, total: int,
                       modified_folders: set[Path]) -> bool:
        """Phases 1–4 for one batch; returns ``True`` when the crawl should stop."""
        hashes = self._phase_hash(batch)
        if batch_start == 0:
            self.phase.emit("Checking database for changes…")
        to_extract, stl_backfill, existing_ids = self._native_diff(batch, hashes)
        if to_extract and batch_start == 0:
            self.phase.emit("Extracting new/changed archives…")
        extracted = self._phase_extract(to_extract, stl_backfill)
        return self._native_upsert(batch, batch_start, total, hashes, existing_ids,
                                   *extracted, modified_folders)

    def recrawl(self, archives: list[Path]) -> None:
        """Re-extract *archives* (all under ``source_dir``) as a forced rescan and
        refresh their folders' sidecars; no move or deletion handling.  The
        browser's Recrawl action uses it (synchronously)."""
        self._reset_state()
        self._config.force_rescan = True
        modified_folders: set[Path] = set()
        for start in range(0, len(archives), self.BATCH_SIZE):
            if self._process_batch(archives[start:start + self.BATCH_SIZE], start, len(archives),
                                   modified_folders):
                break
        thumb_base = get_thumbnail_base_dir()
        if modified_folders and thumb_base is not None and self._config.metadata_sidecar_enabled:
            self._post_export_metadata(modified_folders, thumb_base)

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
        for ap, ah, asize, amtime in self._lib.crawl_existing(batch_strs):
            existing[ap] = (ah, asize, amtime)
        self._existing_hashes = {ap: row[0] for ap, row in existing.items()}

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

    def _post_export_metadata(self, modified_folders: set[Path], thumb_base: Path) -> None:
        """Post-crawl: write METADATA.7z sidecars for all modified folders."""
        include_thumbs = self._config.metadata_thumbnails
        self.log.emit(f"Updating METADATA.7z in {len(modified_folders)} folder(s)…")
        logger.info("Post-crawl: updating METADATA.7z in %d folder(s) (thumbnails=%s)",
                    len(modified_folders), include_thumbs)
        for folder in sorted(modified_folders):
            if self._stop_requested:
                break
            try:
                export_folder_metadata(folder, None, thumb_base,
                                       include_thumbnails=include_thumbs)
                self.log.emit(f"  Updated METADATA.7z in {folder.name}")
            except Exception as exc:
                logger.error("Failed to update METADATA.7z in %s: %s",
                             folder, exc, exc_info=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Native database phases (rust/stl-core/src/storage/crawl.rs)
    # ------------------------------------------------------------------

    @staticmethod
    def _native_library():
        """The native ``LibraryDb`` the crawl writes through."""
        from app.database import native_db
        return native_db.library()

    def _native_diff(self, batch: list[Path], hashes: dict[Path, str]):
        """Phase 2 natively: returns ``(to_extract, stl_backfill, {path: model id})``."""
        candidates = getattr(self, "_move_candidates", {}) or {}
        d = self._lib.crawl_diff(
            [(str(p), hashes[p]) for p in batch], bool(self._config.force_rescan),
            bool(self._config.store_stl_file_list),
            {h: rec["id"] for h, rec in candidates.items()})
        for path in d["unreadable"]:
            msg = f"WARNING: Cannot open archive (possibly corrupted): {Path(path).name}"
            self.warning.emit(msg)
            logger.warning("Corrupted or unreadable archive: %s", path)
        for h in d["consumed"]:
            rec = candidates.pop(h, None)
            moved = next((m for m in d["moved"] if m[0] == h), None)
            if rec is not None and moved is not None:
                self._early_moved_ids.add(rec["id"])
                self._moved_new_paths.add(moved[2])
                self.log.emit(f"Moved: {rec['filename']} → {moved[2]}")
                logger.info("File moved: %s → %s", rec["path"], moved[2])
                self.file_moved.emit(rec["path"], moved[2])
        by_str = {str(p): p for p in batch}
        return ([by_str[x] for x in d["to_extract"]], [by_str[x] for x in d["stl_backfill"]],
                dict(d["existing"]))

    def _pattern_match(self, archive_path: Path) -> Optional[dict]:
        """The path pattern's match for *archive_path* with Python's date and
        number parses, as the native upsert takes it (``None``: no match)."""
        if not self._pattern:
            return None
        try:
            rel_path = "/" + str(archive_path.relative_to(self.source_dir)).replace("\\", "/")
        except ValueError:
            rel_path = str(archive_path)
        extracted = parse_path(self._pattern, rel_path)
        if not extracted:
            return None
        seq = None
        if extracted.get("sequence"):
            try:
                seq = int(extracted["sequence"])
            except ValueError:
                seq = None
        date = parse_release_date(extracted["release_date"]) if extracted.get("release_date") else None
        return {"fields": list(extracted.items()), "release_date": date, "sequence": seq}

    def _folder_images(self, archive_path: Path, max_thumb_size: int) -> list[tuple]:
        """``_store_folder_images``' files with their thumbnails precomputed:
        ``(file path, jpeg or None, dHash or None)``."""
        from app.utils.thumbnailer import make_thumbnail
        out = []
        folder = archive_path.parent
        while True:
            try:
                folder.relative_to(self.source_dir)
            except ValueError:
                break
            try:
                candidates = sorted(p for p in folder.iterdir()
                                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
            except OSError:
                candidates = []
            for p in candidates:
                try:
                    raw = p.read_bytes()
                except OSError:
                    continue
                jpeg = make_thumbnail(raw, max_thumb_size)
                h = compute_dhash_from_jpeg(jpeg) if jpeg else None
                out.append((str(p), jpeg, format_dhash(h) if h is not None else None))
            if folder == self.source_dir:
                break
            folder = folder.parent
        return out

    def _native_changed(self, archive_path: Path, current_hash: str, existing_id, contents,
                        all_names, stl_info, uncompressed_size, precomp) -> dict:
        """What Python computes for a new or changed archive (``Changed``)."""
        cfg = self._config
        meta = extract_metadata(archive_path, contents=contents, source_dir=self.source_dir)
        self._log_metadata(meta)
        if all_names is None:
            all_names = list_contents(archive_path, cfg.unrar_path)
        warnings = detect_warnings(
            all_names,
            check_presupported=cfg.warn_presupported_stl,
            check_no_images=cfg.warn_no_images,
            check_nested_archive=cfg.warn_nested_archive,
            check_no_printable=cfg.warn_no_printable,
            presupport_patterns=cfg.presupport_patterns,
        )
        year, month = parse_release_date(meta.release_date or "")
        image_names = [
            n for n in contents
            if Path(n.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
            or Path(n.replace("\\", "/")).suffix.lower() == ".pdf"
        ]
        self.log.emit(f"  {len(contents)} relevant file(s) extracted ({len(image_names)} image(s))")
        candidates = image_names[:cfg.max_thumbnails]
        pre = precomp or {}
        thumbs = []
        for name in candidates:
            jpeg, dhash_int = pre.get(name, (None, None))
            thumbs.append([name, jpeg or None, format_dhash(dhash_int) if jpeg and dhash_int is not None else None, None])
        if cfg.embed_during_crawl:
            resolved = [t for t in thumbs if t[1]]
            try:
                from app.utils import image_embedding as _emb
                from app.utils.image_embedding import to_blob
                vecs = _emb.embed_batch([t[1] for t in resolved])
                for t, v in zip(resolved, vecs):
                    t[3] = to_blob(v) if v is not None else None
            except Exception:
                logger.debug("  Embedding during crawl failed", exc_info=True)
        existing_hash = self._existing_hashes.get(str(archive_path))
        fresh = existing_id is None or existing_hash != current_hash
        return {
            "title": meta.title, "creator": meta.creator, "source": meta.distributor,
            "release_year": year, "release_month": month, "description": meta.description,
            "release": meta.release, "tags": list(meta.tags),
            "presupports": detect_presupport(all_names), "warnings": warnings or None,
            "scales": detect_scale([archive_path.name] + all_names),
            "uncompressed_size": uncompressed_size,
            "thumbs": [tuple(t) for t in thumbs],
            "folder_images": self._folder_images(archive_path, cfg.max_thumb_size) if fresh else [],
            "stl_info": list(stl_info.items()) if cfg.store_stl_file_list and stl_info is not None else None,
        }

    def _native_config(self) -> dict:
        cfg = self._config
        from app.utils.image_embedding import EMBED_DIM
        return {
            "store_stl": bool(cfg.store_stl_file_list),
            "warn_low_compression": bool(cfg.warn_low_compression),
            "low_compression_threshold": float(cfg.low_compression_threshold),
            "force_creator": cfg.force_creator or None, "force_source": cfg.force_source or None,
            "force_sculptor": cfg.force_sculptor or None, "force_category": cfg.force_category or None,
            "force_collection": cfg.force_collection or None, "force_release": cfg.force_release or None,
            "force_scale": cfg.force_scale or None,
            "force_date": parse_release_date(cfg.force_date) if cfg.force_date else None,
            "embed_dim": EMBED_DIM,
        }

    def _native_upsert(self, batch, batch_start, total, hashes, existing_ids, contents_map,
                       all_names_map, stl_info_map, uncompressed_map, precomp_map,
                       modified_folders) -> bool:
        """Phase 4 natively; returns ``True`` when the crawl should stop."""
        from app.utils.thumbnailer import native_store
        inputs = []
        for idx, archive_path in enumerate(batch, start=batch_start + 1):
            existing_id = existing_ids.get(str(archive_path))
            contents = contents_map.get(archive_path)
            if contents is not None or self._config.log_unchanged:
                self.log.emit(f"[{idx}/{total}] {archive_path.name}")
            changed = None
            if contents is not None:
                changed = self._native_changed(
                    archive_path, hashes[archive_path], existing_id, contents,
                    all_names_map.get(archive_path), stl_info_map.get(archive_path),
                    uncompressed_map.get(archive_path), precomp_map.get(archive_path))
            inputs.append({"path": str(archive_path), "hash": hashes[archive_path],
                           "existing": existing_id, "extracted": self._pattern_match(archive_path),
                           "changed": changed})
            if self._stop_requested:
                break
        results = self._lib.crawl_upsert_batch(
            inputs, self._native_config(), native_store(),
            self._consecutive_failures, self.MAX_CONSECUTIVE_FAILURES)
        for k, (inp, (error, thumbs, folder_imgs)) in enumerate(zip(inputs, results)):
            idx = batch_start + 1 + k
            archive_path = batch[k]
            if error is not None:
                self.log.emit(f"  ERROR: {error}")
                logger.error("Upsert failed for %s: %s", archive_path.name, error)
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    abort_msg = (f"Crawl aborted: {self._consecutive_failures} consecutive upsert "
                                 f"failures — last error: {error}")
                    self.log.emit(abort_msg)
                    logger.error(abort_msg)
                    return True
            else:
                self._consecutive_failures = 0
                if inp["changed"] is not None:
                    if inp["existing"] is None:
                        self._stats_new += 1
                        self._new_archive_paths.add(str(archive_path))
                        self.log.emit("  new")
                        self.archive_result.emit(str(archive_path), "new")
                    else:
                        self._stats_updated += 1
                        self.log.emit("  updated")
                        self.archive_result.emit(str(archive_path), "updated")
                    self.log.emit(f"  {thumbs} thumbnail(s) stored")
                    if folder_imgs:
                        self.log.emit(f"  {folder_imgs} folder image(s) stored")
                    modified_folders.add(archive_path.parent)
                else:
                    self._stats_unchanged += 1
                    if str(archive_path) in self._moved_new_paths:
                        modified_folders.add(archive_path.parent)
            self.progress.emit(idx, total)
        self.counts_changed.emit(self._stats_new, self._stats_updated, self._stats_unchanged)
        return self._stop_requested

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


class _NativeCrawlServices:
    """What the native crawl asks Python for (see ``stl_core::crawler::Request``).

    Each method answers one request with the same functions the Python
    pipeline uses, so a native crawl gives the Python crawl's results.
    """

    def __init__(self, worker: "CrawlerWorker") -> None:
        self._w = worker
        self._cfg = worker._config

    def hash(self, path: str) -> str:
        return archive_hash(Path(path), unrar_path=self._cfg.unrar_path)

    def extract(self, path: str):
        names: list[str] = []
        stl: dict = {}
        unc: list[int] = []
        try:
            data = extract_archive_contents(Path(path), unrar_path=self._cfg.unrar_path, all_names_out=names,
                                            stl_info_out=stl, uncompressed_size_out=unc)
        except Exception as exc:
            self._w.warning.emit(f"WARNING: Failed to extract {Path(path).name} — file may be corrupted: {exc}")
            logger.error("Extraction failed for %s: %s", path, exc, exc_info=True)
            return [], [], [], None
        return list(data.items()), names, list(stl.items()), (unc[0] if unc else None)

    def list_stl(self, path: str):
        return list(list_stl_members(Path(path), unrar_path=self._cfg.unrar_path).items())

    def thumbnail(self, data: bytes, pdf: bool):
        from app.utils.thumbnailer import _make_thumbnail_py, make_thumbnail, pdf_first_page_to_image_bytes
        if pdf:
            raw = pdf_first_page_to_image_bytes(data) or b""
            return make_thumbnail(raw, self._cfg.max_thumb_size) if raw else None
        return _make_thumbnail_py(data, self._cfg.max_thumb_size)

    def embed(self, jpegs: list):
        try:
            from app.utils import image_embedding as _emb
            from app.utils.image_embedding import to_blob
            return [to_blob(v) if v is not None else None for v in _emb.embed_batch(list(jpegs))]
        except Exception:
            logger.debug("Embedding during crawl failed", exc_info=True)
            return [None] * len(jpegs)

    def metadata(self, path: str, files: list):
        from app.crawler.metadata import _extract_metadata_py
        m = _extract_metadata_py(Path(path), dict(files), self._w.source_dir)
        return {"title": m.title, "creator": m.creator, "distributor": m.distributor,
                "release_date": m.release_date, "url": m.url, "description": m.description,
                "tags": [str(t) for t in m.tags], "release": m.release}

    def rules(self, archive_name: str, names: list):
        cfg = self._cfg
        warnings = detect_warnings(
            names, check_presupported=cfg.warn_presupported_stl, check_no_images=cfg.warn_no_images,
            check_nested_archive=cfg.warn_nested_archive, check_no_printable=cfg.warn_no_printable,
            presupport_patterns=cfg.presupport_patterns)
        return detect_presupport(names), warnings or "", detect_scale([archive_name] + list(names))

    def sidecar_import(self, folder: str) -> int:
        return len(import_folder_metadata(Path(folder), None, get_thumbnail_base_dir()))

    def sidecar_export(self, folder: str) -> None:
        try:
            export_folder_metadata(Path(folder), None, get_thumbnail_base_dir(),
                                   include_thumbnails=self._cfg.metadata_thumbnails)
        except Exception as exc:
            logger.error("Failed to update METADATA.7z in %s: %s", folder, exc, exc_info=True)
