"""Filter-tree widgets and the background query worker for the browser.

Extracted from ``browser_tab.py``. ``FilterItemDelegate`` renders the tristate
checkboxes as coloured circles, ``FilterTreeWidget`` is the left-hand
include/exclude tree with its check-cycle logic, and ``_FilterTreeWorker`` runs
the faceted-count/distinct-value queries off the GUI thread: it serves the
unfiltered tree from the filter cache and otherwise calls
:func:`app.services.filter_tree.filter_tree_data`.
"""
from PyQt6.QtCore import (
    Qt, QObject, QRunnable, QPointF, QRect, QRectF, pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QStyle, QStyleOptionViewItem, QStyledItemDelegate,
    QTreeWidget, QTreeWidgetItem,
)



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

    def __init__(self, gen: int, spec, show_flags: dict,
                 show_counts: bool, alpha_enabled: bool, alpha_threshold: int,
                 checked_data: set, excluded_data: set,
                 expanded_sections: "set | None") -> None:
        super().__init__()
        self.signals = self._Signals()
        self._gen = gen
        self._spec = spec          # FilterSpec, or None for the unfiltered tree
        self._show = show_flags
        self._show_counts = show_counts
        self._alpha_enabled = alpha_enabled
        self._alpha_threshold = alpha_threshold
        self._checked = checked_data
        self._excluded = excluded_data
        self._expanded = expanded_sections

    def run(self) -> None:
        show = self._show

        # ── Serve from cache for unscoped (no active filters) requests ─────────
        if self._spec is None or self._spec.is_empty():
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
                    "scope_by_section": None,
                }
                self.signals.ready.emit(self._gen, payload)
                return

        from app.services.filter_tree import filter_tree_data
        data = filter_tree_data(self._spec, show, self._show_counts)

        # Build folder tree from sorted directories
        from app.database.filter_cache import build_folder_tree
        sorted_dirs = data["sorted_dirs"]
        folder_tree = build_folder_tree(sorted_dirs) if sorted_dirs else []

        scoped = data.pop("scoped")
        payload = {
            **data,
            "folder_tree": folder_tree,
            "checked_data": self._checked,
            "excluded_data": self._excluded,
            "expanded_sections": self._expanded,
            "show": self._show,
            "show_counts": self._show_counts,
            "alpha_enabled": self._alpha_enabled,
            "alpha_threshold": self._alpha_threshold,
            # The tree only tests this for None: were section scopes applied?
            "scope_by_section": True if scoped else None,
        }

        # Persist unscoped results so subsequent loads skip the DB queries.
        if not scoped:
            from app.database.filter_cache import save_filter_cache
            save_filter_cache(payload)

        self.signals.ready.emit(self._gen, payload)
