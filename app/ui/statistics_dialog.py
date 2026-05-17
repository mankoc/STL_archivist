"""
Statistics dialog — aggregated counts and rankings from the database.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui._charts import BarChartWidget

logger = logging.getLogger(__name__)

def _fmt_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}" if unit != "B" else f"{n_bytes} B"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def _dir_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
class StatsWorker(QThread):
    """Gather all statistics in a background thread to keep the UI responsive."""

    finished = pyqtSignal(dict)

    def run(self) -> None:
        from sqlalchemy import func
        from app.database.db import session_scope
        from app.database.models import (
            STLModel, Image, Sculptor, Collection, Category, Tag, Release, Scale, model_tags,
        )
        from app.ui.config_dialog import get_db_path, get_data_dir

        stats: dict = {}

        try:
            with session_scope() as s:
                # --- Counts ---
                stats["total_models"]      = s.query(func.count(STLModel.id)).scalar() or 0
                stats["total_images"]      = s.query(func.count(Image.id)).scalar() or 0
                stats["total_sculptors"]   = s.query(func.count(Sculptor.id)).scalar() or 0
                stats["total_collections"] = s.query(func.count(Collection.id)).scalar() or 0
                stats["total_categories"]  = s.query(func.count(Category.id)).scalar() or 0
                stats["total_releases"]    = s.query(func.count(Release.id)).scalar() or 0
                stats["total_scales"]      = s.query(func.count(Scale.id)).scalar() or 0
                stats["total_tags"]        = s.query(func.count(Tag.id)).scalar() or 0
                stats["total_archive_size"] = s.query(func.sum(STLModel.archive_size)).scalar() or 0
                stats["total_file_size"]    = s.query(func.sum(STLModel.file_size)).scalar() or 0

                # --- Content quality ---
                stats["presupported"] = s.query(func.count(STLModel.id)).filter(
                    STLModel.presupports.isnot(None),
                    STLModel.presupports.notin_(["", "No"]),
                ).scalar() or 0

                stats["with_warnings"] = s.query(func.count(STLModel.id)).filter(
                    STLModel.warning.isnot(None),
                ).scalar() or 0

                stats["no_images"] = s.query(func.count(STLModel.id)).outerjoin(
                    Image, STLModel.id == Image.model_id
                ).filter(Image.id.is_(None)).scalar() or 0

                stats["with_url"] = s.query(func.count(STLModel.id)).filter(
                    STLModel.url.isnot(None),
                    STLModel.url != "",
                ).scalar() or 0

                # --- By source ---
                stats["by_source"] = s.query(
                    STLModel.source, func.count(STLModel.id)
                ).group_by(STLModel.source).order_by(func.count(STLModel.id).desc()).all()

                # --- By year (top 10 by count) ---
                stats["by_year"] = s.query(
                    STLModel.release_year, func.count(STLModel.id)
                ).filter(
                    STLModel.release_year.isnot(None)
                ).group_by(STLModel.release_year).order_by(
                    func.count(STLModel.id).desc()
                ).limit(10).all()

                # --- Top sculptors ---
                stats["top_sculptors"] = s.query(
                    Sculptor.name, func.count(STLModel.id).label("n")
                ).join(STLModel, STLModel.sculptor_id == Sculptor.id).group_by(
                    Sculptor.id
                ).order_by(func.count(STLModel.id).desc()).limit(15).all()

                # --- Top tags ---
                stats["top_tags"] = s.query(
                    Tag.name, func.count(model_tags.c.model_id).label("n")
                ).join(model_tags, model_tags.c.tag_id == Tag.id).group_by(
                    Tag.id
                ).order_by(func.count(model_tags.c.model_id).desc()).limit(15).all()

                # --- Top collections ---
                stats["top_collections"] = s.query(
                    Collection.name, func.count(STLModel.id).label("n")
                ).join(STLModel, STLModel.collection_id == Collection.id).group_by(
                    Collection.id
                ).order_by(func.count(STLModel.id).desc()).limit(10).all()

            # --- Storage ---
            db_path = Path(get_db_path())
            stats["db_size"]   = db_path.stat().st_size if db_path.exists() else 0
            lmdb_dir = Path(get_data_dir()) / "thumbnails.lmdb"
            stats["lmdb_size"] = _dir_size(lmdb_dir)

        except Exception as exc:
            logger.error("Failed to gather statistics: %s", exc, exc_info=True)
            stats["error"] = str(exc)

        self.finished.emit(stats)


# ---------------------------------------------------------------------------
class StatisticsDialog(QDialog):
    """Modal dialog showing aggregated statistics from the database."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Database Statistics")
        self.resize(680, 720)
        self.setModal(True)
        self._worker: StatsWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        self._container = QWidget()
        self._content_layout = QVBoxLayout(self._container)
        self._content_layout.setSpacing(10)
        self._content_layout.addStretch()
        scroll.setWidget(self._container)
        root.addWidget(scroll)

        self._status_lbl = QLabel("Loading statistics…")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_lbl)

        style = QApplication.style()
        SP = style.StandardPixmap
        btn_row = QHBoxLayout()

        self._refresh_btn = QPushButton(style.standardIcon(SP.SP_BrowserReload), "Refresh")
        self._refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()

        close_btn = QPushButton(style.standardIcon(SP.SP_DialogCloseButton), "Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("Loading statistics…")
        self._status_lbl.setVisible(True)
        self._clear_content()

        self._worker = StatsWorker()
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, stats: dict) -> None:
        self._worker = None
        self._refresh_btn.setEnabled(True)

        if "error" in stats:
            self._status_lbl.setText(f"Error: {stats['error']}")
            return

        self._status_lbl.setVisible(False)
        self._populate(stats)

    # ------------------------------------------------------------------
    def _clear_content(self) -> None:
        layout = self._content_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate(self, s: dict) -> None:
        layout = self._content_layout
        total  = s["total_models"]

        def pct(n: int) -> str:
            return f" ({100 * n / total:.1f}%)" if total else ""

        # ── Library overview ─────────────────────────────────────────
        ov = self._group("Library Overview")
        self._row(ov, "Archives (models)",  f"{total:,}")
        self._row(ov, "Images",             f"{s['total_images']:,}")
        self._row(ov, "Sculptors",          f"{s['total_sculptors']:,}")
        self._row(ov, "Collections",        f"{s['total_collections']:,}")
        self._row(ov, "Categories",         f"{s['total_categories']:,}")
        self._row(ov, "Releases",           f"{s['total_releases']:,}")
        self._row(ov, "Scales",             f"{s['total_scales']:,}")
        self._row(ov, "Tags",               f"{s['total_tags']:,}")
        self._row(ov, "Total size (compressed)",   _fmt_size(s["total_archive_size"]))
        self._row(ov, "Total size (uncompressed)", _fmt_size(s["total_file_size"]))
        layout.addWidget(ov)

        # ── Storage ──────────────────────────────────────────────────
        st = self._group("Storage")
        self._row(st, "Database file",     _fmt_size(s["db_size"]))
        self._row(st, "Thumbnails (LMDB)", _fmt_size(s["lmdb_size"]))
        self._row(st, "Total",             _fmt_size(s["db_size"] + s["lmdb_size"]))
        layout.addWidget(st)

        # ── Content ──────────────────────────────────────────────────
        ct = self._group("Content")
        self._row(ct, "Presupported",    f"{s['presupported']:,}{pct(s['presupported'])}")
        self._row(ct, "With warnings",   f"{s['with_warnings']:,}{pct(s['with_warnings'])}")
        self._row(ct, "No images",       f"{s['no_images']:,}{pct(s['no_images'])}")
        self._row(ct, "With source URL", f"{s['with_url']:,}{pct(s['with_url'])}")
        layout.addWidget(ct)

        # ── By source ────────────────────────────────────────────────
        if s["by_source"]:
            layout.addWidget(self._bar_group(
                "By Source",
                [(src or "unknown", cnt) for src, cnt in s["by_source"]],
                total,
            ))

        # ── By year ──────────────────────────────────────────────────
        if s["by_year"]:
            layout.addWidget(self._bar_group(
                "By Release Year (top 10)",
                [(str(yr), cnt) for yr, cnt in s["by_year"]],
                total,
            ))

        # ── Top sculptors ────────────────────────────────────────────
        if s["top_sculptors"]:
            layout.addWidget(self._bar_group(
                "Top Sculptors",
                list(s["top_sculptors"]),
                total,
            ))

        # ── Top tags ─────────────────────────────────────────────────
        if s["top_tags"]:
            layout.addWidget(self._bar_group(
                "Top Tags",
                list(s["top_tags"]),
                total,
            ))

        # ── Top collections ──────────────────────────────────────────
        if s["top_collections"]:
            layout.addWidget(self._bar_group(
                "Top Collections",
                list(s["top_collections"]),
                total,
            ))

        layout.addStretch()

    # ------------------------------------------------------------------
    @staticmethod
    def _group(title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setLayout(QFormLayout())
        return box

    @staticmethod
    def _row(box: QGroupBox, label: str, value: str) -> None:
        lbl = QLabel(value)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addRow(label + ":", lbl)

    @staticmethod
    def _bar_group(title: str, data: list[tuple[str, int]], total: int) -> QGroupBox:
        box = QGroupBox(title)
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(8, 6, 8, 8)
        chart = BarChartWidget(data, total)
        vbox.addWidget(chart)
        return box

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)
