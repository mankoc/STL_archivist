"""
Shared chart widgets for the statistics dialogs.
"""
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QFontMetrics, QPainter, QPalette
from PyQt6.QtWidgets import QSizePolicy, QWidget

_ROW_H       = 26
_BAR_H_FRAC  = 0.55


class BarChartWidget(QWidget):
    """Horizontal bar chart painted with QPainter — no extra dependencies.

    Each row shows: label | ████████░░░░ | count (pct%)
    Bar lengths scale to the maximum value in *data*.
    Colors follow the active widget palette so the chart adapts to any theme.
    """

    def __init__(self, data: list[tuple[str, int]], total: int, parent=None) -> None:
        super().__init__(parent)
        self._data    = data
        self._total   = total
        self._max_val = max((v for _, v in data), default=1) or 1
        self._label_w: int | None  = None
        self._suffix_w: int | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(len(data) * _ROW_H + 8)

    def sizeHint(self) -> QSize:
        return QSize(400, len(self._data) * _ROW_H + 8)

    def paintEvent(self, _event) -> None:
        if not self._data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        palette   = self.palette()
        bar_color = palette.color(QPalette.ColorRole.Highlight)
        bg_color  = palette.color(QPalette.ColorRole.AlternateBase)
        text_col  = palette.color(QPalette.ColorRole.Text)
        dim_col   = palette.color(QPalette.ColorRole.PlaceholderText)

        fm = QFontMetrics(self.font())

        if self._label_w is None:
            self._label_w = min(
                max(fm.horizontalAdvance(n) for n, _ in self._data) + 16,
                220,
            )
        if self._suffix_w is None:
            sample = f"{self._max_val:,}  100.0%"
            self._suffix_w = fm.horizontalAdvance(sample) + 16

        label_w  = self._label_w
        suffix_w = self._suffix_w
        bar_area = max(self.width() - label_w - suffix_w - 8, 20)
        bar_h    = max(int(_ROW_H * _BAR_H_FRAC), 6)

        for i, (name, count) in enumerate(self._data):
            y = i * _ROW_H + 4

            label = fm.elidedText(name, Qt.TextElideMode.ElideRight, label_w - 4)
            painter.setPen(text_col)
            painter.drawText(
                QRect(0, y, label_w - 4, _ROW_H),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

            bar_x = label_w + 4
            bar_y = y + (_ROW_H - bar_h) // 2
            painter.fillRect(QRect(bar_x, bar_y, bar_area, bar_h), bg_color)

            fill_w = max(int(bar_area * count / self._max_val), 0)
            if fill_w:
                painter.fillRect(QRect(bar_x, bar_y, fill_w, bar_h), bar_color)

            pct    = f"{100 * count / self._total:.1f}%" if self._total else "—"
            suffix = f"{count:,}  {pct}"
            painter.setPen(dim_col)
            painter.drawText(
                QRect(bar_x + bar_area + 6, y, suffix_w, _ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                suffix,
            )

        painter.end()
