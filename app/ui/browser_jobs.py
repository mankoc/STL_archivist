"""Background worker threads and the rescan progress dialog for the browser.

Extracted from ``browser_tab.py``. ``_ImageDropRunnable`` encodes dropped images
off the GUI thread; ``RescanWorker`` re-extracts archive metadata and
``ThumbnailGeneratorWorker`` (re)builds thumbnails, both reporting progress via
signals; ``RescanProgressDialog`` is the progress window that drives a
``RescanWorker`` (kept together because it references it). Crawler/config imports
stay deferred inside the workers' ``run()`` methods.
"""
from PyQt6.QtCore import QObject, QRunnable, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
)

from app.database.db import session_scope


class _ImageDropRunnable(QRunnable):
    """Read dropped image files and encode thumbnails off the GUI thread.

    File reads and JPEG resize/encode (the slow part that scales with the number
    and size of dropped files) run here. The resulting pre-encoded JPEGs are sent
    back to the GUI thread, where the DB inserts and LMDB writes happen — keeping
    those on the single existing writer and avoiding SQLite contention.
    """
    class _Signals(QObject):
        ready = pyqtSignal(int, list, list)  # model_id, [(filename, jpeg)], [error_name]

    def __init__(self, model_id: int, paths: list):
        super().__init__()
        self.signals   = self._Signals()
        self._model_id = model_id
        self._paths    = paths
        self.setAutoDelete(True)

    def run(self):
        from app.utils.thumbnailer import make_thumbnail
        encoded: list = []
        errors:  list = []
        for path in self._paths:
            try:
                raw = path.read_bytes()
            except OSError:
                errors.append(path.name)
                continue
            jpeg = make_thumbnail(raw)
            if jpeg is None:
                errors.append(path.name)
                continue
            encoded.append((path.name, jpeg))
        self.signals.ready.emit(self._model_id, encoded, errors)


class _WarningDetailRunnable(QRunnable):
    """Work out which archive members triggered each of a model's warnings.

    Reading the archive catalog is file I/O, so it happens here rather than in
    the click handler; the GUI thread only renders the result.
    """

    class _Signals(QObject):
        # archive_path, [(warning, [member names])], error message ("" when fine)
        ready = pyqtSignal(str, list, str)

    def __init__(self, archive_path: str, warning_text: str):
        super().__init__()
        self.signals  = self._Signals()
        self._path    = archive_path
        self._warning = warning_text
        self.setAutoDelete(True)

    def run(self):
        from pathlib import Path
        from app.crawler.archive import list_contents
        from app.crawler.rules import explain_warnings
        from app.ui.settings_loader import build_crawler_config

        lines = [w.strip() for w in (self._warning or "").split("\n") if w.strip()]
        path  = Path(self._path) if self._path else None
        error = ""
        matches: dict = {}

        if path is None or not path.is_file():
            error = "The archive is no longer at the catalogued path, so its files cannot be listed."
        else:
            try:
                config = build_crawler_config()
                names  = list_contents(path, config.unrar_path)
                if names:
                    # Every check is enabled here regardless of configuration: the
                    # question being answered is "what produced this stored
                    # warning", not "what would a crawl warn about now".
                    matches = explain_warnings(
                        names,
                        check_presupported=True,
                        check_no_images=True,
                        check_nested_archive=True,
                        check_no_printable=True,
                        presupport_patterns=config.presupport_patterns,
                    )
                else:
                    error = "The archive could not be opened, so its files cannot be listed."
            except Exception as exc:
                error = f"The archive could not be read: {exc}"

        # Keep the stored warning's own lines and order — a warning that no longer
        # reproduces still needs a row, shown as having no current matches.
        sections = [(line, matches.get(line, [])) for line in lines]
        self.signals.ready.emit(self._path, sections, error)


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
        from app.database.db import session_scope
        from app.database.models import STLModel, STLFile
        from app.database.helpers import get_or_create_scale
        from app.ui.settings_loader import build_crawler_config
        from app.utils.path_pattern import parse_release_date

        _log = logging.getLogger(__name__)
        # Must come from settings, not CrawlerConfig() defaults: a bare config
        # carries unrar_path="", so every .rar would fail to open here even when
        # the user has configured the tool — and a failed open used to look like
        # an archive with no members (see the guard below).
        config = build_crawler_config()
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
                    if not all_names:
                        # extract_archive_contents() logs and swallows open failures,
                        # returning an empty member list.  Rewriting the record from
                        # that would assert "no images"/"no printable files" on an
                        # archive we simply could not read, so stop before touching it.
                        self.file_error.emit(
                            p.name, "Could not read archive contents — left unchanged"
                        )
                        continue

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
                        check_presupported=config.warn_presupported_stl,
                        check_no_images=config.warn_no_images,
                        check_nested_archive=config.warn_nested_archive,
                        check_no_printable=config.warn_no_printable,
                        presupport_patterns=config.presupport_patterns,
                    )
                    m.warning = warnings or None

                    scales = detect_scale([p.name] + all_names)
                    if scales:
                        m.scales = [get_or_create_scale(session, s) for s in scales]

                    if config.store_stl_file_list:
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
        self.setWindowTitle(self.tr("Rescan Files"))
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
        self._bg_btn     = QPushButton(self.tr("Send to Background"))
        self._cancel_btn = QPushButton(self.tr("Cancel"))
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
                    presupport_patterns=get_presupport_patterns(),
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
