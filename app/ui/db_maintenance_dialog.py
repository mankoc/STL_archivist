"""
Database Maintenance — verify that all catalogued archives still exist on disk,
relocate moved archives by content hash, or remove stale records.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.constants import IMAGE_SUFFIXES
from app.ui.browser_common import _fmt_size
from app.crawler.rules import (
    explain_warnings, RETIRED_WARNING_SUPPORTS, WARNING_NO_IMAGES,
    WARNING_PRESUPPORTED,
)

logger = logging.getLogger(__name__)

from app.services.maintenance import (  # noqa: E402 — status strings shared with the service
    STATUS_OK as _STATUS_OK, STATUS_MISSING as _STATUS_MISSING,
    STATUS_FLAGGED as _STATUS_FLAGGED, STATUS_FOUND as _STATUS_FOUND,
    STATUS_MODIFIED as _STATUS_MODIFIED, STATUS_MOVED_DB as _STATUS_MOVED_DB,
    STATUS_DUPLICATE as _STATUS_DUPLICATE,
)

_COL_ID       = 0
_COL_TITLE    = 1
_COL_PATH     = 2
_COL_STATUS   = 3
_COL_MATCH    = 4

def _color_missing() -> QColor:
    from app.ui.config_dialog import get_missing_file_color
    return QColor(get_missing_file_color())

def _color_found() -> QColor:
    from app.ui.config_dialog import get_found_file_color
    return QColor(get_found_file_color())

def _color_modified() -> QColor:
    return QColor(180, 100, 0)

def _color_flagged() -> QColor:
    return QColor(140, 60, 180)   # purple — flagged by crawler, not yet confirmed absent

def _color_duplicate() -> QColor:
    return QColor(180, 120, 0)   # amber — duplicate record in the database

def _color_moved_db() -> QColor:
    return QColor(30, 100, 180)

_COLOR_FG = QColor(255, 255, 255)


class VerifyWorker(QThread):
    """Check whether each archive_path in the database still exists on disk.

    Thin wrapper around :func:`app.services.maintenance.verify_archives`.

    Signals:
        progress(current, total): Archive index and total count.
        log(message): Human-readable status line for the UI log panel.
        verify_finished(results): List of result dicts once verification completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    verify_finished = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.services.maintenance import verify_archives
        self._stop_requested = False
        results = verify_archives(
            progress=self.progress.emit, log=self.log.emit,
            should_stop=lambda: self._stop_requested,
        )
        self.verify_finished.emit(results)


class SearchWorker(QThread):
    """Scan a folder tree for archives that match missing database records by content hash.

    For archives stored with a fallback fingerprint ("fallback:{size}:{mtime}") the
    file size is used as a fast pre-filter before attempting hash comparison.  All
    other matches are confirmed via the full CRC-based content hash (reads archive
    catalog only — no decompression required).

    Signals:
        progress(current, total): Candidate file index and total candidate count.
        log(message): Human-readable status line for the UI log panel.
        found(model_id, new_path): Emitted each time a missing archive is located.
        search_finished(): Emitted when the scan completes or is stopped.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    found    = pyqtSignal(int, str)
    search_finished = pyqtSignal()

    def __init__(self, search_root: Path, targets: dict, parent=None) -> None:
        """
        targets: mapping of {archive_hash: model_id} for the missing records to locate.
        """
        super().__init__(parent)
        self._stop_requested = False
        self._search_root = search_root
        self._targets = targets

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.crawler.archive import ARCHIVE_SUFFIXES, METADATA_FILENAME, archive_hash
        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.ui.config_dialog import get_unrar_path

        self._stop_requested = False
        unrar = get_unrar_path()

        # Extract expected file sizes from fallback fingerprints ("fallback:{size}:{mtime}")
        # so we can skip obviously wrong candidates before computing their hash.
        fallback_sizes: dict[str, int] = {}
        for h in self._targets:
            if h.startswith("fallback:"):
                parts = h.split(":")
                if len(parts) >= 2:
                    try:
                        fallback_sizes[h] = int(parts[1])
                    except ValueError:
                        pass

        candidates = [
            p for p in self._search_root.rglob("*")
            if p.suffix.lower() in ARCHIVE_SUFFIXES and p.name != METADATA_FILENAME
        ]
        total = len(candidates)
        if total == 0:
            self.log.emit("No archive files found in the selected folder.")
            self.search_finished.emit()
            return

        self.log.emit(
            f"Scanning {total} archive(s) in {self._search_root} "
            f"for {len(self._targets)} missing record(s)…"
        )

        matched = 0
        remaining = dict(self._targets)

        for idx, candidate in enumerate(candidates, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break
            if not remaining:
                self.log.emit("All missing archives have been found.")
                break

            self.progress.emit(idx, total)

            # Size pre-filter: if all remaining targets have fallback fingerprints and
            # this candidate's size doesn't match any of them, skip the expensive hash.
            if fallback_sizes and all(h in fallback_sizes for h in remaining):
                try:
                    cand_size = candidate.stat().st_size
                    if not any(cand_size == sz for h, sz in fallback_sizes.items() if h in remaining):
                        continue
                except OSError:
                    pass

            try:
                h = archive_hash(candidate, unrar_path=unrar)
            except Exception as exc:
                self.log.emit(f"  Could not hash {candidate.name}: {exc}")
                continue

            if h not in remaining:
                continue

            model_id = remaining.pop(h)
            new_path = str(candidate)
            try:
                with session_scope() as session:
                    model = session.query(STLModel).filter(STLModel.id == model_id).first()
                    if model:
                        model.archive_path = new_path
                self.found.emit(model_id, new_path)
                self.log.emit(f"  [{idx}/{total}] Found: {candidate.name} → updated record #{model_id}")
                matched += 1
            except Exception as exc:
                self.log.emit(f"  ERROR updating DB for {candidate.name}: {exc}")
                logger.error(
                    "Failed to update archive_path for model %d: %s", model_id, exc, exc_info=True
                )

        self.log.emit(
            f"Search complete — {matched} of {len(self._targets)} missing archive(s) located."
        )
        self.search_finished.emit()


class DHashWorker(QThread):
    """Compute and persist dHash values for Image records that are missing them.

    For each image, LMDB thumbnail bytes are tried first; if absent the legacy
    thumbnail blob stored in the DB column is used as a fallback.

    Signals:
        progress(current, total): Image index and total count.
        log(message): Human-readable status line for the UI log panel.
        hash_finished(computed, skipped): Counts once the run completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    hash_finished = pyqtSignal(int, int)   # (computed, skipped)

    _BATCH = 200

    def __init__(self, recompute_all: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._recompute_all = recompute_all

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.database.db import session_scope
        from app.database.models import Image as DBImage
        from app.utils.thumbnailer import get_thumbnail_bytes
        from app.utils.image_hash import DHASH_HEX_LEN, compute_dhash_from_jpeg, format_dhash

        self._stop_requested = False
        computed = 0
        skipped  = 0

        with session_scope() as session:
            query = session.query(DBImage.id, DBImage.thumbnail)
            if not self._recompute_all:
                # Only images without a current hash: none yet, or one in an older
                # format (64-bit, 16 hex chars) that comparisons skip.
                from sqlalchemy import func
                query = query.filter(
                    DBImage.dhash.is_(None) | (func.length(DBImage.dhash) != DHASH_HEX_LEN)
                )
            rows = query.all()

        total = len(rows)
        if total == 0:
            self.log.emit("All images already have a dHash computed.")
            self.hash_finished.emit(0, 0)
            return

        verb = "Recomputing" if self._recompute_all else "Computing"
        self.log.emit(f"{verb} dHash for {total} image(s)…")

        pending: list[tuple[int, str]] = []   # (image_id, dhash_hex)

        def flush() -> None:
            if not pending:
                return
            with session_scope() as s:
                for img_id, dhash_hex in pending:
                    img = s.query(DBImage).filter(DBImage.id == img_id).first()
                    if img:
                        img.dhash = dhash_hex
            pending.clear()

        for idx, (image_id, legacy_blob) in enumerate(rows, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break

            self.progress.emit(idx, total)

            jpeg = get_thumbnail_bytes(image_id) or legacy_blob
            if not jpeg:
                skipped += 1
                continue

            h = compute_dhash_from_jpeg(jpeg)
            if h is None:
                skipped += 1
                continue

            pending.append((image_id, format_dhash(h)))
            computed += 1

            if len(pending) >= self._BATCH:
                flush()

        flush()

        self.log.emit(
            f"Done — {computed} dHash(es) computed, "
            f"{skipped} skipped (no thumbnail or decode error)."
        )
        self.hash_finished.emit(computed, skipped)


class EmbeddingWorker(QThread):
    """Compute and persist learned image embeddings (CLIP) for thumbnails.

    Optional feature: if embeddings are unavailable (missing onnxruntime/numpy
    or model file) it logs the reason and finishes without doing anything.

    Signals:
        progress(current, total): Image index and total count.
        log(message): Human-readable status line for the UI log panel.
        embed_finished(computed, skipped): Counts once the run completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    embed_finished = pyqtSignal(int, int)   # (computed, skipped)

    # Batch size read from config at run time; fallback keeps the class usable in tests.
    _INFER_BATCH = 32  # overridden in run()
    # How many DB writes to accumulate before flushing to SQLite.
    _WRITE_BATCH = 200

    def __init__(self, recompute_all: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._recompute_all = recompute_all

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.database import vectors
        from app.database.db import session_scope
        from app.database.models import Image as DBImage
        from app.utils.thumbnailer import get_thumbnail_bytes
        from app.utils import image_embedding as emb
        from app.ui.config_dialog import get_ai_embeddings_batch_size

        self._stop_requested = False
        self._INFER_BATCH = get_ai_embeddings_batch_size()

        if not emb.is_available():
            self.log.emit(emb.unavailable_reason())
            self.embed_finished.emit(0, 0)
            return

        with session_scope() as session:
            orphans = vectors.prune_orphans(session)
            if orphans:
                self.log.emit(f"Removed {orphans} embedding(s) of deleted images.")
            query = session.query(DBImage.id, DBImage.thumbnail)
            if not self._recompute_all:
                query = query.filter(DBImage.id.notin_(vectors.current_ids()))
            rows = query.all()

        total = len(rows)
        if total == 0:
            self.log.emit("All images already have a current embedding.")
            self.embed_finished.emit(0, 0)
            return

        verb = "Recomputing" if self._recompute_all else "Computing"
        self.log.emit(f"{verb} image embeddings for {total} image(s)…")

        pending: list = []   # (image_id, vector)

        def flush() -> None:
            if not pending:
                return
            with session_scope() as s:
                vectors.store(s, pending)
            pending.clear()

        computed = 0
        skipped  = 0
        idx = 0

        # Process images in inference-sized batches so the GPU runs at full
        # utilisation (one sess.run call per INFER_BATCH instead of per image).
        for batch_start in range(0, total, self._INFER_BATCH):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break

            batch_rows = rows[batch_start: batch_start + self._INFER_BATCH]
            jpegs: list[bytes | None] = []
            ids: list[int] = []
            for image_id, legacy_blob in batch_rows:
                jpeg = get_thumbnail_bytes(image_id) or legacy_blob
                jpegs.append(jpeg or b"")
                ids.append(image_id)

            vecs = emb.embed_batch(jpegs)

            for image_id, vec in zip(ids, vecs):
                idx += 1
                self.progress.emit(idx, total)
                if vec is None:
                    skipped += 1
                else:
                    pending.append((image_id, vec))
                    computed += 1

            if len(pending) >= self._WRITE_BATCH:
                flush()

        flush()

        self.log.emit(
            f"Done — {computed} embedding(s) computed, "
            f"{skipped} skipped (no thumbnail or decode error)."
        )
        self.embed_finished.emit(computed, skipped)


class _WarningRecheckWorker(QThread):
    """Re-evaluate one warning line across every catalogued archive.

    Reads each archive's catalog only - no member is decompressed - so this is
    far cheaper than a full rescan, and it is the only way to pick up a rule
    that has changed since the archive was catalogued.

    Only this worker's own warning line is touched; every other warning on the
    record is left exactly as it was.  Adding the line requires the warning to
    be enabled in Configuration, but a stale line is always cleared, so turning
    the option off and running this cleans up after it.

    Subclasses supply four things: :attr:`warning_line`, :meth:`_should_warn`,
    :meth:`_may_add` and :meth:`_scan_message`.

    Signals:
        progress(current, total): Archive index and total count.
        log(message): Human-readable status line for the UI log panel.
        check_finished(cleared, added, skipped): Counts once the run completes.

    ``check_finished`` is deliberately not named ``finished``: that would shadow
    ``QThread.finished``, which is what actually fires when the thread has wound
    down.  Dropping the worker reference on a same-named custom signal - emitted
    from inside ``run()``, before the thread has finished - is what produces
    "QThread: Destroyed while thread is still running".
    """

    progress       = pyqtSignal(int, int)
    log            = pyqtSignal(str)
    check_finished = pyqtSignal(int, int, int)   # (cleared, added, skipped)

    _BATCH = 200

    #: Warning text this worker owns, from :mod:`app.crawler.rules`.
    warning_line: str = ""

    #: Warning texts this worker should strip wherever it finds them, because
    #: the rule that produced them no longer exists.  Removed even when the
    #: archive cannot be read: dead text needs no verification.
    retired_lines: tuple = ()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    # -- subclass hooks -------------------------------------------------
    def _should_warn(self, names: list, config) -> bool:
        """Whether an archive with these members should carry the warning."""
        raise NotImplementedError

    def _may_add(self, config) -> bool:
        """Whether configuration allows *adding* it (clearing is always allowed)."""
        raise NotImplementedError

    def _scan_message(self, total: int) -> str:
        """Opening log line."""
        raise NotImplementedError

    # -------------------------------------------------------------------
    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from app.crawler.archive import list_contents
        from app.crawler.rules import sort_warning_lines
        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.ui.settings_loader import build_crawler_config

        self._stop_requested = False
        config = build_crawler_config()

        with session_scope() as session:
            records = session.query(
                STLModel.id, STLModel.archive_path, STLModel.warning
            ).all()

        total = len(records)
        if total == 0:
            self.log.emit("No records found in the database.")
            self.check_finished.emit(0, 0, 0)
            return

        self.log.emit(self._scan_message(total))

        def _evaluate(path_str) -> "bool | None":
            """True/False, or None when the archive could not be read at all."""
            if not path_str:
                return None
            p = Path(path_str)
            if not p.is_file():
                return None
            names = list_contents(p, config.unrar_path)
            if not names:
                # list_contents() logs and swallows open failures, returning [].
                # Acting on that would rewrite records we never actually read.
                return None
            return self._should_warn(names, config)

        cleared = added = skipped = 0
        pending: list[tuple[int, "str | None"]] = []

        def flush() -> None:
            if not pending:
                return
            with session_scope() as s:
                for model_id, new_warning in pending:
                    m = s.get(STLModel, model_id)
                    if m is not None:
                        m.warning = new_warning
            pending.clear()

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = {
                pool.submit(_evaluate, path): (model_id, warning)
                for model_id, path, warning in records
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                if self._stop_requested:
                    self.log.emit("Stopped by user.")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                self.progress.emit(idx, total)
                model_id, warning = futures[fut]
                try:
                    should_warn = fut.result()
                except Exception as exc:   # defensive: a reader raising anything
                    logger.warning("Warning re-check failed for model %s: %s", model_id, exc)
                    should_warn = None

                lines = [l for l in (warning or "").split("\n") if l.strip()]
                retired = [l for l in lines if l in self.retired_lines]
                if retired:
                    lines = [l for l in lines if l not in self.retired_lines]
                    cleared += len(retired)

                if should_warn is None:
                    skipped += 1
                    if not retired:
                        continue
                else:
                    flagged = self.warning_line in lines
                    if not should_warn and flagged:
                        lines.remove(self.warning_line)
                        cleared += 1
                    elif should_warn and not flagged and self._may_add(config):
                        lines.append(self.warning_line)
                        added += 1
                    elif not retired:
                        continue

                pending.append((model_id, "\n".join(sort_warning_lines(lines)) or None))
                if len(pending) >= self._BATCH:
                    flush()

        flush()
        self.log.emit(
            f"Done - {cleared} stale warning(s) cleared, {added} added, "
            f"{skipped} skipped (archive missing or unreadable)."
        )
        self.check_finished.emit(cleared, added, skipped)


class ImageWarningWorker(_WarningRecheckWorker):
    """Fix the "archive without images" warning across the library.

    Notices images the crawler could not see at the time - a format added to
    ``IMAGE_SUFFIXES`` since the archive was catalogued, for instance.
    """

    warning_line = WARNING_NO_IMAGES

    def _should_warn(self, names: list, config) -> bool:
        return not any(
            Path(n.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES for n in names
        )

    def _may_add(self, config) -> bool:
        return config.warn_no_images

    def _scan_message(self, total: int) -> str:
        return f"Checking {total} archive(s) for image files..."


class PresupportWarningWorker(_WarningRecheckWorker):
    """Fix the "models with presupported stl" warning across the library.

    Re-applies the current rule, which counts only ``.stl`` members - named as
    presupported, or sitting under a presupport folder - so archives flagged by
    an older, looser rule (an image, or a folder entry whose name merely
    contained "presupported") are cleared without a full rescan.

    Also strips the retired "Archive with supports" line, which the presupported
    warning now subsumes.
    """

    warning_line = WARNING_PRESUPPORTED
    retired_lines = (RETIRED_WARNING_SUPPORTS,)

    def _should_warn(self, names: list, config) -> bool:
        return WARNING_PRESUPPORTED in explain_warnings(
            names,
            check_presupported=True,
            check_no_images=False,
            check_nested_archive=False,
            check_no_printable=False,
            presupport_patterns=config.presupport_patterns,
        )

    def _may_add(self, config) -> bool:
        return config.warn_presupported_stl

    def _scan_message(self, total: int) -> str:
        return f"Checking {total} archive(s) for presupported .stl files..."


class DedupWorker(QThread):
    """Collapse identical thumbnails so each distinct payload is stored once.

    The heavy work lives in :func:`app.utils.thumbnailer.deduplicate_store`;
    this just relays its progress and formats the summary.

    Signals:
        progress(current, total): Position within the current phase.
        log(message): Human-readable status line for the UI log panel.
        dedup_finished(stats): Result dict from ``deduplicate_store`` ({} on error).
    """

    progress       = pyqtSignal(int, int)
    log            = pyqtSignal(str)
    dedup_finished = pyqtSignal(dict)

    _PHASE_LABEL = {
        "convert": "Converting thumbnails",
        "sweep":   "Removing unreferenced thumbnails",
        "compact": "Compacting the store",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._phase = ""

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.utils.thumbnailer import deduplicate_store, store_stats

        self._stop_requested = False
        before = store_stats()
        if not before["thumbnails"]:
            self.log.emit("No thumbnails stored.")
            self.dedup_finished.emit({})
            return

        self.log.emit(
            f"Scanning {before['thumbnails']:,} thumbnail(s) "
            f"({_fmt_size(before['payload_bytes'])})…"
        )

        def _on_progress(done: int, total: int, phase: str) -> None:
            if phase != self._phase:
                self._phase = phase
                self.log.emit(self._PHASE_LABEL.get(phase, phase) + "…")
            self.progress.emit(done, max(total, 1))

        try:
            stats = deduplicate_store(
                progress=_on_progress,
                should_stop=lambda: self._stop_requested,
            )
        except Exception as exc:
            self.log.emit(f"ERROR deduplicating thumbnails: {exc}")
            logger.error("Thumbnail deduplication failed: %s", exc, exc_info=True)
            self.dedup_finished.emit({})
            return

        saved = stats["payload_before"] - stats["payload_after"]
        shared = stats["thumbnails"] - stats["blobs_after"]
        self.log.emit(
            f"{stats['thumbnails']:,} thumbnail(s) share "
            f"{stats['blobs_after']:,} distinct image(s) — "
            f"{shared:,} redundant copies avoided."
        )
        if stats["converted"]:
            self.log.emit(f"Converted {stats['converted']:,} thumbnail(s) to shared storage.")
        if stats["orphans_removed"]:
            self.log.emit(
                f"Removed {stats['orphans_removed']:,} thumbnail(s) no model referenced."
            )
        self.log.emit(
            f"Payload {_fmt_size(stats['payload_before'])} → "
            f"{_fmt_size(stats['payload_after'])} "
            f"({_fmt_size(saved)} saved); store file "
            f"{_fmt_size(stats['size_before'])} → "
            f"{_fmt_size(stats['size_after'])}."
        )
        if stats["stopped"]:
            self.log.emit("Stopped by user — re-run to finish the remainder.")
        self.dedup_finished.emit(stats)


class CompactWorker(QThread):
    """Rewrite the LMDB thumbnail store defragmented to reclaim disk space.

    Signals:
        progress(current, total): Percent of the compacted copy written.
        log(message): Human-readable status line for the UI log panel.
        compact_finished(size_before, size_after): On-disk sizes in bytes (both -1 on error).
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    compact_finished = pyqtSignal(int, int)   # (size_before, size_after); (-1, -1) on error

    def run(self) -> None:
        from app.utils.thumbnailer import compact_store

        self.log.emit("Compacting thumbnail store — this may take a while…")
        try:
            # Reported as a percentage: raw byte counts overflow the bar's int
            # range, and MB granularity loses stores smaller than 1 MB.
            before, after = compact_store(
                progress=lambda done, total: self.progress.emit(
                    done * 100 // max(total, 1), 100
                )
            )
        except Exception as exc:
            self.log.emit(f"ERROR compacting thumbnail store: {exc}")
            logger.error("Thumbnail store compaction failed: %s", exc, exc_info=True)
            self.compact_finished.emit(-1, -1)
            return

        saved = before - after
        self.log.emit(
            f"Done — store went from {before / 1024**2:.1f} MB to {after / 1024**2:.1f} MB "
            f"({saved / 1024**2:.1f} MB reclaimed)."
        )
        self.compact_finished.emit(before, after)


class CompactDatabaseWorker(QThread):
    """Rewrite the SQLite database without free pages (VACUUM) to reclaim disk space.

    Signals:
        progress(current, total): ``(0, 0)`` while running (busy bar), ``(1, 1)`` when done.
        log(message): Human-readable status line for the UI log panel.
        db_compact_finished(size_before, size_after): Bytes on disk (both -1 on error).
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    db_compact_finished = pyqtSignal(object, object)   # ints may exceed 32 bits

    def run(self) -> None:
        import time
        from app.database.db import database_path, get_engine
        from app.services.maintenance import compact_database

        db_path = database_path()
        if db_path is None:
            self.log.emit("The database is in memory; there is nothing to compact.")
            self.db_compact_finished.emit(-1, -1)
            return

        self.log.emit("Compacting database — this may take a minute on a large library…")
        self.progress.emit(0, 0)   # VACUUM reports no progress: show a busy bar
        t0 = time.perf_counter()
        try:
            before, after, wal_truncated = compact_database(get_engine(), db_path)
        except Exception as exc:
            msg = str(exc)
            if "locked" in msg:
                msg += " — another job is writing to the database; let it finish and retry."
            self.log.emit(f"ERROR compacting database: {msg}")
            logger.error("Database compaction failed: %s", exc, exc_info=True)
            self.progress.emit(0, 1)
            self.db_compact_finished.emit(-1, -1)
            return

        self.progress.emit(1, 1)
        self.log.emit(
            f"Done in {time.perf_counter() - t0:.0f} s — database went from "
            f"{before / 1024**2:.1f} MB to {after / 1024**2:.1f} MB "
            f"({(before - after) / 1024**2:.1f} MB reclaimed)."
        )
        if not wal_truncated:
            self.log.emit("The write-ahead log is still in use by another view; it will "
                          "shrink at a later checkpoint.")
        self.db_compact_finished.emit(before, after)


class PruneImagesWorker(QThread):
    """Reduce the number of images stored per model down to a target maximum.

    For every model that has more than *keep* images, the oldest *keep* images
    (lowest id = insertion order) are retained and the rest are deleted from the
    database along with their LMDB thumbnails. Run *Compact Thumbnail Store*
    afterwards to reclaim the freed disk space (LMDB never shrinks on its own).
    Thin wrapper around :func:`app.services.maintenance.prune_images`.

    Signals:
        progress(current, total): Models processed so far and total count.
        log(message): Human-readable status line for the UI log panel.
        prune_finished(models_pruned, images_removed): Counts once the run completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    prune_finished = pyqtSignal(int, int)   # (models_pruned, images_removed)

    def __init__(self, keep: int, parent=None) -> None:
        super().__init__(parent)
        self._keep = max(0, int(keep))
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.services.maintenance import prune_images
        self._stop_requested = False
        pruned, removed = prune_images(
            self._keep, progress=self.progress.emit, log=self.log.emit,
            should_stop=lambda: self._stop_requested,
        )
        self.prune_finished.emit(pruned, removed)


class FindDuplicateEntriesWorker(QThread):
    """Find DB records that share the same archive content (hash) or the same
    filename + file size, indicating the same physical file has been catalogued
    more than once.

    Thin wrapper around :func:`app.services.maintenance.find_duplicate_entries`.

    Signals:
        progress(current, total)
        log(message)
        duplicates_finished(results): list of result dicts, same schema as VerifyWorker.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    duplicates_finished = pyqtSignal(list)

    def run(self) -> None:
        from app.services.maintenance import find_duplicate_entries
        self.duplicates_finished.emit(
            find_duplicate_entries(progress=self.progress.emit, log=self.log.emit)
        )


class MovedInDbWorker(QThread):
    """Search the DB for records whose content matches a missing archive at a different path.

    For each missing record, looks for another DB record sharing the same content
    hash, or the same filename + file size, whose path still exists on disk.

    Signals:
        progress(current, total): Index of missing record being checked.
        log(message): Human-readable status line.
        found(missing_model_id, match_path): Emitted when a match is located.
        moved_finished(count): Total number of matches found.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    found    = pyqtSignal(int, str)   # (missing_model_id, match_archive_path)
    moved_finished = pyqtSignal(int)

    def __init__(self, missing_records: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._missing = missing_records

    def run(self) -> None:
        from collections import defaultdict
        from app.database.db import get_session
        from app.database.models import STLModel

        if not self._missing:
            self.log.emit("No missing records to check.")
            self.moved_finished.emit(0)
            return

        self.log.emit(f"Searching {len(self._missing)} missing record(s) in the database…")

        session = get_session()
        try:
            rows = session.query(
                STLModel.id, STLModel.archive_path,
                STLModel.archive_hash, STLModel.archive_size,
            ).all()
        finally:
            session.close()

        # Build lookup tables keyed by hash and by (filename, size)
        hash_to_candidates:     dict[str, list[tuple[str, int]]] = defaultdict(list)
        namesize_to_candidates: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
        for model_id, path, ahash, asize in rows:
            if not path:
                continue
            if ahash:
                hash_to_candidates[ahash].append((path, model_id))
            if asize is not None:
                namesize_to_candidates[(Path(path).name, asize)].append((path, model_id))

        matched = 0
        total   = len(self._missing)
        for idx, rec in enumerate(self._missing, 1):
            self.progress.emit(idx, total)

            candidates: list[tuple[str, int]] = []
            if rec.get("hash"):
                candidates = hash_to_candidates.get(rec["hash"], [])
            if not candidates and rec.get("path") and rec.get("size") is not None:
                fname = Path(rec["path"]).name
                candidates = namesize_to_candidates.get((fname, rec["size"]), [])

            for match_path, match_id in candidates:
                if match_id == rec["id"]:
                    continue
                if match_path and Path(match_path).exists():
                    self.found.emit(rec["id"], match_path)
                    matched += 1
                    break

        self.log.emit(f"Found {matched} potential DB match(es) for missing archive(s).")
        self.moved_finished.emit(matched)


class DbMaintenanceDialog(QDialog):
    """Database maintenance dialog.

    Three operations are available after running Verify:
      - Relocate: search a user-selected folder tree for missing archives by content hash.
      - Remove Selected: delete chosen records from the database.
      - Remove All Missing: delete every record whose archive file no longer exists.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Database Maintenance"))
        self.resize(900, 620)
        self.setModal(True)
        self._verify_worker:    VerifyWorker                 | None = None
        self._search_worker:    SearchWorker                 | None = None
        self._dhash_worker:     DHashWorker                  | None = None
        self._embed_worker:     EmbeddingWorker              | None = None
        self._moved_db_worker:  MovedInDbWorker              | None = None
        self._dup_entry_worker: FindDuplicateEntriesWorker   | None = None
        self._compact_worker:   CompactWorker                | None = None
        self._db_compact_worker: CompactDatabaseWorker       | None = None
        self._prune_worker:     PruneImagesWorker            | None = None
        self._img_warn_worker:  ImageWarningWorker           | None = None
        self._presup_worker:    PresupportWarningWorker      | None = None
        self._dedup_worker:     DedupWorker                  | None = None
        self._results: list[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        desc = QLabel(
            self.tr("Verify that every archive catalogued in the database still exists on disk. "
            "Missing archives can be relocated by searching a folder recursively, "
            "or removed from the database.")
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        style = QApplication.style()
        SP = style.StandardPixmap

        # Action buttons laid out in a 3-column grid (filled row by row).
        btn_grid = QGridLayout()
        self._action_buttons: list[QPushButton] = []

        def _add_btn(b: QPushButton) -> None:
            idx = len(self._action_buttons)
            self._action_buttons.append(b)
            btn_grid.addWidget(b, idx // 3, idx % 3)

        self._verify_btn = QPushButton(style.standardIcon(SP.SP_BrowserReload), self.tr("Verify"))
        self._verify_btn.setToolTip(self.tr("Check all archive paths for existence on disk"))
        self._verify_btn.clicked.connect(self._start_verify)
        _add_btn(self._verify_btn)

        self._stop_btn = QPushButton(style.standardIcon(SP.SP_MediaStop), self.tr("Stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        _add_btn(self._stop_btn)

        self._search_btn = QPushButton(style.standardIcon(SP.SP_DirOpenIcon), self.tr("Search in Folder…"))
        self._search_btn.setToolTip(
            self.tr("Pick a folder to search recursively for selected (or all) missing archives")
        )
        self._search_btn.setEnabled(False)
        self._search_btn.clicked.connect(self._start_search)
        _add_btn(self._search_btn)

        self._find_moved_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogContentsView), self.tr("Find Moved in DB…")
        )
        self._find_moved_btn.setToolTip(
            self.tr("Check whether missing archives have been re-crawled at a different location "
            "and offer to transfer their metadata")
        )
        self._find_moved_btn.setEnabled(False)
        self._find_moved_btn.clicked.connect(self._start_find_moved_in_db)
        _add_btn(self._find_moved_btn)

        self._find_dup_entries_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Find Duplicate Entries…")
        )
        self._find_dup_entries_btn.setToolTip(
            self.tr("Find records that share the same archive content hash or the same "
            "filename + file size, indicating the same file is catalogued more than once")
        )
        self._find_dup_entries_btn.clicked.connect(self._start_find_duplicate_entries)
        _add_btn(self._find_dup_entries_btn)

        self._recompute_dhash_btn = QPushButton(
            style.standardIcon(SP.SP_BrowserReload), self.tr("Recompute Image Hashes")
        )
        self._recompute_dhash_btn.setToolTip(
            self.tr("Recompute the perceptual hash (dHash) of every thumbnail. "
            "Run this after upgrading so image-similarity searches use the current "
            "128-bit algorithm.")
        )
        self._recompute_dhash_btn.clicked.connect(lambda: self._start_dhash(recompute_all=True))
        _add_btn(self._recompute_dhash_btn)

        from app.utils import image_embedding as _emb
        from app.ui.config_dialog import get_ai_embeddings_enabled
        self._embed_available = _emb.is_available() and get_ai_embeddings_enabled()
        self._embed_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogInfoView), self.tr("Compute Image Embeddings")
        )
        if self._embed_available:
            self._embed_btn.setToolTip(
                self.tr("Compute AI image embeddings (CLIP) for every thumbnail so "
                "image-similarity searches can match the same model across different "
                "renders, angles, and colours.")
            )
        else:
            self._embed_btn.setEnabled(False)
            self._embed_btn.setToolTip(
                _emb.unavailable_reason() if not _emb.is_available()
                else self.tr("AI embeddings are disabled in Configuration → Browser")
            )
        self._embed_btn.clicked.connect(lambda: self._start_embeddings(recompute_all=True))
        _add_btn(self._embed_btn)

        self._check_images_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogContentsView), self.tr("Check Images Warning")
        )
        self._check_images_btn.setToolTip(
            self.tr("Re-read every archive's file list and fix the 'Archive without images' "
            "warning: clear it where the archive does contain images, and add it where it "
            "does not. Only the archive catalog is read, so this is much faster than a full "
            "rescan. Useful after an update adds support for a new image format.")
        )
        self._check_images_btn.clicked.connect(self._start_check_images)
        _add_btn(self._check_images_btn)

        self._check_presup_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogDetailedView),
            self.tr("Check Presupport Warning")
        )
        self._check_presup_btn.setToolTip(
            self.tr("Re-read every archive's file list and fix the 'Models with presupported "
            "stl' warning: clear it where no presupported .stl is present, and add it where "
            "one is. Only the archive catalog is read, so this is much faster than a full "
            "rescan. Run it after the presupport rules change to clear warnings raised by "
            "an older rule.")
        )
        self._check_presup_btn.clicked.connect(self._start_check_presupports)
        _add_btn(self._check_presup_btn)

        self._dedup_btn = QPushButton(
            style.standardIcon(SP.SP_DialogSaveButton), self.tr("Deduplicate Thumbnails")
        )
        self._dedup_btn.setToolTip(
            self.tr("Store each distinct thumbnail once instead of once per model. "
                    "Libraries that share renders across archives often carry a large "
                    "proportion of byte-identical thumbnails; collapsing them can halve "
                    "the store. Also removes thumbnails no model references any more, "
                    "then compacts. Safe to stop and re-run.")
        )
        self._dedup_btn.clicked.connect(self._start_dedup)
        _add_btn(self._dedup_btn)

        self._compact_btn = QPushButton(
            style.standardIcon(SP.SP_DriveHDIcon), self.tr("Compact Thumbnail Store")
        )
        self._compact_btn.setToolTip(
            self.tr("Reclaim disk space from deleted thumbnails by rewriting the "
            "thumbnail store. LMDB never shrinks on its own, so run this after "
            "removing many models or images.")
        )
        self._compact_btn.clicked.connect(self._start_compact)
        _add_btn(self._compact_btn)

        self._db_compact_btn = QPushButton(
            style.standardIcon(SP.SP_DriveHDIcon), self.tr("Compact Database")
        )
        self._db_compact_btn.setToolTip(
            self.tr("Reclaim disk space in the database file by rewriting it without the "
            "free space left by deleted models, images and embeddings (SQLite VACUUM). "
            "SQLite never shrinks the file on its own. Avoid crawling while it runs.")
        )
        self._db_compact_btn.clicked.connect(self._start_db_compact)
        _add_btn(self._db_compact_btn)

        self._prune_images_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), self.tr("Prune Images per Model…")
        )
        self._prune_images_btn.setToolTip(
            self.tr("Reduce the number of stored images per model to a chosen maximum. "
            "The oldest images are kept; the extra ones and their thumbnails are deleted.")
        )
        self._prune_images_btn.clicked.connect(self._start_prune_images)
        _add_btn(self._prune_images_btn)

        self._merge_moved_btn = QPushButton(
            style.standardIcon(SP.SP_ArrowRight), self.tr("Merge Moved")
        )
        self._merge_moved_btn.setToolTip(
            self.tr("Transfer metadata from selected 'Moved (in DB)' records to their new locations")
        )
        self._merge_moved_btn.setEnabled(False)
        self._merge_moved_btn.clicked.connect(self._merge_moved_records)
        _add_btn(self._merge_moved_btn)

        self._remove_sel_btn = QPushButton(style.standardIcon(SP.SP_TrashIcon), self.tr("Remove Selected"))
        self._remove_sel_btn.setToolTip(self.tr("Remove the selected records from the database"))
        self._remove_sel_btn.setEnabled(False)
        self._remove_sel_btn.clicked.connect(self._remove_selected)
        _add_btn(self._remove_sel_btn)

        self._remove_all_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), self.tr("Remove All Missing")
        )
        self._remove_all_btn.setToolTip(
            self.tr("Remove every record whose archive no longer exists on disk")
        )
        self._remove_all_btn.setEnabled(False)
        self._remove_all_btn.clicked.connect(self._remove_all_missing)
        _add_btn(self._remove_all_btn)

        self._remove_flagged_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), self.tr("Remove Flagged Missing")
        )
        self._remove_flagged_btn.setToolTip(
            self.tr("Remove records flagged as missing by the crawler that are still absent on disk")
        )
        self._remove_flagged_btn.setEnabled(False)
        self._remove_flagged_btn.clicked.connect(self._remove_flagged_missing)
        _add_btn(self._remove_flagged_btn)

        self._close_btn = QPushButton(
            style.standardIcon(SP.SP_DialogCloseButton), self.tr("Close")
        )
        self._close_btn.clicked.connect(self.reject)
        _add_btn(self._close_btn)

        for _col in range(3):
            btn_grid.setColumnStretch(_col, 1)
        root.addLayout(btn_grid)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        filter_row = QHBoxLayout()
        self._only_failed_cb = QCheckBox(self.tr("Show only problems (missing / modified)"))
        self._only_failed_cb.toggled.connect(self._apply_filter)
        filter_row.addWidget(self._only_failed_cb)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            [self.tr("ID"), self.tr("Title"), self.tr("Archive Path"), self.tr("Status"), self.tr("Match Path")]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_ID,     QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_TITLE,  QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_PATH,   QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_MATCH,  QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(_COL_ID, True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

        root.addWidget(QLabel(self.tr("Log:")))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root.addWidget(self._log)

    # ------------------------------------------------------------------
    def _own_worker(self, attr: str, worker: QThread) -> QThread:
        """Store *worker* on ``self.<attr>`` and manage its lifetime.

        Two things every worker here needs.  The thread is parented to the
        dialog, so Qt owns it and a cleared Python attribute can never be what
        destroys a running thread.  And the attribute is cleared from
        ``QThread.finished`` — the real one, which fires after ``run()`` has
        returned — rather than from a result handler called mid-``run()``.
        Doing the latter is what produces "QThread: Destroyed while thread is
        still running"; every worker in this module names its result signal
        something other than ``finished`` so the real signal stays reachable.
        """
        setattr(self, attr, worker)
        worker.setParent(self)
        worker.finished.connect(lambda a=attr: self._release_worker(a))
        return worker

    def _release_worker(self, attr: str) -> None:
        """Drop the reference to a worker whose thread has fully stopped."""
        worker = getattr(self, attr, None)
        setattr(self, attr, None)
        if worker is not None:
            worker.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self._verify_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._search_btn.setEnabled(False)
        self._find_moved_btn.setEnabled(False)
        self._find_dup_entries_btn.setEnabled(not busy)
        self._recompute_dhash_btn.setEnabled(not busy)
        self._embed_btn.setEnabled(not busy and self._embed_available)
        self._compact_btn.setEnabled(not busy)
        self._db_compact_btn.setEnabled(not busy)
        self._prune_images_btn.setEnabled(not busy)
        self._check_images_btn.setEnabled(not busy)
        self._check_presup_btn.setEnabled(not busy)
        self._dedup_btn.setEnabled(not busy)
        self._merge_moved_btn.setEnabled(False)
        self._remove_sel_btn.setEnabled(False)
        self._remove_all_btn.setEnabled(False)
        self._remove_flagged_btn.setEnabled(False)
        self._close_btn.setEnabled(not busy)

    def _set_idle(self) -> None:
        has_missing  = any(r["status"] in (_STATUS_MISSING, _STATUS_MODIFIED) for r in self._results)
        has_flagged  = any(r["status"] == _STATUS_FLAGGED for r in self._results)
        has_moved_db = any(r["status"] == _STATUS_MOVED_DB for r in self._results)
        has_any      = bool(self._results)
        self._verify_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._search_btn.setEnabled(has_missing or has_flagged)
        self._find_moved_btn.setEnabled(has_missing or has_flagged)
        self._find_dup_entries_btn.setEnabled(True)
        self._recompute_dhash_btn.setEnabled(True)
        self._embed_btn.setEnabled(self._embed_available)
        self._compact_btn.setEnabled(True)
        self._db_compact_btn.setEnabled(True)
        self._prune_images_btn.setEnabled(True)
        self._check_images_btn.setEnabled(True)
        self._check_presup_btn.setEnabled(True)
        self._dedup_btn.setEnabled(True)
        self._merge_moved_btn.setEnabled(has_moved_db)
        self._remove_sel_btn.setEnabled(has_any)
        self._remove_all_btn.setEnabled(has_missing)
        self._remove_flagged_btn.setEnabled(has_flagged)
        self._close_btn.setEnabled(True)

    def _stop(self) -> None:
        for worker in (
            self._verify_worker, self._search_worker,
            self._dhash_worker, self._embed_worker, self._moved_db_worker,
            self._prune_worker, self._img_warn_worker, self._presup_worker,
            self._dedup_worker,
        ):
            if worker and worker.isRunning():
                worker.stop()
        # FindDuplicateEntriesWorker has no stop() — it finishes quickly
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _start_verify(self) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._table.setRowCount(0)
        self._results.clear()
        self._set_busy(True)

        self._own_worker("_verify_worker", VerifyWorker())
        self._verify_worker.log.connect(self._on_log)
        self._verify_worker.progress.connect(self._on_progress)
        self._verify_worker.verify_finished.connect(self._on_verify_finished)
        self._verify_worker.start()

    def _on_verify_finished(self, results: list) -> None:
        self._results = results
        self._populate_table(results)
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_dhash(self, recompute_all: bool = False) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_dhash_worker", DHashWorker(recompute_all=recompute_all))
        self._dhash_worker.log.connect(self._on_log)
        self._dhash_worker.progress.connect(self._on_progress)
        self._dhash_worker.hash_finished.connect(self._on_dhash_finished)
        self._dhash_worker.start()

    def _on_dhash_finished(self, computed: int, skipped: int) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_embeddings(self, recompute_all: bool = False) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_embed_worker", EmbeddingWorker(recompute_all=recompute_all))
        self._embed_worker.log.connect(self._on_log)
        self._embed_worker.progress.connect(self._on_progress)
        self._embed_worker.embed_finished.connect(self._on_embed_finished)
        self._embed_worker.start()

    def _on_embed_finished(self, computed: int, skipped: int) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_check_images(self) -> None:
        if self._img_warn_worker is not None and self._img_warn_worker.isRunning():
            return
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_img_warn_worker", ImageWarningWorker())
        self._img_warn_worker.log.connect(self._on_log)
        self._img_warn_worker.progress.connect(self._on_progress)
        self._img_warn_worker.check_finished.connect(self._on_warning_recheck_finished)
        self._img_warn_worker.start()

    def _start_check_presupports(self) -> None:
        if self._presup_worker is not None and self._presup_worker.isRunning():
            return
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_presup_worker", PresupportWarningWorker())
        self._presup_worker.log.connect(self._on_log)
        self._presup_worker.progress.connect(self._on_progress)
        self._presup_worker.check_finished.connect(self._on_warning_recheck_finished)
        self._presup_worker.start()

    def _on_warning_recheck_finished(self, cleared: int, added: int, skipped: int) -> None:
        """Shared completion slot for both warning re-check workers.

        The worker reference is released from the real ``QThread.finished`` by
        ``_own_worker``, not here, and the browser is refreshed by the caller
        once this dialog closes - so there is nothing to do but go idle.
        """
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_dedup(self) -> None:
        if self._dedup_worker is not None and self._dedup_worker.isRunning():
            return
        reply = QMessageBox.question(
            self, self.tr("Deduplicate Thumbnails"),
            self.tr("Store each distinct thumbnail once instead of once per model?\n\n"
                    "Every thumbnail stays viewable; only redundant copies are removed. "
                    "On a large library this can take a few minutes — you can stop it "
                    "and re-run later without losing progress."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_dedup_worker", DedupWorker())
        self._dedup_worker.log.connect(self._on_log)
        self._dedup_worker.progress.connect(self._on_progress)
        self._dedup_worker.dedup_finished.connect(self._on_dedup_finished)
        self._dedup_worker.start()

    def _on_dedup_finished(self, _stats: dict) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_compact(self) -> None:
        reply = QMessageBox.question(
            self, self.tr("Compact Thumbnail Store"),
            self.tr("Rewrite the thumbnail store to reclaim disk space from deleted "
            "thumbnails?\n\nThe store is briefly closed and rebuilt; avoid browsing "
            "thumbnails until it finishes."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_compact_worker", CompactWorker())
        self._compact_worker.log.connect(self._on_log)
        self._compact_worker.progress.connect(self._on_progress)
        self._compact_worker.compact_finished.connect(self._on_compact_finished)
        self._compact_worker.start()

    def _on_compact_finished(self, size_before: int, size_after: int) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_db_compact(self) -> None:
        from app.database.db import database_path, session_scope
        from app.services.maintenance import database_file_bytes, database_free_bytes

        db_path = database_path()
        if db_path is None:
            return
        with session_scope() as session:          # two header reads: cheap
            free = database_free_bytes(session)
        size = database_file_bytes(db_path)
        reply = QMessageBox.question(
            self, self.tr("Compact Database"),
            self.tr("Rewrite the database to reclaim unused space?\n\n"
                    "Database on disk: {size} MB, of which about {free} MB is free space.\n\n"
                    "This can take a minute on a large library and needs free disk space "
                    "about the size of the database. Don't crawl or edit while it runs.")
                .replace("{size}", f"{size / 1024**2:.0f}")
                .replace("{free}", f"{free / 1024**2:.0f}"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log.clear()
        self._set_busy(True)
        self._stop_btn.setEnabled(False)          # VACUUM cannot be interrupted

        self._own_worker("_db_compact_worker", CompactDatabaseWorker())
        self._db_compact_worker.log.connect(self._on_log)
        self._db_compact_worker.progress.connect(self._on_progress)
        self._db_compact_worker.db_compact_finished.connect(self._on_db_compact_finished)
        self._db_compact_worker.start()

    def _on_db_compact_finished(self, _size_before, _size_after) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    # ------------------------------------------------------------------
    def _start_prune_images(self) -> None:
        from app.ui.config_dialog import get_max_thumbnails

        keep, ok = QInputDialog.getInt(
            self, self.tr("Prune Images per Model"),
            self.tr("Maximum number of images to keep per model\n"
                    "(the oldest images are kept; extras are deleted):"),
            value=get_max_thumbnails(), min=1, max=1000,
        )
        if not ok:
            return

        reply = QMessageBox.warning(
            self, self.tr("Prune Images per Model"),
            f"Permanently delete all but the first <b>{keep}</b> image(s) of every model "
            "that has more?<br><br>"
            "The extra images and their thumbnails are removed from the database. "
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._own_worker("_prune_worker", PruneImagesWorker(keep))
        self._prune_worker.log.connect(self._on_log)
        self._prune_worker.progress.connect(self._on_progress)
        self._prune_worker.prune_finished.connect(self._on_prune_finished)
        self._prune_worker.start()

    def _on_prune_finished(self, models_pruned: int, images_removed: int) -> None:
        self._set_idle()
        from app.utils.sound import play_notification
        play_notification("maintenance")

    def _populate_table(self, results: list) -> None:
        self._table.setRowCount(0)
        for row in results:
            r = self._table.rowCount()
            self._table.insertRow(r)

            items = [
                QTableWidgetItem(str(row["id"])),
                QTableWidgetItem(row["title"]),
                QTableWidgetItem(row["path"]),
                QTableWidgetItem(row["status"]),
                QTableWidgetItem(row.get("match_path", "")),
            ]
            for item in items:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            status = row["status"]
            if status == _STATUS_MISSING:
                self._color_row(items, _color_missing())
            elif status == _STATUS_FLAGGED:
                self._color_row(items, _color_flagged())
            elif status == _STATUS_FOUND:
                self._color_row(items, _color_found())
            elif status == _STATUS_MODIFIED:
                self._color_row(items, _color_modified())
            elif status == _STATUS_MOVED_DB:
                self._color_row(items, _color_moved_db())
            elif status == _STATUS_DUPLICATE:
                self._color_row(items, _color_duplicate())

            for col, item in enumerate(items):
                self._table.setItem(r, col, item)

        self._apply_filter()

    def _apply_filter(self) -> None:
        only_problems = self._only_failed_cb.isChecked()
        _OK_STATUSES = {_STATUS_OK, _STATUS_FOUND}
        for row in range(self._table.rowCount()):
            status_item = self._table.item(row, _COL_STATUS)
            if status_item is None:
                continue
            hide = only_problems and status_item.text() in _OK_STATUSES
            self._table.setRowHidden(row, hide)
        # Show match-path column when moved-in-DB or duplicate rows are present
        has_match = any(r.get("status") in (_STATUS_MOVED_DB, _STATUS_DUPLICATE) for r in self._results)
        self._table.setColumnHidden(_COL_MATCH, not has_match)

    @staticmethod
    def _color_row(items: list[QTableWidgetItem], bg: QColor) -> None:
        bg_brush = QBrush(bg)
        fg_brush = QBrush(_COLOR_FG)
        for item in items:
            item.setBackground(bg_brush)
            item.setForeground(fg_brush)

    # ------------------------------------------------------------------
    def _start_search(self) -> None:
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        _searchable = {_STATUS_MISSING, _STATUS_FLAGGED}

        if selected_rows:
            targets = {
                self._results[r]["hash"]: self._results[r]["id"]
                for r in selected_rows
                if r < len(self._results) and self._results[r]["status"] in _searchable
            }
            if not targets:
                QMessageBox.information(
                    self, self.tr("Search"),
                    self.tr("None of the selected rows are missing — nothing to search for.")
                )
                return
        else:
            targets = {
                r["hash"]: r["id"]
                for r in self._results
                if r["status"] in _searchable
            }

        if not targets:
            QMessageBox.information(self, self.tr("Search"), self.tr("No missing archives to search for."))
            return

        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Select Folder to Search"), "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return

        self._set_busy(True)
        self._log.appendPlainText(
            f"Searching for {len(targets)} missing archive(s) in: {folder}"
        )

        self._own_worker("_search_worker", SearchWorker(Path(folder), targets))
        self._search_worker.log.connect(self._on_log)
        self._search_worker.progress.connect(self._on_progress)
        self._search_worker.found.connect(self._on_archive_found)
        self._search_worker.search_finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_archive_found(self, model_id: int, new_path: str) -> None:
        for i, row in enumerate(self._results):
            if row["id"] == model_id:
                row["path"]   = new_path
                row["status"] = _STATUS_FOUND
                items = [self._table.item(i, col) for col in range(4)]
                self._table.item(i, _COL_PATH).setText(new_path)
                self._table.item(i, _COL_STATUS).setText(_STATUS_FOUND)
                self._color_row([it for it in items if it], _color_found())
                if self._only_failed_cb.isChecked():
                    self._table.setRowHidden(i, True)
                break

    def _on_search_finished(self) -> None:
        self._set_idle()

    # ------------------------------------------------------------------
    def _start_find_moved_in_db(self) -> None:
        missing = [r for r in self._results if r["status"] in (_STATUS_MISSING, _STATUS_MODIFIED, _STATUS_FLAGGED)]
        if not missing:
            QMessageBox.information(self, self.tr("Find Moved"), self.tr("No missing archives to check."))
            return

        self._set_busy(True)
        self._log.appendPlainText(
            f"Searching the database for {len(missing)} missing archive(s)…"
        )

        self._own_worker("_moved_db_worker", MovedInDbWorker(missing))
        self._moved_db_worker.log.connect(self._on_log)
        self._moved_db_worker.progress.connect(self._on_progress)
        self._moved_db_worker.found.connect(self._on_moved_in_db_found)
        self._moved_db_worker.moved_finished.connect(self._on_moved_in_db_finished)
        self._moved_db_worker.start()

    def _on_moved_in_db_found(self, missing_id: int, match_path: str) -> None:
        for i, row in enumerate(self._results):
            if row["id"] == missing_id:
                row["status"]     = _STATUS_MOVED_DB
                row["match_path"] = match_path
                self._table.item(i, _COL_STATUS).setText(_STATUS_MOVED_DB)
                match_item = self._table.item(i, _COL_MATCH)
                if match_item:
                    match_item.setText(match_path)
                items = [self._table.item(i, c) for c in range(5)]
                self._color_row([it for it in items if it], _color_moved_db())
                break
        self._apply_filter()

    def _on_moved_in_db_finished(self, count: int) -> None:
        self._set_idle()

    def _merge_moved_records(self) -> None:
        """Transfer metadata from 'Moved (in DB)' records to their matched new locations."""
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if selected_rows:
            to_merge = [
                self._results[r] for r in selected_rows
                if r < len(self._results)
                and self._results[r]["status"] == _STATUS_MOVED_DB
                and self._results[r].get("match_path")
            ]
        else:
            to_merge = [
                r for r in self._results
                if r["status"] == _STATUS_MOVED_DB and r.get("match_path")
            ]

        if not to_merge:
            QMessageBox.information(
                self, self.tr("Merge Moved"),
                self.tr("No 'Moved (in DB)' records selected (or none found).\n\n"
                "Run 'Find Moved in DB' first, then select rows to merge."),
            )
            return

        reply = QMessageBox.question(
            self, self.tr("Merge Moved Records"),
            f"Transfer metadata from <b>{len(to_merge)}</b> missing record(s) to their "
            f"matched locations?\n\n"
            "• The missing record's manually-edited metadata will be kept.\n"
            "• The matched (re-crawled) record at the new path will be removed.\n"
            "• Thumbnails of the removed record will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.services import moves
        from app.utils.thumbnailer import delete_thumbnails

        merged = 0
        errors = 0
        try:
            thumb_refs: list[str] = []
            with session_scope() as session:
                for rec in to_merge:
                    match_path = rec["match_path"]
                    old_model = session.query(STLModel).filter_by(id=rec["id"]).first()
                    new_model = session.query(STLModel).filter_by(
                        archive_path=match_path
                    ).first()
                    if old_model is None or new_model is None:
                        errors += 1
                        continue
                    # Keeps the missing record's metadata at the new location
                    # (also clears its missing flag and updates its folder).
                    thumb_refs += moves.merge_into_missing(session, old_model, new_model)
                    merged += 1
            delete_thumbnails(thumb_refs)   # after commit, one LMDB transaction

        except Exception as exc:
            self._log.appendPlainText(f"ERROR during merge: {exc}")
            logger.error("Merge moved records failed: %s", exc, exc_info=True)

        # Update UI to reflect merged rows
        merged_ids = {r["id"] for r in to_merge}
        for i, row in enumerate(self._results):
            if row["id"] in merged_ids and row["status"] == _STATUS_MOVED_DB:
                row["path"]       = row["match_path"]
                row["status"]     = _STATUS_FOUND
                row["match_path"] = ""
                self._table.item(i, _COL_PATH).setText(row["path"])
                self._table.item(i, _COL_STATUS).setText(_STATUS_FOUND)
                match_item = self._table.item(i, _COL_MATCH)
                if match_item:
                    match_item.setText("")
                items = [self._table.item(i, c) for c in range(5)]
                self._color_row([it for it in items if it], _color_found())

        self._apply_filter()
        self._log.appendPlainText(
            f"Merged {merged} record(s)."
            + (f" {errors} error(s)." if errors else "")
        )
        self._set_idle()

    # ------------------------------------------------------------------
    def _remove_selected(self) -> None:
        selected_rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        if not selected_rows:
            QMessageBox.information(self, self.tr("Remove"), self.tr("No rows selected."))
            return

        count = len(selected_rows)
        reply = QMessageBox.warning(
            self,
            self.tr("Remove Records"),
            f"Permanently remove <b>{count}</b> record(s) from the database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ids_to_remove = [
            self._results[r]["id"] for r in selected_rows if r < len(self._results)
        ]
        self._remove_records(ids_to_remove)

        for r in selected_rows:
            if r < len(self._results):
                del self._results[r]
                self._table.removeRow(r)

        self._log.appendPlainText(f"Removed {len(ids_to_remove)} record(s) from the database.")
        self._refresh_action_buttons()

    def _remove_all_missing(self) -> None:
        missing = [r for r in self._results if r["status"] == _STATUS_MISSING]
        if not missing:
            QMessageBox.information(self, self.tr("Remove"), self.tr("No missing archives to remove."))
            return

        count = len(missing)
        reply = QMessageBox.warning(
            self,
            self.tr("Remove All Missing"),
            f"Permanently remove <b>{count}</b> missing archive record(s) from the database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._remove_records([r["id"] for r in missing])
        self._results = [r for r in self._results if r["status"] != _STATUS_MISSING]
        self._populate_table(self._results)
        self._log.appendPlainText(f"Removed {count} missing record(s) from the database.")
        self._refresh_action_buttons()

    def _start_find_duplicate_entries(self) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._table.setRowCount(0)
        self._results.clear()
        self._set_busy(True)

        self._own_worker("_dup_entry_worker", FindDuplicateEntriesWorker())
        self._dup_entry_worker.log.connect(self._on_log)
        self._dup_entry_worker.progress.connect(self._on_progress)
        self._dup_entry_worker.duplicates_finished.connect(self._on_dup_entries_finished)
        self._dup_entry_worker.start()

    def _on_dup_entries_finished(self, results: list) -> None:
        self._results = results
        self._populate_table(results)
        self._set_idle()

    def _remove_flagged_missing(self) -> None:
        flagged = [r for r in self._results if r["status"] == _STATUS_FLAGGED]
        if not flagged:
            QMessageBox.information(self, self.tr("Remove Flagged"), self.tr("No flagged-missing records to remove."))
            return

        count = len(flagged)
        reply = QMessageBox.warning(
            self,
            self.tr("Remove Flagged Missing"),
            f"Permanently remove <b>{count}</b> record(s) flagged as missing by the crawler?<br><br>"
            "These are archives that the crawler could not find on disk and could not match "
            "to a new location. Removing them is permanent.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._remove_records([r["id"] for r in flagged])
        self._results = [r for r in self._results if r["status"] != _STATUS_FLAGGED]
        self._populate_table(self._results)
        self._log.appendPlainText(f"Removed {count} flagged-missing record(s) from the database.")
        self._refresh_action_buttons()

    def _remove_records(self, ids: list[int]) -> None:
        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.database.helpers import prune_unused_lookups

        try:
            with session_scope() as session:
                for model_id in ids:
                    model = session.query(STLModel).filter(STLModel.id == model_id).first()
                    if model:
                        session.delete(model)
                session.flush()
                deleted = prune_unused_lookups(session)
            if deleted:
                summary = ", ".join(f"{n} {k}" for k, n in deleted.items())
                self._log.appendPlainText(f"Pruned unused lookups: {summary}")
        except Exception as exc:
            self._log.appendPlainText(f"ERROR removing records: {exc}")
            logger.error("Failed to remove DB records: %s", exc, exc_info=True)

    def _refresh_action_buttons(self) -> None:
        has_missing  = any(r["status"] in (_STATUS_MISSING, _STATUS_MODIFIED) for r in self._results)
        has_flagged  = any(r["status"] == _STATUS_FLAGGED for r in self._results)
        has_moved_db = any(r["status"] == _STATUS_MOVED_DB for r in self._results)
        self._search_btn.setEnabled(has_missing or has_flagged)
        self._find_moved_btn.setEnabled(has_missing or has_flagged)
        self._find_dup_entries_btn.setEnabled(True)
        self._recompute_dhash_btn.setEnabled(True)
        self._embed_btn.setEnabled(self._embed_available)
        self._compact_btn.setEnabled(True)
        self._db_compact_btn.setEnabled(True)
        self._prune_images_btn.setEnabled(True)
        self._check_images_btn.setEnabled(True)
        self._check_presup_btn.setEnabled(True)
        self._dedup_btn.setEnabled(True)
        self._merge_moved_btn.setEnabled(has_moved_db)
        self._remove_all_btn.setEnabled(has_missing)
        self._remove_flagged_btn.setEnabled(has_flagged)
        self._remove_sel_btn.setEnabled(bool(self._results))

    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)          # 0 = busy animation
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}" if total else "")

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        for worker in (
            self._verify_worker, self._search_worker,
            self._dhash_worker, self._embed_worker, self._moved_db_worker,
            self._prune_worker, self._img_warn_worker, self._presup_worker,
            self._dedup_worker,
        ):
            if worker and worker.isRunning():
                worker.stop()
                worker.wait(3000)
        if self._dup_entry_worker and self._dup_entry_worker.isRunning():
            self._dup_entry_worker.wait(3000)
        # Compaction must not be interrupted mid-swap — wait for it to finish.
        if self._compact_worker and self._compact_worker.isRunning():
            self._compact_worker.wait(60000)
        # VACUUM cannot be interrupted and the thread is parented to this dialog.
        if self._db_compact_worker and self._db_compact_worker.isRunning():
            self._db_compact_worker.wait()
        super().closeEvent(event)
