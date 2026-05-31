"""
Dialog that compares archive contents stored in the stl_files table and
thumbnail images using perceptual hashing, reporting pairs of archives
that share a high fraction of identical files or similar-looking images.

Two separate tabs handle the two modes:
  STL Files  — inverted-index overlap on (filename, file_size)
  Images     — dHash perceptual matching on LMDB thumbnails (pure Pillow)
"""
import io
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QPoint, QThread, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QMenu, QMessageBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QGroupBox, QFormLayout, QTabWidget, QWidget, QScrollArea, QSplitter,
)

logger = logging.getLogger(__name__)

_TAB_STL  = 0
_TAB_IMGS = 1

# ── STL tab column indices ────────────────────────────────────────────────────
_S_ID_A    = 0   # hidden
_S_TITLE_A = 1
_S_PATH_A  = 2
_S_SIZE_A  = 3
_S_FILES_A = 4
_S_ID_B    = 5   # hidden
_S_TITLE_B = 6
_S_PATH_B  = 7
_S_SIZE_B  = 8
_S_FILES_B = 9
_S_MATCH   = 10
_S_SIM     = 11
_S_NCOLS   = 12

# ── Images tab column indices ─────────────────────────────────────────────────
_I_ID_A    = 0   # hidden
_I_TITLE_A = 1
_I_PATH_A  = 2
_I_SIZE_A  = 3
_I_IMGS_A  = 4
_I_ID_B    = 5   # hidden
_I_TITLE_B = 6
_I_PATH_B  = 7
_I_SIZE_B  = 8
_I_IMGS_B  = 9
_I_PAIRS   = 10
_I_DIST    = 11
_I_NCOLS   = 12


# ─────────────────────────────────────────────────────────────────────────────
# Shared table helper
# ─────────────────────────────────────────────────────────────────────────────

def _item(text: str, user_data=None,
          align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if user_data is not None:
        it.setData(Qt.ItemDataRole.UserRole, user_data)
    if align is not None:
        it.setTextAlignment(align)
    return it


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric UserRole value instead of text."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        try:
            return float(a) < float(b)
        except (TypeError, ValueError):
            return super().__lt__(other)


def _fmt_size(n: int) -> str:
    """Return a human-readable string for *n* bytes."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _size_item(path: str) -> QTableWidgetItem:
    """Return a centre-aligned, numerically sortable size cell for *path*."""
    c = Qt.AlignmentFlag.AlignCenter
    try:
        n = Path(path).stat().st_size
    except OSError:
        it = _NumericItem("?")
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        it.setData(Qt.ItemDataRole.UserRole, -1)
        it.setTextAlignment(c)
        return it
    it = _NumericItem(_fmt_size(n))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    it.setData(Qt.ItemDataRole.UserRole, n)
    it.setTextAlignment(c)
    return it


# ─────────────────────────────────────────────────────────────────────────────
# Perceptual hashing — pure Pillow, no extra dependencies
# ─────────────────────────────────────────────────────────────────────────────

DHASH_SIZE    = 8                          # grid size per gradient direction
DHASH_BITS    = 2 * DHASH_SIZE * DHASH_SIZE  # 128 (horizontal + vertical)
DHASH_HEX_LEN = DHASH_BITS // 4              # 32 hex chars


def _dhash(jpeg_bytes: bytes, hash_size: int = DHASH_SIZE) -> Optional[int]:
    """Compute a 128-bit difference hash (horizontal + vertical) from JPEG bytes.

    The image is converted to greyscale and resized to
    (hash_size+1) × (hash_size+1) pixels.  Two ``hash_size²``-bit hashes are
    computed and concatenated: one comparing each pixel to its right neighbour
    (horizontal gradient) and one comparing each pixel to the one below it
    (vertical gradient).  Capturing both directions discriminates better than
    a horizontal-only hash, reducing false matches between images that share a
    horizontal gradient but differ vertically.

    Returns None if the image cannot be decoded.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
        img = img.resize((hash_size + 1, hash_size + 1), Image.LANCZOS)
        px  = img.load()
        result = 0
        bit = 0
        # Horizontal gradient: compare each pixel to the one to its right.
        for row in range(hash_size):
            for col in range(hash_size):
                if px[col, row] > px[col + 1, row]:
                    result |= 1 << bit
                bit += 1
        # Vertical gradient: compare each pixel to the one below it.
        for row in range(hash_size):
            for col in range(hash_size):
                if px[col, row] > px[col, row + 1]:
                    result |= 1 << bit
                bit += 1
        return result
    except Exception:
        return None


def compute_dhash_from_jpeg(jpeg_bytes: bytes) -> Optional[int]:
    """Public wrapper used by other modules to compute dHash from JPEG bytes."""
    return _dhash(jpeg_bytes)


def format_dhash(value: int) -> str:
    """Format a dHash integer as a fixed-width, zero-padded hex string.

    The fixed width (:data:`DHASH_HEX_LEN`) lets readers recognise hashes made
    by an older/narrower algorithm by their differing length and recompute them.
    """
    return f"{value:0{DHASH_HEX_LEN}x}"


def parse_dhash(stored) -> Optional[int]:
    """Parse a stored dHash, accepting only the current algorithm's bit width.

    Older 64-bit hashes (16 hex chars), integers, and malformed values return
    ``None`` so callers recompute rather than compare incompatible fingerprints.
    """
    if not isinstance(stored, str) or len(stored) != DHASH_HEX_LEN:
        return None
    try:
        return int(stored, 16)
    except ValueError:
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _find_candidates(
    hashes: list[tuple[int, int, int]],   # (image_id, model_id, hash_value)
    max_dist: int,
) -> set[tuple[int, int]]:
    """Return index pairs (i, j) with i < j that may have Hamming distance ≤ max_dist.

    Uses the pigeonhole principle: split the 64-bit hash into (max_dist + 1)
    equal segments.  Two hashes that differ in ≤ max_dist bits must share at
    least one segment verbatim, so the segment inverted index has no false
    negatives.  False positives are removed by the caller via exact distance.

    For max_dist = 0 a plain dict grouping is used instead.
    """
    if not hashes:
        return set()

    if max_dist == 0:
        bucket: dict[int, list[int]] = defaultdict(list)
        for idx, (_, _, h) in enumerate(hashes):
            bucket[h].append(idx)
        out: set[tuple[int, int]] = set()
        for grp in bucket.values():
            for a in range(len(grp)):
                for b in range(a + 1, len(grp)):
                    out.add((grp[a], grp[b]))
        return out

    num_seg  = max_dist + 1
    seg_bits = DHASH_BITS // num_seg
    seg_mask = (1 << seg_bits) - 1

    index: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, (_, _, h) in enumerate(hashes):
        for s in range(num_seg):
            index[(s, (h >> (s * seg_bits)) & seg_mask)].append(idx)

    candidates: set[tuple[int, int]] = set()
    for idx, (_, _, h) in enumerate(hashes):
        for s in range(num_seg):
            seg_val = (h >> (s * seg_bits)) & seg_mask
            for other in index[(s, seg_val)]:
                if other > idx:
                    candidates.add((idx, other))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# STL-files worker
# ─────────────────────────────────────────────────────────────────────────────

class _ContentDupWorker(QThread):
    """Find archive pairs that share a high fraction of STL files."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, threshold: float, parent=None) -> None:
        super().__init__(parent)
        self._threshold = threshold
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from app.database.db import session_scope
        from app.database.models import STLFile, STLModel

        try:
            with session_scope() as session:
                self.progress.emit("Loading STL file records…")
                rows = (
                    session.query(STLFile.model_id, STLFile.filename, STLFile.file_size)
                    .join(STLModel, STLModel.id == STLFile.model_id)
                    .filter(STLFile.file_size.isnot(None))
                    .filter(STLModel.is_missing.isnot(True))
                    .all()
                )
                if not rows:
                    self.finished.emit([])
                    return

                model_files: dict[int, set[tuple]] = defaultdict(set)
                for model_id, filename, file_size in rows:
                    if self._stop:
                        self.finished.emit([])
                        return
                    model_files[model_id].add((filename, file_size))

                self.progress.emit(f"Building index over {len(model_files)} archive(s)…")
                file_to_models: dict[tuple, set[int]] = defaultdict(set)
                for model_id, files in model_files.items():
                    if self._stop:
                        self.finished.emit([])
                        return
                    for key in files:
                        file_to_models[key].add(model_id)

                self.progress.emit("Computing pair overlaps…")
                pair_overlap: dict[tuple[int, int], int] = defaultdict(int)
                for key, models in file_to_models.items():
                    if len(models) < 2:
                        continue
                    if self._stop:
                        self.finished.emit([])
                        return
                    ms = sorted(models)
                    for i in range(len(ms)):
                        for j in range(i + 1, len(ms)):
                            pair_overlap[(ms[i], ms[j])] += 1

                self.progress.emit("Filtering by threshold…")
                model_file_count = {mid: len(f) for mid, f in model_files.items()}
                candidate_pairs = []
                for (id_a, id_b), overlap in pair_overlap.items():
                    if self._stop:
                        self.finished.emit([])
                        return
                    ta = model_file_count.get(id_a, 0)
                    tb = model_file_count.get(id_b, 0)
                    if ta == 0 or tb == 0:
                        continue
                    sim = overlap / max(ta, tb)
                    if sim >= self._threshold:
                        candidate_pairs.append((id_a, id_b, overlap, ta, tb, sim))

                if not candidate_pairs:
                    self.finished.emit([])
                    return

                self.progress.emit("Loading model titles…")
                all_ids: set[int] = set()
                for id_a, id_b, *_ in candidate_pairs:
                    all_ids.add(id_a)
                    all_ids.add(id_b)
                models_map = {
                    m.id: m
                    for m in session.query(STLModel)
                        .filter(STLModel.id.in_(list(all_ids)))
                        .all()
                }

            results = []
            for id_a, id_b, overlap, ta, tb, sim in sorted(candidate_pairs, key=lambda x: -x[5]):
                ma = models_map.get(id_a)
                mb = models_map.get(id_b)
                if ma and mb:
                    results.append({
                        "id_a":    id_a,
                        "title_a": ma.title or Path(ma.archive_path).stem,
                        "path_a":  ma.archive_path or "",
                        "total_a": ta,
                        "id_b":    id_b,
                        "title_b": mb.title or Path(mb.archive_path).stem,
                        "path_b":  mb.archive_path or "",
                        "total_b": tb,
                        "overlap": overlap,
                        "sim":     sim,
                    })
            self.finished.emit(results)

        except Exception as exc:
            logger.error("STL content duplicate search failed: %s", exc, exc_info=True)
            self.finished.emit([])


# ─────────────────────────────────────────────────────────────────────────────
# Image duplicate worker
# ─────────────────────────────────────────────────────────────────────────────

class _ImageDupWorker(QThread):
    """Find archive pairs that share perceptually similar thumbnail images."""

    progress = pyqtSignal(str)
    prog_val = pyqtSignal(int, int)   # (current, total) for hash-computation phase
    finished = pyqtSignal(list)

    def __init__(self, max_dist: int, parent=None) -> None:
        super().__init__(parent)
        self._max_dist = max_dist
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from app.database.db import session_scope
        from app.database.models import Image, STLModel
        from app.utils.thumbnailer import get_thumbnail_bytes

        try:
            with session_scope() as session:
                self.progress.emit("Loading image records…")
                image_rows = (
                    session.query(Image.id, Image.model_id)
                    .join(STLModel, STLModel.id == Image.model_id)
                    .filter(Image.thumbnail_path.isnot(None))
                    .filter(STLModel.is_missing.isnot(True))
                    .all()
                )
                if not image_rows:
                    self.finished.emit([])
                    return

                total = len(image_rows)
                self.prog_val.emit(0, total)
                self.progress.emit(f"Computing perceptual hashes for {total} image(s)…")

                # (image_id, model_id, dhash_value)
                hashes: list[tuple[int, int, int]] = []
                model_img_count: dict[int, int] = defaultdict(int)

                for n, (img_id, model_id) in enumerate(image_rows):
                    if self._stop:
                        self.finished.emit([])
                        return
                    # Prefer a stored dhash in the Image row when available to avoid recomputing
                    # (load Image.dhash lazily via a separate query to keep the main query simple)
                    from app.database.db import get_session
                    s = get_session()
                    try:
                        from sqlalchemy import text
                        row = s.execute(
                            text("SELECT dhash FROM images WHERE id = :id"),
                            {"id": img_id},
                        ).fetchone()
                        dh = row[0] if row is not None else None
                    finally:
                        s.close()
                    # Accept the stored hash only if it matches the current algorithm;
                    # otherwise (missing or an older/narrower hash) recompute it.
                    h_int = parse_dhash(dh)
                    if h_int is None:
                        jpeg = get_thumbnail_bytes(img_id)
                        if jpeg:
                            h_int = _dhash(jpeg)
                    if h_int is not None:
                        hashes.append((img_id, model_id, h_int))
                        model_img_count[model_id] += 1
                    if (n + 1) % 50 == 0:
                        self.prog_val.emit(n + 1, total)

                self.prog_val.emit(total, total)

                if not hashes:
                    self.finished.emit([])
                    return

                self.progress.emit(
                    f"Finding similar image pairs among {len(hashes)} hashed image(s)…"
                )
                candidates = _find_candidates(hashes, self._max_dist)

                if self._stop:
                    self.finished.emit([])
                    return

                self.progress.emit(f"Scoring {len(candidates)} candidate pair(s)…")

                # (model_a, model_b) → list of per-image-pair Hamming distances
                pair_dists: dict[tuple[int, int], list[int]] = defaultdict(list)

                for i, j in candidates:
                    if self._stop:
                        self.finished.emit([])
                        return
                    _, model_a, h_a = hashes[i]
                    _, model_b, h_b = hashes[j]
                    if model_a == model_b:
                        continue
                    dist = _hamming(h_a, h_b)
                    if dist <= self._max_dist:
                        key = (min(model_a, model_b), max(model_a, model_b))
                        pair_dists[key].append(dist)

                if not pair_dists:
                    self.finished.emit([])
                    return

                self.progress.emit("Loading model titles…")
                all_ids: set[int] = set()
                for id_a, id_b in pair_dists:
                    all_ids.add(id_a)
                    all_ids.add(id_b)
                models_map = {
                    m.id: m
                    for m in session.query(STLModel)
                        .filter(STLModel.id.in_(list(all_ids)))
                        .all()
                }

            results = []
            for (id_a, id_b), dists in pair_dists.items():
                ma = models_map.get(id_a)
                mb = models_map.get(id_b)
                if ma and mb:
                    results.append({
                        "id_a":    id_a,
                        "title_a": ma.title or Path(ma.archive_path).stem,
                        "path_a":  ma.archive_path or "",
                        "imgs_a":  model_img_count.get(id_a, 0),
                        "id_b":    id_b,
                        "title_b": mb.title or Path(mb.archive_path).stem,
                        "path_b":  mb.archive_path or "",
                        "imgs_b":  model_img_count.get(id_b, 0),
                        "pairs":   len(dists),
                        "best":    min(dists),
                    })

            # Sort: lowest best-distance first, then most matching pairs
            results.sort(key=lambda r: (r["best"], -r["pairs"]))
            self.finished.emit(results)

        except Exception as exc:
            logger.error("Image duplicate search failed: %s", exc, exc_info=True)
            self.finished.emit([])


# ─────────────────────────────────────────────────────────────────────────────
# Thumbnail hover popup
# ─────────────────────────────────────────────────────────────────────────────

class _ThumbnailPopup(QFrame):
    """Frameless tooltip-style window showing two archive thumbnails side-by-side."""

    _MAX_DIM = 240

    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.ToolTip | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        self._lbl_a = QLabel()
        self._lbl_b = QLabel()
        for lbl in (self._lbl_a, self._lbl_b):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setMinimumSize(self._MAX_DIM, self._MAX_DIM)
            layout.addWidget(lbl)

    def set_images(self, data_a: Optional[bytes], data_b: Optional[bytes]) -> None:
        self._set_label(self._lbl_a, data_a, "A")
        self._set_label(self._lbl_b, data_b, "B")

    def _set_label(self, lbl: QLabel, data: Optional[bytes], side: str) -> None:
        if data:
            px = QPixmap()
            if px.loadFromData(data):
                px = px.scaled(
                    self._MAX_DIM, self._MAX_DIM,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                lbl.setPixmap(px)
                return
        lbl.clear()
        lbl.setText(f"No image ({side})")


class _ThumbStrip(QScrollArea):
    """Horizontally-scrolling strip of thumbnails for one archive."""

    _THUMB = 110

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(self._THUMB + 30)
        host = QWidget()
        self._row = QHBoxLayout(host)
        self._row.setContentsMargins(4, 4, 4, 4)
        self._row.setSpacing(4)
        self._row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.setWidget(host)

    def set_thumbs(self, blobs: list[bytes], empty_text: str = "") -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not blobs:
            placeholder = QLabel(empty_text)
            placeholder.setEnabled(False)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._row.addWidget(placeholder)
            return
        for data in blobs:
            px = QPixmap()
            if not px.loadFromData(data):
                continue
            px = px.scaled(
                self._THUMB, self._THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lbl = QLabel()
            lbl.setPixmap(px)
            lbl.setFixedSize(self._THUMB, self._THUMB)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._row.addWidget(lbl)


class _ImgHoverFilter(QObject):
    """Viewport event filter that shows a thumbnail popup when hovering image result rows."""

    def __init__(
        self,
        table: QTableWidget,
        popup: _ThumbnailPopup,
        get_thumb_fn,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._popup = popup
        self._get_thumb = get_thumb_fn
        self._last_row = -1

    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if t == QEvent.Type.MouseMove:
            item = self._table.itemAt(event.pos())
            row = item.row() if item else -1
            if row != self._last_row:
                self._last_row = row
                if row >= 0:
                    self._show_for_row(row, event.globalPosition().toPoint())
                else:
                    self._popup.hide()
            elif row >= 0:
                self._move_popup(event.globalPosition().toPoint())
        elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
            self._popup.hide()
            self._last_row = -1
        return False

    def _show_for_row(self, row: int, gpos) -> None:
        id_a = self._table.item(row, _I_ID_A).data(Qt.ItemDataRole.UserRole)
        id_b = self._table.item(row, _I_ID_B).data(Qt.ItemDataRole.UserRole)
        self._popup.set_images(self._get_thumb(id_a), self._get_thumb(id_b))
        self._popup.adjustSize()
        self._move_popup(gpos)
        self._popup.show()
        self._popup.raise_()

    def _move_popup(self, gpos) -> None:
        self._popup.move(gpos.x() + 16, gpos.y() + 16)


# ─────────────────────────────────────────────────────────────────────────────
# Dialog
# ─────────────────────────────────────────────────────────────────────────────

class FindContentDuplicatesDialog(QDialog):
    """Non-modal dialog for finding archives that share STL file content or
    perceptually similar preview images.

    Emits ``show_models_requested(list[int])`` so the parent window can filter
    the browser to show only the selected or all flagged archives.
    """

    show_models_requested = pyqtSignal(list)
    show_duplicate_pairs_requested = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Find Duplicate Archives by Content"))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1050, 720)
        self._stl_worker: Optional[_ContentDupWorker] = None
        self._img_worker: Optional[_ImageDupWorker]   = None
        self._stl_results: list[dict] = []
        self._img_results: list[dict] = []
        self._img_model_thumb: dict[int, Optional[bytes]] = {}
        self._stl_thumb_cache: dict[int, list[bytes]] = {}
        self._img_popup = _ThumbnailPopup(parent=self)
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_stl_tab(),    self.tr("STL Files"))
        self._tabs.addTab(self._build_images_tab(), self.tr("Images"))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs)

        btn_row = QHBoxLayout()
        self._show_pair_btn = QPushButton(self.tr("Show Pair in Main Table"))
        self._show_pair_btn.setEnabled(False)
        self._show_pair_btn.clicked.connect(self._show_pair)
        self._show_all_btn = QPushButton(self.tr("Show All Duplicates in Main Table"))
        self._show_all_btn.setEnabled(False)
        self._show_all_btn.clicked.connect(self._show_all)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(self._show_pair_btn)
        btn_row.addWidget(self._show_all_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _build_stl_tab(self) -> QWidget:
        page   = QWidget()
        layout = QVBoxLayout(page)

        grp  = QGroupBox(self.tr("Search Parameters"))
        form = QFormLayout(grp)
        self._stl_thresh_spin = QSpinBox()
        self._stl_thresh_spin.setRange(1, 100)
        self._stl_thresh_spin.setValue(80)
        self._stl_thresh_spin.setSuffix(" %")
        self._stl_thresh_spin.setToolTip(
            self.tr("Minimum fraction of files (relative to the larger archive) "
            "that must be identical for a pair to be flagged.")
        )
        form.addRow(self.tr("Similarity threshold:"), self._stl_thresh_spin)
        form.addRow("", QLabel(
            self.tr("<small>Two archives are flagged when at least this fraction of the "
            "<i>larger</i> archive's STL files appear in both (matched by filename "
            "and size). Requires <b>Store STL file list</b> to be enabled in "
            "Settings and archives to have been crawled.</small>")
        ))
        layout.addWidget(grp)

        ctrl = QHBoxLayout()
        self._stl_find_btn = QPushButton(self.tr("Find Duplicates"))
        self._stl_find_btn.setDefault(True)
        self._stl_find_btn.clicked.connect(self._start_stl_search)
        self._stl_stop_btn = QPushButton(self.tr("Stop"))
        self._stl_stop_btn.setEnabled(False)
        self._stl_stop_btn.clicked.connect(self._stop_stl_search)
        self._stl_progress = QProgressBar()
        self._stl_progress.setRange(0, 0)
        self._stl_progress.setVisible(False)
        ctrl.addWidget(self._stl_find_btn)
        ctrl.addWidget(self._stl_stop_btn)
        ctrl.addWidget(self._stl_progress, stretch=1)
        layout.addLayout(ctrl)

        self._stl_status = QLabel(self.tr("Ready."))
        layout.addWidget(self._stl_status)

        self._stl_table = _make_table(
            ["ID A", "Title A", "Path A", "Size A", "Files A",
             "ID B", "Title B", "Path B", "Size B", "Files B",
             "Matching", "Similarity"],
            hidden=[_S_ID_A, _S_ID_B],
            stretch=[_S_TITLE_A, _S_PATH_A, _S_TITLE_B, _S_PATH_B],
        )
        self._stl_table.itemSelectionChanged.connect(self._on_stl_selection)
        self._stl_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._stl_table.customContextMenuRequested.connect(self._stl_context_menu)

        # ── Image preview: thumbnails of both archives in the selected pair ──
        self._stl_preview = QGroupBox(self.tr("Images in selected pair"))
        prev_cols = QHBoxLayout(self._stl_preview)
        prev_cols.setSpacing(10)

        col_a = QVBoxLayout()
        self._stl_prev_title_a = QLabel(self.tr("Archive A"))
        self._stl_prev_title_a.setTextFormat(Qt.TextFormat.PlainText)
        self._stl_strip_a = _ThumbStrip()
        col_a.addWidget(self._stl_prev_title_a)
        col_a.addWidget(self._stl_strip_a)

        col_b = QVBoxLayout()
        self._stl_prev_title_b = QLabel(self.tr("Archive B"))
        self._stl_prev_title_b.setTextFormat(Qt.TextFormat.PlainText)
        self._stl_strip_b = _ThumbStrip()
        col_b.addWidget(self._stl_prev_title_b)
        col_b.addWidget(self._stl_strip_b)

        prev_cols.addLayout(col_a, 1)
        prev_cols.addLayout(col_b, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._stl_table)
        splitter.addWidget(self._stl_preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)

        self._update_stl_preview()
        return page

    def _build_images_tab(self) -> QWidget:
        page   = QWidget()
        layout = QVBoxLayout(page)

        grp  = QGroupBox(self.tr("Search Parameters"))
        form = QFormLayout(grp)
        self._img_dist_spin = QSpinBox()
        self._img_dist_spin.setRange(0, 32)
        self._img_dist_spin.setValue(10)
        self._img_dist_spin.setToolTip(
            self.tr("Maximum Hamming distance between 128-bit dHash fingerprints.\n"
            "0 = bit-for-bit identical pixels\n"
            "≤6 = very similar (different resolution / minor JPEG artefacts)\n"
            "≤16 = noticeably similar (recolour, slight crop)\n"
            "≤30 = loosely similar (use with caution — many false positives)")
        )
        form.addRow(self.tr("Max hash distance:"), self._img_dist_spin)
        form.addRow("", QLabel(
            self.tr("<small>Preview thumbnails are compared with a perceptual difference "
            "hash (dHash). Two thumbnails are considered similar when their "
            "128-bit hashes differ in at most this many bits. A distance of 0 "
            "catches only near-identical images; 10 typically catches the same "
            "render at different resolutions or with slight colour shifts. "
            "Higher values increase recall but also false positives.</small>")
        ))
        layout.addWidget(grp)

        ctrl = QHBoxLayout()
        self._img_find_btn = QPushButton(self.tr("Find Duplicates"))
        self._img_find_btn.clicked.connect(self._start_img_search)
        self._img_stop_btn = QPushButton(self.tr("Stop"))
        self._img_stop_btn.setEnabled(False)
        self._img_stop_btn.clicked.connect(self._stop_img_search)
        self._img_progress = QProgressBar()
        self._img_progress.setRange(0, 1)
        self._img_progress.setVisible(False)
        ctrl.addWidget(self._img_find_btn)
        ctrl.addWidget(self._img_stop_btn)
        ctrl.addWidget(self._img_progress, stretch=1)
        layout.addLayout(ctrl)

        self._img_status = QLabel(self.tr("Ready."))
        layout.addWidget(self._img_status)

        self._img_table = _make_table(
            ["ID A", "Title A", "Path A", "Size A", "Images A",
             "ID B", "Title B", "Path B", "Size B", "Images B",
             "Matching Pairs", "Best Distance"],
            hidden=[_I_ID_A, _I_ID_B],
            stretch=[_I_TITLE_A, _I_PATH_A, _I_TITLE_B, _I_PATH_B],
        )
        self._img_table.itemSelectionChanged.connect(self._on_img_selection)
        self._img_table.viewport().setMouseTracking(True)
        self._img_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._img_table.customContextMenuRequested.connect(self._img_context_menu)
        self._img_hover_filter = _ImgHoverFilter(
            self._img_table, self._img_popup, self._get_model_thumb, parent=self
        )
        self._img_table.viewport().installEventFilter(self._img_hover_filter)
        layout.addWidget(self._img_table)
        return page

    # ── STL search ────────────────────────────────────────────────────────────

    def _start_stl_search(self) -> None:
        self._stl_table.setRowCount(0)
        self._stl_results = []
        self._stl_thumb_cache.clear()
        self._stl_find_btn.setEnabled(False)
        self._stl_stop_btn.setEnabled(True)
        self._stl_progress.setRange(0, 0)
        self._stl_progress.setVisible(True)
        self._stl_status.setText(self.tr("Searching…"))
        self._refresh_bottom_buttons()

        self._stl_worker = _ContentDupWorker(
            self._stl_thresh_spin.value() / 100.0, parent=self
        )
        self._stl_worker.progress.connect(self._stl_status.setText)
        self._stl_worker.finished.connect(self._on_stl_finished)
        self._stl_worker.start()

    def _stop_stl_search(self) -> None:
        if self._stl_worker and self._stl_worker.isRunning():
            self._stl_worker.stop()
            self._stl_worker.wait()
        self._stl_find_btn.setEnabled(True)
        self._stl_stop_btn.setEnabled(False)
        self._stl_progress.setVisible(False)
        self._stl_status.setText(self.tr("Search stopped."))

    def _on_stl_finished(self, results: list) -> None:
        self._stl_results = results
        self._stl_find_btn.setEnabled(True)
        self._stl_stop_btn.setEnabled(False)
        self._stl_progress.setVisible(False)

        self._stl_table.setSortingEnabled(False)
        self._stl_table.setRowCount(0)
        c = Qt.AlignmentFlag.AlignCenter
        for row_idx, r in enumerate(results):
            self._stl_table.insertRow(row_idx)
            self._stl_table.setItem(row_idx, _S_ID_A,    _item(str(r["id_a"]),    r["id_a"]))
            self._stl_table.setItem(row_idx, _S_TITLE_A, _item(r["title_a"]))
            self._stl_table.setItem(row_idx, _S_PATH_A,  _item(r["path_a"]))
            self._stl_table.setItem(row_idx, _S_SIZE_A,  _size_item(r["path_a"]))
            self._stl_table.setItem(row_idx, _S_FILES_A, _item(str(r["total_a"]), align=c))
            self._stl_table.setItem(row_idx, _S_ID_B,    _item(str(r["id_b"]),    r["id_b"]))
            self._stl_table.setItem(row_idx, _S_TITLE_B, _item(r["title_b"]))
            self._stl_table.setItem(row_idx, _S_PATH_B,  _item(r["path_b"]))
            self._stl_table.setItem(row_idx, _S_SIZE_B,  _size_item(r["path_b"]))
            self._stl_table.setItem(row_idx, _S_FILES_B, _item(str(r["total_b"]), align=c))
            self._stl_table.setItem(row_idx, _S_MATCH,   _item(str(r["overlap"]), align=c))
            self._stl_table.setItem(row_idx, _S_SIM,     _item(f"{r['sim']:.0%}", align=c))
        self._stl_table.setSortingEnabled(True)

        n = len(results)
        self._stl_status.setText(
            f"{n} duplicate pair(s) found." if n else
            "No duplicate pairs found. Ensure 'Store STL file list' is enabled "
            "in Settings and archives have been crawled."
        )
        self._refresh_bottom_buttons()

    def _on_stl_selection(self) -> None:
        if self._tabs.currentIndex() == _TAB_STL:
            self._refresh_bottom_buttons()
        self._update_stl_preview()

    def _update_stl_preview(self) -> None:
        """Show thumbnails of both archives in the singly-selected result row."""
        rows = {i.row() for i in self._stl_table.selectedIndexes()}
        if len(rows) != 1:
            self._stl_prev_title_a.setText(self.tr("Archive A"))
            self._stl_prev_title_b.setText(self.tr("Archive B"))
            empty = self.tr("Select a result to preview its images.")
            self._stl_strip_a.set_thumbs([], empty)
            self._stl_strip_b.set_thumbs([], empty)
            return

        row = next(iter(rows))
        id_a = self._stl_table.item(row, _S_ID_A).data(Qt.ItemDataRole.UserRole)
        id_b = self._stl_table.item(row, _S_ID_B).data(Qt.ItemDataRole.UserRole)
        title_a = self._stl_table.item(row, _S_TITLE_A).text()
        title_b = self._stl_table.item(row, _S_TITLE_B).text()
        thumbs_a = self._archive_thumbs(id_a)
        thumbs_b = self._archive_thumbs(id_b)
        self._stl_prev_title_a.setText(self.tr("Archive A: %s") % title_a)
        self._stl_prev_title_b.setText(self.tr("Archive B: %s") % title_b)
        no_img = self.tr("No images in this archive.")
        self._stl_strip_a.set_thumbs(thumbs_a, no_img)
        self._stl_strip_b.set_thumbs(thumbs_b, no_img)

    def _archive_thumbs(self, model_id: int, limit: int = 24) -> list[bytes]:
        """Return up to *limit* thumbnail JPEGs for *model_id* (cached)."""
        if model_id in self._stl_thumb_cache:
            return self._stl_thumb_cache[model_id]
        blobs: list[bytes] = []
        try:
            from app.database.db import session_scope
            from app.database.models import Image
            from app.utils.thumbnailer import get_thumbnail_bytes_many
            with session_scope() as session:
                ids = [
                    iid for (iid,) in session.query(Image.id)
                    .filter(Image.model_id == model_id, Image.thumbnail_path.isnot(None))
                    .order_by(Image.id)
                    .limit(limit)
                    .all()
                ]
            data = get_thumbnail_bytes_many(ids)
            blobs = [data[i] for i in ids if i in data]
        except Exception as exc:
            logger.warning("Failed to load archive thumbnails: %s", exc)
        self._stl_thumb_cache[model_id] = blobs
        return blobs

    def _show_compare_menu(
        self,
        table: QTableWidget,
        id_col_a: int,
        id_col_b: int,
        path_col_a: int,
        path_col_b: int,
        pos: QPoint,
    ) -> None:
        """Show context menu for the clicked row with compare and open-folder actions."""
        item = table.itemAt(pos)
        if not item:
            return
        row    = item.row()
        id_a   = table.item(row, id_col_a).data(Qt.ItemDataRole.UserRole)
        id_b   = table.item(row, id_col_b).data(Qt.ItemDataRole.UserRole)
        path_a = (table.item(row, path_col_a).text()
                  if table.item(row, path_col_a) else "")
        path_b = (table.item(row, path_col_b).text()
                  if table.item(row, path_col_b) else "")

        style       = QApplication.style()
        menu        = QMenu(self)
        compare_act = menu.addAction(
            style.standardIcon(style.StandardPixmap.SP_FileDialogContentsView),
            self.tr("Compare Archive Contents…"),
        )
        menu.addSeparator()
        open_a_act = menu.addAction(
            style.standardIcon(style.StandardPixmap.SP_DirOpenIcon),
            self.tr("Open Folder of Archive A in Explorer"),
        )
        open_a_act.setEnabled(bool(path_a))
        open_b_act = menu.addAction(
            style.standardIcon(style.StandardPixmap.SP_DirOpenIcon),
            self.tr("Open Folder of Archive B in Explorer"),
        )
        open_b_act.setEnabled(bool(path_b))

        delete_a_act = delete_b_act = None
        from app.ui.config_dialog import get_allow_file_delete
        if get_allow_file_delete():
            menu.addSeparator()
            delete_a_act = menu.addAction(
                style.standardIcon(style.StandardPixmap.SP_TrashIcon),
                self.tr("Delete Archive A from Disk…"),
            )
            delete_b_act = menu.addAction(
                style.standardIcon(style.StandardPixmap.SP_TrashIcon),
                self.tr("Delete Archive B from Disk…"),
            )

        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action == compare_act:
            from app.ui.file_list_dialog import FileComparisonDialog
            FileComparisonDialog(id_a, id_b, parent=self).exec()
        elif action == open_a_act and path_a:
            from app.ui.config_dialog import open_folder_with_file_manager
            open_folder_with_file_manager(str(Path(path_a).parent))
        elif action == open_b_act and path_b:
            from app.ui.config_dialog import open_folder_with_file_manager
            open_folder_with_file_manager(str(Path(path_b).parent))
        elif delete_a_act and action == delete_a_act:
            self._delete_archive_from_disk(id_a, table, row)
        elif delete_b_act and action == delete_b_act:
            self._delete_archive_from_disk(id_b, table, row)

    def _stl_context_menu(self, pos: QPoint) -> None:
        self._show_compare_menu(self._stl_table, _S_ID_A, _S_ID_B, _S_PATH_A, _S_PATH_B, pos)

    # ── Image search ──────────────────────────────────────────────────────────

    def _start_img_search(self) -> None:
        self._img_table.setRowCount(0)
        self._img_results = []
        self._img_find_btn.setEnabled(False)
        self._img_stop_btn.setEnabled(True)
        self._img_progress.setRange(0, 1)
        self._img_progress.setValue(0)
        self._img_progress.setVisible(True)
        self._img_status.setText(self.tr("Searching…"))
        self._refresh_bottom_buttons()

        self._img_worker = _ImageDupWorker(
            self._img_dist_spin.value(), parent=self
        )
        self._img_worker.progress.connect(self._img_status.setText)
        self._img_worker.prog_val.connect(self._on_img_progress)
        self._img_worker.finished.connect(self._on_img_finished)
        self._img_worker.start()

    def _on_img_progress(self, current: int, total: int) -> None:
        self._img_progress.setRange(0, total)
        self._img_progress.setValue(current)

    def _stop_img_search(self) -> None:
        if self._img_worker and self._img_worker.isRunning():
            self._img_worker.stop()
            self._img_worker.wait()
        self._img_find_btn.setEnabled(True)
        self._img_stop_btn.setEnabled(False)
        self._img_progress.setVisible(False)
        self._img_status.setText(self.tr("Search stopped."))

    def _on_img_finished(self, results: list) -> None:
        self._img_results = results
        self._img_find_btn.setEnabled(True)
        self._img_stop_btn.setEnabled(False)
        self._img_progress.setVisible(False)

        self._img_table.setSortingEnabled(False)
        self._img_table.setRowCount(0)
        c = Qt.AlignmentFlag.AlignCenter
        for row_idx, r in enumerate(results):
            self._img_table.insertRow(row_idx)
            self._img_table.setItem(row_idx, _I_ID_A,    _item(str(r["id_a"]),  r["id_a"]))
            self._img_table.setItem(row_idx, _I_TITLE_A, _item(r["title_a"]))
            self._img_table.setItem(row_idx, _I_PATH_A,  _item(r["path_a"]))
            self._img_table.setItem(row_idx, _I_SIZE_A,  _size_item(r["path_a"]))
            self._img_table.setItem(row_idx, _I_IMGS_A,  _item(str(r["imgs_a"]), align=c))
            self._img_table.setItem(row_idx, _I_ID_B,    _item(str(r["id_b"]),  r["id_b"]))
            self._img_table.setItem(row_idx, _I_TITLE_B, _item(r["title_b"]))
            self._img_table.setItem(row_idx, _I_PATH_B,  _item(r["path_b"]))
            self._img_table.setItem(row_idx, _I_SIZE_B,  _size_item(r["path_b"]))
            self._img_table.setItem(row_idx, _I_IMGS_B,  _item(str(r["imgs_b"]), align=c))
            self._img_table.setItem(row_idx, _I_PAIRS,   _item(str(r["pairs"]),  align=c))
            self._img_table.setItem(row_idx, _I_DIST,    _item(str(r["best"]),   align=c))
        self._img_table.setSortingEnabled(True)

        n = len(results)
        self._img_status.setText(
            f"{n} archive pair(s) with similar images found." if n else
            "No similar image pairs found. "
            "Ensure thumbnails exist (crawl with thumbnails enabled)."
        )
        all_ids = {r["id_a"] for r in results} | {r["id_b"] for r in results}
        self._img_model_thumb = self._load_model_thumbs(all_ids)
        self._refresh_bottom_buttons()

    def _on_img_selection(self) -> None:
        if self._tabs.currentIndex() == _TAB_IMGS:
            self._refresh_bottom_buttons()

    def _img_context_menu(self, pos: QPoint) -> None:
        self._show_compare_menu(self._img_table, _I_ID_A, _I_ID_B, _I_PATH_A, _I_PATH_B, pos)

    def _delete_archive_from_disk(
        self, model_id: int, table: QTableWidget, row: int
    ) -> None:
        from app.database.db import session_scope
        from app.services.file_deletion import delete_archives
        from app.ui.config_dialog import get_trash_folder_path, get_use_trash_folder

        use_trash  = get_use_trash_folder()
        trash_path = get_trash_folder_path()
        verb       = "Move to trash" if use_trash else "Permanently delete"

        reply = QMessageBox.warning(
            self,
            f"{verb} archive",
            f"Are you sure you want to <b>{verb.lower()}</b> this archive file "
            f"from disk?<br><br>This will also remove it from the database.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            report = delete_archives(session, [model_id], use_trash, trash_path)

        if report.failed:
            QMessageBox.warning(
                self,
                self.tr("Delete Archive"),
                f"Failed to delete the archive:\n{report.failed[0][1]}",
            )
        else:
            table.removeRow(row)
            self._refresh_bottom_buttons()

    # ── Thumbnail helpers ─────────────────────────────────────────────────────

    def _get_model_thumb(self, model_id: int) -> Optional[bytes]:
        return self._img_model_thumb.get(model_id)

    def _load_model_thumbs(self, model_ids: set[int]) -> dict[int, Optional[bytes]]:
        from app.database.db import session_scope
        from app.database.models import Image
        from app.utils.thumbnailer import get_thumbnail_bytes

        result: dict[int, Optional[bytes]] = {}
        if not model_ids:
            return result
        try:
            with session_scope() as session:
                rows = (
                    session.query(Image.model_id, Image.id)
                    .filter(
                        Image.model_id.in_(list(model_ids)),
                        Image.thumbnail_path.isnot(None),
                    )
                    .all()
                )
            first_img: dict[int, int] = {}
            for model_id, img_id in rows:
                if model_id not in first_img:
                    first_img[model_id] = img_id
            for model_id, img_id in first_img.items():
                result[model_id] = get_thumbnail_bytes(img_id)
        except Exception as exc:
            logger.warning(self.tr("Failed to load model thumbnails for popup: %s"), exc)
        return result

    # ── Shared bottom buttons ─────────────────────────────────────────────────

    def _on_tab_changed(self, _index: int) -> None:
        if _index != _TAB_IMGS:
            self._img_popup.hide()
        self._refresh_bottom_buttons()

    def _refresh_bottom_buttons(self) -> None:
        tab = self._tabs.currentIndex()
        tbl = self._stl_table if tab == _TAB_STL else self._img_table
        rows = {i.row() for i in tbl.selectedIndexes()}
        self._show_pair_btn.setEnabled(len(rows) == 1)
        self._show_all_btn.setEnabled(tbl.rowCount() > 0)

    def _show_pair(self) -> None:
        tab = self._tabs.currentIndex()
        if tab == _TAB_STL:
            tbl, col_a, col_b = self._stl_table, _S_ID_A, _S_ID_B
        else:
            tbl, col_a, col_b = self._img_table, _I_ID_A, _I_ID_B
        rows = {i.row() for i in tbl.selectedIndexes()}
        if len(rows) != 1:
            return
        row = next(iter(rows))
        id_a = tbl.item(row, col_a).data(Qt.ItemDataRole.UserRole)
        id_b = tbl.item(row, col_b).data(Qt.ItemDataRole.UserRole)
        self.show_models_requested.emit([id_a, id_b])

    def _show_all(self) -> None:
        tab = self._tabs.currentIndex()
        if tab == _TAB_STL:
            pairs = [[r["id_a"], r["id_b"]] for r in self._stl_results]
        else:
            pairs = [[r["id_a"], r["id_b"]] for r in self._img_results]
        self.show_duplicate_pairs_requested.emit(pairs)
        self.close()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._img_popup.hide()
        for w in (self._stl_worker, self._img_worker):
            if w and w.isRunning():
                w.stop()
                w.wait()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level table factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_table(
    headers: list[str],
    hidden: list[int],
    stretch: list[int],
) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
    t.setSortingEnabled(False)
    t.verticalHeader().setVisible(False)
    hdr = t.horizontalHeader()
    for col in range(len(headers)):
        mode = (QHeaderView.ResizeMode.Stretch if col in stretch
                else QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(col, mode)
    for col in hidden:
        t.setColumnHidden(col, True)
    return t
