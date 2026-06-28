"""Paged, SQL-backed table model for the browser and its loader workers.

Extracted from ``browser_tab.py``. ``_SqlTableModel`` keeps the sorted list of
matching model IDs in memory and lazily fetches row data one page at a time:
``_IdLoaderThread`` streams the ID list in the background and
``_PageFetchRunnable`` fetches a single page (with its tags/scales) off the GUI
thread. Only ``_MAX_PAGES`` pages are resident at once (LRU eviction).
"""
from PyQt6.QtCore import (
    Qt, QObject, QThread, QThreadPool, QRunnable, QModelIndex,
    QAbstractTableModel, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont

from app.database.models import model_tags as _mt_global
from app.utils.path_pattern import format_release_date
from app.ui.browser_common import (
    COL_THUMB, COL_TITLE, COL_CREATOR, COL_SOURCE, COL_DATE, COL_TAGS,
    COL_SCULPTOR, COL_COLLECTION, COL_SUBCOLLECTION, COL_SEQUENCE, COL_RELEASE,
    COL_PATH, COL_SCALE, COL_PRESUPPORTED, COL_ADDED, COL_WARNING, COL_FILESIZE,
    COL_CATEGORY, COL_COMPRESSION, _HEADERS, _fmt_size, MODEL_ID_ROLE, _pack_row,
)


class _IdLoaderThread(QThread):
    ids_ready       = pyqtSignal(list)   # complete sorted list[int]
    finished_signal = pyqtSignal(int)    # total count

    def __init__(self, id_stmt, pair_order=None, parent=None):
        super().__init__(parent)
        self._id_stmt    = id_stmt
        self._pair_order = pair_order
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        from app.database.db import get_session
        session = get_session()
        try:
            if self._pair_order is not None:
                all_ids = [row[0] for row in session.execute(self._id_stmt).all()]
                all_ids.sort(key=lambda i: self._pair_order.get(i, len(self._pair_order)))
            else:
                all_ids = []
                result = session.execute(
                    self._id_stmt.execution_options(yield_per=2000)
                )
                for row in result:
                    if self._stop:
                        break
                    all_ids.append(row[0])
            if not self._stop:
                self.ids_ready.emit(all_ids)
                self.finished_signal.emit(len(all_ids))
        finally:
            session.close()


class _PageFetchRunnable(QRunnable):
    class _Signals(QObject):
        page_ready = pyqtSignal(int, list)   # page_idx, rows

    def __init__(self, page_idx: int, ids: list, pair_for_id: dict):
        super().__init__()
        self.signals      = self._Signals()
        self._page_idx    = page_idx
        self._ids         = ids
        self._pair_for_id = pair_for_id
        self.setAutoDelete(True)

    def run(self):
        from app.database.db import get_session, get_models_view
        from app.database.models import Tag, Scale, model_scales as _ms
        from sqlalchemy import select as sa_sel, func
        session = get_session()
        try:
            mv   = get_models_view()
            ids  = self._ids
            stmt = sa_sel(
                mv.c.id, mv.c.title, mv.c.creator, mv.c.source,
                mv.c.release_year, mv.c.release_month,
                mv.c.archive_path, mv.c.presupports, mv.c.warning,
                mv.c.archive_size, mv.c.compression_rate,
                mv.c.sequence, mv.c.created_at,
                mv.c.sculptor_name, mv.c.collection_name,
                mv.c.subcollection_name, mv.c.category_name,
                mv.c.release_name,
            ).select_from(mv).where(mv.c.id.in_(ids))
            rows_by_id = {row.id: row for row in session.execute(stmt).all()}

            tags_stmt = (
                sa_sel(_mt_global.c.model_id, func.group_concat(Tag.name, ", "))
                .join(Tag, _mt_global.c.tag_id == Tag.id)
                .where(_mt_global.c.model_id.in_(ids))
                .group_by(_mt_global.c.model_id)
            )
            tags_for = {mid: ts or "" for mid, ts in session.execute(tags_stmt).all()}

            scales_stmt = (
                sa_sel(_ms.c.model_id, func.group_concat(Scale.name, ", "))
                .join(Scale, _ms.c.scale_id == Scale.id)
                .where(_ms.c.model_id.in_(ids))
                .group_by(_ms.c.model_id)
            )
            scales_for = {mid: ss or "" for mid, ss in session.execute(scales_stmt).all()}

            pair_for_id = self._pair_for_id
            packed = [
                _pack_row(rows_by_id[mid], tags_for, scales_for, pair_for_id)
                for mid in ids if mid in rows_by_id
            ]
            self.signals.page_ready.emit(self._page_idx, packed)
        except Exception:
            self.signals.page_ready.emit(self._page_idx, [])
        finally:
            session.close()


class _SqlTableModel(QAbstractTableModel):
    """Memory-efficient paged table model backed by SQLite.

    Keeps sorted model IDs in memory (~4 MB for 500K models) and fetches
    row data in pages via QThreadPool on demand.  Only _MAX_PAGES pages are
    resident at once; older pages are evicted via LRU.
    """
    _PAGE_SIZE = 200
    _MAX_PAGES = 60   # 60 x 200 = 12 000 rows resident at most

    load_started  = pyqtSignal()
    load_finished = pyqtSignal(int)        # total visible rows
    cell_edited   = pyqtSignal(int, int, str)  # row, col, new_text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ordered_ids: list              = []
        self._page_cache:  dict              = {}
        self._page_lru:    list              = []
        self._pending_pages: set             = set()
        self._pair_for_id: dict              = {}
        self._pair_colors                    = None
        self._warn_bg                        = None
        self._warn_fg                        = None
        self._warn_font                      = None
        self._main_stmt                      = None
        self._pair_order                     = None
        self._sort_col: int                  = COL_TITLE
        self._sort_order                     = Qt.SortOrder.AscendingOrder
        self._search_text: str               = ""
        self._id_loader                      = None

    # -- QAbstractTableModel interface ------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ordered_ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() not in (COL_THUMB, COL_ADDED, COL_WARNING,
                                  COL_FILESIZE, COL_COMPRESSION):
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._ordered_ids):
            return None
        if role == MODEL_ID_ROLE:
            return self._ordered_ids[row]
        page_idx = row // self._PAGE_SIZE
        page = self._page_cache.get(page_idx)
        if page is None:
            self._request_page(page_idx)
            return "" if role == Qt.ItemDataRole.DisplayRole and col != COL_THUMB else None
        offset = row % self._PAGE_SIZE
        if offset >= len(page):
            return None
        return self._role_data(page[offset], col, role)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        row, col = index.row(), index.column()
        if col in (COL_THUMB, COL_ADDED, COL_WARNING, COL_FILESIZE, COL_COMPRESSION):
            return False
        text = str(value).strip() if value is not None else ""
        # Optimistically update cache
        page_idx = row // self._PAGE_SIZE
        offset   = row % self._PAGE_SIZE
        page = self._page_cache.get(page_idx)
        if page is not None and offset < len(page):
            _TI = {
                COL_TITLE: 1, COL_CREATOR: 2, COL_SOURCE: 3, COL_TAGS: 19,
                COL_SCULPTOR: 13, COL_COLLECTION: 14, COL_SUBCOLLECTION: 15,
                COL_CATEGORY: 16, COL_SEQUENCE: 11, COL_RELEASE: 17,
                COL_PATH: 6, COL_SCALE: 18, COL_PRESUPPORTED: 7,
            }
            ti = _TI.get(col)
            if ti is not None:
                t = list(page[offset])
                t[ti] = text if text else None
                page[offset] = tuple(t)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self.cell_edited.emit(row, col, text)
        return True

    def sort(self, column: int, order) -> None:
        if self._main_stmt is None or self._pair_order is not None:
            return
        self._sort_col   = column
        self._sort_order = order
        self._do_reload()

    # -- Public API -------------------------------------------------------

    def model_id_for_row(self, row: int):
        return self._ordered_ids[row] if 0 <= row < len(self._ordered_ids) else None

    def all_visible_ids(self) -> list:
        return list(self._ordered_ids)

    def load(self, main_stmt, pair_order=None, pair_for_id=None, pair_colors=None) -> None:
        self._main_stmt   = main_stmt
        self._pair_order  = pair_order
        self._pair_for_id = pair_for_id or {}
        self._pair_colors = pair_colors
        from app.ui.config_dialog import get_warn_row_bg_color, get_warn_row_fg_color
        self._warn_bg   = QColor(get_warn_row_bg_color())
        self._warn_fg   = QColor(get_warn_row_fg_color())
        self._warn_font = QFont()
        self._warn_font.setPointSize(16)
        self._warn_font.setBold(True)
        self._do_reload()

    def set_search(self, text: str) -> None:
        if text == self._search_text:
            return
        self._search_text = text
        if self._main_stmt is not None:
            self._do_reload()

    def stop(self) -> None:
        if self._id_loader is not None:
            self._id_loader.stop()
            self._id_loader = None

    # -- Internal ---------------------------------------------------------

    def _do_reload(self) -> None:
        if self._id_loader is not None:
            self._id_loader.stop()
            self._id_loader = None
        self._pending_pages.clear()
        self.beginResetModel()
        self._ordered_ids = []
        self._page_cache  = {}
        self._page_lru    = []
        self.endResetModel()
        self.load_started.emit()
        id_stmt = self._build_id_stmt()
        loader  = _IdLoaderThread(id_stmt, self._pair_order, parent=self)
        loader.ids_ready.connect(self._on_ids_ready)
        loader.finished_signal.connect(self._on_load_finished)
        self._id_loader = loader
        loader.start()

    def _build_id_stmt(self):
        from app.database.db import get_models_view
        from sqlalchemy import select as sa_sel
        mv      = get_models_view()
        id_stmt = sa_sel(mv.c.id).select_from(mv)
        if self._main_stmt is not None and self._main_stmt.whereclause is not None:
            id_stmt = id_stmt.where(self._main_stmt.whereclause)
        if self._search_text and self._pair_order is None:
            id_stmt = self._apply_search_where(id_stmt, mv)
        if self._pair_order is None:
            id_stmt = id_stmt.order_by(self._sort_expr(mv))
        return id_stmt

    def _apply_search_where(self, stmt, mv):
        from sqlalchemy import or_, select as sa_sel
        from app.database.models import Tag
        pat     = f"%{self._search_text}%"
        tag_sub = (
            sa_sel(_mt_global.c.model_id)
            .join(Tag, _mt_global.c.tag_id == Tag.id)
            .where(Tag.name.ilike(pat))
        )
        return stmt.where(or_(
            mv.c.title.ilike(pat),
            mv.c.creator.ilike(pat),
            mv.c.sculptor_name.ilike(pat),
            mv.c.collection_name.ilike(pat),
            mv.c.archive_path.ilike(pat),
            mv.c.id.in_(tag_sub),
        ))

    def _sort_expr(self, mv):
        from sqlalchemy import asc, desc, nullslast
        _COLS = {
            COL_TITLE:         mv.c.title,
            COL_CREATOR:       mv.c.creator,
            COL_SOURCE:        mv.c.source,
            COL_SCULPTOR:      mv.c.sculptor_name,
            COL_COLLECTION:    mv.c.collection_name,
            COL_SUBCOLLECTION: mv.c.subcollection_name,
            COL_SEQUENCE:      mv.c.sequence,
            COL_RELEASE:       mv.c.release_name,
            COL_PATH:          mv.c.archive_path,
            # COL_SCALE is many-to-many (not in the view) — not sortable, like Tags.
            COL_PRESUPPORTED:  mv.c.presupports,
            COL_ADDED:         mv.c.created_at,
            COL_FILESIZE:      mv.c.archive_size,
            COL_COMPRESSION:   mv.c.compression_rate,
            COL_CATEGORY:      mv.c.category_name,
        }
        col_expr = _COLS.get(self._sort_col, mv.c.title)
        fn = desc if self._sort_order == Qt.SortOrder.DescendingOrder else asc
        return nullslast(fn(col_expr))

    def _request_page(self, page_idx: int) -> None:
        if page_idx in self._pending_pages:
            return
        self._pending_pages.add(page_idx)
        start    = page_idx * self._PAGE_SIZE
        end      = min(start + self._PAGE_SIZE, len(self._ordered_ids))
        ids      = self._ordered_ids[start:end]
        runnable = _PageFetchRunnable(page_idx, ids, self._pair_for_id)
        runnable.signals.page_ready.connect(self._on_page_ready)
        QThreadPool.globalInstance().start(runnable)

    def _on_ids_ready(self, ids: list) -> None:
        if ids:
            self.beginInsertRows(QModelIndex(), 0, len(ids) - 1)
            self._ordered_ids = ids
            self.endInsertRows()
            self._request_page(0)
        else:
            self._ordered_ids = []

    def _on_load_finished(self, total: int) -> None:
        self._id_loader = None
        self.load_finished.emit(total)

    def _on_page_ready(self, page_idx: int, rows: list) -> None:
        self._pending_pages.discard(page_idx)
        if page_idx in self._page_lru:
            self._page_lru.remove(page_idx)
        self._page_lru.append(page_idx)
        while len(self._page_lru) > self._MAX_PAGES:
            old = self._page_lru.pop(0)
            self._page_cache.pop(old, None)
        self._page_cache[page_idx] = rows
        start = page_idx * self._PAGE_SIZE
        end   = start + len(rows) - 1
        if start <= end:
            self.dataChanged.emit(
                self.index(start, 0),
                self.index(end, self.columnCount() - 1),
            )

    def _role_data(self, t: tuple, col: int, role: int):
        # t layout: 0=id,1=title,2=creator,3=source,4=yr,5=mo,6=archive_path,
        #           7=presupports,8=warning,9=archive_size,10=compression_rate,
        #           11=sequence,12=created_at,13=sculptor_name,14=collection_name,
        #           15=subcollection_name,16=category_name,17=release_name,
        #           18=scales_str,19=tags_str,20=pair_idx
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_THUMB:         return None
            if col == COL_TITLE:         return t[1] or ""
            if col == COL_CREATOR:       return t[2] or ""
            if col == COL_SOURCE:        return t[3] or ""
            if col == COL_DATE:          return format_release_date(t[4], t[5])
            if col == COL_TAGS:          return t[19] or ""
            if col == COL_SCULPTOR:      return t[13] or ""
            if col == COL_COLLECTION:    return t[14] or ""
            if col == COL_SUBCOLLECTION: return t[15] or ""
            if col == COL_SEQUENCE:      return str(t[11]) if t[11] is not None else ""
            if col == COL_RELEASE:       return t[17] or ""
            if col == COL_PATH:          return t[6] or ""
            if col == COL_SCALE:         return t[18] or ""
            if col == COL_PRESUPPORTED:  return t[7] or ""
            if col == COL_ADDED:         return t[12].strftime("%Y-%m-%d %H:%M") if t[12] else ""
            if col == COL_WARNING:       return "⚠" if t[8] else ""
            if col == COL_FILESIZE:
                raw = t[9] if t[9] is not None else -1
                return _fmt_size(raw) if raw >= 0 else ""
            if col == COL_CATEGORY:      return t[16] or ""
            if col == COL_COMPRESSION:
                cr = t[10]
                return f"{1/cr:.1f}x" if cr and cr > 0 else ""
            return ""
        if role == Qt.ItemDataRole.UserRole:
            if col == COL_SEQUENCE:
                try:    return int(t[11]) if t[11] is not None else -1
                except: return -1
            if col == COL_FILESIZE:
                return t[9] if t[9] is not None else -1
            if col == COL_COMPRESSION:
                return t[10] if t[10] else -1.0
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return t[8] or None if col == COL_WARNING else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_WARNING, COL_COMPRESSION):
                return Qt.AlignmentFlag.AlignCenter
            return None
        if role == Qt.ItemDataRole.BackgroundRole:
            if self._pair_colors is not None and t[20] >= 0:
                return self._pair_colors[t[20] % 2]
            if t[8]:
                return self._warn_bg
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._warn_fg if t[8] else None
        if role == Qt.ItemDataRole.FontRole:
            return self._warn_font if (t[8] and col == COL_WARNING) else None
        if role == MODEL_ID_ROLE:
            return t[0]
        return None
