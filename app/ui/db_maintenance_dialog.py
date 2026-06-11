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
    QHBoxLayout,
    QHeaderView,
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

logger = logging.getLogger(__name__)

_STATUS_OK             = "OK"
_STATUS_MISSING        = "Missing"
_STATUS_FLAGGED        = "Flagged Missing"
_STATUS_FOUND          = "Relocated"
_STATUS_MODIFIED       = "Modified"
_STATUS_MOVED_DB       = "Moved (in DB)"
_STATUS_DUPLICATE      = "Duplicate"

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

    Signals:
        progress(current, total): Archive index and total count.
        log(message): Human-readable status line for the UI log panel.
        finished(results): List of result dicts once verification completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.database.db import session_scope
        from app.database.models import STLModel

        self._stop_requested = False
        results: list[dict] = []

        with session_scope() as session:
            records = session.query(
                STLModel.id,
                STLModel.title,
                STLModel.archive_path,
                STLModel.archive_hash,
                STLModel.archive_size,
                STLModel.is_missing,
            ).all()

        total = len(records)
        if total == 0:
            self.log.emit("No records found in the database.")
            self.finished.emit([])
            return

        self.log.emit(f"Verifying {total} archive(s)…")
        n_missing = 0
        n_flagged = 0
        n_modified = 0
        for idx, (model_id, title, path, ahash, stored_size, is_missing) in enumerate(records, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break
            self.progress.emit(idx, total)
            p = Path(path) if path else None
            if is_missing and (p is None or not p.is_file()):
                # Previously flagged by the crawler and still absent on disk
                status = _STATUS_FLAGGED
                n_flagged += 1
            elif p is None or not p.is_file():
                status = _STATUS_MISSING
                n_missing += 1
            elif stored_size is not None and p.stat().st_size != stored_size:
                status = _STATUS_MODIFIED
                n_modified += 1
            else:
                status = _STATUS_OK
                if is_missing:
                    # File reappeared — clear the flag opportunistically
                    from app.database.db import session_scope as _ss
                    try:
                        with _ss() as s:
                            m = s.get(STLModel, model_id)
                            if m:
                                m.is_missing = False
                    except Exception:
                        pass
            results.append({
                "id":         model_id,
                "title":      title or "",
                "path":       path or "",
                "hash":       ahash or "",
                "size":       stored_size,
                "status":     status,
                "match_path": "",
            })

        ok_count = total - n_missing - n_flagged - n_modified
        self.log.emit(
            f"Done — {ok_count} OK, {n_modified} modified, "
            f"{n_missing} missing, {n_flagged} flagged missing "
            f"out of {total} archive(s)."
        )
        self.finished.emit(results)


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
        finished(): Emitted when the scan completes or is stopped.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    found    = pyqtSignal(int, str)
    finished = pyqtSignal()

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
            self.finished.emit()
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
        self.finished.emit()


class DHashWorker(QThread):
    """Compute and persist dHash values for Image records that are missing them.

    For each image, LMDB thumbnail bytes are tried first; if absent the legacy
    thumbnail blob stored in the DB column is used as a fallback.

    Signals:
        progress(current, total): Image index and total count.
        log(message): Human-readable status line for the UI log panel.
        finished(computed, skipped): Counts once the run completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(int, int)   # (computed, skipped)

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
        from app.ui.find_content_duplicates_dialog import compute_dhash_from_jpeg, format_dhash

        self._stop_requested = False
        computed = 0
        skipped  = 0

        with session_scope() as session:
            query = session.query(DBImage.id, DBImage.thumbnail)
            if not self._recompute_all:
                # Only fill in images that have no hash yet.
                query = query.filter(DBImage.dhash.is_(None))
            rows = query.all()

        total = len(rows)
        if total == 0:
            self.log.emit("All images already have a dHash computed.")
            self.finished.emit(0, 0)
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
        self.finished.emit(computed, skipped)


class EmbeddingWorker(QThread):
    """Compute and persist learned image embeddings (CLIP) for thumbnails.

    Optional feature: if embeddings are unavailable (missing onnxruntime/numpy
    or model file) it logs the reason and finishes without doing anything.

    Signals:
        progress(current, total): Image index and total count.
        log(message): Human-readable status line for the UI log panel.
        finished(computed, skipped): Counts once the run completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(int, int)   # (computed, skipped)

    _BATCH = 100

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
        from app.utils import image_embedding as emb

        self._stop_requested = False

        if not emb.is_available():
            self.log.emit(emb.unavailable_reason())
            self.finished.emit(0, 0)
            return

        with session_scope() as session:
            query = session.query(DBImage.id, DBImage.thumbnail)
            if not self._recompute_all:
                # Images with no embedding, or one from a different model version.
                query = query.filter(
                    DBImage.embedding.is_(None)
                    | (DBImage.embedding_model != emb.EMBED_MODEL_ID)
                )
            rows = query.all()

        total = len(rows)
        if total == 0:
            self.log.emit("All images already have a current embedding.")
            self.finished.emit(0, 0)
            return

        verb = "Recomputing" if self._recompute_all else "Computing"
        self.log.emit(f"{verb} image embeddings for {total} image(s)…")

        pending: list[tuple[int, bytes]] = []   # (image_id, blob)

        def flush() -> None:
            if not pending:
                return
            with session_scope() as s:
                for img_id, blob in pending:
                    img = s.query(DBImage).filter(DBImage.id == img_id).first()
                    if img:
                        img.embedding = blob
                        img.embedding_model = emb.EMBED_MODEL_ID
            pending.clear()

        computed = 0
        skipped  = 0
        for idx, (image_id, legacy_blob) in enumerate(rows, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break

            self.progress.emit(idx, total)

            jpeg = get_thumbnail_bytes(image_id) or legacy_blob
            if not jpeg:
                skipped += 1
                continue

            vec = emb.embed_jpeg(jpeg)
            if vec is None:
                skipped += 1
                continue

            pending.append((image_id, emb.to_blob(vec)))
            computed += 1

            if len(pending) >= self._BATCH:
                flush()

        flush()

        self.log.emit(
            f"Done — {computed} embedding(s) computed, "
            f"{skipped} skipped (no thumbnail or decode error)."
        )
        self.finished.emit(computed, skipped)


class FindDuplicateEntriesWorker(QThread):
    """Find DB records that share the same archive content (hash) or the same
    filename + file size, indicating the same physical file has been catalogued
    more than once.

    Matching rules (applied in order, most reliable first):
    1. Identical ``archive_hash`` — definitive content duplicate.
    2. Identical ``(filename, archive_size)`` — likely duplicate; only used
       when the hash is a fallback fingerprint or absent.

    Each record in a duplicate group is emitted as a result row; the
    ``match_path`` field shows the path of the *first* record in the group so
    the pair relationship is immediately visible.

    Signals:
        progress(current, total)
        log(message)
        finished(results): list of result dicts, same schema as VerifyWorker.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(list)

    def run(self) -> None:
        from collections import defaultdict
        from app.database.db import session_scope
        from app.database.models import STLModel

        self.log.emit("Scanning for duplicate entries…")

        with session_scope() as session:
            rows = session.query(
                STLModel.id,
                STLModel.title,
                STLModel.archive_path,
                STLModel.archive_hash,
                STLModel.archive_size,
            ).all()

        total = len(rows)
        self.progress.emit(0, total)

        # Group by hash (skip fallback fingerprints for the hash bucket)
        hash_groups:     dict[str, list]  = defaultdict(list)
        namesize_groups: dict[tuple, list] = defaultdict(list)

        for idx, (mid, title, path, ahash, asize) in enumerate(rows, 1):
            self.progress.emit(idx, total)
            rec = {
                "id":       mid,
                "title":    title or "",
                "path":     path or "",
                "hash":     ahash or "",
                "size":     asize,
                "status":   _STATUS_DUPLICATE,
                "match_path": "",
            }
            if ahash and not ahash.startswith("fallback:"):
                hash_groups[ahash].append(rec)
            elif path and asize is not None:
                namesize_groups[(Path(path).name, asize)].append(rec)

        results: list[dict] = []

        for group in hash_groups.values():
            if len(group) < 2:
                continue
            first_path = group[0]["path"]
            for rec in group:
                rec["match_path"] = first_path if rec["path"] != first_path else group[1]["path"]
                results.append(rec)

        for group in namesize_groups.values():
            if len(group) < 2:
                continue
            # Avoid re-reporting records already caught by the hash bucket
            already = {r["id"] for r in results}
            group = [r for r in group if r["id"] not in already]
            if len(group) < 2:
                continue
            first_path = group[0]["path"]
            for rec in group:
                rec["match_path"] = first_path if rec["path"] != first_path else group[1]["path"]
                results.append(rec)

        n = len(results)
        if n:
            self.log.emit(f"Found {n} record(s) in {n // 2 + n % 2} duplicate group(s).")
        else:
            self.log.emit("No duplicate entries found.")
        self.finished.emit(results)


class MovedInDbWorker(QThread):
    """Search the DB for records whose content matches a missing archive at a different path.

    For each missing record, looks for another DB record sharing the same content
    hash, or the same filename + file size, whose path still exists on disk.

    Signals:
        progress(current, total): Index of missing record being checked.
        log(message): Human-readable status line.
        found(missing_model_id, match_path): Emitted when a match is located.
        finished(count): Total number of matches found.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    found    = pyqtSignal(int, str)   # (missing_model_id, match_archive_path)
    finished = pyqtSignal(int)

    def __init__(self, missing_records: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._missing = missing_records

    def run(self) -> None:
        from collections import defaultdict
        from app.database.db import get_session
        from app.database.models import STLModel

        if not self._missing:
            self.log.emit("No missing records to check.")
            self.finished.emit(0)
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
        self.finished.emit(matched)


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
        btn_row = QHBoxLayout()

        self._verify_btn = QPushButton(style.standardIcon(SP.SP_BrowserReload), self.tr("Verify"))
        self._verify_btn.setToolTip(self.tr("Check all archive paths for existence on disk"))
        self._verify_btn.clicked.connect(self._start_verify)
        btn_row.addWidget(self._verify_btn)

        self._stop_btn = QPushButton(style.standardIcon(SP.SP_MediaStop), self.tr("Stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._search_btn = QPushButton(style.standardIcon(SP.SP_DirOpenIcon), self.tr("Search in Folder…"))
        self._search_btn.setToolTip(
            self.tr("Pick a folder to search recursively for selected (or all) missing archives")
        )
        self._search_btn.setEnabled(False)
        self._search_btn.clicked.connect(self._start_search)
        btn_row.addWidget(self._search_btn)

        self._find_moved_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogContentsView), self.tr("Find Moved in DB…")
        )
        self._find_moved_btn.setToolTip(
            self.tr("Check whether missing archives have been re-crawled at a different location "
            "and offer to transfer their metadata")
        )
        self._find_moved_btn.setEnabled(False)
        self._find_moved_btn.clicked.connect(self._start_find_moved_in_db)
        btn_row.addWidget(self._find_moved_btn)

        self._find_dup_entries_btn = QPushButton(
            style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Find Duplicate Entries…")
        )
        self._find_dup_entries_btn.setToolTip(
            self.tr("Find records that share the same archive content hash or the same "
            "filename + file size, indicating the same file is catalogued more than once")
        )
        self._find_dup_entries_btn.clicked.connect(self._start_find_duplicate_entries)
        btn_row.addWidget(self._find_dup_entries_btn)

        self._recompute_dhash_btn = QPushButton(
            style.standardIcon(SP.SP_BrowserReload), self.tr("Recompute Image Hashes")
        )
        self._recompute_dhash_btn.setToolTip(
            self.tr("Recompute the perceptual hash (dHash) of every thumbnail. "
            "Run this after upgrading so image-similarity searches use the current "
            "128-bit algorithm.")
        )
        self._recompute_dhash_btn.clicked.connect(lambda: self._start_dhash(recompute_all=True))
        btn_row.addWidget(self._recompute_dhash_btn)

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
        btn_row.addWidget(self._embed_btn)

        self._merge_moved_btn = QPushButton(
            style.standardIcon(SP.SP_ArrowRight), self.tr("Merge Moved")
        )
        self._merge_moved_btn.setToolTip(
            self.tr("Transfer metadata from selected 'Moved (in DB)' records to their new locations")
        )
        self._merge_moved_btn.setEnabled(False)
        self._merge_moved_btn.clicked.connect(self._merge_moved_records)
        btn_row.addWidget(self._merge_moved_btn)

        self._remove_sel_btn = QPushButton(style.standardIcon(SP.SP_TrashIcon), self.tr("Remove Selected"))
        self._remove_sel_btn.setToolTip(self.tr("Remove the selected records from the database"))
        self._remove_sel_btn.setEnabled(False)
        self._remove_sel_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._remove_sel_btn)

        self._remove_all_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), self.tr("Remove All Missing")
        )
        self._remove_all_btn.setToolTip(
            self.tr("Remove every record whose archive no longer exists on disk")
        )
        self._remove_all_btn.setEnabled(False)
        self._remove_all_btn.clicked.connect(self._remove_all_missing)
        btn_row.addWidget(self._remove_all_btn)

        self._remove_flagged_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), self.tr("Remove Flagged Missing")
        )
        self._remove_flagged_btn.setToolTip(
            self.tr("Remove records flagged as missing by the crawler that are still absent on disk")
        )
        self._remove_flagged_btn.setEnabled(False)
        self._remove_flagged_btn.clicked.connect(self._remove_flagged_missing)
        btn_row.addWidget(self._remove_flagged_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton(
            style.standardIcon(SP.SP_DialogCloseButton), self.tr("Close")
        )
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

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
    def _set_busy(self, busy: bool) -> None:
        self._verify_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._search_btn.setEnabled(False)
        self._find_moved_btn.setEnabled(False)
        self._find_dup_entries_btn.setEnabled(not busy)
        self._recompute_dhash_btn.setEnabled(not busy)
        self._embed_btn.setEnabled(not busy and self._embed_available)
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
        self._merge_moved_btn.setEnabled(has_moved_db)
        self._remove_sel_btn.setEnabled(has_any)
        self._remove_all_btn.setEnabled(has_missing)
        self._remove_flagged_btn.setEnabled(has_flagged)
        self._close_btn.setEnabled(True)

    def _stop(self) -> None:
        for worker in (
            self._verify_worker, self._search_worker,
            self._dhash_worker, self._embed_worker, self._moved_db_worker,
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

        self._verify_worker = VerifyWorker()
        self._verify_worker.log.connect(self._on_log)
        self._verify_worker.progress.connect(self._on_progress)
        self._verify_worker.finished.connect(self._on_verify_finished)
        self._verify_worker.start()

    def _on_verify_finished(self, results: list) -> None:
        self._results = results
        self._populate_table(results)
        self._verify_worker = None
        self._set_idle()

    # ------------------------------------------------------------------
    def _start_dhash(self, recompute_all: bool = False) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._dhash_worker = DHashWorker(recompute_all=recompute_all)
        self._dhash_worker.log.connect(self._on_log)
        self._dhash_worker.progress.connect(self._on_progress)
        self._dhash_worker.finished.connect(self._on_dhash_finished)
        self._dhash_worker.start()

    def _on_dhash_finished(self, computed: int, skipped: int) -> None:
        self._dhash_worker = None
        self._set_idle()

    # ------------------------------------------------------------------
    def _start_embeddings(self, recompute_all: bool = False) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._set_busy(True)

        self._embed_worker = EmbeddingWorker(recompute_all=recompute_all)
        self._embed_worker.log.connect(self._on_log)
        self._embed_worker.progress.connect(self._on_progress)
        self._embed_worker.finished.connect(self._on_embed_finished)
        self._embed_worker.start()

    def _on_embed_finished(self, computed: int, skipped: int) -> None:
        self._embed_worker = None
        self._set_idle()

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

        self._search_worker = SearchWorker(Path(folder), targets)
        self._search_worker.log.connect(self._on_log)
        self._search_worker.progress.connect(self._on_progress)
        self._search_worker.found.connect(self._on_archive_found)
        self._search_worker.finished.connect(self._on_search_finished)
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
        self._search_worker = None
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

        self._moved_db_worker = MovedInDbWorker(missing)
        self._moved_db_worker.log.connect(self._on_log)
        self._moved_db_worker.progress.connect(self._on_progress)
        self._moved_db_worker.found.connect(self._on_moved_in_db_found)
        self._moved_db_worker.finished.connect(self._on_moved_in_db_finished)
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
        self._moved_db_worker = None
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
        from app.utils.thumbnailer import delete_thumbnail

        merged = 0
        errors = 0
        try:
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

                    # Capture new location's filesystem metadata before deletion
                    new_path  = new_model.archive_path
                    new_hash  = new_model.archive_hash
                    new_size  = new_model.archive_size
                    new_mtime = new_model.archive_mtime_ns
                    new_compr = new_model.compression_rate

                    # Delete new model's images/thumbnails
                    for img in list(new_model.images):
                        if img.thumbnail_path:
                            delete_thumbnail(img.thumbnail_path)
                        session.delete(img)
                    session.flush()

                    # Remove the re-crawled (shallow) record
                    session.delete(new_model)
                    session.flush()

                    # Redirect old record to new location
                    old_model.archive_path     = new_path
                    old_model.archive_hash     = new_hash
                    old_model.archive_size     = new_size
                    old_model.archive_mtime_ns = new_mtime
                    if new_compr is not None:
                        old_model.compression_rate = new_compr
                    session.flush()
                    merged += 1

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

        self._dup_entry_worker = FindDuplicateEntriesWorker()
        self._dup_entry_worker.log.connect(self._on_log)
        self._dup_entry_worker.progress.connect(self._on_progress)
        self._dup_entry_worker.finished.connect(self._on_dup_entries_finished)
        self._dup_entry_worker.start()

    def _on_dup_entries_finished(self, results: list) -> None:
        self._results = results
        self._populate_table(results)
        self._dup_entry_worker = None
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
        self._merge_moved_btn.setEnabled(has_moved_db)
        self._remove_all_btn.setEnabled(has_missing)
        self._remove_flagged_btn.setEnabled(has_flagged)
        self._remove_sel_btn.setEnabled(bool(self._results))

    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        for worker in (
            self._verify_worker, self._search_worker,
            self._dhash_worker, self._embed_worker, self._moved_db_worker,
        ):
            if worker and worker.isRunning():
                worker.stop()
                worker.wait(3000)
        if self._dup_entry_worker and self._dup_entry_worker.isRunning():
            self._dup_entry_worker.wait(3000)
        super().closeEvent(event)
