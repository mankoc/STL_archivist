"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, category, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
from pathlib import Path as FSPath, Path

_ICONS_DIR = FSPath(__file__).parent.parent.parent / "resources" / "icons"

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSize, QStringListModel, QEvent, pyqtSignal, QItemSelection, QTimer, QThread, QThreadPool, QRunnable, QRect, QPointF, QRectF, QUrl, QObject
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QPixmap, QPixmapCache, QIcon, QKeySequence, QShortcut, QColor, QFont, QPainter, QPen, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QStyledItemDelegate, QComboBox, QCompleter,
    QDialog, QSizePolicy, QApplication, QPushButton,
    QProgressBar, QProgressDialog, QStyle, QStyleOptionViewItem,
    QTextEdit, QDialogButtonBox,
)
from PyQt6.QtCore import QSettings

from sqlalchemy import func, or_, and_, select as sa_select
from sqlalchemy.orm import selectinload

from app.database.db import get_session, session_scope, get_models_view
from app.ui import lookup_cache as _lookup_cache
from app.ui.thumb_loader import ThumbnailLoaderWorker
from app.database.models import STLModel, Tag, Sculptor, Collection, Subcollection, Category, Release, Scale, Image, model_tags as _mt_global
from app.utils.thumbnailer import pixmap_from_path, save_thumbnail
from app.utils.path_pattern import parse_release_date, format_release_date

_IMAGE_DROP_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'})

# Column indices
COL_THUMB         = 0
COL_TITLE         = 1
COL_CREATOR       = 2
COL_SOURCE        = 3
COL_DATE          = 4
COL_TAGS          = 5
COL_SCULPTOR      = 6
COL_COLLECTION    = 7
COL_SUBCOLLECTION = 8
COL_SEQUENCE      = 9
COL_RELEASE       = 10
COL_PATH          = 11
COL_SCALE         = 12
COL_PRESUPPORTED  = 13
COL_ADDED         = 14
COL_WARNING       = 15
COL_FILESIZE      = 16
COL_CATEGORY      = 17
COL_COMPRESSION   = 18
_HEADERS = ["", "Title", "Creator", "Source", "Date", "Tags",
            "Sculptor", "Collection", "Subcollection", "Sequence", "Release", "Path",
             "Scale", "Presupports", "Added", "Warnings", "File Size", "Category",
             "Compression"]



def _fmt_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _prune_ancestor_paths(paths: list[str]) -> list[str]:
    """Return a minimal covering subset: drop paths whose ancestor is already present.

    Prevents SQLite "Expression tree is too large" when a high-level folder is
    checked in the tree and hundreds of descendant paths are also collected.
    """
    normalized = sorted(p.replace("\\", "/").rstrip("/") for p in paths)
    result: list[str] = []
    for p in normalized:
        if not any(p.startswith(kept + "/") for kept in result):
            result.append(p)
    return result


class _BrowserProxyModel(QSortFilterProxyModel):
    """Proxy model that sorts the File Size column numerically via UserRole data."""

    _NUMERIC_COLS: "frozenset[int]"  # populated after COL_* constants are defined

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sort_cache: dict[tuple[int, int], object] = {}

    def setSourceModel(self, model) -> None:
        super().setSourceModel(model)
        self._sort_cache.clear()
        if model is not None:
            model.modelReset.connect(self._sort_cache.clear)

    def _sort_key(self, index) -> object:
        col = index.column()
        row = index.row()
        key = (col, row)
        if key not in self._sort_cache:
            v = index.data(Qt.ItemDataRole.UserRole)
            if col == COL_SEQUENCE:
                try:
                    v = int(v) if v is not None else -1
                except Exception:
                    v = -1
            else:
                v = v if v is not None else -1
            self._sort_cache[key] = v
        return self._sort_cache[key]

    def lessThan(self, left, right):
        col = left.column()
        if col in (COL_FILESIZE, COL_COMPRESSION, COL_SEQUENCE):
            return self._sort_key(left) < self._sort_key(right)
        return super().lessThan(left, right)

MODEL_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class ClickableLabel(QLabel):
    """QLabel that emits a *clicked* signal on left mouse button press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageViewerDialog(QDialog):
    """Full-screen image viewer — click anywhere to close."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image")
        self.setModal(True)
        self._pixmap = pixmap

        screen = self.screen().availableGeometry()
        max_w = int(screen.width() * 0.9)
        max_h = int(screen.height() * 0.9)

        scaled = pixmap.scaled(max_w, max_h,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

        label = QLabel()
        label.setPixmap(scaled)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.mousePressEvent = lambda _: self.accept()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        self.resize(scaled.width(), scaled.height())


class RescanWorker(QThread):
    """Background thread that re-extracts metadata for a list of model IDs."""

    progress    = pyqtSignal(int, int, str)  # (current_index, total, filename)
    file_error  = pyqtSignal(str, str)       # (filename, error_message)
    finished_ok = pyqtSignal(int)            # updated_count

    def __init__(self, model_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self._model_ids = model_ids
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        import logging
        from pathlib import Path
        from app.crawler.archive import archive_hash, extract_archive_contents
        from app.crawler.metadata import extract_metadata
        from app.crawler.rules import detect_warnings, detect_presupport, detect_scale
        from app.config import CrawlerConfig
        from app.database.db import session_scope
        from app.database.models import STLModel, STLFile
        from app.database.helpers import get_or_create_scale
        from app.ui.config_dialog import (
            get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive,
            get_store_stl_file_list, get_presupport_patterns,
        )
        from app.utils.path_pattern import parse_release_date

        _log = logging.getLogger(__name__)
        config = CrawlerConfig()
        total = len(self._model_ids)
        updated = 0

        for i, mid in enumerate(self._model_ids, start=1):
            if self._stop:
                break
            with session_scope() as session:
                m = session.get(STLModel, mid)
                if not m or not m.archive_path:
                    self.progress.emit(i, total, "")
                    continue
                p = Path(m.archive_path)
                self.progress.emit(i, total, p.name)
                try:
                    if not p.exists():
                        self.file_error.emit(p.name, "Archive not found")
                        continue

                    try:
                        st = p.stat()
                        fs_size  = int(st.st_size)
                        fs_mtime = int(st.st_mtime_ns)
                    except Exception:
                        fs_size = fs_mtime = None

                    need_hash = not (
                        fs_size is not None and fs_mtime is not None
                        and m.archive_size is not None and m.archive_mtime_ns is not None
                        and fs_size == m.archive_size and fs_mtime == m.archive_mtime_ns
                    )
                    new_hash = archive_hash(p, unrar_path=config.unrar_path) if need_hash else m.archive_hash

                    all_names: list[str] = []
                    uncompressed_out: list[int] = []
                    contents = extract_archive_contents(
                        p,
                        unrar_path=config.unrar_path,
                        all_names_out=all_names,
                        stl_info_out=None,
                        uncompressed_size_out=uncompressed_out,
                    )
                    uncompressed = uncompressed_out[0] if uncompressed_out else None
                    meta = extract_metadata(p, contents=contents, source_dir=p.parent)

                    m.archive_hash    = new_hash
                    m.archive_size    = fs_size
                    m.archive_mtime_ns = fs_mtime
                    if uncompressed and uncompressed > 0 and fs_size is not None:
                        m.compression_rate = round(fs_size / uncompressed, 4)

                    m.title       = meta.title       or m.title
                    m.creator     = meta.creator     or m.creator
                    m.source      = meta.distributor or m.source
                    if meta.release_date:
                        ry, rm = parse_release_date(meta.release_date)
                        if ry is not None:
                            m.release_year  = ry
                            m.release_month = rm
                    m.url         = meta.url         or m.url
                    m.description = meta.description or m.description

                    m.presupports = detect_presupport(all_names)
                    warnings = detect_warnings(
                        all_names,
                        check_presupported=get_warn_presupported_stl(),
                        check_no_images=get_warn_no_images(),
                        check_nested_archive=get_warn_nested_archive(),
                        check_supports=get_warn_presupported_stl(),
                        supports_patterns=get_presupport_patterns(),
                    )
                    m.warning = warnings or None

                    scales = detect_scale([p.name] + all_names)
                    if scales:
                        m.scale = get_or_create_scale(session, scales[0])

                    if get_store_stl_file_list():
                        for old in list(m.stl_files):
                            session.delete(old)
                        session.flush()
                        _stl_exts = {'.stl', '.obj', '.lys', '.chitubox'}
                        for fname in all_names:
                            if Path(fname.replace('\\', '/')).suffix.lower() in _stl_exts:
                                session.add(STLFile(model=m, filename=fname,
                                                    file_size=None, archive_hash=new_hash))

                    updated += 1
                except Exception as exc:
                    _log.exception("Rescan failed for %s", p)
                    self.file_error.emit(p.name, str(exc))

        self.finished_ok.emit(updated)


class RescanProgressDialog(QDialog):
    """Progress window for a running RescanWorker; supports 'Send to Background'."""

    def __init__(self, worker: RescanWorker, total: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rescan Files")
        self.setMinimumWidth(440)
        self.setModal(True)
        self._worker = worker
        self._in_background = False

        layout = QVBoxLayout(self)

        self._label = QLabel(f"Rescanning 0 / {total}…")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, total)
        self._bar.setValue(0)
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._bg_btn     = QPushButton("Send to Background")
        self._cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self._bg_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_worker_done)
        self._bg_btn.clicked.connect(self._send_to_bg)
        self._cancel_btn.clicked.connect(self._cancel)

    def _on_progress(self, current: int, total: int, filename: str) -> None:
        self._bar.setValue(current)
        name = filename or "…"
        self._label.setText(f"Rescanning {current} / {total}: {name}")

    def _on_worker_done(self, _updated: int) -> None:
        if not self._in_background:
            self.accept()

    def _send_to_bg(self) -> None:
        self._in_background = True
        self.accept()

    def _cancel(self) -> None:
        self._worker.stop()
        self.reject()

    @property
    def in_background(self) -> bool:
        return self._in_background


class _ComboDelegate(QStyledItemDelegate):
    """Base delegate that shows an editable QComboBox populated by a query callable."""

    def _get_items(self) -> list[str]:
        raise NotImplementedError

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self._get_items())
        combo.setCurrentIndex(-1)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class SourceDelegate(_ComboDelegate):
    """QComboBox of distinct source/distributor strings."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("source")


class SculptorDelegate(_ComboDelegate):
    """QComboBox of all sculptor names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("sculptor")


class CollectionDelegate(_ComboDelegate):
    """QComboBox of all Collection names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("collection")


class SubcollectionDelegate(_ComboDelegate):
    """QComboBox of all Subcollection names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("subcollection")


class CategoryDelegate(_ComboDelegate):
    """QComboBox of all Category names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("category")


class ReleaseDelegate(_ComboDelegate):
    """QComboBox of all Release names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("release")


class ScaleDelegate(_ComboDelegate):
    """QComboBox of all Scale names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("scale")


_PRESUPPORTS_OPTIONS = ["", "No", "Lychee", "Chitubox", "Presupported"]


class PresupportsDelegate(QStyledItemDelegate):
    """Fixed-option combo for the presupports column."""

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItems(_PRESUPPORTS_OPTIONS)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText())


class TagDelegate(QStyledItemDelegate):
    """QLineEdit that autocompletes each comma-separated token from the tag pool."""

    def createEditor(self, parent, option, index) -> "_TagLineEdit":
        with session_scope() as session:
            all_tags = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
        edit = _TagLineEdit(all_tags, parent)
        return edit

    def setEditorData(self, editor, index) -> None:
        editor.setText(index.data() or "")
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class _TagLineEdit(QLineEdit):
    """QLineEdit with per-token autocomplete for comma-separated tag lists."""

    def __init__(self, all_tags: list[str], parent=None) -> None:
        super().__init__(parent)
        self._completer = QCompleter(self)
        self._completer.setModel(QStringListModel(all_tags, self._completer))
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._on_text_edited)

    def _current_token(self) -> str:
        return self.text()[:self.cursorPosition()].rsplit(",", 1)[-1].lstrip()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str) -> None:
        self._show_completions()

    def _show_completions(self) -> None:
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str) -> None:
        text = self.text()
        cursor = self.cursorPosition()
        before = text[:cursor].rsplit(",", 1)
        prefix = (before[0] + ", ") if len(before) == 2 else ""
        after = text[cursor:]
        new_text = prefix + chosen + (", " if after.lstrip(",").strip() else ", ")
        self.setText(new_text)
        self.setCursorPosition(len(new_text))


class ThumbnailGeneratorWorker(QThread):
    """Background worker that (re-)generates thumbnails for a list of model IDs."""

    progress = pyqtSignal(int, int)   # (current, total)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, model_ids: list[int], max_thumbnails: int, parent=None) -> None:
        super().__init__(parent)
        self._model_ids = model_ids
        self._max_thumbnails = max_thumbnails
        self._stop = False

    def stop(self) -> None:
        """Request the worker to stop as soon as possible."""
        self._stop = True

    def run(self) -> None:
        """Re-extract and save thumbnails for every model in *model_ids*, then update sidecars."""
        from pathlib import Path
        from app.database.db import session_scope
        from app.database.models import STLModel, Image as DBImage
        from app.crawler.archive import extract_archive_contents, IMAGE_SUFFIXES
        from app.utils.thumbnailer import save_thumbnail, delete_thumbnail, get_thumbnail_base_dir
        from app.ui.config_dialog import get_max_thumb_size, get_unrar_path

        max_size  = get_max_thumb_size()
        unrar     = get_unrar_path()
        total     = len(self._model_ids)
        modified_folders: set[Path] = set()

        for idx, model_id in enumerate(self._model_ids, start=1):
            if self._stop:
                self.log.emit("Thumbnail generation cancelled")
                break
            self.progress.emit(idx, total)

            with session_scope() as session:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue

                archive_path = Path(stl.archive_path)
                self.log.emit(f"[{idx}/{total}] {archive_path.name}")

                if not archive_path.exists():
                    self.log.emit(f"  WARNING: archive not found, skipping")
                    continue

                # Delete existing thumbnails
                for old_img in list(stl.images):
                    if old_img.thumbnail_path:
                        delete_thumbnail(old_img.thumbnail_path)
                    session.delete(old_img)
                session.flush()

                # Extract and generate new thumbnails
                from app.crawler.rules import detect_warnings
                from app.ui.config_dialog import get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive, get_presupport_patterns

                all_names: list[str] = []
                contents = extract_archive_contents(
                    archive_path, unrar_path=unrar, all_names_out=all_names,
                )
                image_names = sorted(
                    name for name in contents
                    if Path(name.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
                )

                count = 0
                for filename in image_names:
                    if self._stop:
                        break
                    if count >= self._max_thumbnails:
                        break
                    raw_bytes = contents[filename]
                    img = DBImage(model=stl, filename=filename)
                    session.add(img)
                    session.flush()
                    rel_path = save_thumbnail(raw_bytes, img.id, max_size=max_size)
                    if rel_path:
                        img.thumbnail_path = rel_path
                        count += 1
                    else:
                        session.delete(img)
                        self.log.emit(f"  WARNING: thumbnail conversion failed for {filename}")

                self.log.emit(f"  {count} thumbnail(s) stored")

                warnings = detect_warnings(
                    all_names,
                    check_presupported=get_warn_presupported_stl(),
                    check_no_images=get_warn_no_images(),
                    check_nested_archive=get_warn_nested_archive(),
                    check_supports=get_warn_presupported_stl(),
                    supports_patterns=get_presupport_patterns(),
                )
                stl.warning = warnings or None
                if warnings:
                    self.log.emit(f"  WARNING: {warnings.replace(chr(10), '; ')}")

                modified_folders.add(archive_path.parent)

        # Update METADATA.7z for all affected folders (if option is enabled)
        thumb_base = get_thumbnail_base_dir()
        if modified_folders and thumb_base is not None:
            from app.ui.config_dialog import get_metadata_thumbnails_enabled, get_metadata_sidecar_enabled
            from app.crawler.folder_metadata import export_folder_metadata
            include_thumbs = get_metadata_thumbnails_enabled()
            if get_metadata_sidecar_enabled():
                self.log.emit(f"Updating METADATA.7z in {len(modified_folders)} folder(s)…")
                with session_scope() as session:
                    for folder in sorted(modified_folders):
                        export_folder_metadata(folder, session, thumb_base,
                                               include_thumbnails=True)
                        self.log.emit(f"  Updated METADATA.7z in {folder.name}")

        self.finished.emit()


class FilterItemDelegate(QStyledItemDelegate):
    """Replaces Qt's default tristate checkbox with colored circle indicators:
        Unchecked        — gray outline circle  (not selected)
        Checked          — green filled circle with white +  (include)
        PartiallyChecked — red filled circle with white −  (exclude)
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        is_checkable = bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        check_state  = index.data(Qt.ItemDataRole.CheckStateRole)

        if not is_checkable or check_state is None:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        widget = option.widget
        style  = widget.style() if widget else QApplication.style()

        check_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget
        )

        # Let the style draw the full item (icon + text + selection background).
        # Then overdraw the default checkbox indicator with our custom circle.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        if check_rect.isValid():
            self._draw_indicator(painter, check_rect, Qt.CheckState(check_state))

    @staticmethod
    def _draw_indicator(painter: QPainter, rect: QRect, state: Qt.CheckState) -> None:
        size = (min(rect.width(), rect.height()) - 4) * 1.5
        if size < 4:
            return
        cx   = rect.center().x()
        cy   = rect.center().y()
        half = size / 2.0
        rf   = QRectF(cx - half, cy - half, size, size)
        pw   = max(1.5, size * 0.14)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if state == Qt.CheckState.Unchecked:
            # Neutral gray circle — "parent / not selected"
            from app.ui.config_dialog import get_status_neutral_color
            painter.setPen(QPen(QColor(get_status_neutral_color()), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rf)

        elif state == Qt.CheckState.Checked:
            # Green filled circle with white "+"
            from app.ui.config_dialog import get_status_ok_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(get_status_ok_color()))
            painter.drawEllipse(rf)
            pen = QPen(QColor("white"), pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pad = 0.25
            painter.drawLine(QPointF(cx, cy - half * (1 - pad * 2)),
                             QPointF(cx, cy + half * (1 - pad * 2)))
            painter.drawLine(QPointF(cx - half * (1 - pad * 2), cy),
                             QPointF(cx + half * (1 - pad * 2), cy))

        elif state == Qt.CheckState.PartiallyChecked:
            # Red filled circle with white "−"
            from app.ui.config_dialog import get_status_warn_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(get_status_warn_color()))
            painter.drawEllipse(rf)
            pen = QPen(QColor("white"), pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pad = 0.25
            painter.drawLine(QPointF(cx - half * (1 - pad * 2), cy),
                             QPointF(cx + half * (1 - pad * 2), cy))

        painter.restore()


class FilterTreeWidget(QTreeWidget):
    """QTreeWidget whose checkable items cycle through three states on every click
    (Unchecked → Checked → PartiallyChecked → Unchecked), whether the click lands
    on the checkbox indicator or the item text.

    State semantics:
        Unchecked       — no filter applied for this value
        Checked         — include items that match this value
        PartiallyChecked — exclude items that match this value
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setItemDelegate(FilterItemDelegate(self))
        # Maps id(item) → state before the last checkbox click so _on_filter_item_changed
        # can decide which state comes next in the cycle.
        self._pre_click_states: dict[int, Qt.CheckState] = {}

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        item = self.itemAt(event.pos())
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            super().mousePressEvent(event)
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            super().mousePressEvent(event)
            return

        # Determine whether the click landed on the checkbox widget.
        vis_rect = self.visualItemRect(item)
        indicator_w = self.style().pixelMetric(
            QStyle.PixelMetric.PM_IndicatorWidth, None, self
        )
        is_checkbox_click = event.pos().x() <= vis_rect.left() + indicator_w + 4

        if is_checkbox_click:
            # Store pre-click state; let Qt perform its normal Unchecked↔Checked toggle,
            # then _on_filter_item_changed will complete the 3-step cycle.
            self._pre_click_states[id(item)] = item.checkState(0)
            super().mousePressEvent(event)
        else:
            # Text click on any item → default Qt behaviour (select / expand-collapse).
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        item = self.itemAt(event.pos())
        if item is None:
            super().mouseDoubleClickEvent(event)
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            super().mouseDoubleClickEvent(event)
            return

        # Ignore double-clicks on the checkbox area and on group headers.
        vis_rect = self.visualItemRect(item)
        indicator_w = self.style().pixelMetric(
            QStyle.PixelMetric.PM_IndicatorWidth, None, self
        )
        is_checkbox_click = event.pos().x() <= vis_rect.left() + indicator_w + 4
        is_group = isinstance(data, tuple) and data[0] == "group"

        if is_checkbox_click or is_group:
            super().mouseDoubleClickEvent(event)
            return

        # Double-click on a leaf item's text → solo this filter.
        self._solo_item(item)
        event.accept()

    @staticmethod
    def _next_check_state(state: Qt.CheckState) -> Qt.CheckState:
        if state == Qt.CheckState.Unchecked:
            return Qt.CheckState.Checked
        if state == Qt.CheckState.Checked:
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Unchecked

    def _solo_item(self, target: QTreeWidgetItem) -> None:
        """Check only *target*, clearing every other checkable item in the tree."""
        self.blockSignals(True)
        self._clear_all(self.invisibleRootItem())
        target.setCheckState(0, Qt.CheckState.Checked)
        self.blockSignals(False)
        self.itemChanged.emit(target, 0)

    def _clear_all(self, root: QTreeWidgetItem) -> None:
        d = root.data(0, Qt.ItemDataRole.UserRole)
        if d is not None and (root.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            root.setCheckState(0, Qt.CheckState.Unchecked)
        for i in range(root.childCount()):
            self._clear_all(root.child(i))


class _FilterTreeWorker(QRunnable):
    """Runs all DB queries for the filter tree off the GUI thread.

    Emits ``signals.ready(gen, payload)`` when done; the GUI thread slot
    checks *gen* against the current generation counter and discards stale
    results.  The *payload* dict contains only Python primitives (no ORM
    objects or Qt types) so it is safe to pass across the thread boundary.
    """

    class _Signals(QObject):
        ready = pyqtSignal(int, object)  # (gen, payload dict)

    def __init__(self, gen: int, scope_by_section, show_flags: dict,
                 show_counts: bool, alpha_enabled: bool, alpha_threshold: int,
                 checked_data: set, excluded_data: set,
                 expanded_sections: "set | None") -> None:
        super().__init__()
        self.signals = self._Signals()
        self._gen = gen
        self._scope = scope_by_section
        self._show = show_flags
        self._show_counts = show_counts
        self._alpha_enabled = alpha_enabled
        self._alpha_threshold = alpha_threshold
        self._checked = checked_data
        self._excluded = excluded_data
        self._expanded = expanded_sections

    def run(self) -> None:  # noqa: C901 (complexity is inherent; each section is independent)
        from app.database.db import get_session
        from app.database.models import (
            STLModel, Tag, Sculptor, Collection, Subcollection,
            Category, Release, Scale, model_tags as _mt,
        )
        from sqlalchemy import func as _func
        from pathlib import Path as _P

        scope = self._scope
        show = self._show

        # ── Serve from cache for unscoped (no active filters) requests ─────────
        if scope is None:
            from app.database.filter_cache import load_filter_cache
            cached = load_filter_cache()
            if cached is not None:
                payload = {
                    **cached,
                    "checked_data":     self._checked,
                    "excluded_data":    self._excluded,
                    "expanded_sections": self._expanded,
                    "show":             self._show,
                    "show_counts":      self._show_counts,
                    "alpha_enabled":    self._alpha_enabled,
                    "alpha_threshold":  self._alpha_threshold,
                    "scope_by_section": self._scope,
                }
                self.signals.ready.emit(self._gen, payload)
                return

        def _s(section):
            return scope.get(section) if scope else None

        session = get_session()
        try:
            # ── Counts ─────────────────────────────────────────────────────────
            _counts: dict = {}
            _path_counts: "dict[str, int] | None" = None
            _scoped_counts: dict = {}
            _sc_path_counts: "dict[str, int] | None" = None

            if self._show_counts:
                _grp: dict = {}

                def _count_col(col, kind: str, *, none_filter: bool = True) -> None:
                    q = session.query(col, _func.count(STLModel.id))
                    if none_filter:
                        q = q.filter(col.isnot(None))
                    total = 0
                    for v, c in q.group_by(col).all():
                        _counts[(kind, v)] = c
                        total += c
                    _grp[kind] = total

                _count_col(STLModel.creator,          "creator")
                _count_col(STLModel.source,           "source")
                _count_col(STLModel.sculptor_id,      "sculptor_id")
                _count_col(STLModel.collection_id,    "collection_id")
                _count_col(STLModel.subcollection_id, "subcollection_id")
                _count_col(STLModel.category_id,      "category_id")
                _count_col(STLModel.release_id,       "release_id")
                _count_col(STLModel.scale_id,         "scale_id")
                _count_col(STLModel.presupports,      "presupports")

                _year_cnt: dict = {}
                _date_total = 0
                for yr, mo, c in (
                    session.query(STLModel.release_year, STLModel.release_month, _func.count(STLModel.id))
                    .filter(STLModel.release_year.isnot(None))
                    .group_by(STLModel.release_year, STLModel.release_month)
                    .all()
                ):
                    _counts[("date_month", (yr, mo))] = c
                    _year_cnt[yr] = _year_cnt.get(yr, 0) + c
                    _date_total += c
                for yr, c in _year_cnt.items():
                    _counts[("date_year", yr)] = c
                _grp["date"] = _date_total

                _all_warnings = [
                    w for (w,) in session.query(STLModel.warning)
                    .filter(STLModel.warning.isnot(None)).all()
                ]
                for _wt in ("Models with presupported stl", "Archive without images",
                            "Nested archives", "Archive with supports",
                            "Archive without printable files"):
                    _counts[("warning", _wt)] = sum(1 for w in _all_warnings if _wt in w)
                _grp["warning"] = sum(1 for w in _all_warnings if w)

                # Use pre-computed archive_folder when available (SQL GROUP BY),
                # falling back to Python path parsing for older rows where it is NULL.
                _direct: dict = {}
                for (folder, cnt) in (
                    session.query(STLModel.archive_folder, _func.count(STLModel.id))
                    .filter(STLModel.archive_folder.isnot(None))
                    .group_by(STLModel.archive_folder)
                    .all()
                ):
                    _direct[folder] = _direct.get(folder, 0) + cnt
                for (ap,) in session.query(STLModel.archive_path).filter(
                    STLModel.archive_folder.is_(None),
                    STLModel.archive_path.isnot(None),
                ).all():
                    folder = _P(ap).parent.as_posix()
                    _direct[folder] = _direct.get(folder, 0) + 1

                for k, v in _grp.items():
                    _counts[("group", k)] = v

                for tn, c in (
                    session.query(Tag.name, _func.count(_mt.c.model_id))
                    .join(_mt, Tag.id == _mt.c.tag_id)
                    .group_by(Tag.name)
                    .all()
                ):
                    _counts[("tag", tn)] = c
                _counts[("group", "tag")] = (
                    session.query(_func.count(_mt.c.model_id.distinct())).scalar() or 0
                )

                _path_counts = {}
                for folder, _c in _direct.items():
                    _path_counts[folder] = _path_counts.get(folder, 0) + _c
                    _f = _P(folder)
                    while True:
                        _p = _f.parent
                        if _p == _f:
                            break
                        _ps = _p.as_posix()
                        _path_counts[_ps] = _path_counts.get(_ps, 0) + _c
                        _f = _p
                _counts[("group", "path")] = sum(_direct.values())

                # Scoped counts
                if scope is not None:
                    from app.database.models import model_tags as _mt2

                    def _sc_count_col(col, kind: str, sc_ids, *, none_filter=True) -> None:
                        rows = (
                            session.query(col, _func.count(STLModel.id))
                            .filter(STLModel.id.in_(sc_ids))
                        )
                        if none_filter:
                            rows = rows.filter(col.isnot(None))
                        rows = rows.group_by(col).all()
                        for v, c in rows:
                            _scoped_counts[(kind, v)] = c
                        _scoped_counts[("group", kind.rstrip("_id"))] = sum(c for _, c in rows)

                    for section, col, kind in (
                        ("creator",       STLModel.creator,          "creator"),
                        ("source",        STLModel.source,           "source"),
                        ("sculptor",      STLModel.sculptor_id,      "sculptor_id"),
                        ("collection",    STLModel.collection_id,    "collection_id"),
                        ("subcollection", STLModel.subcollection_id, "subcollection_id"),
                        ("category",      STLModel.category_id,      "category_id"),
                        ("release",       STLModel.release_id,       "release_id"),
                        ("scale",         STLModel.scale_id,         "scale_id"),
                        ("presupports",   STLModel.presupports,      "presupports"),
                    ):
                        sc_ids = _s(section)
                        if sc_ids is not None:
                            _sc_count_col(col, kind, sc_ids)

                    _tag_sc = _s("tag")
                    if _tag_sc is not None:
                        for tn, c in (
                            session.query(Tag.name, _func.count(_mt2.c.model_id))
                            .join(_mt2, Tag.id == _mt2.c.tag_id)
                            .filter(_mt2.c.model_id.in_(_tag_sc))
                            .group_by(Tag.name)
                            .all()
                        ):
                            _scoped_counts[("tag", tn)] = c
                        _scoped_counts[("group", "tag")] = (
                            session.query(_func.count(_mt2.c.model_id.distinct()))
                            .filter(_mt2.c.model_id.in_(_tag_sc))
                            .scalar() or 0
                        )

                    _date_sc = _s("date")
                    if _date_sc is not None:
                        _sc_year: dict = {}
                        _drows = (
                            session.query(STLModel.release_year, STLModel.release_month, _func.count(STLModel.id))
                            .filter(STLModel.id.in_(_date_sc))
                            .filter(STLModel.release_year.isnot(None))
                            .group_by(STLModel.release_year, STLModel.release_month)
                            .all()
                        )
                        for yr, mo, c in _drows:
                            _scoped_counts[("date_month", (yr, mo))] = c
                            _sc_year[yr] = _sc_year.get(yr, 0) + c
                        for yr, c in _sc_year.items():
                            _scoped_counts[("date_year", yr)] = c
                        _scoped_counts[("group", "date")] = sum(c for _, _, c in _drows)

                    _warn_sc = _s("warning")
                    if _warn_sc is not None:
                        _sc_warns = [
                            w for (w,) in session.query(STLModel.warning)
                            .filter(STLModel.id.in_(_warn_sc))
                            .filter(STLModel.warning.isnot(None))
                            .all()
                        ]
                        for _wt in ("Models with presupported stl", "Archive without images",
                                    "Nested archives", "Archive with supports",
                                    "Archive without printable files"):
                            _scoped_counts[("warning", _wt)] = sum(1 for w in _sc_warns if _wt in w)
                        _scoped_counts[("group", "warning")] = sum(1 for w in _sc_warns if w)

                    _path_sc = _s("path")
                    if _path_sc is not None:
                        _sc_direct: dict = {}
                        for (folder, cnt) in (
                            session.query(STLModel.archive_folder, _func.count(STLModel.id))
                            .filter(STLModel.id.in_(_path_sc))
                            .filter(STLModel.archive_folder.isnot(None))
                            .group_by(STLModel.archive_folder)
                            .all()
                        ):
                            _sc_direct[folder] = _sc_direct.get(folder, 0) + cnt
                        for (ap,) in (
                            session.query(STLModel.archive_path)
                            .filter(STLModel.id.in_(_path_sc))
                            .filter(STLModel.archive_folder.is_(None))
                            .filter(STLModel.archive_path.isnot(None))
                            .all()
                        ):
                            folder = _P(ap).parent.as_posix()
                            _sc_direct[folder] = _sc_direct.get(folder, 0) + 1
                        _sc_path_counts = {}
                        for folder, _c in _sc_direct.items():
                            _sc_path_counts[folder] = _sc_path_counts.get(folder, 0) + _c
                            _f = _P(folder)
                            while True:
                                _p = _f.parent
                                if _p == _f:
                                    break
                                _ps = _p.as_posix()
                                _sc_path_counts[_ps] = _sc_path_counts.get(_ps, 0) + _c
                                _f = _p
                        _scoped_counts[("group", "path")] = sum(_sc_direct.values())

            # ── Distinct value lists for each visible section ───────────────────
            def _scoped_ids(section: str):
                return _s(section)

            creators: list[str] = []
            if show.get("author"):
                _cs = _scoped_ids("creator")
                q = session.query(STLModel.creator).distinct().order_by(STLModel.creator)
                if _cs is not None:
                    q = q.filter(STLModel.id.in_(_cs))
                creators = [c for (c,) in q.all() if c]

            tags: list[str] = []
            if show.get("tags"):
                _ts = _scoped_ids("tag")
                q = session.query(Tag).order_by(Tag.name)
                if _ts is not None:
                    q = (
                        q.join(_mt, Tag.id == _mt.c.tag_id)
                        .filter(_mt.c.model_id.in_(_ts))
                        .distinct()
                    )
                tags = [t.name for t in q.all()]

            sources: list[str] = []
            if show.get("source"):
                _ss = _scoped_ids("source")
                q = session.query(STLModel.source).distinct().order_by(STLModel.source)
                if _ss is not None:
                    q = q.filter(STLModel.id.in_(_ss))
                sources = [s for (s,) in q.all() if s]

            dates: list = []
            if show.get("date"):
                _ds = _scoped_ids("date")
                q = (
                    session.query(STLModel.release_year, STLModel.release_month)
                    .filter(STLModel.release_year.isnot(None))
                    .distinct()
                    .order_by(STLModel.release_year.desc(), STLModel.release_month)
                )
                if _ds is not None:
                    q = q.filter(STLModel.id.in_(_ds))
                dates = list(q.all())

            sculptors: list = []
            if show.get("sculptor"):
                _scs = _scoped_ids("sculptor")
                q = session.query(Sculptor).order_by(Sculptor.name)
                if _scs is not None:
                    _sc_ids = {
                        sid for (sid,) in
                        session.query(STLModel.sculptor_id)
                        .filter(STLModel.id.in_(_scs))
                        .filter(STLModel.sculptor_id.isnot(None))
                        .distinct()
                    }
                    q = q.filter(Sculptor.id.in_(_sc_ids))
                sculptors = [(sc.name, sc.id) for sc in q.all()]

            collections: list = []
            if show.get("collection"):
                _cos = _scoped_ids("collection")
                q = session.query(Collection).order_by(Collection.name)
                if _cos is not None:
                    _col_ids = {
                        cid for (cid,) in
                        session.query(STLModel.collection_id)
                        .filter(STLModel.id.in_(_cos))
                        .filter(STLModel.collection_id.isnot(None))
                        .distinct()
                    }
                    q = q.filter(Collection.id.in_(_col_ids))
                collections = [(col.name, col.id) for col in q.all()]

            sub_by_col: list = []  # list[(col_name, col_id, [(sub_name, sub_id)])]
            if show.get("subcollection"):
                _subs = _scoped_ids("subcollection")
                _scoped_sub_ids = None
                if _subs is not None:
                    _scoped_sub_ids = {
                        sid for (sid,) in
                        session.query(STLModel.subcollection_id)
                        .filter(STLModel.id.in_(_subs))
                        .filter(STLModel.subcollection_id.isnot(None))
                        .distinct()
                    }
                _col_map: dict = {}
                for col_name, col_id, sub_name, sub_id in (
                    session.query(Collection.name, Collection.id, Subcollection.name, Subcollection.id)
                    .join(Subcollection, Subcollection.collection_id == Collection.id)
                    .order_by(Collection.name, Subcollection.name)
                    .all()
                ):
                    if _scoped_sub_ids is not None and sub_id not in _scoped_sub_ids:
                        continue
                    if col_id not in _col_map:
                        _col_map[col_id] = (col_name, [])
                    _col_map[col_id][1].append((sub_name, sub_id))
                sub_by_col = [(name, col_id, subs) for col_id, (name, subs) in _col_map.items()]

            categories: list = []
            if show.get("category"):
                _cas = _scoped_ids("category")
                q = session.query(Category).order_by(Category.name)
                if _cas is not None:
                    _cat_ids = {
                        cid for (cid,) in
                        session.query(STLModel.category_id)
                        .filter(STLModel.id.in_(_cas))
                        .filter(STLModel.category_id.isnot(None))
                        .distinct()
                    }
                    q = q.filter(Category.id.in_(_cat_ids))
                categories = [(cat.name, cat.id) for cat in q.all()]

            releases: list = []
            if show.get("release"):
                _rs = _scoped_ids("release")
                q = session.query(Release).order_by(Release.name)
                if _rs is not None:
                    _rel_ids = {
                        rid for (rid,) in
                        session.query(STLModel.release_id)
                        .filter(STLModel.id.in_(_rs))
                        .filter(STLModel.release_id.isnot(None))
                        .distinct()
                    }
                    q = q.filter(Release.id.in_(_rel_ids))
                releases = [(r.name, r.id) for r in q.all()]

            scales: list = []
            if show.get("scale"):
                _sls = _scoped_ids("scale")
                q = session.query(Scale).order_by(Scale.name)
                if _sls is not None:
                    _scale_ids = {
                        sid for (sid,) in
                        session.query(STLModel.scale_id)
                        .filter(STLModel.id.in_(_sls))
                        .filter(STLModel.scale_id.isnot(None))
                        .distinct()
                    }
                    q = q.filter(Scale.id.in_(_scale_ids))
                scales = [(sc.name, sc.id) for sc in q.all()]

            presupports: list[str] = []
            if show.get("presupports"):
                _ps2 = _scoped_ids("presupports")
                q = (
                    session.query(STLModel.presupports)
                    .filter(STLModel.presupports.isnot(None))
                    .distinct()
                    .order_by(STLModel.presupports)
                )
                if _ps2 is not None:
                    q = q.filter(STLModel.id.in_(_ps2))
                presupports = [v for (v,) in q.all() if v]

            # ── Path dirs ──────────────────────────────────────────────────────
            sorted_dirs: list[str] = []
            scoped_dirs: "list[str] | None" = None
            new_dirs_frozen: "frozenset | None" = None
            if show.get("path"):
                _path_sc2 = _scoped_ids("path")
                if _path_sc2 is not None:
                    _sd: set[str] = set()
                    for (folder,) in (
                        session.query(STLModel.archive_folder)
                        .filter(STLModel.id.in_(_path_sc2))
                        .filter(STLModel.archive_folder.isnot(None))
                        .distinct()
                        .all()
                    ):
                        _sd.add(folder)
                    # fallback for rows without archive_folder
                    for (ap,) in (
                        session.query(STLModel.archive_path)
                        .filter(STLModel.id.in_(_path_sc2))
                        .filter(STLModel.archive_folder.is_(None))
                        .filter(STLModel.archive_path.isnot(None))
                        .distinct()
                        .all()
                    ):
                        _sd.add(_P(ap).parent.as_posix())
                    scoped_dirs = sorted(_sd)
                else:
                    _all_dirs: set[str] = set()
                    for (folder,) in (
                        session.query(STLModel.archive_folder)
                        .filter(STLModel.archive_folder.isnot(None))
                        .distinct()
                        .all()
                    ):
                        _all_dirs.add(folder)
                    # fallback for rows without archive_folder
                    for (ap,) in (
                        session.query(STLModel.archive_path)
                        .filter(STLModel.archive_folder.is_(None))
                        .distinct()
                        .all()
                    ):
                        if ap:
                            _all_dirs.add(_P(ap).parent.as_posix())
                    new_dirs_frozen = frozenset(_all_dirs)
                    sorted_dirs = sorted(_all_dirs)

        finally:
            session.close()

        payload = {
            "counts": _counts,
            "path_counts": _path_counts,
            "scoped_counts": _scoped_counts,
            "sc_path_counts": _sc_path_counts,
            "creators": creators,
            "tags": tags,
            "sources": sources,
            "dates": dates,
            "sculptors": sculptors,
            "collections": collections,
            "sub_by_col": sub_by_col,
            "categories": categories,
            "releases": releases,
            "scales": scales,
            "presupports": presupports,
            "sorted_dirs": sorted_dirs,
            "scoped_dirs": scoped_dirs,
            "new_dirs_frozen": new_dirs_frozen,
            "checked_data": self._checked,
            "excluded_data": self._excluded,
            "expanded_sections": self._expanded,
            "show": self._show,
            "show_counts": self._show_counts,
            "alpha_enabled": self._alpha_enabled,
            "alpha_threshold": self._alpha_threshold,
            "scope_by_section": self._scope,
        }

        # Persist unscoped results so subsequent loads skip the DB queries.
        if scope is None:
            from app.database.filter_cache import save_filter_cache
            save_filter_cache(payload)

        self.signals.ready.emit(self._gen, payload)


class TableLoaderWorker(QThread):
    """Fetches table rows in a background thread and emits them in batches.

    Receives a pre-built SQLAlchemy ``select()`` statement (constructed on the
    main thread; SA expressions are thread-safe to pass).  Executes it with its
    own session, merges pre-fetched tag strings, and emits batches of plain
    Python tuples so the main thread can create ``QStandardItem`` rows without
    blocking.
    """

    rows_ready = pyqtSignal(list)  # list[tuple] — one tuple per row
    finished   = pyqtSignal(int)   # total rows emitted

    _BATCH = 200

    def __init__(
        self,
        main_stmt,
        tags_stmt,
        pair_order:   dict | None = None,  # {model_id: sort_position} for dup pairs
        pair_for_id:  dict | None = None,  # {model_id: pair_index}
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._main_stmt  = main_stmt
        self._tags_stmt  = tags_stmt
        self._pair_order = pair_order
        self._pair_for_id = pair_for_id
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from app.database.db import get_session
        session = get_session()
        try:
            # Tags: single GROUP_CONCAT query → {model_id: "tag1, tag2"}
            tags_for: dict[int, str] = {}
            for mid, ts in session.execute(self._tags_stmt).all():
                tags_for[mid] = ts or ""

            pair_for_id = self._pair_for_id or {}

            if self._pair_order is not None:
                # Duplicate-pairs mode: collect all, sort by pair position, then batch
                all_rows = session.execute(self._main_stmt).all()
                all_rows.sort(key=lambda r: self._pair_order.get(r.id, len(self._pair_order)))
                batched = []
                for row in all_rows:
                    if self._stop:
                        break
                    batched.append(self._pack(row, tags_for, pair_for_id))
                    if len(batched) >= self._BATCH:
                        self.rows_ready.emit(batched)
                        batched = []
                if batched and not self._stop:
                    self.rows_ready.emit(batched)
                if not self._stop:
                    self.finished.emit(len(all_rows))
            else:
                # Normal mode: stream with yield_per for progressive display
                result = session.execute(
                    self._main_stmt.execution_options(yield_per=self._BATCH)
                )
                batch: list = []
                total = 0
                for row in result:
                    if self._stop:
                        break
                    batch.append(self._pack(row, tags_for, pair_for_id))
                    total += 1
                    if len(batch) >= self._BATCH:
                        self.rows_ready.emit(batch)
                        batch = []
                if batch and not self._stop:
                    self.rows_ready.emit(batch)
                if not self._stop:
                    self.finished.emit(total)
        finally:
            session.close()

    @staticmethod
    def _pack(row, tags_for: dict, pair_for_id: dict) -> tuple:
        return (
            row.id,
            row.title,
            row.creator,
            row.source,
            row.release_year,
            row.release_month,
            row.archive_path,
            row.presupports,
            row.warning,
            row.archive_size,
            row.compression_rate,
            row.sequence,
            row.created_at,
            row.sculptor_name,
            row.collection_name,
            row.subcollection_name,
            row.category_name,
            row.release_name,
            row.scale_name,
            tags_for.get(row.id, ""),
            pair_for_id.get(row.id, -1),
        )


class BrowserTab(QWidget):
    """Main model-browser widget.

    Composed of three panels in a horizontal splitter:
    - Left: collapsible filter tree (Author / Tags / Type / Date / Path).
    - Centre: search bar + sortable, editable QTableView of all models.
    - Right: thumbnail sidebar populated on single-click.

    Signals:
        model_count_changed(int): Emitted after each table reload with the new row count.
    """

    model_count_changed = pyqtSignal(int)
    loading_started = pyqtSignal()
    # Emitted when a background thumbnail generation reports progress
    thumbnail_progress_updated = pyqtSignal(int, int)
    thumbnail_finished = pyqtSignal()
    thumbnail_started = pyqtSignal()
    # Emitted while a rescan runs in background (current, total)
    rescan_progress = pyqtSignal(int, int)
    rescan_finished = pyqtSignal()
    crawl_path_requested = pyqtSignal(str)

    # Internal: requests the background loader to fetch bytes for a page of images.
    # Connected to ThumbnailLoaderWorker.load via QueuedConnection.
    _thumb_load_requested = pyqtSignal(int, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._table_loader: TableLoaderWorker | None = None
        self._load_gen: int = 0          # incremented on each _load_table call
        self._load_pair_colors: list | None = None  # QColor list for dup-pair mode
        self._rescan_dialog: RescanProgressDialog | None = None
        self._current_model_id: int | None = None
        self._info_pane_enabled: bool = False
        self._thumb_pixmaps: list[QPixmap] = []      # unscaled pixmaps for current page (by slot)
        self._thumb_all_images: list = []             # list of (image_id, thumbnail_path) tuples
        self._thumb_id_to_slot: dict[int, int] = {}  # image_id → slot index in current page
        self._thumb_page: int = 0
        self._thumb_show_all: bool = False

        # --- Background thumbnail loader ---
        QPixmapCache.setCacheLimit(64 * 1024)  # 64 MB Qt pixmap cache
        self._thumb_worker = ThumbnailLoaderWorker()
        self._thumb_thread = QThread(self)
        self._thumb_worker.moveToThread(self._thumb_thread)
        self._thumb_thread.finished.connect(self._thumb_worker.deleteLater)
        self._thumb_load_requested.connect(
            self._thumb_worker.load, Qt.ConnectionType.QueuedConnection
        )
        self._thumb_worker.bytes_ready.connect(self._on_thumb_bytes_ready)
        self._thumb_thread.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_thumb_loader)
        self._cached_path_dirs: frozenset[str] = frozenset()
        self._cached_sorted_dirs: list[str] = []
        # Cache for the info panel: model_id → dict with url, description, creator fields
        self._panel_cache: dict[int, dict] = {}
        # Generation counter for async filter-tree rebuilds (discard stale results)
        self._filter_gen: int = 0
        # Debounce rapid cell edits: keyed by (row, col) → QStandardItem
        self._pending_edits: dict[tuple[int, int], "QStandardItem"] = {}
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(400)  # ms
        self._edit_timer.timeout.connect(self._flush_pending_edits)
        # Debounce search-box keystrokes to avoid scanning on every character
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)  # ms
        self._search_timer.timeout.connect(
            lambda: self._on_search(self._search_edit.text())
        )
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ---- Left: filter tree ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        filter_header = QHBoxLayout()
        filter_header.addWidget(QLabel("Filter"))
        filter_header.addStretch()
        self._apply_filters_btn = QPushButton("Apply")
        self._apply_filters_btn.setToolTip("Apply the selected filters to the table")
        self._apply_filters_btn.clicked.connect(self._apply_checked_filters)
        filter_header.addWidget(self._apply_filters_btn)
        self._clear_filters_btn = QPushButton("Clear")
        self._clear_filters_btn.setFlat(True)
        self._clear_filters_btn.setToolTip("Clear all active filters")
        self._clear_filters_btn.clicked.connect(self._clear_filters)
        filter_header.addWidget(self._clear_filters_btn)
        left_layout.addLayout(filter_header)

        self._filter_tree = FilterTreeWidget()
        self._filter_tree.setHeaderHidden(True)
        self._filter_tree.itemChanged.connect(self._on_filter_item_changed)
        self._filter_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._filter_tree.customContextMenuRequested.connect(self._filter_tree_context_menu)
        left_layout.addWidget(self._filter_tree)

        splitter.addWidget(left)

        # ---- Centre: search bar + table ----
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Title, creator or tag…")
        self._search_edit.textChanged.connect(lambda _: self._search_timer.start())
        search_row.addWidget(self._search_edit)
        centre_layout.addLayout(search_row)

        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(_HEADERS)
        self._model.itemChanged.connect(self._on_item_changed)

        self._proxy = _BrowserProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # search all columns

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Interactive)
        self._table.setColumnHidden(COL_THUMB, True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(COL_SOURCE,        SourceDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TAGS,          TagDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCULPTOR,      SculptorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_COLLECTION,    CollectionDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SUBCOLLECTION, SubcollectionDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_CATEGORY,      CategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RELEASE,       ReleaseDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCALE,         ScaleDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_PRESUPPORTED,  PresupportsDelegate(self._table))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_context_menu)
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._save_header_state)
        header.sectionResized.connect(self._save_header_state)
        self._restore_header_state()
        self._table.selectionModel().currentChanged.connect(self._on_current_changed)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.clicked.connect(self._on_table_click)
        from app.ui.config_dialog import get_hotkeys as _get_hotkeys
        _hk = _get_hotkeys()
        QShortcut(QKeySequence.StandardKey.SelectAll,      self._table, self._table.selectAll)
        QShortcut(QKeySequence(_hk["edit_model"]),         self._table, self._edit_selected)
        QShortcut(QKeySequence(_hk["mass_edit"]),          self._table, self._mass_edit_selected)
        QShortcut(QKeySequence(_hk["apply_filter"]),       self,         self._apply_checked_filters)
        QShortcut(QKeySequence(_hk["delete_from_db"]),     self._table, self._delete_selected_kb)
        QShortcut(QKeySequence(_hk["open_folder"]),        self._table, self._open_folder_selected_kb)
        QShortcut(QKeySequence(_hk["show_files"]),         self._table, self._show_files_kb)
        QShortcut(QKeySequence(_hk["image_grid"]),         self._table, self.show_image_grid)
        centre_layout.addWidget(self._table)

        splitter.addWidget(centre)

        # ---- Right: thumbnail sidebar + creator info panel ----
        right = QWidget()
        self._right_pane = right
        right.setMinimumWidth(160)
        right_outer = QVBoxLayout(right)
        right_outer.setContentsMargins(4, 0, 0, 0)

        right_vsplit = QSplitter(Qt.Orientation.Vertical)
        right_vsplit.setHandleWidth(6)
        self._right_vsplit = right_vsplit

        # Top: images
        img_widget = QWidget()
        img_layout = QVBoxLayout(img_widget)
        img_layout.setContentsMargins(0, 0, 0, 0)
        img_layout.addWidget(QLabel("Images"))

        self._thumb_content = QWidget()
        self._thumb_vlayout = QVBoxLayout(self._thumb_content)
        self._thumb_vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._thumb_vlayout.setSpacing(6)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidget(self._thumb_content)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.setAcceptDrops(True)
        self._thumb_scroll.installEventFilter(self)
        img_layout.addWidget(self._thumb_scroll)

        # Pagination controls (hidden unless images exceed the page size)
        self._thumb_page_row = QWidget()
        _pr_layout = QHBoxLayout(self._thumb_page_row)
        _pr_layout.setContentsMargins(0, 2, 0, 0)
        _pr_layout.setSpacing(4)
        self._thumb_prev_btn = QPushButton("◀")
        self._thumb_prev_btn.setFixedWidth(28)
        self._thumb_prev_btn.clicked.connect(self._thumb_prev_page)
        self._thumb_page_label = QLabel()
        self._thumb_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_next_btn = QPushButton("▶")
        self._thumb_next_btn.setFixedWidth(28)
        self._thumb_next_btn.clicked.connect(self._thumb_next_page)
        self._thumb_show_all_btn = QPushButton("Show All")
        self._thumb_show_all_btn.clicked.connect(self._thumb_toggle_show_all)
        _pr_layout.addWidget(self._thumb_prev_btn)
        _pr_layout.addWidget(self._thumb_page_label, 1)
        _pr_layout.addWidget(self._thumb_next_btn)
        _pr_layout.addWidget(self._thumb_show_all_btn)
        self._thumb_page_row.setVisible(False)
        img_layout.addWidget(self._thumb_page_row)

        right_vsplit.addWidget(img_widget)

        # Bottom: model info panel
        self._creator_panel = QFrame()
        self._creator_panel.setFrameShape(QFrame.Shape.StyledPanel)
        creator_layout = QVBoxLayout(self._creator_panel)
        creator_layout.setContentsMargins(4, 4, 4, 4)
        creator_layout.setSpacing(0)
        self._info_scroll = QScrollArea()
        self._info_scroll.setWidgetResizable(True)
        self._info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._info_lbl = QLabel()
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._info_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._info_lbl.setOpenExternalLinks(True)
        self._info_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._info_scroll.setWidget(self._info_lbl)
        creator_layout.addWidget(self._info_scroll)
        self._creator_panel.setVisible(False)
        right_vsplit.addWidget(self._creator_panel)

        right_vsplit.setStretchFactor(0, 3)
        right_vsplit.setStretchFactor(1, 1)
        right_outer.addWidget(right_vsplit)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setHandleWidth(6)
        self._thumb_splitter = splitter

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Reload all data from the database."""
        from app.database.filter_cache import invalidate_filter_cache
        invalidate_filter_cache()
        _lookup_cache.invalidate()
        self._panel_cache.clear()
        self._populate_filter_tree()
        self._apply_checked_filters()
        self._clear_thumbnails()

    def image_pane_visible(self) -> bool:
        return self._right_pane.isVisible()

    def info_pane_visible(self) -> bool:
        return self._info_pane_enabled

    def toggle_info_pane(self) -> bool:
        """Enable or disable the model info panel. Returns the new enabled state."""
        self._info_pane_enabled = not self._info_pane_enabled
        if not self._info_pane_enabled:
            self._creator_panel.setVisible(False)
        elif self._current_model_id is not None:
            # Re-populate immediately if a model is already selected
            src_index = self._proxy.mapToSource(
                self._table.selectionModel().currentIndex()
            )
            r = src_index.row()
            if r >= 0:
                self._update_model_info_panel(self._current_model_id, self._row_data_at(r))
        return self._info_pane_enabled

    def toggle_image_pane(self) -> bool:
        """Show or hide the right thumbnail pane. Returns the new visibility state."""
        visible = not self._right_pane.isVisible()
        if visible:
            sizes = self._thumb_splitter.sizes()
            saved = getattr(self, "_saved_right_width", 220)
            total = sum(sizes)
            self._thumb_splitter.setSizes([sizes[0], total - sizes[0] - saved, saved])
        else:
            sizes = self._thumb_splitter.sizes()
            self._saved_right_width = sizes[2] or 220
            self._thumb_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        self._right_pane.setVisible(visible)
        return visible

    # ------------------------------------------------------------------
    def _populate_filter_tree(
        self,
        scope_by_section: dict | None = None,
    ) -> None:
        """Kick off a background worker to rebuild the filter tree.

        All DB queries run in a QRunnable; the GUI thread slot
        ``_on_filter_data_ready`` builds the Qt tree items when the data
        arrives.
        """
        # ── Capture UI state on the GUI thread before going async ──────────────
        checked_data:  set = set()
        excluded_data: set = set()
        expanded_sections: "set[str] | None" = None

        root_item = self._filter_tree.invisibleRootItem()
        if root_item.childCount() > 0:
            expanded_sections = set()
            def _collect_state(item: QTreeWidgetItem):
                d = item.data(0, Qt.ItemDataRole.UserRole)
                if d:
                    state = item.checkState(0)
                    if state == Qt.CheckState.Checked:
                        checked_data.add(d)
                    elif state == Qt.CheckState.PartiallyChecked:
                        excluded_data.add(d)
                if item.childCount() and item.isExpanded():
                    expanded_sections.add(d if d else item.text(0))
                for i in range(item.childCount()):
                    _collect_state(item.child(i))
            for i in range(root_item.childCount()):
                _collect_state(root_item.child(i))

        # ── Read config and visibility flags on the GUI thread ─────────────────
        from app.ui.config_dialog import (
            get_color_scheme as _get_cs,
            get_filter_show_counts as _get_fsc,
            get_filter_alpha_enabled,
            get_filter_alpha_threshold,
        )
        header = self._table.horizontalHeader()
        show_flags = {
            "author":        not header.isSectionHidden(COL_CREATOR),
            "tags":          not header.isSectionHidden(COL_TAGS),
            "source":        not header.isSectionHidden(COL_SOURCE),
            "date":          not header.isSectionHidden(COL_DATE),
            "sculptor":      not header.isSectionHidden(COL_SCULPTOR),
            "collection":    not header.isSectionHidden(COL_COLLECTION),
            "subcollection": not header.isSectionHidden(COL_SUBCOLLECTION),
            "category":      not header.isSectionHidden(COL_CATEGORY),
            "release":       not header.isSectionHidden(COL_RELEASE),
            "scale":         not header.isSectionHidden(COL_SCALE),
            "presupports":   not header.isSectionHidden(COL_PRESUPPORTED),
            "warnings":      not header.isSectionHidden(COL_WARNING),
            "path":          not header.isSectionHidden(COL_PATH),
        }

        # ── Bump generation; clear tree while worker runs ───────────────────────
        self._filter_gen += 1
        gen = self._filter_gen
        self._filter_tree.blockSignals(True)
        self._filter_tree.clear()

        worker = _FilterTreeWorker(
            gen=gen,
            scope_by_section=scope_by_section,
            show_flags=show_flags,
            show_counts=_get_fsc(),
            alpha_enabled=get_filter_alpha_enabled(),
            alpha_threshold=get_filter_alpha_threshold(),
            checked_data=checked_data,
            excluded_data=excluded_data,
            expanded_sections=expanded_sections,
        )
        worker.signals.ready.connect(self._on_filter_data_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_filter_data_ready(self, gen: int, payload: dict) -> None:
        """Build the filter tree from pre-fetched data (called on GUI thread)."""
        if gen != self._filter_gen:
            return  # superseded by a newer call

        checked_data     = payload["checked_data"]
        excluded_data    = payload["excluded_data"]
        expanded_sections = payload["expanded_sections"]
        _counts          = payload["counts"]
        _path_counts     = payload["path_counts"]
        _scoped_counts   = payload["scoped_counts"]
        _sc_path_counts  = payload["sc_path_counts"]
        show             = payload["show"]
        show_counts      = payload["show_counts"]
        alpha_enabled    = payload["alpha_enabled"]
        alpha_threshold  = payload["alpha_threshold"]
        scope_by_section = payload["scope_by_section"]

        new_dirs_frozen  = payload.get("new_dirs_frozen")
        if new_dirs_frozen is not None:
            if new_dirs_frozen != self._cached_path_dirs:
                self._cached_path_dirs    = new_dirs_frozen
                self._cached_sorted_dirs  = payload["sorted_dirs"]

        from app.ui.config_dialog import get_color_scheme as _get_cs
        _v = "light" if "light" in _get_cs().lower() else "dark"

        def _icon(name: str):
            p = _ICONS_DIR / f"{name}_{_v}.svg"
            return QIcon(str(p)) if p.exists() else QIcon()

        _icon_author      = _icon("icon_author")
        _icon_tag         = _icon("icon_tag")
        _icon_type        = _icon("icon_type")
        _icon_path        = _icon("icon_path")
        _icon_date        = _icon("icon_date")
        _icon_sculptor    = _icon("icon_sculptor")
        _icon_category    = _icon("icon_category")
        _icon_subcategory = _icon("icon_subcategory")
        _icon_release     = _icon("icon_release")
        _icon_scale       = _icon("icon_scale")
        _icon_presupports = _icon("icon_presupports")
        _icon_warnings    = _icon("icon_warnings")

        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable

        _SCOPED_KINDS = (
            "creator", "source", "tag", "date_month", "date_year",
            "sculptor_id", "collection_id", "subcollection_id", "category_id",
            "release_id", "scale_id", "presupports", "warning", "path", "group",
        )
        _SCOPED_GROUPS = (
            "creator", "source", "tag", "date", "sculptor",
            "collection", "subcollection", "category", "release",
            "scale", "presupports", "warning", "path",
        )

        def _c(data) -> "int | None":
            if scope_by_section is not None and data[0] in _SCOPED_KINDS and (
                data[0] != "group" or data[1] in _SCOPED_GROUPS
            ):
                v = _scoped_counts.get(data)
                if v is None:
                    v = _counts.get(data)
            else:
                v = _counts.get(data)
            return v if v else None

        def _make_leaf(label: str, data, count: "int | None" = None) -> QTreeWidgetItem:
            display = f"{label} ({count})" if count is not None else label
            item = QTreeWidgetItem([display])
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            item.setFlags(_CHECKABLE)
            if data in checked_data:
                state = Qt.CheckState.Checked
            elif data in excluded_data:
                state = Qt.CheckState.PartiallyChecked
            else:
                state = Qt.CheckState.Unchecked
            item.setCheckState(0, state)
            return item

        def _add_children(parent: QTreeWidgetItem,
                          entries: "list[tuple[str, object, int | None]]") -> None:
            if not alpha_enabled or len(entries) <= alpha_threshold:
                for label, data, count in entries:
                    parent.addChild(_make_leaf(label, data, count))
                return
            letter_nodes: dict[str, QTreeWidgetItem] = {}
            for label, data, count in entries:
                first = label[0].upper() if label else "#"
                if not first.isalpha():
                    first = "#"
                if first not in letter_nodes:
                    ln = QTreeWidgetItem([first])
                    ln.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    parent.addChild(ln)
                    letter_nodes[first] = ln
                letter_nodes[first].addChild(_make_leaf(label, data, count))

        # ── Build tree items from pre-fetched data ──────────────────────────────
        if show.get("author"):
            authors_item = _make_leaf("Creators", ("group", "creator"), _c(("group", "creator")))
            authors_item.setIcon(0, _icon_author)
            self._filter_tree.addTopLevelItem(authors_item)
            _add_children(authors_item, [
                (c, ("creator", c), _c(("creator", c)))
                for c in payload["creators"]
            ])

        if show.get("tags"):
            tags_item = _make_leaf("Tags", ("group", "tag"), _c(("group", "tag")))
            tags_item.setIcon(0, _icon_tag)
            self._filter_tree.addTopLevelItem(tags_item)
            _add_children(tags_item, [
                (name, ("tag", name), _c(("tag", name)))
                for name in payload["tags"]
            ])

        if show.get("source"):
            types_item = _make_leaf("Type", ("group", "source"), _c(("group", "source")))
            types_item.setIcon(0, _icon_type)
            self._filter_tree.addTopLevelItem(types_item)
            _add_children(types_item, [
                (t, ("source", t), _c(("source", t)))
                for t in payload["sources"]
            ])

        if show.get("date"):
            _MONTH_NAMES = [
                "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            ]
            dates_item = _make_leaf("Date", ("group", "date"), _c(("group", "date")))
            dates_item.setIcon(0, _icon_date)
            self._filter_tree.addTopLevelItem(dates_item)
            year_items: dict[int, QTreeWidgetItem] = {}
            for year, month in payload["dates"]:
                if year not in year_items:
                    yi = _make_leaf(str(year), ("date_year", year), _c(("date_year", year)))
                    dates_item.addChild(yi)
                    year_items[year] = yi
                if month is not None:
                    label = _MONTH_NAMES[month] if 1 <= month <= 12 else str(month)
                    year_items[year].addChild(
                        _make_leaf(label, ("date_month", (year, month)),
                                   _c(("date_month", (year, month))))
                    )

        if show.get("sculptor"):
            sculptor_item = _make_leaf("Sculptor", ("group", "sculptor"), _c(("group", "sculptor")))
            sculptor_item.setIcon(0, _icon_sculptor)
            self._filter_tree.addTopLevelItem(sculptor_item)
            _add_children(sculptor_item, [
                (name, ("sculptor_id", sid), _c(("sculptor_id", sid)))
                for name, sid in payload["sculptors"]
            ])

        if show.get("collection"):
            collection_item = _make_leaf("Collection", ("group", "collection"),
                                         _c(("group", "collection")))
            collection_item.setIcon(0, _icon_category)
            self._filter_tree.addTopLevelItem(collection_item)
            _add_children(collection_item, [
                (name, ("collection_id", cid), _c(("collection_id", cid)))
                for name, cid in payload["collections"]
            ])

        if show.get("subcollection"):
            subcol_section = _make_leaf("Subcollection", ("group", "subcollection"),
                                        _c(("group", "subcollection")))
            subcol_section.setIcon(0, _icon_subcategory)
            self._filter_tree.addTopLevelItem(subcol_section)
            for col_name, col_id, subs in payload["sub_by_col"]:
                col_node = QTreeWidgetItem([col_name])
                col_node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                subcol_section.addChild(col_node)
                for sub_name, sub_id in subs:
                    col_node.addChild(
                        _make_leaf(sub_name, ("subcollection_id", sub_id),
                                   _c(("subcollection_id", sub_id)))
                    )

        if show.get("category"):
            category_item = _make_leaf("Category", ("group", "category"), _c(("group", "category")))
            category_item.setIcon(0, _icon_category)
            self._filter_tree.addTopLevelItem(category_item)
            _add_children(category_item, [
                (name, ("category_id", cid), _c(("category_id", cid)))
                for name, cid in payload["categories"]
            ])

        if show.get("release"):
            release_item = _make_leaf("Release", ("group", "release"), _c(("group", "release")))
            release_item.setIcon(0, _icon_release)
            self._filter_tree.addTopLevelItem(release_item)
            _add_children(release_item, [
                (name, ("release_id", rid), _c(("release_id", rid)))
                for name, rid in payload["releases"]
            ])

        if show.get("scale"):
            scale_item = _make_leaf("Scale", ("group", "scale"), _c(("group", "scale")))
            scale_item.setIcon(0, _icon_scale)
            self._filter_tree.addTopLevelItem(scale_item)
            _add_children(scale_item, [
                (name, ("scale_id", sid), _c(("scale_id", sid)))
                for name, sid in payload["scales"]
            ])

        if show.get("presupports"):
            presup_item = _make_leaf("Presupports", ("group", "presupports"),
                                     _c(("group", "presupports")))
            presup_item.setIcon(0, _icon_presupports)
            self._filter_tree.addTopLevelItem(presup_item)
            for val in payload["presupports"]:
                presup_item.addChild(_make_leaf(val, ("presupports", val), _c(("presupports", val))))

        if show.get("warnings"):
            _WARNING_TYPES = [
                "Models with presupported stl",
                "Archive without images",
                "Nested archives",
                "Archive with supports",
                "Archive without printable files",
            ]
            warn_item = _make_leaf("Warnings", ("group", "warning"), _c(("group", "warning")))
            warn_item.setIcon(0, _icon_warnings)
            self._filter_tree.addTopLevelItem(warn_item)
            for wtype in _WARNING_TYPES:
                warn_item.addChild(_make_leaf(wtype, ("warning", wtype), _c(("warning", wtype))))

        if show.get("path"):
            paths_item = _make_leaf("Path", ("group", "path"), _c(("group", "path")))
            paths_item.setIcon(0, _icon_path)
            self._filter_tree.addTopLevelItem(paths_item)
            scoped_dirs = payload.get("scoped_dirs")
            if scoped_dirs is not None:
                self._build_path_tree(paths_item, scoped_dirs, checked_data, excluded_data,
                                      path_counts=_sc_path_counts)
            else:
                self._build_path_tree(paths_item, self._cached_sorted_dirs,
                                      checked_data, excluded_data,
                                      path_counts=_path_counts)

        self._filter_tree.blockSignals(False)

        if expanded_sections is not None:
            def _restore_expansion(item: QTreeWidgetItem) -> None:
                d = item.data(0, Qt.ItemDataRole.UserRole)
                item.setExpanded((d if d else item.text(0)) in expanded_sections)
                for i in range(item.childCount()):
                    _restore_expansion(item.child(i))
            root = self._filter_tree.invisibleRootItem()
            for i in range(root.childCount()):
                _restore_expansion(root.child(i))

    def _filter_tree_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu
        item = self._filter_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or data[0] != "path":
            return
        path = data[1]
        menu = QMenu(self._filter_tree)
        act = menu.addAction(f"Run Crawler on: {path}")
        if menu.exec(self._filter_tree.viewport().mapToGlobal(pos)) == act:
            self.crawl_path_requested.emit(path)

    @staticmethod
    def _build_path_tree(root_item: QTreeWidgetItem, dirs: list[str],
                         checked_data: set | None = None,
                         excluded_data: set | None = None,
                         path_counts: dict[str, int] | None = None) -> None:
        """Build a nested tree of directory segments under *root_item*."""
        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        node_map: dict[str, QTreeWidgetItem] = {}

        for d in dirs:
            parts = FSPath(d).parts
            for depth in range(1, len(parts) + 1):
                full = FSPath(*parts[:depth]).as_posix()
                if full in node_map:
                    continue
                base_label = parts[depth - 1]
                cnt = path_counts.get(full) if path_counts is not None else None
                label = f"{base_label} ({cnt})" if cnt is not None else base_label
                parent = node_map.get(FSPath(*parts[:depth - 1]).as_posix()) if depth > 1 else root_item
                if parent is None:
                    parent = root_item
                data = ("path", full)
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, data)
                item.setFlags(_CHECKABLE)
                if checked_data and data in checked_data:
                    state = Qt.CheckState.Checked
                elif excluded_data and data in excluded_data:
                    state = Qt.CheckState.PartiallyChecked
                else:
                    state = Qt.CheckState.Unchecked
                item.setCheckState(0, state)
                parent.addChild(item)
                node_map[full] = item

    def show_model_ids(self, ids: list[int]) -> None:
        """Show only the models with the given IDs, ignoring all sidebar filters."""
        self._load_table(id_filters=ids)
        if self._proxy.rowCount() > 0:
            self._table.setCurrentIndex(self._proxy.index(0, 0))

    def show_duplicate_pairs(self, pairs: list) -> None:
        """Show duplicate pairs sorted by pair and color-coded with alternating colors."""
        all_ids = list({pid for pair in pairs for pid in pair})
        self._load_table(id_filters=all_ids, duplicate_pairs=pairs)
        if self._proxy.rowCount() > 0:
            self._table.setCurrentIndex(self._proxy.index(0, 0))

    def _load_table(
        self,
        author_filters:        list[str] | None = None,
        author_exc_filters:    list[str] | None = None,
        tag_filters:           list[str] | None = None,
        tag_exc_filters:       list[str] | None = None,
        type_filters:          list[str] | None = None,
        type_exc_filters:      list[str] | None = None,
        path_filters:          list[str] | None = None,
        path_exc_filters:      list[str] | None = None,
        date_filters:          list[tuple[int, int | None]] | None = None,
        date_exc_filters:      list[tuple[int, int | None]] | None = None,
        sculptor_filters:      list[str] | None = None,
        sculptor_exc_filters:  list[str] | None = None,
        collection_filters:    list[str] | None = None,
        collection_exc_filters: list[str] | None = None,
        subcollection_filters: list[str] | None = None,
        subcollection_exc_filters: list[str] | None = None,
        category_filters:      list[str] | None = None,
        category_exc_filters:  list[str] | None = None,
        release_filters:       list[str] | None = None,
        release_exc_filters:   list[str] | None = None,
        scale_filters:         list[str] | None = None,
        scale_exc_filters:     list[str] | None = None,
        presupports_filters:   list[str] | None = None,
        presupports_exc_filters: list[str] | None = None,
        warning_filters:       list[str] | None = None,
        warning_exc_filters:   list[str] | None = None,
        has_value_groups:      list[str] | None = None,
        no_value_groups:       list[str] | None = None,
        id_filters:            list[int] | None = None,
        duplicate_pairs:       list[list[int]] | None = None,
    ) -> None:
        """Reload the table with optional include/exclude filter lists.

        Within each category the values are OR-ed; across categories they are AND-ed.
        *_exc_filters* variants exclude rows that match those values.
        *date_filters* / *date_exc_filters* are lists of (year, month_or_None) tuples.

        Data is fetched in a background thread (TableLoaderWorker) and appended
        to the model in 200-row batches so the UI stays responsive during loading.
        """
        self._stop_loader()
        self._load_gen += 1
        gen = self._load_gen

        self._loading = True
        self.loading_started.emit()
        self._model.removeRows(0, self._model.rowCount())

        # ------------------------------------------------------------------
        # Build flat SELECT from models_view — the view pre-joins the six FK
        # lookup tables, so no outer-join construction is needed here.
        # SA expressions are thread-safe to construct here and execute in the
        # worker's own session.
        # ------------------------------------------------------------------
        mv = get_models_view()

        def _date_clauses(pairs):
            return [
                (mv.c.release_year == y) if mo is None
                else and_(mv.c.release_year == y, mv.c.release_month == mo)
                for y, mo in pairs
            ]

        stmt = sa_select(
            mv.c.id, mv.c.title, mv.c.creator, mv.c.source,
            mv.c.release_year, mv.c.release_month,
            mv.c.archive_path, mv.c.presupports, mv.c.warning,
            mv.c.archive_size, mv.c.compression_rate,
            mv.c.sequence, mv.c.created_at,
            mv.c.sculptor_name,
            mv.c.collection_name,
            mv.c.subcollection_name,
            mv.c.category_name,
            mv.c.release_name,
            mv.c.scale_name,
        ).select_from(mv)

        _tag_sub = sa_select(_mt_global.c.model_id).join(Tag, _mt_global.c.tag_id == Tag.id)

        # --- explicit ID set ---
        if id_filters:
            stmt = stmt.where(mv.c.id.in_(id_filters))
        # --- include filters ---
        if author_filters:
            stmt = stmt.where(mv.c.creator.in_(author_filters))
        if type_filters:
            stmt = stmt.where(mv.c.source.in_(type_filters))
        if tag_filters:
            stmt = stmt.where(mv.c.id.in_(_tag_sub.where(Tag.name.in_(tag_filters))))
        if path_filters:
            _norm = func.replace(mv.c.archive_path, "\\", "/")
            _pruned_paths = _prune_ancestor_paths(path_filters)
            stmt = stmt.where(or_(*(_norm.like(p.rstrip("/") + "/%") for p in _pruned_paths)))
        if date_filters:
            stmt = stmt.where(or_(*_date_clauses(date_filters)))
        if sculptor_filters:
            stmt = stmt.where(mv.c.sculptor_id.in_(sculptor_filters))
        if collection_filters:
            stmt = stmt.where(mv.c.collection_id.in_(collection_filters))
        if subcollection_filters:
            stmt = stmt.where(mv.c.subcollection_id.in_(subcollection_filters))
        if category_filters:
            stmt = stmt.where(mv.c.category_id.in_(category_filters))
        if release_filters:
            stmt = stmt.where(mv.c.release_id.in_(release_filters))
        if scale_filters:
            stmt = stmt.where(mv.c.scale_id.in_(scale_filters))
        if presupports_filters:
            stmt = stmt.where(mv.c.presupports.in_(presupports_filters))
        if warning_filters:
            stmt = stmt.where(or_(*(mv.c.warning.contains(w) for w in warning_filters)))
        # --- exclude filters ---
        if author_exc_filters:
            stmt = stmt.where(~mv.c.creator.in_(author_exc_filters))
        if type_exc_filters:
            stmt = stmt.where(~mv.c.source.in_(type_exc_filters))
        if tag_exc_filters:
            stmt = stmt.where(mv.c.id.notin_(_tag_sub.where(Tag.name.in_(tag_exc_filters))))
        if path_exc_filters:
            _norm = func.replace(mv.c.archive_path, "\\", "/")
            _pruned_exc = _prune_ancestor_paths(path_exc_filters)
            stmt = stmt.where(~or_(*(_norm.like(p.rstrip("/") + "/%") for p in _pruned_exc)))
        if date_exc_filters:
            stmt = stmt.where(~or_(*_date_clauses(date_exc_filters)))
        if sculptor_exc_filters:
            stmt = stmt.where(~mv.c.sculptor_id.in_(sculptor_exc_filters))
        if collection_exc_filters:
            stmt = stmt.where(~mv.c.collection_id.in_(collection_exc_filters))
        if subcollection_exc_filters:
            stmt = stmt.where(~mv.c.subcollection_id.in_(subcollection_exc_filters))
        if category_exc_filters:
            stmt = stmt.where(~mv.c.category_id.in_(category_exc_filters))
        if release_exc_filters:
            stmt = stmt.where(~mv.c.release_id.in_(release_exc_filters))
        if scale_exc_filters:
            stmt = stmt.where(~mv.c.scale_id.in_(scale_exc_filters))
        if presupports_exc_filters:
            stmt = stmt.where(~mv.c.presupports.in_(presupports_exc_filters))
        if warning_exc_filters:
            stmt = stmt.where(and_(*(~mv.c.warning.contains(w) for w in warning_exc_filters)))

        # --- group-level has/no-value filters ---
        _has_tag_sub = sa_select(_mt_global.c.model_id).distinct()
        _group_has_value = {
            "creator":       lambda: and_(mv.c.creator.isnot(None),       mv.c.creator != ""),
            "tag":           lambda: mv.c.id.in_(_has_tag_sub),
            "source":        lambda: and_(mv.c.source.isnot(None),         mv.c.source != ""),
            "date":          lambda: mv.c.release_year.isnot(None),
            "sculptor":      lambda: mv.c.sculptor_id.isnot(None),
            "collection":    lambda: mv.c.collection_id.isnot(None),
            "subcollection": lambda: mv.c.subcollection_id.isnot(None),
            "category":      lambda: mv.c.category_id.isnot(None),
            "release":       lambda: mv.c.release_id.isnot(None),
            "scale":         lambda: mv.c.scale_id.isnot(None),
            "presupports":   lambda: and_(mv.c.presupports.isnot(None),   mv.c.presupports != ""),
            "warning":       lambda: and_(mv.c.warning.isnot(None),       mv.c.warning != ""),
            "path":          lambda: and_(mv.c.archive_path.isnot(None),  mv.c.archive_path != ""),
        }
        if has_value_groups:
            for _key in has_value_groups:
                _cond = _group_has_value.get(_key)
                if _cond:
                    stmt = stmt.where(_cond())
        if no_value_groups:
            for _key in no_value_groups:
                _cond = _group_has_value.get(_key)
                if _cond:
                    stmt = stmt.where(~_cond())

        # --- duplicate-pairs ordering ---
        pair_order:  dict | None = None
        pair_for_id: dict | None = None
        if duplicate_pairs is not None:
            from app.ui.config_dialog import get_pair_colors
            _ca, _cb = get_pair_colors()
            self._load_pair_colors = [QColor(_ca), QColor(_cb)]
            _seen: set[int] = set()
            pair_for_id = {}
            pair_order  = {}
            _pos = 0
            for _pi, _pair in enumerate(duplicate_pairs):
                for _pid in _pair:
                    if _pid not in _seen:
                        _seen.add(_pid)
                        pair_order[_pid]  = _pos
                        _pos += 1
                        pair_for_id[_pid] = _pi
        else:
            self._load_pair_colors = None
            stmt = stmt.order_by(mv.c.title)

        # Tags GROUP_CONCAT query — scoped to the same filtered model set as stmt.
        _id_sub = sa_select(mv.c.id).select_from(mv)
        if stmt.whereclause is not None:
            _id_sub = _id_sub.where(stmt.whereclause)
        tags_stmt = (
            sa_select(_mt_global.c.model_id, func.group_concat(Tag.name, ", "))
            .join(Tag, _mt_global.c.tag_id == Tag.id)
            .where(_mt_global.c.model_id.in_(_id_sub))
            .group_by(_mt_global.c.model_id)
        )

        # ------------------------------------------------------------------
        # Start background worker — appends rows via _append_rows signal slot
        # ------------------------------------------------------------------
        worker = TableLoaderWorker(stmt, tags_stmt, pair_order, pair_for_id, parent=self)
        self._table_loader = worker
        worker.rows_ready.connect(
            lambda rows, g=gen: self._append_rows(rows, g)
        )
        worker.finished.connect(
            lambda n, g=gen: self._on_load_finished(n, g)
        )
        worker.start()

    # ------------------------------------------------------------------
    def _stop_loader(self) -> None:
        """Stop any in-flight TableLoaderWorker gracefully."""
        if self._table_loader is not None:
            self._table_loader.stop()
            self._table_loader = None

    def _append_rows(self, rows: list, gen: int) -> None:
        """Slot: receive a batch of raw tuples from TableLoaderWorker and build QStandardItem rows."""
        if gen != self._load_gen:
            return  # stale batch from a cancelled load

        from app.ui.config_dialog import get_warn_row_bg_color, get_warn_row_fg_color
        warn_bg   = QColor(get_warn_row_bg_color())
        warn_fg   = QColor(get_warn_row_fg_color())
        warn_font = QFont()
        warn_font.setPointSize(16)
        warn_font.setBold(True)
        pair_colors = self._load_pair_colors

        for (mid, title, creator, source, yr, mo, ap, presupports, warning,
             archive_size, compression_rate, sequence, created_at,
             sculptor_name, collection_name, subcollection_name, category_name,
             release_name, scale_name, tags_str, pair_idx) in rows:

            thumb_item = QStandardItem()
            thumb_item.setData(mid, MODEL_ID_ROLE)
            thumb_item.setEditable(False)

            seq_str  = str(sequence) if sequence is not None else ""
            seq_item = QStandardItem(seq_str)
            seq_item.setEditable(True)
            seq_item.setData(sequence, Qt.ItemDataRole.UserRole)

            added_str  = created_at.strftime("%Y-%m-%d %H:%M") if created_at else ""
            added_item = QStandardItem(added_str)
            added_item.setEditable(False)

            warning_item = QStandardItem("⚠" if warning else "")
            warning_item.setEditable(False)
            warning_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if warning:
                warning_item.setData(warning, Qt.ItemDataRole.ToolTipRole)
                warning_item.setBackground(warn_bg)
                warning_item.setForeground(warn_fg)
                warning_item.setFont(warn_font)

            raw_size   = archive_size if archive_size is not None else -1
            size_item  = QStandardItem(_fmt_size(raw_size) if raw_size >= 0 else "")
            size_item.setData(raw_size, Qt.ItemDataRole.UserRole)
            size_item.setEditable(False)

            cr = compression_rate
            if cr and cr > 0:
                comp_display = f"{1 / cr:.1f}x"
                comp_sort    = cr
            else:
                comp_display = ""
                comp_sort    = -1.0
            comp_item = QStandardItem(comp_display)
            comp_item.setData(comp_sort, Qt.ItemDataRole.UserRole)
            comp_item.setEditable(False)
            comp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            path_item = QStandardItem(ap or "")
            path_item.setEditable(False)

            row = [
                thumb_item,
                QStandardItem(title or ""),
                QStandardItem(creator or ""),
                QStandardItem(source or ""),
                QStandardItem(format_release_date(yr, mo)),
                QStandardItem(tags_str),
                QStandardItem(sculptor_name or ""),
                QStandardItem(collection_name or ""),
                QStandardItem(subcollection_name or ""),
                seq_item,
                QStandardItem(release_name or ""),
                path_item,
                QStandardItem(scale_name or ""),
                QStandardItem(presupports or ""),
                added_item,
                warning_item,
                size_item,
                QStandardItem(category_name or ""),
                comp_item,
            ]
            if pair_colors is not None and pair_idx >= 0:
                _color = pair_colors[pair_idx % 2]
                for _it in row:
                    _it.setBackground(_color)

            self._model.appendRow(row)

        self.model_count_changed.emit(self._model.rowCount())

    def _on_load_finished(self, _total: int, gen: int) -> None:
        """Slot: called when TableLoaderWorker has emitted all rows."""
        if gen != self._load_gen:
            return
        self._loading = False
        self._table_loader = None
        self.model_count_changed.emit(self._model.rowCount())

    # ------------------------------------------------------------------
    def _on_item_changed(self, item: QStandardItem) -> None:
        """Debounce cell edits; actual DB write is deferred to *_flush_pending_edits*."""
        if self._loading:
            return
        col = item.column()
        if col in (COL_THUMB, COL_ADDED, COL_WARNING, COL_FILESIZE, COL_COMPRESSION):
            return
        # Debounce: store the latest item for this cell and (re)start the timer.
        self._pending_edits[(item.row(), col)] = item
        self._edit_timer.start()

    def _flush_pending_edits(self) -> None:
        """Write all debounced cell edits to the DB in a single pass."""
        items = list(self._pending_edits.values())
        self._pending_edits.clear()
        for item in items:
            self._write_item(item)

    def _write_item(self, item: QStandardItem) -> None:
        """Persist a single edited table cell to the database."""
        col = item.column()
        model_id = self._model.item(item.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is None:
            return
        value = item.text().strip() or None
        with session_scope() as session:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None:
                return
            if col == COL_TITLE:
                stl.title = value
            elif col == COL_CREATOR:
                stl.creator = value
            elif col == COL_SOURCE:
                stl.source = value
            elif col == COL_DATE:
                _year, _month = parse_release_date(value or "")
                stl.release_year  = _year
                stl.release_month = _month
            elif col == COL_TAGS:
                stl.tags.clear()
                for name in [t.strip().lower() for t in (value or "").split(",") if t.strip()]:
                    tag = session.query(Tag).filter_by(name=name).first()
                    if tag is None:
                        tag = Tag(name=name)
                        session.add(tag)
                        session.flush()
                    stl.tags.append(tag)
            elif col == COL_SCULPTOR:
                if value:
                    sc = session.query(Sculptor).filter_by(name=value).first()
                    if sc is None:
                        sc = Sculptor(name=value)
                        session.add(sc)
                        session.flush()
                    stl.sculptor = sc
                else:
                    stl.sculptor = None
            elif col == COL_COLLECTION:
                if value:
                    col_obj = session.query(Collection).filter_by(name=value).first()
                    if col_obj is None:
                        col_obj = Collection(name=value)
                        session.add(col_obj)
                        session.flush()
                    stl.collection = col_obj
                else:
                    stl.collection = None
                    stl.subcollection = None
            elif col == COL_SUBCOLLECTION:
                if value:
                    q = session.query(Subcollection).filter_by(name=value)
                    if stl.collection_id:
                        q = q.filter_by(collection_id=stl.collection_id)
                    sc = q.first()
                    if sc is None and stl.collection_id:
                        sc = Subcollection(name=value, collection_id=stl.collection_id)
                        session.add(sc)
                        session.flush()
                    if sc:
                        stl.subcollection = sc
                else:
                    stl.subcollection = None
            elif col == COL_CATEGORY:
                if value:
                    cat = session.query(Category).filter_by(name=value).first()
                    if cat is None:
                        cat = Category(name=value)
                        session.add(cat)
                        session.flush()
                    stl.category = cat
                else:
                    stl.category = None
            elif col == COL_SEQUENCE:
                try:
                    stl.sequence = int(value) if value else None
                except ValueError:
                    pass
            elif col == COL_RELEASE:
                if value:
                    rel = session.query(Release).filter_by(name=value).first()
                    if rel is None:
                        rel = Release(name=value)
                        session.add(rel)
                        session.flush()
                    stl.release = rel
                else:
                    stl.release = None
            elif col == COL_SCALE:
                if value:
                    sc = session.query(Scale).filter_by(name=value).first()
                    if sc is None:
                        sc = Scale(name=value)
                        session.add(sc)
                        session.flush()
                    stl.scale = sc
                else:
                    stl.scale = None
            elif col == COL_PRESUPPORTED:
                stl.presupports = value.strip() or None

            # Update per-folder METADATA.7z sidecar with the saved change
            if stl.archive_path:
                from pathlib import Path as _Path
                from app.crawler.folder_metadata import export_folder_metadata
                from app.utils.thumbnailer import get_thumbnail_base_dir
                from app.ui.config_dialog import get_metadata_thumbnails_enabled, get_metadata_sidecar_enabled
                thumb_base = get_thumbnail_base_dir()
                if thumb_base is not None and get_metadata_sidecar_enabled():
                    try:
                        export_folder_metadata(
                            _Path(stl.archive_path).parent,
                            session, thumb_base,
                            include_thumbnails=get_metadata_thumbnails_enabled(),
                        )
                    except Exception:
                        pass  # best-effort; never block the edit

    # ------------------------------------------------------------------
    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    def _on_filter_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        """Called when a checkbox in the filter tree changes state."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return

        # Complete the tristate cycle for checkbox-click events.
        # mousePressEvent stores the pre-click state; here we map it to the next state.
        prev = self._filter_tree._pre_click_states.pop(id(item), None)
        if prev is not None:
            current = item.checkState(0)
            if prev == Qt.CheckState.Unchecked:
                next_state = Qt.CheckState.Checked
            elif prev == Qt.CheckState.Checked:
                next_state = Qt.CheckState.PartiallyChecked
            else:  # PartiallyChecked → reset
                next_state = Qt.CheckState.Unchecked
            if next_state != current:
                self._filter_tree.blockSignals(True)
                item.setCheckState(0, next_state)
                self._filter_tree.blockSignals(False)

        # Propagate the final state down to all children (path, date, etc.).
        # Group headers are excluded because their children have different semantics.
        kind = data[0] if isinstance(data, tuple) else None
        if kind != "group" and item.childCount() > 0:
            self._filter_tree.blockSignals(True)
            self._propagate_state_to_children(item, item.checkState(0))
            self._filter_tree.blockSignals(False)

        self._mark_filters_pending()

    @staticmethod
    def _propagate_state_to_children(item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        """Recursively set all checkable descendants of *item* to *state*."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(0, state)
            BrowserTab._propagate_state_to_children(child, state)

    def _mark_filters_pending(self) -> None:
        """Highlight the Apply button to indicate unapplied filter changes."""
        self._apply_filters_btn.setProperty("pending", True)
        self._apply_filters_btn.style().unpolish(self._apply_filters_btn)
        self._apply_filters_btn.style().polish(self._apply_filters_btn)
        self._apply_filters_btn.setStyleSheet(
            "QPushButton { font-weight: bold; }"
        )

    def _clear_filters_pending(self) -> None:
        """Remove the pending highlight from the Apply button."""
        self._apply_filters_btn.setProperty("pending", False)
        self._apply_filters_btn.setStyleSheet("")

    def _apply_checked_filters(self) -> None:
        """Collect include (Checked) and exclude (PartiallyChecked) filter items and reload."""
        self._clear_filters_pending()
        author_inc:          list[str]   = []; author_exc:          list[str]   = []
        tag_inc:             list[str]   = []; tag_exc:             list[str]   = []
        type_inc:            list[str]   = []; type_exc:            list[str]   = []
        path_inc:            list[str]   = []; path_exc:            list[str]   = []
        date_inc:            list[tuple] = []; date_exc:            list[tuple] = []
        sculptor_inc:        list[int]   = []; sculptor_exc:        list[int]   = []
        collection_inc:      list[int]   = []; collection_exc:      list[int]   = []
        subcollection_inc:   list[int]   = []; subcollection_exc:   list[int]   = []
        category_inc:        list[int]   = []; category_exc:        list[int]   = []
        release_inc:         list[int]   = []; release_exc:         list[int]   = []
        scale_inc:           list[int]   = []; scale_exc:           list[int]   = []
        presupports_inc:     list[str]   = []; presupports_exc:     list[str]   = []
        warning_inc:         list[str]   = []; warning_exc:         list[str]   = []
        has_value_groups:    set[str]    = set()
        no_value_groups:     set[str]    = set()

        _buckets = {
            "creator":           (author_inc,       author_exc),
            "tag":               (tag_inc,          tag_exc),
            "source":            (type_inc,         type_exc),
            "path":              (path_inc,         path_exc),
            "sculptor_id":       (sculptor_inc,     sculptor_exc),
            "collection_id":     (collection_inc,   collection_exc),
            "subcollection_id":  (subcollection_inc, subcollection_exc),
            "category_id":       (category_inc,     category_exc),
            "release_id":        (release_inc,      release_exc),
            "scale_id":          (scale_inc,        scale_exc),
            "presupports":       (presupports_inc,  presupports_exc),
            "warning":           (warning_inc,      warning_exc),
        }

        def _collect(item: QTreeWidgetItem):
            data  = item.data(0, Qt.ItemDataRole.UserRole)
            state = item.checkState(0)
            if data and state in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                is_inc = state == Qt.CheckState.Checked
                kind, value = data
                if kind == "group":
                    (has_value_groups if is_inc else no_value_groups).add(value)
                elif kind in ("date_year", "date_month"):
                    target = (date_inc if is_inc else date_exc)
                    target.append((value, None) if kind == "date_year" else value)
                elif kind in _buckets:
                    _buckets[kind][0 if is_inc else 1].append(value)
            for i in range(item.childCount()):
                _collect(item.child(i))

        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _collect(root.child(i))

        self._load_table(
            author_filters=author_inc or None,               author_exc_filters=author_exc or None,
            tag_filters=tag_inc or None,                     tag_exc_filters=tag_exc or None,
            type_filters=type_inc or None,                   type_exc_filters=type_exc or None,
            path_filters=path_inc or None,                   path_exc_filters=path_exc or None,
            date_filters=date_inc or None,                   date_exc_filters=date_exc or None,
            sculptor_filters=sculptor_inc or None,           sculptor_exc_filters=sculptor_exc or None,
            collection_filters=collection_inc or None,       collection_exc_filters=collection_exc or None,
            subcollection_filters=subcollection_inc or None, subcollection_exc_filters=subcollection_exc or None,
            category_filters=category_inc or None,           category_exc_filters=category_exc or None,
            release_filters=release_inc or None,             release_exc_filters=release_exc or None,
            scale_filters=scale_inc or None,                 scale_exc_filters=scale_exc or None,
            presupports_filters=presupports_inc or None,     presupports_exc_filters=presupports_exc or None,
            warning_filters=warning_inc or None,             warning_exc_filters=warning_exc or None,
            has_value_groups=has_value_groups or None,
            no_value_groups=no_value_groups or None,
        )

        # Compute per-section scopes for faceted filter pane.
        # Each section's scope = IDs matching all OTHER active filters (not its own).
        _has_creator     = bool(author_inc or author_exc)
        _has_type        = bool(type_inc or type_exc)
        _has_path        = bool(path_inc or path_exc)
        _has_tag         = bool(tag_inc or tag_exc)
        _has_date        = bool(date_inc or date_exc)
        _has_sculptor    = bool(sculptor_inc or sculptor_exc)
        _has_col         = bool(collection_inc or collection_exc)
        _has_sub         = bool(subcollection_inc or subcollection_exc)
        _has_category    = bool(category_inc or category_exc)
        _has_rel         = bool(release_inc or release_exc)
        _has_scale       = bool(scale_inc or scale_exc)
        _has_presupports = bool(presupports_inc or presupports_exc)
        _has_warning     = bool(warning_inc or warning_exc)
        _has_group       = bool(has_value_groups or no_value_groups)
        _has_any = any([
            _has_creator, _has_type, _has_path, _has_tag, _has_date,
            _has_sculptor, _has_col, _has_sub, _has_category, _has_rel,
            _has_scale, _has_presupports, _has_warning, _has_group,
        ])

        scope_by_section: dict | None = None

        if _has_any:
            # Build a subquery for each filter section (all filters except that
            # section's own).  Using sa_select().subquery() means the DB sees
            # "WHERE id IN (SELECT id FROM models WHERE …)" — a proper SQL
            # subquery — instead of a huge IN (?, ?, …) parameter list.

            # Materialise path-filter IDs once: replace(archive_path) LIKE can't
            # use an index, so repeating the scan once per scoped-count query is
            # expensive (~37 ms × 26 queries).  A single LIKE pass gives us a
            # plain integer list; subsequent queries use the primary-key index.
            _path_ids_mat: list[int] | None = None
            if _has_path:
                _norm_m = func.replace(STLModel.archive_path, "\\", "/")
                _pq = sa_select(STLModel.id)
                if path_inc:
                    _pq = _pq.where(or_(*(_norm_m.like(p.rstrip("/") + "/%") for p in _prune_ancestor_paths(path_inc))))
                if path_exc:
                    _pq = _pq.where(~or_(*(_norm_m.like(p.rstrip("/") + "/%") for p in _prune_ancestor_paths(path_exc))))
                with session_scope() as _ms:
                    _path_ids_mat = [r[0] for r in _ms.execute(_pq).all()]

            def _run_id_subquery(
                skip_creator=False, skip_type=False, skip_path=False,
                skip_tag=False, skip_date=False, skip_sculptor=False,
                skip_col=False, skip_sub=False, skip_category=False,
                skip_rel=False, skip_scale=False, skip_presupports=False,
                skip_warning=False,
            ):
                q = sa_select(STLModel.id)
                _has_condition = False
                def _w(cond):
                    nonlocal q, _has_condition
                    q = q.where(cond)
                    _has_condition = True
                if not skip_creator:
                    if author_inc:  _w(STLModel.creator.in_(author_inc))
                    if author_exc:  _w(~STLModel.creator.in_(author_exc))
                if not skip_type:
                    if type_inc:    _w(STLModel.source.in_(type_inc))
                    if type_exc:    _w(~STLModel.source.in_(type_exc))
                if not skip_tag:
                    if tag_inc:     _w(STLModel.tags.any(Tag.name.in_(tag_inc)))
                    if tag_exc:     _w(~STLModel.tags.any(Tag.name.in_(tag_exc)))
                if not skip_path:
                    if _path_ids_mat is not None:
                        _w(STLModel.id.in_(_path_ids_mat))
                    else:
                        _n = func.replace(STLModel.archive_path, "\\", "/")
                        if path_inc:
                            _w(or_(*(_n.like(p.rstrip("/") + "/%") for p in _prune_ancestor_paths(path_inc))))
                        if path_exc:
                            _w(~or_(*(_n.like(p.rstrip("/") + "/%") for p in _prune_ancestor_paths(path_exc))))
                if not skip_date:
                    def _dc(pairs):
                        return [and_(STLModel.release_year == y, STLModel.release_month == mo) if mo is not None else (STLModel.release_year == y) for y, mo in pairs]
                    if date_inc:    _w(or_(*_dc(date_inc)))
                    if date_exc:    _w(~or_(*_dc(date_exc)))
                if not skip_sculptor:
                    if sculptor_inc: _w(STLModel.sculptor_id.in_(sculptor_inc))
                    if sculptor_exc: _w(~STLModel.sculptor_id.in_(sculptor_exc))
                if not skip_col:
                    if collection_inc:    _w(STLModel.collection_id.in_(collection_inc))
                    if collection_exc:    _w(~STLModel.collection_id.in_(collection_exc))
                if not skip_sub:
                    if subcollection_inc: _w(STLModel.subcollection_id.in_(subcollection_inc))
                    if subcollection_exc: _w(~STLModel.subcollection_id.in_(subcollection_exc))
                if not skip_category:
                    if category_inc: _w(STLModel.category_id.in_(category_inc))
                    if category_exc: _w(~STLModel.category_id.in_(category_exc))
                if not skip_rel:
                    if release_inc:  _w(STLModel.release_id.in_(release_inc))
                    if release_exc:  _w(~STLModel.release_id.in_(release_exc))
                if not skip_scale:
                    if scale_inc:    _w(STLModel.scale_id.in_(scale_inc))
                    if scale_exc:    _w(~STLModel.scale_id.in_(scale_exc))
                if not skip_presupports:
                    if presupports_inc: _w(STLModel.presupports.in_(presupports_inc))
                    if presupports_exc: _w(~STLModel.presupports.in_(presupports_exc))
                if not skip_warning:
                    if warning_inc:  _w(or_(*(STLModel.warning.contains(w) for w in warning_inc)))
                    if warning_exc:  _w(and_(*(~STLModel.warning.contains(w) for w in warning_exc)))
                _group_conds = {
                    "creator":       lambda: and_(STLModel.creator.isnot(None),      STLModel.creator != ""),
                    "tag":           lambda: STLModel.tags.any(),
                    "source":        lambda: and_(STLModel.source.isnot(None),        STLModel.source != ""),
                    "date":          lambda: STLModel.release_year.isnot(None),
                    "sculptor":      lambda: STLModel.sculptor_id.isnot(None),
                    "collection":    lambda: STLModel.collection_id.isnot(None),
                    "subcollection": lambda: STLModel.subcollection_id.isnot(None),
                    "category":      lambda: STLModel.category_id.isnot(None),
                    "release":       lambda: STLModel.release_id.isnot(None),
                    "scale":         lambda: STLModel.scale_id.isnot(None),
                    "presupports":   lambda: and_(STLModel.presupports.isnot(None),   STLModel.presupports != ""),
                    "warning":       lambda: and_(STLModel.warning.isnot(None),       STLModel.warning != ""),
                }
                for _k in has_value_groups:
                    _c2 = _group_conds.get(_k)
                    if _c2: _w(_c2())
                for _k in no_value_groups:
                    _c2 = _group_conds.get(_k)
                    if _c2: _w(~_c2())
                return q.subquery() if _has_condition else None

            scope_by_section = {
                "creator":       _run_id_subquery(skip_creator=True),
                "source":        _run_id_subquery(skip_type=True),
                "tag":           _run_id_subquery(skip_tag=True),
                "path":          _run_id_subquery(skip_path=True),
                "date":          _run_id_subquery(skip_date=True),
                "sculptor":      _run_id_subquery(skip_sculptor=True),
                "collection":    _run_id_subquery(skip_col=True),
                "subcollection": _run_id_subquery(skip_sub=True),
                "category":      _run_id_subquery(skip_category=True),
                "release":       _run_id_subquery(skip_rel=True),
                "scale":         _run_id_subquery(skip_scale=True),
                "presupports":   _run_id_subquery(skip_presupports=True),
                "warning":       _run_id_subquery(skip_warning=True),
            }

        self._populate_filter_tree(scope_by_section=scope_by_section)

    def _clear_filters(self) -> None:
        """Uncheck all filter items and reload the full table."""
        self._filter_tree.blockSignals(True)
        def _uncheck(item: QTreeWidgetItem):
            if item.data(0, Qt.ItemDataRole.UserRole) is not None:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            for i in range(item.childCount()):
                _uncheck(item.child(i))
        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _uncheck(root.child(i))
        self._filter_tree.blockSignals(False)
        self._clear_filters_pending()
        self._load_table()
        self._populate_filter_tree()

    def _row_data_at(self, r: int) -> dict:
        """Extract all displayable fields from table row *r* into a flat dict."""
        return {
            "title":         self._model.item(r, COL_TITLE).text(),
            "creator":       self._model.item(r, COL_CREATOR).text(),
            "source":        self._model.item(r, COL_SOURCE).text(),
            "date":          self._model.item(r, COL_DATE).text(),
            "tags":          self._model.item(r, COL_TAGS).text(),
            "sculptor":      self._model.item(r, COL_SCULPTOR).text(),
            "collection":    self._model.item(r, COL_COLLECTION).text(),
            "subcollection": self._model.item(r, COL_SUBCOLLECTION).text(),
            "category":      self._model.item(r, COL_CATEGORY).text(),
            "release":       self._model.item(r, COL_RELEASE).text(),
            "scale":         self._model.item(r, COL_SCALE).text(),
            "presupports":   self._model.item(r, COL_PRESUPPORTED).text(),
            "sequence":      self._model.item(r, COL_SEQUENCE).text(),
            "path":          self._model.item(r, COL_PATH).text(),
            "filesize":      self._model.item(r, COL_FILESIZE).text(),
            "compression":   self._model.item(r, COL_COMPRESSION).text(),
            "added":         self._model.item(r, COL_ADDED).text(),
            "warning":       self._model.item(r, COL_WARNING).data(Qt.ItemDataRole.ToolTipRole) or "",
        }

    def _on_current_changed(self, current, previous) -> None:
        if not current.isValid():
            self._current_model_id = None
            self._clear_thumbnails()
            self._creator_panel.setVisible(False)
            return
        src_index = self._proxy.mapToSource(current)
        r = src_index.row()
        model_id = self._model.item(r, COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._current_model_id = model_id
            self._show_thumbnails(model_id)
            if self._info_pane_enabled:
                self._update_model_info_panel(model_id, self._row_data_at(r))

    def _update_model_info_panel(self, model_id: int, row_data: dict) -> None:
        """Build the HTML info panel from row data + cached url/description/creator profile."""
        from html import escape

        if model_id not in self._panel_cache:
            from app.database.models import Creator as CreatorModel
            creator_name = row_data["creator"]
            with session_scope() as session:
                m = session.query(STLModel.url, STLModel.description).filter_by(id=model_id).first()
                c = (
                    session.query(
                        CreatorModel.full_name,
                        CreatorModel.patreon_url,
                        CreatorModel.artstation_url,
                        CreatorModel.gumroad_url,
                        CreatorModel.myminifactory_url,
                    ).filter_by(name=creator_name).first()
                    if creator_name else None
                )
            self._panel_cache[model_id] = {
                "url":         (m.url         or "") if m else "",
                "description": (m.description or "") if m else "",
                "creator": {
                    "full_name":         (c.full_name         or "") if c else "",
                    "patreon_url":       (c.patreon_url       or "") if c else "",
                    "artstation_url":    (c.artstation_url    or "") if c else "",
                    "gumroad_url":       (c.gumroad_url       or "") if c else "",
                    "myminifactory_url": (c.myminifactory_url or "") if c else "",
                } if c else None,
            }

        cached = self._panel_cache[model_id]
        url         = cached["url"]
        description = cached["description"]
        creator_info = cached["creator"]

        def _row(label: str, value: str, is_link: bool = False) -> str:
            if not value:
                return ""
            val_html = (
                f'<a href="{escape(value)}">{escape(value)}</a>' if is_link
                else escape(value)
            )
            return f"<tr><td style='color:gray;white-space:nowrap;padding-right:6px'>{escape(label)}</td><td>{val_html}</td></tr>"

        rows: list[str] = []
        rows.append(_row("Title",         row_data["title"]))
        rows.append(_row("Creator",       row_data["creator"]))
        if creator_info:
            if creator_info["full_name"]:
                rows.append(_row("Full name",   creator_info["full_name"]))
            if creator_info["patreon_url"]:
                rows.append(_row("Patreon",     creator_info["patreon_url"], is_link=True))
            if creator_info["artstation_url"]:
                rows.append(_row("ArtStation",  creator_info["artstation_url"], is_link=True))
            if creator_info["gumroad_url"]:
                rows.append(_row("Gumroad",     creator_info["gumroad_url"], is_link=True))
            if creator_info["myminifactory_url"]:
                rows.append(_row("MyMiniFactory", creator_info["myminifactory_url"], is_link=True))
        rows.append(_row("Source",        row_data["source"]))
        rows.append(_row("Date",          row_data["date"]))
        rows.append(_row("Sculptor",      row_data["sculptor"]))
        rows.append(_row("Collection",    row_data["collection"]))
        rows.append(_row("Subcollection", row_data["subcollection"]))
        rows.append(_row("Category",      row_data["category"]))
        rows.append(_row("Release",       row_data["release"]))
        rows.append(_row("Scale",         row_data["scale"]))
        rows.append(_row("Presupported",  row_data["presupports"]))
        rows.append(_row("Sequence",      row_data["sequence"]))
        rows.append(_row("Tags",          row_data["tags"]))
        rows.append(_row("URL",           url, is_link=bool(url)))
        rows.append(_row("File size",     row_data["filesize"]))
        rows.append(_row("Compression",   row_data["compression"]))
        rows.append(_row("Added",         row_data["added"]))
        rows.append(_row("Path",          row_data["path"]))
        if row_data["warning"]:
            rows.append(_row("Warning",   row_data["warning"]))
        if description:
            rows.append(_row("Description", description))

        body = "".join(r for r in rows if r)
        html = f"<table style='font-size:small'>{body}</table>"
        self._info_lbl.setText(html)
        self._creator_panel.setVisible(self._info_pane_enabled)

    def _open_creator_editor(self, creator_name: str) -> None:
        from app.ui.creator_editor_dialog import CreatorEditorDialog
        dlg = CreatorEditorDialog(creator_name, parent=self)
        if dlg.exec() and self._current_model_id is not None and self._info_pane_enabled:
            self._panel_cache.pop(self._current_model_id, None)
            src_index = self._proxy.mapToSource(
                self._table.selectionModel().currentIndex()
            )
            r = src_index.row()
            if r >= 0:
                self._update_model_info_panel(self._current_model_id, self._row_data_at(r))

    def _open_author_editor(self) -> None:
        from app.ui.author_editor_dialog import AuthorEditorDialog
        dlg = AuthorEditorDialog(parent=self)
        dlg.exec()
        self.refresh()

    def _on_double_click(self, index) -> None:
        if index.column() != COL_PATH:
            return  # let the cell editor handle it
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._open_editor(model_id)

    def _on_table_click(self, index) -> None:
        if index.column() != COL_WARNING:
            return
        src_index = self._proxy.mapToSource(index)
        warning_item = self._model.item(src_index.row(), COL_WARNING)
        if not warning_item or not warning_item.text():
            return
        warning_text = warning_item.data(Qt.ItemDataRole.ToolTipRole) or ""
        if not warning_text:
            return
        archive_path = self._model.item(src_index.row(), COL_PATH).text()
        self._show_warning_detail(archive_path, warning_text)

    def _show_warning_detail(self, archive_path: str, warning_text: str) -> None:
        import re as _re
        from pathlib import Path as _P
        from app.crawler.archive import list_contents
        from app.constants import ARCHIVE_SUFFIXES
        from app.ui.config_dialog import get_presupport_patterns

        path = _P(archive_path)
        names = list_contents(path) if path.exists() else []
        warnings = [w.strip() for w in warning_text.split("\n") if w.strip()]

        _presupport_rxs = []
        for p in get_presupport_patterns():
            try:
                _presupport_rxs.append(_re.compile(p, _re.IGNORECASE))
            except _re.error:
                pass

        sections: list[tuple[str, list[str]]] = []
        for w in warnings:
            if w in ("Models with presupported stl", "Archive with supports"):
                matched = [n.replace("\\", "/") for n in names
                           if any(rx.search(_P(n.replace("\\", "/")).name) for rx in _presupport_rxs)]
                sections.append((w, matched))
            elif w == "Archive without images":
                sections.append((w, []))
            elif w == "Nested archives":
                matched = [n.replace("\\", "/") for n in names
                           if _P(n.replace("\\", "/")).suffix.lower() in ARCHIVE_SUFFIXES]
                sections.append((w, matched))
            else:
                sections.append((w, []))

        dlg = QDialog(self)
        dlg.setWindowTitle("Warning Details")
        dlg.setMinimumSize(480, 300)
        layout = QVBoxLayout(dlg)

        header = QLabel(f"<b>{path.name}</b>")
        header.setWordWrap(True)
        layout.addWidget(header)

        text = QTextEdit()
        text.setReadOnly(True)

        html_parts: list[str] = []
        for w, files in sections:
            html_parts.append(
                f'<p><span style="color:#ff4444;font-size:14px;">⚠</span>&nbsp;<b>{w}</b></p>'
            )
            if files:
                items = "".join(f"<li>{f}</li>" for f in files)
                html_parts.append(f'<ul style="margin-top:0">{items}</ul>')
            elif w == "Archive without images":
                html_parts.append(
                    '<p style="margin-left:20px;color:gray">'
                    "<i>No image files were found in the archive.</i></p>"
                )

        text.setHtml("".join(html_parts))
        layout.addWidget(text)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.exec()

    def _edit_selected(self) -> None:
        """Open the single-model editor or mass editor depending on selection count."""
        ids = self._selected_model_ids()
        if len(ids) == 1:
            self._open_editor(ids[0])
        elif len(ids) > 1:
            self._open_mass_editor(ids)

    def _mass_edit_selected(self) -> None:
        """Open the mass editor for all currently selected models (Ctrl+M)."""
        ids = self._selected_model_ids()
        if ids:
            self._open_mass_editor(ids)

    def _delete_selected_kb(self) -> None:
        ids = self._selected_model_ids()
        if ids:
            self._delete_models(ids)

    def _open_folder_selected_kb(self) -> None:
        indexes = self._table.selectedIndexes()
        if not indexes:
            return
        src_row = self._proxy.mapToSource(indexes[0]).row()
        self._open_containing_folder(src_row)

    def _show_files_kb(self) -> None:
        ids = self._selected_model_ids()
        if len(ids) == 1:
            self._show_files(ids[0])

    def _selected_model_ids(self) -> list[int]:
        rows = {self._proxy.mapToSource(idx).row()
                for idx in self._table.selectedIndexes()}
        return [
            self._model.item(r, COL_THUMB).data(MODEL_ID_ROLE)
            for r in sorted(rows)
            if self._model.item(r, COL_THUMB) is not None
        ]

    def _save_header_state(self) -> None:
        """Persist column widths, order, and visibility to QSettings."""
        settings = QSettings()
        settings.setValue("browser/header_state", self._table.horizontalHeader().saveState())
        settings.setValue("browser/header_column_count", self._model.columnCount())

    def _restore_header_state(self) -> None:
        """Restore previously saved column widths, order, and visibility."""
        settings = QSettings()
        state        = settings.value("browser/header_state")
        saved_count  = settings.value("browser/header_column_count", type=int)
        current_count = self._model.columnCount()

        if state and saved_count == current_count:
            self._table.horizontalHeader().restoreState(state)
            # Always keep COL_THUMB hidden after restoring
            self._table.setColumnHidden(COL_THUMB, True)
            # restoreState() preserves saved resize modes — re-assert Interactive so
            # columns that were previously Stretch remain manually resizable.
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Interactive)
        else:
            # Column layout changed — discard stale state and use defaults
            settings.remove("browser/header_state")
            settings.remove("browser/header_column_count")
            self._table.setColumnWidth(COL_TITLE, 260)
            self._table.setColumnWidth(COL_PATH,  320)
            self._table.setColumnHidden(COL_COMPRESSION, True)

    def _header_context_menu(self, pos) -> None:
        """Show a column-visibility toggle menu when the table header is right-clicked."""
        menu = QMenu(self)
        header = self._table.horizontalHeader()
        for col in range(1, self._model.columnCount()):  # col 0 (thumb) is always hidden
            label = _HEADERS[col]
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(col))
            action.setData(col)
        chosen = menu.exec(header.mapToGlobal(pos))
        if chosen is not None:
            col = chosen.data()
            header.setSectionHidden(col, not chosen.isChecked())
            self._save_header_state()
            _FILTER_COLS = (
                COL_CREATOR, COL_TAGS, COL_SOURCE, COL_PATH, COL_DATE,
                COL_SCULPTOR, COL_COLLECTION, COL_SUBCOLLECTION, COL_CATEGORY,
                COL_RELEASE, COL_SCALE, COL_PRESUPPORTED, COL_WARNING,
            )
            if col in _FILTER_COLS:
                self._populate_filter_tree()

    def _context_menu(self, pos) -> None:
        """Show the row-level context menu (edit, delete, thumbnails, sidecar, etc.)."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)

        selected_ids = self._selected_model_ids()

        style = QApplication.style()
        SP = style.StandardPixmap
        menu = QMenu(self)
        
        show_files_act = menu.addAction(style.standardIcon(SP.SP_FileDialogContentsView), "Show Files…")
        show_files_act.setVisible(len(selected_ids) == 1)
        open_folder_act = menu.addAction(style.standardIcon(SP.SP_DirOpenIcon), "Open Containing Folder")
        open_folder_act.setVisible(len(selected_ids) == 1)
        menu.addSeparator()

        edit_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Model…")
        edit_act.setVisible(len(selected_ids) == 1)
        mass_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), f"Mass Edit {len(selected_ids)} model(s)…")
        mass_act.setVisible(len(selected_ids) > 1)
        menu.addSeparator()

        _warn_px = style.standardIcon(SP.SP_MessageBoxWarning).pixmap(16, 16)
        _p = QPainter(_warn_px)
        _p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        _p.fillRect(_warn_px.rect(), QColor("red"))
        _p.end()
        delete_menu = menu.addMenu(QIcon(_warn_px), "Delete")

        delete_act = delete_menu.addAction(style.standardIcon(SP.SP_TrashIcon), f"Delete {len(selected_ids)} model(s) from DB…")

        from app.ui.config_dialog import get_allow_file_delete
        delete_file_act = None
        if get_allow_file_delete():
            delete_file_act = delete_menu.addAction(style.standardIcon(SP.SP_DialogDiscardButton), f"Delete {len(selected_ids)} archive file(s) from disk…")

        add_tag_act = None
        edit_tags_act = None
        edit_collections_act = None
        edit_subcollections_act = None
        edit_categories_act = None
        edit_scales_act = None
        edit_creator_act = None
        edit_authors_act = None
        src_col = self._proxy.mapToSource(index).column()
        if src_col == COL_CREATOR:
            menu.addSeparator()
            edit_creator_act = menu.addAction(
                style.standardIcon(SP.SP_FileDialogDetailedView),
                "Edit Creator Information…",
            )
            edit_authors_act = menu.addAction(
                style.standardIcon(SP.SP_FileDialogDetailedView),
                "Edit Authors…",
            )
        elif src_col == COL_TAGS:
            menu.addSeparator()
            add_tag_act = menu.addAction(style.standardIcon(SP.SP_FileDialogNewFolder), "Add Tag…")
            edit_tags_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Tags…")
        elif src_col == COL_COLLECTION:
            menu.addSeparator()
            edit_collections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Collections…")
        elif src_col == COL_SUBCOLLECTION:
            menu.addSeparator()
            edit_subcollections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Subcollections…")
        elif src_col == COL_CATEGORY:
            menu.addSeparator()
            edit_categories_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Categories…")
        elif src_col == COL_SCALE:
            menu.addSeparator()
            edit_scales_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Scales…")

        from app.ui.config_dialog import get_file_sorting_enabled
        sort_act = None
        if get_file_sorting_enabled():
            menu.addSeparator()
            sort_act = menu.addAction(
                style.standardIcon(SP.SP_DirOpenIcon),
                f"Sort {len(selected_ids)} file(s) into directory…",
            )

        menu.addSeparator()
        rescan_act = menu.addAction(style.standardIcon(SP.SP_BrowserReload), f"Rescan {len(selected_ids)} file(s)")
        thumb_act = menu.addAction(
            style.standardIcon(SP.SP_BrowserReload),
            f"Generate Thumbnails for {len(selected_ids)} model(s)…",
        )

        # Recrawl action: only enabled in debug mode
        recrawl_act = None
        from app.ui.config_dialog import get_debug_menu_enabled
        if get_debug_menu_enabled():
            recrawl_act = menu.addAction(style.standardIcon(SP.SP_BrowserReload), f"Recrawl {len(selected_ids)} model(s)…")

        from app.ui.config_dialog import get_metadata_sidecar_enabled
        sidecar_act = None
        if get_metadata_sidecar_enabled():
            sidecar_act = menu.addAction(
                style.standardIcon(SP.SP_DriveHDIcon),
                f"Create / Update Sidecar for {len(selected_ids)} model(s)…",
            )

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == show_files_act:
            self._show_files(model_id)
        elif action == open_folder_act:
            self._open_containing_folder(src_index.row())
        elif action == edit_act:
            self._open_editor(model_id)
        elif action == mass_act:
            self._open_mass_editor(selected_ids)
        elif action == delete_act:
            self._delete_models(selected_ids)
        elif delete_file_act and action == delete_file_act:
            self._delete_files_from_disk(selected_ids)
        elif sort_act and action == sort_act:
            self._sort_files(selected_ids)
        elif action == thumb_act:
            self._generate_thumbnails(selected_ids)
        elif action == rescan_act:
            self._rescan_files(selected_ids)
        elif recrawl_act and action == recrawl_act:
            self._recrawl_selection(selected_ids)
        elif sidecar_act and action == sidecar_act:
            self._create_sidecar_for_selection(selected_ids)
        elif add_tag_act and action == add_tag_act:
            self.add_tag()
        elif edit_tags_act and action == edit_tags_act:
            self._open_tag_editor()
        elif edit_collections_act and action == edit_collections_act:
            self._open_category_editor()
        elif edit_subcollections_act and action == edit_subcollections_act:
            self._open_subcategory_editor()
        elif edit_categories_act and action == edit_categories_act:
            self._open_category_editor(new_category=True)
        elif edit_scales_act and action == edit_scales_act:
            self._open_scale_editor()
        elif edit_creator_act and action == edit_creator_act:
            creator_name = self._model.item(src_index.row(), COL_CREATOR).text()
            if creator_name:
                self._open_creator_editor(creator_name)
        elif edit_authors_act and action == edit_authors_act:
            self._open_author_editor()

    def _show_files(self, model_id: int) -> None:
        from app.ui.file_list_dialog import FileListDialog
        FileListDialog(model_id, parent=self).exec()

    def _open_containing_folder(self, source_row: int) -> None:
        archive_path = self._model.item(source_row, COL_PATH).text()
        if not archive_path:
            return
        folder = Path(archive_path).parent
        from app.ui.config_dialog import open_folder_with_file_manager
        open_folder_with_file_manager(str(folder))

    def _recrawl_selection(self, model_ids: list[int]) -> None:
        """Run the crawler extraction for the selected model(s) and show a preview
        of the changes before applying them to the DB.

        This is synchronous and may be slow for many models – callers already
        invoke it from the UI thread via the context menu.
        """
        if not model_ids:
            return

        from app.ui.recrawl_dialog import RecrawlPreviewDialog
        from app.crawler.archive import extract_archive_contents, list_contents, archive_hash
        from app.crawler.metadata import extract_metadata
        from app.database.db import session_scope
        from app.config import CrawlerConfig

        changes_all: dict[int, list[tuple[str, str, str]]] = {}

        # For each selected model, open the archive, extract relevant files and
        # compute the metadata. Build a list of (field, current, new) tuples.
        with session_scope() as session:
            models = session.query(STLModel).filter(STLModel.id.in_(model_ids)).all()
            for m in models:
                p = m.archive_path
                try:
                    archive_path = Path(p)
                except Exception:
                    continue
                # Extract only the relevant files (no thumbnail generation here)
                contents = extract_archive_contents(archive_path, unrar_path=CrawlerConfig().unrar_path)
                meta = extract_metadata(archive_path, contents=contents, source_dir=archive_path.parent)

                # Compare fields and collect changes
                changes: list[tuple[str, str, str]] = []
                def _add_if_diff(field: str, current: str, new: str) -> None:
                    cur = "" if current is None else str(current)
                    nw = "" if new is None else str(new)
                    if cur != nw:
                        changes.append((field, cur, nw))

                _add_if_diff("Title", m.title, meta.title)
                _add_if_diff("Creator", m.creator, meta.creator)
                # ModelMetadata uses 'distributor' as the field name for the source/platform
                _add_if_diff("Source", m.source, meta.distributor)
                # release_date -> year/month
                if meta.release_date:
                    ry, rm = parse_release_date(meta.release_date)
                    new_date = format_release_date(ry, rm)
                else:
                    new_date = ""
                _add_if_diff("Release date", format_release_date(m.release_year, m.release_month), new_date)
                _add_if_diff("URL", m.url, meta.url)

                # Tags comparison – join into a comma-separated string for preview
                cur_tags = ", ".join(t.name for t in m.tags)
                new_tags = ", ".join(meta.tags)
                _add_if_diff("Tags", cur_tags, new_tags)

                # STL file list preview – show count change only
                cur_files = str(len(m.stl_files))
                new_files = str(len([n for n in (list_contents(archive_path) or []) if Path(n.replace("\\", "/")).suffix.lower() in {".stl", ".obj", ".lys", ".chitubox"}]))
                _add_if_diff("File list count", cur_files, new_files)

                changes_all[m.id] = changes

        # If no changes for any selected model, inform the user
        all_changes = [(f"ID {mid}",) + ch for mid, chlist in changes_all.items() for ch in chlist]
        # Flatten into (field, current, new) with model prefix in the field name
        flat: list[tuple[str, str, str]] = []
        for mid, chlist in changes_all.items():
            for field, cur, nw in chlist:
                flat.append((f"[{mid}] {field}", cur, nw))

        dlg = RecrawlPreviewDialog(f"Recrawl preview for {len(model_ids)} model(s)", flat, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # User accepted — apply changes by re-running the upsert logic for each archive
        # We'll reuse the crawler's _upsert_archive via a temporary CrawlerWorker instance
        from app.crawler.crawler import CrawlerWorker
        config = CrawlerConfig()
        worker = CrawlerWorker(str(Path(models[0].archive_path).parent), pattern="", config=config)

        # Run per-model upsert synchronously using session_scope and the crawler helper
        with session_scope() as session:
            for mid in model_ids:
                m = session.query(STLModel).get(mid)
                if not m:
                    continue
                archive_path = Path(m.archive_path)
                # compute hash and extract contents
                cur_hash = archive_hash(archive_path, unrar_path=config.unrar_path)
                data = extract_archive_contents(archive_path, unrar_path=config.unrar_path, all_names_out=None, stl_info_out=None)
                # call the private upsert method on the worker instance
                try:
                    worker._upsert_archive(archive_path, session, config.max_thumbnails, config.max_thumb_size, current_hash=cur_hash, existing=m, contents=data, all_names=None, stl_info=None)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    QMessageBox.warning(self, "Recrawl failed", f"Failed to recrawl {archive_path}: {exc}")
                    continue

        QMessageBox.information(self, "Recrawl complete", f"Recrawl applied for {len(model_ids)} model(s).")

    def _rescan_files(self, model_ids: list[int]) -> None:
        """Re-extract metadata for the selected files in a background thread.

        Shows a progress dialog with a 'Send to Background' button. When sent
        to background the thread keeps running and reports completion via the
        status bar instead of a modal dialog.
        """
        if not model_ids:
            return

        worker = RescanWorker(model_ids, parent=self)
        dlg    = RescanProgressDialog(worker, len(model_ids), parent=self)
        self._rescan_dialog = dlg

        worker.progress.connect(
            lambda curr, tot, _fn: self.rescan_progress.emit(curr, tot)
        )

        def _on_done(updated: int) -> None:
            self.rescan_finished.emit()
            self._rescan_dialog = None
            if dlg.in_background:
                sb = self.window().statusBar()
                if sb:
                    sb.showMessage(f"Rescan complete: {updated} model(s) updated.", 5000)
            else:
                QMessageBox.information(
                    self, "Rescan complete",
                    f"Rescan applied for {updated} model(s).",
                )
            self.refresh()

        def _on_error(filename: str, err: str) -> None:
            QMessageBox.warning(self, "Rescan failed", f"Failed to rescan {filename}:\n{err}")

        worker.finished_ok.connect(_on_done)
        worker.file_error.connect(_on_error)
        worker.start()
        dlg.exec()

    def _delete_files_from_disk(self, model_ids: list[int]) -> None:
        """Confirm with the user, then delegate to the file-deletion service.

        The slot itself stays UI-only: gather config, show the confirmation
        dialog, call :func:`app.services.file_deletion.delete_archives`, and
        report the result.  All file I/O and DB cascade handling lives in
        the service so it can be unit-tested without a ``QApplication``.
        """
        if not model_ids:
            return

        from app.ui.config_dialog import get_trash_folder_path, get_use_trash_folder
        from app.services.file_deletion import delete_archives

        use_trash  = get_use_trash_folder()
        trash_path = get_trash_folder_path()
        verb       = "Move to trash" if use_trash else "Permanently delete"

        reply = QMessageBox.warning(
            self,
            f"{verb} {len(model_ids)} archive(s)",
            f"Are you sure you want to <b>{verb.lower()}</b> the selected "
            f"archive file(s) from your disk?<br><br>"
            f"This will also remove them from the database.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            report = delete_archives(session, model_ids, use_trash, trash_path)

        self.refresh()
        if report.failed:
            QMessageBox.warning(
                self,
                "Delete Files",
                f"Successfully processed {report.deleted} file(s). "
                f"Failed to process {len(report.failed)} file(s).",
            )
        
    def _delete_models(self, model_ids: list[int]) -> None:
        """Confirm then delete models and their LMDB thumbnails from the database."""
        count = len(model_ids)
        reply = QMessageBox.warning(
            self,
            "Delete Model",
            f"Permanently delete <b>{count} model{'s' if count != 1 else ''}</b> "
            f"and their thumbnail files from the database?<br><br>This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.utils.thumbnailer import delete_thumbnail
        with session_scope() as session:
            for model_id in model_ids:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue
                for img in list(stl.images):
                    if img.thumbnail_path:
                        delete_thumbnail(img.thumbnail_path)
                session.delete(stl)

        self.refresh()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        # Listen to the scroll area resize, not just the viewport, as viewport resize
        # can be triggered by the scrollbar appearing/disappearing.
        if obj is self._thumb_scroll:
            etype = event.type()
            if etype == QEvent.Type.Resize:
                self._rescale_thumbnails()
            elif etype in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if self._current_model_id is not None:
                    mime = event.mimeData()
                    if mime.hasUrls() and any(
                        u.isLocalFile()
                        and FSPath(u.toLocalFile()).suffix.lower() in _IMAGE_DROP_EXTS
                        for u in mime.urls()
                    ):
                        event.acceptProposedAction()
                        return True
            elif etype == QEvent.Type.Drop:
                self._handle_image_drop(event)
                return True
        return super().eventFilter(obj, event)

    def _handle_image_drop(self, event) -> None:
        """Import dropped image files as thumbnails for the currently selected model."""
        if self._current_model_id is None:
            return
        paths = [
            FSPath(u.toLocalFile())
            for u in event.mimeData().urls()
            if u.isLocalFile()
            and FSPath(u.toLocalFile()).suffix.lower() in _IMAGE_DROP_EXTS
        ]
        if not paths:
            return
        errors: list[str] = []
        added = 0
        with session_scope() as session:
            for path in paths:
                try:
                    raw = path.read_bytes()
                except OSError:
                    errors.append(path.name)
                    continue
                img_rec = Image(model_id=self._current_model_id, filename=path.name)
                session.add(img_rec)
                session.flush()
                thumb_path = save_thumbnail(raw, img_rec.id)
                if thumb_path is None:
                    errors.append(path.name)
                    session.delete(img_rec)
                    continue
                img_rec.thumbnail_path = thumb_path
                added += 1
        if added:
            self._show_thumbnails(self._current_model_id)
        if errors:
            QMessageBox.warning(
                self, "Image import",
                "Could not import:\n" + "\n".join(errors),
            )

    def _clear_thumbnails(self) -> None:
        """Remove all thumbnail widgets from the sidebar and drop the pixmap cache."""
        self._thumb_pixmaps.clear()
        self._thumb_all_images.clear()
        self._thumb_id_to_slot.clear()
        self._thumb_page = 0
        self._thumb_show_all = False
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._thumb_page_row.setVisible(False)
        self._creator_panel.setVisible(False)

    def _show_thumbnails(self, model_id: int) -> None:
        """Load image records for *model_id* and display the first page."""
        self._clear_thumbnails()
        with session_scope() as session:
            rows = (
                session.query(Image.id, Image.thumbnail_path)
                .filter(Image.model_id == model_id)
                .order_by(Image.id)
                .all()
            )
        self._thumb_all_images = list(rows)
        self._render_thumb_page()

    def _render_thumb_page(self) -> None:
        """Populate the thumbnail panel for the current page (or all images)."""
        from app.ui.config_dialog import get_thumb_page_size
        page_size = get_thumb_page_size()
        total = len(self._thumb_all_images)

        if self._thumb_show_all or total <= page_size:
            images_to_show = self._thumb_all_images
        else:
            start = self._thumb_page * page_size
            images_to_show = self._thumb_all_images[start : start + page_size]

        # Clear existing widgets without resetting all_images / page state
        self._thumb_pixmaps.clear()
        self._thumb_id_to_slot.clear()
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        need_async: list[int] = []

        for slot, (image_id, thumbnail_path) in enumerate(images_to_show):
            cache_key = f"thumb:{image_id}"
            cached = QPixmapCache.find(cache_key) if thumbnail_path else None
            px = cached if cached is not None else QPixmap()

            self._thumb_pixmaps.append(px)
            self._thumb_id_to_slot[image_id] = slot

            if cached is None and thumbnail_path:
                need_async.append(image_id)

            lbl = ClickableLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFrameShape(QFrame.Shape.StyledPanel)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.clicked.connect(
                lambda _slot=slot: (
                    ImageViewerDialog(self._thumb_pixmaps[_slot], self).exec()
                    if not self._thumb_pixmaps[_slot].isNull() else None
                )
            )
            self._thumb_vlayout.addWidget(lbl)

        if need_async and self._current_model_id is not None:
            self._thumb_load_requested.emit(self._current_model_id, need_async)

        # Update / show pagination controls
        if total > page_size:
            if self._thumb_show_all:
                self._thumb_page_label.setText(f"All {total} images")
                self._thumb_prev_btn.setEnabled(False)
                self._thumb_next_btn.setEnabled(False)
                self._thumb_show_all_btn.setText("Paginate")
            else:
                num_pages = (total + page_size - 1) // page_size
                self._thumb_page_label.setText(f"Page {self._thumb_page + 1} / {num_pages}")
                self._thumb_prev_btn.setEnabled(self._thumb_page > 0)
                self._thumb_next_btn.setEnabled(self._thumb_page < num_pages - 1)
                self._thumb_show_all_btn.setText("Show All")
            self._thumb_page_row.setVisible(True)
        else:
            self._thumb_page_row.setVisible(False)

        self._rescale_thumbnails(force=True)

    def _thumb_prev_page(self) -> None:
        if self._thumb_page > 0:
            self._thumb_page -= 1
            self._render_thumb_page()
            self._thumb_scroll.verticalScrollBar().setValue(0)

    def _thumb_next_page(self) -> None:
        from app.ui.config_dialog import get_thumb_page_size
        page_size = get_thumb_page_size()
        num_pages = (len(self._thumb_all_images) + page_size - 1) // page_size
        if self._thumb_page < num_pages - 1:
            self._thumb_page += 1
            self._render_thumb_page()
            self._thumb_scroll.verticalScrollBar().setValue(0)

    def _thumb_toggle_show_all(self) -> None:
        self._thumb_show_all = not self._thumb_show_all
        self._thumb_page = 0
        self._render_thumb_page()
        self._thumb_scroll.verticalScrollBar().setValue(0)

    def _rescale_thumbnails(self, force: bool = False) -> None:
        """Scale all sidebar thumbnails to fit the current scroll-area width."""
        # Use the total scroll area width instead of viewport width to avoid
        # infinite resize loops (which happen when the scrollbar appearing/disappearing
        # changes the viewport width, which in turn changes the image height, etc.)
        w = max(self._thumb_scroll.width() - 36, 60)
        
        if not force and getattr(self, '_last_thumb_w', -1) == w:
            return
        self._last_thumb_w = w

        for i, px in enumerate(self._thumb_pixmaps):
            item = self._thumb_vlayout.itemAt(i)
            if item and item.widget():
                scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
                item.widget().setPixmap(scaled)

    def _on_thumb_bytes_ready(self, model_id: int, image_id: int, jpeg_bytes: bytes) -> None:
        """Receive JPEG bytes from the background loader and update the matching label."""
        if model_id != self._current_model_id:
            return
        slot = self._thumb_id_to_slot.get(image_id)
        if slot is None:
            return

        px = QPixmap()
        if jpeg_bytes:
            px.loadFromData(jpeg_bytes)
            QPixmapCache.insert(f"thumb:{image_id}", px)

        self._thumb_pixmaps[slot] = px

        item = self._thumb_vlayout.itemAt(slot)
        if item and item.widget():
            w = max(self._thumb_scroll.width() - 36, 60)
            scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
            item.widget().setPixmap(scaled)

    def _shutdown_thumb_loader(self) -> None:
        """Stop the background thumbnail thread cleanly on app exit."""
        self._thumb_thread.quit()
        self._thumb_thread.wait(2000)

    def add_tag(self) -> None:
        """Prompt for a new tag name, create it in the DB, and rebuild the filter tree."""
        name, ok = QInputDialog.getText(self, "Add Tag", "Tag name:")
        if not ok or not name.strip():
            return
        name = name.strip().lower()
        with session_scope() as session:
            if session.query(Tag).filter_by(name=name).first():
                QMessageBox.information(self, "Add Tag", f"Tag '{name}' already exists.")
                return
            session.add(Tag(name=name))
        self._populate_filter_tree()

    def _open_tag_editor(self) -> None:
        from app.ui.tag_editor_dialog import TagEditorDialog
        TagEditorDialog(parent=self).exec()
        self.refresh()

    def _open_category_editor(self, new_category: bool = False) -> None:
        if new_category:
            from app.ui.category_editor_dialog import CategoryEditorDialog
            CategoryEditorDialog(parent=self).exec()
        else:
            from app.ui.collection_editor_dialog import CollectionEditorDialog
            CollectionEditorDialog(parent=self).exec()
        self.refresh()

    def _open_subcategory_editor(self) -> None:
        from app.ui.subcollection_editor_dialog import SubcollectionEditorDialog
        SubcollectionEditorDialog(parent=self).exec()
        self.refresh()

    def _open_scale_editor(self) -> None:
        from app.ui.scale_editor_dialog import ScaleEditorDialog
        ScaleEditorDialog(parent=self).exec()
        self.refresh()

    def _open_editor(self, model_id: int) -> None:
        from app.ui.editor_dialog import EditorDialog
        dlg = EditorDialog(model_id, parent=self)
        if dlg.exec():
            self.refresh()

    def _open_mass_editor(self, model_ids: list[int]) -> None:
        from app.ui.mass_editor_dialog import MassEditorDialog
        dlg = MassEditorDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _sort_files(self, model_ids: list[int]) -> None:
        from app.ui.sort_dialog import SortFilesDialog
        dlg = SortFilesDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _generate_thumbnails(self, model_ids: list[int]) -> None:
        """Prompt for a thumbnail count then run ThumbnailGeneratorWorker on *model_ids*."""
        from app.ui.config_dialog import get_max_thumbnails

        count, ok = QInputDialog.getInt(
            self,
            "Generate Thumbnails",
            "Number of thumbnails to generate per model:",
            get_max_thumbnails(),
            1,
            100,
            1,
        )
        if not ok:
            return

        # Custom progress dialog with a "Send to background" button and Cancel
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar

        dlg = QDialog(self)
        dlg.setWindowTitle("Generate Thumbnails")
        dlg.setModal(True)
        vlay = QVBoxLayout(dlg)
        label = QLabel("Generating thumbnails…")
        vlay.addWidget(label)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, len(model_ids))
        progress_bar.setValue(0)
        vlay.addWidget(progress_bar)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        send_bg_btn = QPushButton("Send to background")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(send_bg_btn)
        btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)
        dlg.setMinimumWidth(420)

        self._thumb_worker = ThumbnailGeneratorWorker(model_ids, count, parent=self)
        # Prevent starting if another background job is active
        from app.utils.background_jobs import start_job, active_job
        if not start_job("thumbnails"):
            QMessageBox.warning(self, "Generate Thumbnails", "Another background job is already running. Try again later.")
            return

        def _on_progress(current, total):
            # Update our custom dialog progress
            progress_bar.setValue(current)
            label.setText(f"Processing model {current} of {total}…")
            # Forward progress for optional background display in main window
            self.thumbnail_progress_updated.emit(current, total)

        def _on_finished():
            progress_bar.setValue(len(model_ids))
            # Close the dialog if it's still open
            if dlg.isVisible():
                dlg.accept()
            self.refresh()
            self._thumb_worker = None
            from app.utils.background_jobs import end_job
            end_job("thumbnails")

        self._thumb_worker.progress.connect(_on_progress)
        self._thumb_worker.finished.connect(_on_finished)
        # Wire up buttons: Send to background hides the dialog; Cancel stops the worker
        def _send_background():
            dlg.hide()
            # show current progress in status bar via signal
            self.thumbnail_progress_updated.emit(progress_bar.value(), progress_bar.maximum())

        def _on_cancel():
            # Request worker to stop and close dialog
            if self._thumb_worker is not None:
                self._thumb_worker.stop()
            dlg.reject()
            from app.utils.background_jobs import end_job
            end_job("thumbnails")

        send_bg_btn.clicked.connect(_send_background)
        cancel_btn.clicked.connect(_on_cancel)

        self._thumb_worker.start()
        # Show modal dialog (blocks until accept/reject) — if Send to background was clicked
        # dlg.exec() will return but the worker continues running; if Cancel was clicked,
        # the worker is requested to stop.
        dlg.exec()

        # Ensure main window knows when the worker finishes
        self._thumb_worker.finished.connect(lambda: self.thumbnail_finished.emit())

    def generate_thumbnails_for_selected(self) -> None:
        """Generate thumbnails for the rows currently selected in the table.

        Called from the main-window Tools menu; mirrors the right-click action.
        """
        ids = self._selected_model_ids()
        if not ids:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Generate Thumbnails",
                "Select one or more models in the table first.",
            )
            return
        self._generate_thumbnails(ids)

    def _create_sidecar_for_selection(self, model_ids: list[int]) -> None:
        """Open a sidecar dialog scoped to the folders of the selected models."""
        from pathlib import Path
        from app.ui.metadata_sidecar_dialog import MetadataSidecarDialog

        with session_scope() as session:
            rows = (
                session.query(STLModel.archive_path)
                .filter(STLModel.id.in_(model_ids))
                .all()
            )

        folders = {Path(r.archive_path).parent for r in rows if r.archive_path}
        if not folders:
            QMessageBox.warning(self, "Create Sidecar", "No valid file paths found for the selected models.")
            return

        dlg = MetadataSidecarDialog(folders=folders, parent=self)
        dlg.exec()

    def show_image_grid(self) -> None:
        """Open an image grid for all models currently visible in the table."""
        model_ids = []
        for proxy_row in range(self._proxy.rowCount()):
            src_row = self._proxy.mapToSource(self._proxy.index(proxy_row, 0)).row()
            item = self._model.item(src_row, COL_THUMB)
            if item is not None:
                mid = item.data(MODEL_ID_ROLE)
                if mid is not None:
                    model_ids.append(mid)
        from app.ui.image_grid_dialog import ImageGridDialog
        dlg = ImageGridDialog(model_ids, parent=self)
        dlg.show_selection_requested.connect(self.show_model_ids)
        dlg.exec()
