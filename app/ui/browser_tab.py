"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, category, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
import logging
from pathlib import Path as FSPath, Path

logger = logging.getLogger(__name__)

from app.utils.material_icons import lookup_icon
from PyQt6.QtCore import (
    Qt, QEvent, pyqtSignal, QTimer, QThread, QThreadPool, QBuffer, QByteArray,
    QIODevice, QSize,
)
from PyQt6.QtGui import (
    QPixmap, QPixmapCache, QIcon, QKeySequence, QShortcut, QColor, QPainter, QMovie,
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QDialog, QApplication, QPushButton,
    QProgressBar,
    QTextEdit, QDialogButtonBox, QComboBox,
)
from PyQt6.QtCore import QSettings


from app.ui import lookup_cache as _lookup_cache
from app.ui.thumb_loader import ThumbnailLoaderWorker
from app.ui.browser_table_model import _SqlTableModel
from app.ui.browser_filter_tree import FilterTreeWidget, _FilterTreeWorker
from app.ui.browser_jobs import (
    _CellEditRunnable, _ImageDropRunnable, _WarningDetailRunnable, RescanWorker,
    RescanProgressDialog, ThumbnailGeneratorWorker,
)
from app.ui import sidecar_exporter
from app.services.library_query import (
    FilterSpec, GROUPS as FILTER_GROUPS,
)
from app.ui.browser_dialogs import ClickableLabel, ImageViewerDialog
from app.ui.browser_delegates import (
    SourceDelegate, SculptorDelegate, CollectionDelegate, SubcollectionDelegate,
    CategoryDelegate, ReleaseDelegate, ScaleDelegate, PresupportsDelegate, TagDelegate,
)
from app.ui.browser_common import (
    _IMAGE_DROP_EXTS,
    COL_THUMB, COL_TITLE, COL_CREATOR, COL_SOURCE, COL_DATE, COL_TAGS,
    COL_SCULPTOR, COL_COLLECTION, COL_SUBCOLLECTION, COL_SEQUENCE, COL_RELEASE,
    COL_PATH, COL_SCALE, COL_PRESUPPORTED, COL_ADDED, COL_WARNING, COL_FILESIZE,
    COL_CATEGORY, COL_COMPRESSION, COL_TO_PRINT, _HEADERS,
    _fmt_size,
)
from app.utils.path_pattern import parse_release_date, format_release_date


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
        self._load_pair_colors: list | None = None  # QColor list for dup-pair mode
        self._rescan_dialog: RescanProgressDialog | None = None
        self._current_model_id: int | None = None
        self._info_pane_enabled: bool = False
        self._thumb_pixmaps: list[QPixmap] = []      # unscaled pixmaps for current page (by slot)
        self._thumb_all_images: list = []             # list of (image_id, thumbnail_path) tuples
        self._thumb_id_to_slot: dict[int, int] = {}  # image_id → slot index in current page
        self._thumb_movies: dict[int, QMovie] = {}   # slot → running QMovie (animated GIFs only)
        self._thumb_gif_data: dict[int, bytes] = {}  # image_id → GIF bytes, so paging back re-animates
        self._thumb_page: int = 0
        self._thumb_show_all: bool = False

        # --- Background thumbnail loader ---
        QPixmapCache.setCacheLimit(64 * 1024)  # 64 MB Qt pixmap cache
        # Long-lived loader for the image pane.  Thumbnail *generation* uses
        # its own attribute (_thumb_gen_worker): reusing this one used to drop
        # the only Python reference to the loader after a generation run.
        self._thumb_gen_worker = None
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
        # Debounce rapid cell edits: keyed by (model_id, field) → text.  Keyed
        # by model id, not row, so a table reload during the debounce cannot
        # redirect an edit to another model.  Written on a one-thread pool so
        # batches commit in order.
        self._pending_edits: dict[tuple[int, str], str] = {}
        self._edit_pool = QThreadPool(self)
        self._edit_pool.setMaxThreadCount(1)
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(400)  # ms
        self._edit_timer.timeout.connect(self._flush_pending_edits)
        # Debounce search-box keystrokes to avoid scanning on every character
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)  # ms
        self._build_ui()
        self._search_timer.timeout.connect(
            lambda: self._on_search(self._search_combo.lineEdit().text())
        )
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
        filter_header.addWidget(QLabel(self.tr("Filter")))
        filter_header.addStretch()
        self._apply_filters_btn = QPushButton(self.tr("Apply"))
        self._apply_filters_btn.setToolTip(self.tr("Apply the selected filters to the table"))
        self._apply_filters_btn.clicked.connect(self._apply_checked_filters)
        filter_header.addWidget(self._apply_filters_btn)
        self._clear_filters_btn = QPushButton(self.tr("Clear"))
        self._clear_filters_btn.setFlat(True)
        self._clear_filters_btn.setToolTip(self.tr("Clear all active filters"))
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
        search_row.addWidget(QLabel(self.tr("Search:")))
        self._search_combo = QComboBox()
        self._search_combo.setEditable(True)
        self._search_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._search_combo.setPlaceholderText(self.tr("Title, creator or tag…"))
        self._search_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self._search_combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # Connect both combobox editing and selection
        self._search_combo.lineEdit().textChanged.connect(lambda _: self._search_timer.start())
        self._search_combo.activated.connect(self._on_search_selected)
        self._load_search_history()
        search_row.addWidget(self._search_combo)
        search_row.addStretch()
        centre_layout.addLayout(search_row)

        self._model = _SqlTableModel(parent=self)
        self._model.load_started.connect(self._on_load_started)
        self._model.load_finished.connect(self._on_load_finished_model)
        self._model.cell_edited.connect(self._on_cell_edited)

        self._table = QTableView()
        self._table.setModel(self._model)
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
        img_layout.addWidget(QLabel(self.tr("Images")))

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
        self._thumb_show_all_btn = QPushButton(self.tr("Show All"))
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
        """Reload all data from the database, preserving the active filters.

        ``_apply_checked_filters`` reads the current filter-tree checkboxes (still
        intact at this point), reloads the table with those filters, and then
        rebuilds the filter tree — restoring the checked/excluded state from the
        fresh data.  We must NOT call ``_populate_filter_tree`` first: it clears
        the tree synchronously and rebuilds it on a background thread, so the
        immediate ``_apply_checked_filters`` would otherwise read an empty tree
        and silently drop every active filter.
        """
        from app.database.filter_cache import invalidate_filter_cache
        invalidate_filter_cache()
        _lookup_cache.invalidate()
        self._panel_cache.clear()
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
            r = self._table.selectionModel().currentIndex().row()
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
    def _populate_filter_tree(self, spec: FilterSpec | None = None) -> None:
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
            spec=spec,
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
        _is_dark = "dark" in _get_cs().lower()

        def _icon(name: str):
            return lookup_icon(name, is_dark_mode=_is_dark)

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
            "release_id", "scale_id", "presupports", "warning", "filetype", "path", "group",
        )
        _SCOPED_GROUPS = (
            "creator", "source", "tag", "date", "sculptor",
            "collection", "subcollection", "category", "release",
            "scale", "presupports", "warning", "filetype", "path",
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
                "Archive without printable files",
                "Unreadable archive",
            ]
            warn_item = _make_leaf("Warnings", ("group", "warning"), _c(("group", "warning")))
            warn_item.setIcon(0, _icon_warnings)
            self._filter_tree.addTopLevelItem(warn_item)
            for wtype in _WARNING_TYPES:
                warn_item.addChild(_make_leaf(wtype, ("warning", wtype), _c(("warning", wtype))))

        if payload.get("filetypes"):
            ft_item = _make_leaf(
                self.tr("File Type"), ("group", "filetype"), _c(("group", "filetype"))
            )
            ft_item.setIcon(0, _icon_path)
            self._filter_tree.addTopLevelItem(ft_item)
            for ext in payload["filetypes"]:
                ft_item.addChild(_make_leaf(ext, ("filetype", ext), _c(("filetype", ext))))
            if ("filetype" in (expanded_sections or set())
                    or any(d[0] == "filetype" for d in checked_data)
                    or any(d[0] == "filetype" for d in excluded_data)):
                ft_item.setExpanded(True)

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
        self._load_table(FilterSpec(ids=list(ids)))
        if self._model.rowCount() > 0:
            self._table.setCurrentIndex(self._model.index(0, 0))

    def show_duplicate_pairs(self, pairs: list) -> None:
        """Show duplicate pairs sorted by pair and color-coded with alternating colors."""
        all_ids = list({pid for pair in pairs for pid in pair})
        self._load_table(FilterSpec(ids=all_ids), duplicate_pairs=pairs)
        if self._model.rowCount() > 0:
            self._table.setCurrentIndex(self._model.index(0, 0))

    def _load_table(
        self,
        spec: FilterSpec | None = None,
        *,
        duplicate_pairs: list[list[int]] | None = None,
    ) -> None:
        """Reload the table for *spec* (``None`` = everything).

        The paged model gets the matching ids from
        :func:`app.services.library_query.table_ids` in a background thread (the
        same predicates scope the filter-pane counts) and its rows a page at a
        time from :func:`~app.services.library_query.page_rows`.
        """
        spec = spec or FilterSpec()

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

        self.loading_started.emit()
        self._loading = True
        self._model.load(spec, pair_order, pair_for_id, self._load_pair_colors)

    # ------------------------------------------------------------------
    def _on_load_started(self) -> None:
        self._loading = True

    def _on_load_finished_model(self, total: int) -> None:
        self._loading = False
        self.model_count_changed.emit(total)

    # Table column → field name understood by app.services.editing.apply_cell_edit
    _EDITABLE_FIELDS = {
        COL_TITLE: "title", COL_CREATOR: "creator", COL_SOURCE: "source",
        COL_DATE: "date", COL_TAGS: "tags", COL_SCULPTOR: "sculptor",
        COL_COLLECTION: "collection", COL_SUBCOLLECTION: "subcollection",
        COL_CATEGORY: "category", COL_SEQUENCE: "sequence", COL_RELEASE: "release",
        COL_SCALE: "scale", COL_PRESUPPORTED: "presupports", COL_TO_PRINT: "to_print",
    }

    def _on_cell_edited(self, row: int, col: int, text: str) -> None:
        field = self._EDITABLE_FIELDS.get(col)
        model_id = self._model.model_id_for_row(row)
        if field is None or model_id is None:
            return
        self._pending_edits[(model_id, field)] = text
        self._edit_timer.start()

    def _flush_pending_edits(self) -> None:
        """Hand all debounced cell edits to a background writer as one batch."""
        if not self._pending_edits:
            return
        edits = [(mid, field, text) for (mid, field), text in self._pending_edits.items()]
        self._pending_edits.clear()
        job = _CellEditRunnable(edits)
        job.signals.done.connect(self._on_cell_edits_saved)
        self._edit_pool.start(job)

    def _on_cell_edits_saved(self, folders: list, error: str) -> None:
        if error:
            QMessageBox.warning(self, self.tr("Save Edit"),
                                self.tr("Could not save the edit:") + f"\n{error}")
            return
        # New lookup names (sculptor, release, …) must appear in the delegates.
        _lookup_cache.invalidate()
        sidecar_exporter.schedule(folders)

    def finish_pending_writes(self, msecs: int = 30000) -> None:
        """Commit debounced edits now and wait for them (used on application close)."""
        self._edit_timer.stop()
        self._flush_pending_edits()
        self._edit_pool.waitForDone(msecs)
        # Deliver the queued "saved" signals so their sidecar refresh is scheduled.
        QApplication.processEvents()

    # ------------------------------------------------------------------
    def _load_search_history(self) -> None:
        """Load search history from settings."""
        settings = QSettings()
        history = settings.value("browser/search_history", [])
        if isinstance(history, str):
            # Handle old single-value format
            history = [history] if history else []
        self._search_combo.addItems(history)

    def _save_search_term(self, text: str) -> None:
        """Save a search term to history (keep latest 20 unique)."""
        text = text.strip()
        if not text:
            return

        # Remove if already in history (we'll add it at the top)
        for i in range(self._search_combo.count()):
            if self._search_combo.itemText(i) == text:
                self._search_combo.removeItem(i)
                break

        # Add to top
        self._search_combo.insertItem(0, text)

        # Keep only 20 unique items
        while self._search_combo.count() > 20:
            self._search_combo.removeItem(self._search_combo.count() - 1)

        # Save to settings
        history = [self._search_combo.itemText(i) for i in range(self._search_combo.count())]
        settings = QSettings()
        settings.setValue("browser/search_history", history)

    def _on_search(self, text: str) -> None:
        self._model.set_search(text)
        self._save_search_term(text)

    def _on_search_selected(self, index: int) -> None:
        """Handle selection from dropdown history."""
        if index >= 0:
            text = self._search_combo.itemText(index)
            self._search_combo.lineEdit().setText(text)

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

    # Filter-tree item kind → FilterSpec facet name
    _KIND_TO_FACET = {
        "creator": "creator", "tag": "tag", "source": "source", "path": "path",
        "sculptor_id": "sculptor", "collection_id": "collection",
        "subcollection_id": "subcollection", "category_id": "category",
        "release_id": "release", "scale_id": "scale", "presupports": "presupports",
        "warning": "warning", "filetype": "filetype",
    }

    def _collect_filter_spec(self) -> FilterSpec:
        """Read the tree's include (Checked) / exclude (PartiallyChecked) items."""
        include: dict[str, list] = {}
        exclude: dict[str, list] = {}
        has_value: set[str] = set()
        no_value:  set[str] = set()

        def _collect(item: QTreeWidgetItem):
            data  = item.data(0, Qt.ItemDataRole.UserRole)
            state = item.checkState(0)
            if data and state in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                is_inc = state == Qt.CheckState.Checked
                kind, value = data
                if kind == "group":
                    # Groups without a has-value rule (e.g. "filetype") are ignored.
                    if value in FILTER_GROUPS:
                        (has_value if is_inc else no_value).add(value)
                elif kind in ("date_year", "date_month"):
                    target = include if is_inc else exclude
                    target.setdefault("date", []).append(
                        (value, None) if kind == "date_year" else tuple(value))
                elif kind in self._KIND_TO_FACET:
                    target = include if is_inc else exclude
                    target.setdefault(self._KIND_TO_FACET[kind], []).append(value)
            for i in range(item.childCount()):
                _collect(item.child(i))

        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _collect(root.child(i))
        return FilterSpec(include=include, exclude=exclude,
                          has_value=has_value, no_value=no_value)

    def _apply_checked_filters(self) -> None:
        """Reload the table and the faceted filter pane for the checked items."""
        self._clear_filters_pending()
        spec = self._collect_filter_spec()
        self._load_table(spec)
        # Each section is counted over the models matching all OTHER active
        # filters (app.services.filter_tree / library_query.facet_scopes).
        self._populate_filter_tree(spec=spec)

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
        def _d(col):
            return self._model.data(self._model.index(r, col)) or ""
        return {
            "title":         _d(COL_TITLE),
            "creator":       _d(COL_CREATOR),
            "source":        _d(COL_SOURCE),
            "date":          _d(COL_DATE),
            "tags":          _d(COL_TAGS),
            "sculptor":      _d(COL_SCULPTOR),
            "collection":    _d(COL_COLLECTION),
            "subcollection": _d(COL_SUBCOLLECTION),
            "category":      _d(COL_CATEGORY),
            "release":       _d(COL_RELEASE),
            "scale":         _d(COL_SCALE),
            "presupports":   _d(COL_PRESUPPORTED),
            "sequence":      _d(COL_SEQUENCE),
            "path":          _d(COL_PATH),
            "filesize":      _d(COL_FILESIZE),
            "compression":   _d(COL_COMPRESSION),
            "added":         _d(COL_ADDED),
            "warning":       self._model.data(self._model.index(r, COL_WARNING), Qt.ItemDataRole.ToolTipRole) or "",
        }

    def _on_current_changed(self, current, previous) -> None:
        if not current.isValid():
            self._current_model_id = None
            self._clear_thumbnails()
            self._creator_panel.setVisible(False)
            return
        r = current.row()
        model_id = self._model.model_id_for_row(r)
        if model_id is not None:
            self._current_model_id = model_id
            self._show_thumbnails(model_id)
            if self._info_pane_enabled:
                self._update_model_info_panel(model_id, self._row_data_at(r))

    def _update_model_info_panel(self, model_id: int, row_data: dict) -> None:
        """Build the HTML info panel from row data + cached url/description/creator profile."""
        from html import escape

        if model_id not in self._panel_cache:
            from app.services.browser_data import model_panel
            self._panel_cache[model_id] = model_panel(model_id, row_data["creator"])

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
        html = f"<table style='font-size:120%'>{body}</table>"
        self._info_lbl.setText(html)
        self._creator_panel.setVisible(self._info_pane_enabled)

    def _open_creator_editor(self, creator_name: str) -> None:
        from app.ui.creator_editor_dialog import CreatorEditorDialog
        dlg = CreatorEditorDialog(creator_name, parent=self)
        if dlg.exec() and self._current_model_id is not None and self._info_pane_enabled:
            self._panel_cache.pop(self._current_model_id, None)
            r = self._table.selectionModel().currentIndex().row()
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
        model_id = self._model.model_id_for_row(index.row())
        if model_id is not None:
            self._open_editor(model_id)

    def _on_table_click(self, index) -> None:
        if index.column() == COL_TO_PRINT:
            r, c = index.row(), index.column()
            current_state = self._model.data(self._model.index(r, c), Qt.ItemDataRole.CheckStateRole)
            new_state = Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked
            self._model.setData(self._model.index(r, c), new_state, Qt.ItemDataRole.CheckStateRole)
            return
        if index.column() != COL_WARNING:
            return
        r = index.row()
        warning_text = self._model.data(self._model.index(r, COL_WARNING), Qt.ItemDataRole.ToolTipRole) or ""
        if not warning_text:
            return
        archive_path = self._model.data(self._model.index(r, COL_PATH)) or ""
        runnable = _WarningDetailRunnable(str(archive_path), warning_text)
        runnable.signals.ready.connect(self._show_warning_detail)
        QThreadPool.globalInstance().start(runnable)

    def _show_warning_detail(self, archive_path: str, sections: list, error: str) -> None:
        """Show which archive members triggered each warning (GUI thread).

        ``sections`` comes from :class:`_WarningDetailRunnable`, which did the
        archive read; this only renders it.
        """
        from html import escape
        from pathlib import Path as _P

        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Warning Details"))
        dlg.setMinimumSize(560, 340)
        layout = QVBoxLayout(dlg)

        header = QLabel(f"<b>{escape(_P(archive_path).name)}</b>")
        header.setWordWrap(True)
        layout.addWidget(header)

        text = QTextEdit()
        text.setReadOnly(True)

        # Warnings that fire because something is *absent* have no files to list.
        no_file_notes = {
            "Archive without images":          self.tr("No image files were found in the archive."),
            "Archive without printable files": self.tr("No .stl / .obj / .lys / .chitubox files "
                                                      "were found in the archive."),
            "Archive with low compression rate": self.tr("Flagged by the archive's compressed / "
                                                         "uncompressed size ratio, not by any "
                                                         "particular file."),
            "Unreadable archive":              self.tr("The archive's catalog could not be read "
                                                       "when it was crawled."),
        }

        html_parts: list[str] = []
        if error:
            html_parts.append(
                f'<p style="color:#cc7700"><i>{escape(error)}</i></p>'
            )

        for warning, files in sections:
            html_parts.append(
                f'<p><span style="color:#ff4444;font-size:14px;">&#9888;</span>'
                f'&nbsp;<b>{escape(warning)}</b></p>'
            )
            if files:
                known = [size for _name, size in files if size is not None]
                total = _fmt_size(sum(known)) if known else None
                heading = self.tr("Triggered by {n} file(s):").format(n=len(files))
                if total:
                    # Sizes come from the archive catalog, so they are the
                    # uncompressed sizes of the members, not their packed size.
                    heading += self.tr(" {total} uncompressed").format(total=total)
                    if len(known) != len(files):
                        heading += self.tr(" (size unknown for {n})").format(
                            n=len(files) - len(known)
                        )
                html_parts.append(
                    '<p style="margin-left:20px;margin-bottom:2px;color:gray">'
                    + escape(heading) + "</p>"
                )
                items = "".join(
                    f"<li>{escape(name.replace(chr(92), '/'))}"
                    + (f'&nbsp;<span style="color:gray">&mdash; {_fmt_size(size)}</span>'
                       if size is not None else "")
                    + "</li>"
                    for name, size in files
                )
                html_parts.append(f'<ul style="margin-top:0">{items}</ul>')
            elif warning in no_file_notes:
                html_parts.append(
                    f'<p style="margin-left:20px;color:gray"><i>{escape(no_file_notes[warning])}'
                    "</i></p>"
                )
            elif not error:
                # The warning is stored on the record but nothing in the archive
                # matches it any more — usually the archive changed since the crawl.
                html_parts.append(
                    '<p style="margin-left:20px;color:gray"><i>'
                    + self.tr("No file in the archive matches this warning any more — "
                              "rescan the file to refresh it.")
                    + "</i></p>"
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
        src_row = indexes[0].row()
        self._open_containing_folder(src_row)

    def _show_files_kb(self) -> None:
        ids = self._selected_model_ids()
        if len(ids) == 1:
            self._show_files(ids[0])

    def _selected_model_ids(self) -> list[int]:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        return [
            mid for r in sorted(rows)
            if (mid := self._model.model_id_for_row(r)) is not None
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
        model_id = self._model.model_id_for_row(index.row())

        selected_ids = self._selected_model_ids()

        style = QApplication.style()
        SP = style.StandardPixmap
        menu = QMenu(self)
        
        show_files_act = menu.addAction(style.standardIcon(SP.SP_FileDialogContentsView), self.tr("Show Files…"))
        show_files_act.setVisible(len(selected_ids) == 1)
        open_folder_act = menu.addAction(style.standardIcon(SP.SP_DirOpenIcon), self.tr("Open Containing Folder"))
        open_folder_act.setVisible(len(selected_ids) == 1)
        menu.addSeparator()

        edit_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Model…"))
        edit_act.setVisible(len(selected_ids) == 1)
        mass_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), f"Mass Edit {len(selected_ids)} model(s)…")
        mass_act.setVisible(len(selected_ids) > 1)
        from app.ui.config_dialog import get_ai_tagging_enabled
        ai_tag_act = menu.addAction(self.tr("AI Tagging…"))
        ai_tag_act.setVisible(len(selected_ids) >= 1 and get_ai_tagging_enabled())
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
        src_col = index.column()
        if src_col == COL_CREATOR:
            menu.addSeparator()
            edit_creator_act = menu.addAction(
                style.standardIcon(SP.SP_FileDialogDetailedView),
                self.tr("Edit Creator Information…"),
            )
            edit_authors_act = menu.addAction(
                style.standardIcon(SP.SP_FileDialogDetailedView),
                self.tr("Edit Authors…"),
            )
        elif src_col == COL_TAGS:
            menu.addSeparator()
            add_tag_act = menu.addAction(style.standardIcon(SP.SP_FileDialogNewFolder), self.tr("Add Tag…"))
            edit_tags_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Tags…"))
        elif src_col == COL_COLLECTION:
            menu.addSeparator()
            edit_collections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Collections…"))
        elif src_col == COL_SUBCOLLECTION:
            menu.addSeparator()
            edit_subcollections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Subcollections…"))
        elif src_col == COL_CATEGORY:
            menu.addSeparator()
            edit_categories_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Categories…"))
        elif src_col == COL_SCALE:
            menu.addSeparator()
            edit_scales_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), self.tr("Edit Scales…"))

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
            self._open_containing_folder(index.row())
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
        elif ai_tag_act and action == ai_tag_act:
            self._open_ai_tagger(selected_ids)
        elif edit_collections_act and action == edit_collections_act:
            self._open_category_editor()
        elif edit_subcollections_act and action == edit_subcollections_act:
            self._open_subcategory_editor()
        elif edit_categories_act and action == edit_categories_act:
            self._open_category_editor(new_category=True)
        elif edit_scales_act and action == edit_scales_act:
            self._open_scale_editor()
        elif edit_creator_act and action == edit_creator_act:
            creator_name = self._model.data(self._model.index(index.row(), COL_CREATOR)) or ""
            if creator_name:
                self._open_creator_editor(creator_name)
        elif edit_authors_act and action == edit_authors_act:
            self._open_author_editor()

    def _show_files(self, model_id: int) -> None:
        from app.ui.file_list_dialog import FileListDialog
        FileListDialog(model_id, parent=self).exec()

    def _open_containing_folder(self, source_row: int) -> None:
        archive_path = self._model.data(self._model.index(source_row, COL_PATH)) or ""
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
        from app.crawler.archive import extract_archive_contents, list_contents
        from app.crawler.metadata import extract_metadata
        from app.services.model_views import grid_models, stl_files
        from app.ui.settings_loader import build_crawler_config

        config = build_crawler_config()
        changes_all: dict[int, list[tuple[str, str, str]]] = {}
        models = grid_models(model_ids)

        # For each selected model, open the archive, extract relevant files and
        # compute the metadata. Build a list of (field, current, new) tuples.
        for mid, m in models.items():
            if not m["archive_path"]:
                continue
            archive_path = Path(m["archive_path"])
            # Extract only the relevant files (no thumbnail generation here)
            contents = extract_archive_contents(archive_path, unrar_path=config.unrar_path)
            meta = extract_metadata(archive_path, contents=contents, source_dir=archive_path.parent)

            changes: list[tuple[str, str, str]] = []
            def _add_if_diff(field: str, current, new) -> None:
                cur = "" if current is None else str(current)
                nw = "" if new is None else str(new)
                if cur != nw:
                    changes.append((field, cur, nw))

            _add_if_diff("Title", m["title"], meta.title)
            _add_if_diff("Creator", m["creator"], meta.creator)
            # ModelMetadata uses 'distributor' as the field name for the source/platform
            _add_if_diff("Source", m["source"], meta.distributor)
            if meta.release_date:
                ry, rm = parse_release_date(meta.release_date)
                new_date = format_release_date(ry, rm)
            else:
                new_date = ""
            _add_if_diff("Release date", format_release_date(m["release_year"], m["release_month"]), new_date)
            _add_if_diff("URL", m["url"], meta.url)
            _add_if_diff("Tags", ", ".join(m["tags"]), ", ".join(meta.tags))
            cur_files = str(len(stl_files(mid)))
            new_files = str(len([n for n in (list_contents(archive_path, config.unrar_path) or [])
                                 if Path(n.replace("\\", "/")).suffix.lower() in {".stl", ".obj", ".lys", ".chitubox"}]))
            _add_if_diff("File list count", cur_files, new_files)
            changes_all[mid] = changes

        flat: list[tuple[str, str, str]] = []
        for mid, chlist in changes_all.items():
            for field, cur, nw in chlist:
                flat.append((f"[{mid}] {field}", cur, nw))

        dlg = RecrawlPreviewDialog(f"Recrawl preview for {len(model_ids)} model(s)", flat, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Apply: a forced rescan of these archives through the crawler (its
        # native database phases when available), one worker per folder.
        from app.crawler.crawler import CrawlerWorker
        by_folder: dict[Path, list[Path]] = {}
        for m in models.values():
            if m["archive_path"] and Path(m["archive_path"]).exists():
                p = Path(m["archive_path"])
                by_folder.setdefault(p.parent, []).append(p)
        errors: list[str] = []
        for folder, paths in sorted(by_folder.items()):
            worker = CrawlerWorker(str(folder), pattern="", config=build_crawler_config())
            worker.log.connect(lambda msg: errors.append(msg) if "ERROR" in msg else None)
            worker.recrawl(sorted(paths))
        if errors:
            QMessageBox.warning(self, self.tr("Recrawl failed"), "\n".join(errors[:20]))

        QMessageBox.information(self, self.tr("Recrawl complete"), f"Recrawl applied for {len(model_ids)} model(s).")

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
                    self, self.tr("Rescan complete"),
                    f"Rescan applied for {updated} model(s).",
                )
            self.refresh()

        def _on_error(filename: str, err: str) -> None:
            QMessageBox.warning(self, self.tr("Rescan failed"), f"Failed to rescan {filename}:\n{err}")

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

        report = delete_archives(model_ids, use_trash, trash_path)

        self.refresh()
        if report.failed:
            QMessageBox.warning(
                self,
                self.tr("Delete Files"),
                f"Successfully processed {report.deleted} file(s). "
                f"Failed to process {len(report.failed)} file(s).",
            )
        
    def _delete_models(self, model_ids: list[int]) -> None:
        """Confirm then delete models and their LMDB thumbnails from the database."""
        count = len(model_ids)
        reply = QMessageBox.warning(
            self,
            self.tr("Delete Model"),
            f"Permanently delete <b>{count} model{'s' if count != 1 else ''}</b> "
            f"and their thumbnail files from the database?<br><br>This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.services.browser_data import delete_models
        from app.utils.thumbnailer import delete_thumbnails
        delete_thumbnails(delete_models(model_ids))   # after commit; one LMDB flush, not one per image

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
        """Import dropped image files as thumbnails for the currently selected model.

        The slow part — reading each file and encoding its thumbnail — runs in a
        background ``_ImageDropRunnable``; the DB inserts and LMDB writes happen
        back on the GUI thread in :meth:`_on_image_drop_ready`.
        """
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
        runnable = _ImageDropRunnable(self._current_model_id, paths)
        runnable.signals.ready.connect(self._on_image_drop_ready)
        QThreadPool.globalInstance().start(runnable)

    def _on_image_drop_ready(self, model_id: int, encoded: list, errors: list) -> None:
        """Persist thumbnails encoded by ``_ImageDropRunnable`` (GUI thread)."""
        from app.services.browser_data import add_images
        from app.utils.thumbnailer import write_raw_thumbnail
        failed = add_images(model_id, encoded, write_raw_thumbnail)
        errors += failed
        added = len(encoded) - len(failed)
        if added and self._current_model_id == model_id:
            self._show_thumbnails(model_id)
        if errors:
            QMessageBox.warning(
                self, self.tr("Image import"),
                "Could not import:\n" + "\n".join(errors),
            )

    def _clear_thumbnails(self) -> None:
        """Remove all thumbnail widgets from the sidebar and drop the pixmap cache."""
        self._stop_thumb_movies()
        self._thumb_gif_data.clear()
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
        from app.services.browser_data import model_images
        self._clear_thumbnails()
        self._thumb_all_images = model_images(model_id)
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
        self._stop_thumb_movies()
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
            lbl.setProperty("image_id", image_id)
            lbl.clicked.connect(
                lambda _slot=slot, _iid=image_id: (
                    ImageViewerDialog(
                        self._thumb_pixmaps[_slot], self,
                        animated_data=self._thumb_gif_data.get(_iid),
                    ).exec()
                    if not self._thumb_pixmaps[_slot].isNull() else None
                )
            )
            lbl.context_menu_requested.connect(self._on_image_context_menu)
            self._thumb_vlayout.addWidget(lbl)

            gif = self._thumb_gif_data.get(image_id)
            if gif and cached is not None:
                # Paging back to an already-loaded animated thumbnail: the pixmap
                # cache only holds frame 0, so rebuild the movie from kept bytes
                # rather than making the loader re-read LMDB.
                self._start_thumb_movie(slot, lbl, gif)

        if need_async and self._current_model_id is not None:
            self._thumb_load_requested.emit(self._current_model_id, need_async)

        # Update / show pagination controls
        if total > page_size:
            if self._thumb_show_all:
                self._thumb_page_label.setText(f"All {total} images")
                self._thumb_prev_btn.setEnabled(False)
                self._thumb_next_btn.setEnabled(False)
                self._thumb_show_all_btn.setText(self.tr("Paginate"))
            else:
                num_pages = (total + page_size - 1) // page_size
                self._thumb_page_label.setText(f"Page {self._thumb_page + 1} / {num_pages}")
                self._thumb_prev_btn.setEnabled(self._thumb_page > 0)
                self._thumb_next_btn.setEnabled(self._thumb_page < num_pages - 1)
                self._thumb_show_all_btn.setText(self.tr("Show All"))
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

    def _stop_thumb_movies(self) -> None:
        """Stop and drop every running thumbnail animation.

        Called before the pane is repopulated: a QMovie keeps decoding on the
        GUI thread even once its label is gone, so leaving them running would
        burn CPU per model the user clicks through.
        """
        for movie in self._thumb_movies.values():
            movie.stop()
        self._thumb_movies.clear()

    def _start_thumb_movie(self, slot: int, label, gif_bytes: bytes) -> None:
        """Play *gif_bytes* on *label*, registering the movie under *slot*.

        The QBuffer is parented to the QMovie so it outlives this call; QMovie
        reads from the device lazily and a garbage-collected buffer would leave
        the animation frozen on frame 0.
        """
        movie = QMovie(self)
        buf = QBuffer(movie)
        buf.setData(QByteArray(gif_bytes))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        movie.setDevice(buf)
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        if not movie.isValid():
            return
        self._thumb_movies[slot] = movie
        self._scale_movie(slot, movie)
        label.setMovie(movie)
        movie.start()

    def _scale_movie(self, slot: int, movie: QMovie) -> None:
        """Size *movie* to the pane width, keeping the source aspect ratio."""
        if slot >= len(self._thumb_pixmaps):
            return
        px = self._thumb_pixmaps[slot]
        if px.isNull() or px.width() == 0:
            return
        w = max(self._thumb_scroll.width() - 36, 60)
        movie.setScaledSize(QSize(w, max(1, round(w * px.height() / px.width()))))

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
            if not (item and item.widget()):
                continue
            movie = self._thumb_movies.get(i)
            if movie is not None:
                # setPixmap() would detach the movie and freeze the animation.
                self._scale_movie(i, movie)
                continue
            scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
            item.widget().setPixmap(scaled)

    def _on_thumb_bytes_ready(self, model_id: int, image_id: int, jpeg_bytes: bytes) -> None:
        """Receive JPEG bytes from the background loader and update the matching label."""
        if model_id != self._current_model_id:
            return
        slot = self._thumb_id_to_slot.get(image_id)
        if slot is None:
            return

        from app.utils.thumbnailer import is_animated_thumbnail

        px = QPixmap()
        if jpeg_bytes:
            px.loadFromData(jpeg_bytes)   # frame 0 for a GIF
            QPixmapCache.insert(f"thumb:{image_id}", px)

        self._thumb_pixmaps[slot] = px

        item = self._thumb_vlayout.itemAt(slot)
        if not (item and item.widget()):
            return

        if jpeg_bytes and is_animated_thumbnail(jpeg_bytes):
            self._thumb_gif_data[image_id] = jpeg_bytes
            self._start_thumb_movie(slot, item.widget(), jpeg_bytes)
            return

        w = max(self._thumb_scroll.width() - 36, 60)
        scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        item.widget().setPixmap(scaled)

    def _shutdown_thumb_loader(self) -> None:
        """Stop the background thumbnail thread cleanly on app exit."""
        self._thumb_thread.quit()
        self._thumb_thread.wait(2000)

    def _on_image_context_menu(self, image_id: int) -> None:
        """Show context menu for thumbnail image with delete option."""
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        delete_action = menu.addAction(self.tr("Delete thumbnail"))
        delete_action.triggered.connect(lambda: self._delete_image(image_id))
        menu.exec(QCursor.pos())

    def _delete_image(self, image_id: int) -> None:
        """Delete an image from the database and refresh the thumbnail display."""
        from app.services.browser_data import delete_image
        from app.utils.thumbnailer import delete_thumbnail
        if not delete_image(image_id):
            QMessageBox.warning(self, self.tr("Error"), self.tr("Image not found in database."))
            return
        try:
            delete_thumbnail(image_id)
        except Exception as e:
            logger.warning("Failed to delete thumbnail from LMDB: %s", e)

        QMessageBox.information(self, self.tr("Success"), self.tr("Image deleted successfully."))
        if self._current_model_id is not None:
            self._show_thumbnails(self._current_model_id)

    def add_tag(self) -> None:
        """Prompt for a new tag name, create it in the DB, and rebuild the filter tree."""
        name, ok = QInputDialog.getText(self, self.tr("Add Tag"), self.tr("Tag name:"))
        if not ok or not name.strip():
            return
        from app.services.browser_data import add_tag
        name = name.strip().lower()
        if not add_tag(name):
            QMessageBox.information(self, self.tr("Add Tag"), f"Tag '{name}' already exists.")
            return
        self._populate_filter_tree()

    def _open_tag_editor(self) -> None:
        from app.ui.tag_editor_dialog import TagEditorDialog
        TagEditorDialog(parent=self).exec()
        self.refresh()

    def open_ai_tagger_for_selection(self) -> None:
        """Open AI Tagging for the currently selected rows (toolbar/menu entry)."""
        ids = self._selected_model_ids()
        if not ids:
            QMessageBox.information(
                self, self.tr("AI Tagging"),
                self.tr("Select one or more models in the table first."),
            )
            return
        self._open_ai_tagger(ids)

    def _open_ai_tagger(self, model_ids: list) -> None:
        from app.ui.ai_tagger_dialog import AITaggerDialog
        dlg = AITaggerDialog(model_ids, parent=self)
        dlg.exec()
        if dlg._any_saved:
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
            self.tr("Generate Thumbnails"),
            self.tr("Number of thumbnails to generate per model:"),
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
        dlg.setWindowTitle(self.tr("Generate Thumbnails"))
        dlg.setModal(True)
        vlay = QVBoxLayout(dlg)
        label = QLabel(self.tr("Generating thumbnails…"))
        vlay.addWidget(label)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, len(model_ids))
        progress_bar.setValue(0)
        vlay.addWidget(progress_bar)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        send_bg_btn = QPushButton(self.tr("Send to background"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        btn_row.addWidget(send_bg_btn)
        btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)
        dlg.setMinimumWidth(420)

        self._thumb_gen_worker = ThumbnailGeneratorWorker(model_ids, count, parent=self)
        # Prevent starting if another background job is active
        from app.utils.background_jobs import start_job, active_job
        if not start_job("thumbnails"):
            QMessageBox.warning(self, self.tr("Generate Thumbnails"), self.tr("Another background job is already running. Try again later."))
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
            self._thumb_gen_worker = None
            from app.utils.background_jobs import end_job
            end_job("thumbnails")
            # Tell the main window here rather than after dlg.exec() returns: a
            # short run finishes *during* exec(), which clears _thumb_gen_worker
            # before any post-exec wiring could reference it.
            self.thumbnail_finished.emit()

        self._thumb_gen_worker.progress.connect(_on_progress)
        self._thumb_gen_worker.generation_finished.connect(_on_finished)
        # Wire up buttons: Send to background hides the dialog; Cancel stops the worker
        def _send_background():
            dlg.hide()
            # show current progress in status bar via signal
            self.thumbnail_progress_updated.emit(progress_bar.value(), progress_bar.maximum())

        def _on_cancel():
            # Request worker to stop and close dialog
            if self._thumb_gen_worker is not None:
                self._thumb_gen_worker.stop()
            dlg.reject()
            from app.utils.background_jobs import end_job
            end_job("thumbnails")

        send_bg_btn.clicked.connect(_send_background)
        cancel_btn.clicked.connect(_on_cancel)

        self._thumb_gen_worker.start()
        # Show modal dialog (blocks until accept/reject) — if Send to background was clicked
        # dlg.exec() will return but the worker continues running; if Cancel was clicked,
        # the worker is requested to stop.
        dlg.exec()

    def generate_thumbnails_for_selected(self) -> None:
        """Generate thumbnails for the rows currently selected in the table.

        Called from the main-window Tools menu; mirrors the right-click action.
        """
        ids = self._selected_model_ids()
        if not ids:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                self.tr("Generate Thumbnails"),
                self.tr("Select one or more models in the table first."),
            )
            return
        self._generate_thumbnails(ids)

    def _create_sidecar_for_selection(self, model_ids: list[int]) -> None:
        """Open a sidecar dialog scoped to the folders of the selected models."""
        from pathlib import Path
        from app.ui.metadata_sidecar_dialog import MetadataSidecarDialog

        from app.services.model_edit import archive_paths

        folders = {Path(p).parent for p in archive_paths(model_ids).values() if p}
        if not folders:
            QMessageBox.warning(self, self.tr("Create Sidecar"), self.tr("No valid file paths found for the selected models."))
            return

        dlg = MetadataSidecarDialog(folders=folders, parent=self)
        dlg.exec()

    def show_image_grid(self) -> None:
        """Open an image grid for all models currently visible in the table."""
        model_ids = self._model.all_visible_ids()
        from app.ui.image_grid_dialog import ImageGridDialog
        dlg = ImageGridDialog(model_ids, parent=self)
        dlg.show_selection_requested.connect(self.show_model_ids)
        dlg.exec()
