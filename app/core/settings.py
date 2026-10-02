"""
Qt-free settings provider.

The two machine-wide values (data directory and unrar path) resolve in order:

1. Environment variables ``STL_DATA_DIR`` / ``STL_UNRAR_PATH``.
2. A provider registered via :func:`register_providers` — the desktop app
   registers ``QSettings`` readers from ``app.ui.config_dialog``.
3. Platform defaults.

Every other setting lives in the per-library ``<data_dir>/settings.json``.
Only non-visual settings are defined here; colors, fonts, hotkeys and other
widget-only preferences stay in ``app.ui.config_dialog``, which re-exports
everything below so existing imports keep working.
"""
import copy
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from app.config import CrawlerConfig

DATA_DIR_ENV = "STL_DATA_DIR"
UNRAR_ENV    = "STL_UNRAR_PATH"

MAX_THUMBS_DEFAULT     = 5
MAX_THUMB_SIZE_DEFAULT = 512

_LIBRARY_SETTINGS_FILE = "settings.json"


def _platform_defaults() -> dict:
    """Return a dict of default values that differ by operating system.

    Keys
    ----
    data_dir        : str   — default location for the database and thumbnails
    color_scheme    : str   — initial color theme name (must be a key in THEMES)
    unrar_hint      : str   — placeholder text shown in the unrar path field
    max_workers     : int   — default number of parallel crawler threads
    """
    cpu = os.cpu_count() or 2
    # Use half the logical CPU count, clamped to [2, 8]
    workers = max(2, min(cpu // 2, 8))

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
        data_dir       = str(Path(local_app_data) / "stlLibrary")
        color_scheme   = "Windows 11 Dark"
        unrar_hint     = (
            r"C:\Program Files\WinRAR\UnRAR.exe  "
            r"(or C:\Program Files (x86)\WinRAR\UnRAR.exe)"
        )
    elif sys.platform == "darwin":
        data_dir     = str(Path.home() / "Library" / "Application Support" / "stlLibrary")
        color_scheme = "System Default"
        unrar_hint   = "/opt/homebrew/bin/unrar  (or /usr/local/bin/unrar)"
    else:
        # Linux / BSD — follow XDG Base Directory spec
        xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        data_dir     = str(Path(xdg_data) / "stlLibrary")
        color_scheme = "System Default"
        unrar_hint   = "/usr/bin/unrar"

    return {
        "data_dir":     data_dir,
        "color_scheme": color_scheme,
        "unrar_hint":   unrar_hint,
        "max_workers":  workers,
    }


PLATFORM_DEFAULTS = _platform_defaults()
DATA_DIR_DEFAULT  = PLATFORM_DEFAULTS["data_dir"]

_data_dir_provider: Callable[[], str] | None = None
_unrar_provider:    Callable[[], str] | None = None


def register_providers(
    data_dir: Callable[[], str] | None = None,
    unrar_path: Callable[[], str] | None = None,
) -> None:
    """Install callables consulted when the corresponding env var is unset."""
    global _data_dir_provider, _unrar_provider
    if data_dir is not None:
        _data_dir_provider = data_dir
    if unrar_path is not None:
        _unrar_provider = unrar_path


def get_data_dir() -> str:
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return env
    if _data_dir_provider is not None:
        return _data_dir_provider() or DATA_DIR_DEFAULT
    return DATA_DIR_DEFAULT


def get_unrar_path() -> str:
    """Return the configured path to the unrar/rar executable, or '' if not set."""
    env = os.environ.get(UNRAR_ENV)
    if env:
        return env
    if _unrar_provider is not None:
        return _unrar_provider() or ""
    return ""


def get_db_path() -> str:
    return str(Path(get_data_dir()) / "stlLibrary.db")


def _library_settings_path(data_dir: str | None = None) -> Path:
    return Path(data_dir or get_data_dir()) / _LIBRARY_SETTINGS_FILE


# Parsed settings.json per path, validated by (mtime_ns, size) on every read so
# an external edit is still picked up.  Getters run in paint code and worker
# threads, so a cache hit costs one stat() instead of a read + json.loads().
_settings_cache: dict[str, tuple[tuple[int, int], dict]] = {}
_settings_lock = threading.Lock()


def _load_library_settings(data_dir: str | None = None) -> dict:
    """Return the library settings dict (a private copy — safe to mutate)."""
    path = _library_settings_path(data_dir)
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    with _settings_lock:
        hit = _settings_cache.get(key)
        if hit is not None and hit[0] == stamp:
            return copy.deepcopy(hit[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    with _settings_lock:
        _settings_cache[key] = (stamp, data)
    return copy.deepcopy(data)


def _save_library_settings(settings: dict, data_dir: str | None = None) -> None:
    path = _library_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    with _settings_lock:
        # Drop rather than refresh: the next read re-stats and re-parses once.
        _settings_cache.pop(str(path), None)


def get_max_thumbnails() -> int:
    return int(_load_library_settings().get("max_thumbnails", MAX_THUMBS_DEFAULT))


def get_max_thumb_size() -> int:
    return int(_load_library_settings().get("max_thumb_size", MAX_THUMB_SIZE_DEFAULT))


def get_allow_file_delete() -> bool:
    return bool(_load_library_settings().get("allow_file_delete", False))


def get_use_trash_folder() -> bool:
    return bool(_load_library_settings().get("use_trash_folder", True))


def get_trash_folder_path() -> str:
    return _load_library_settings().get("trash_folder_path", "")


def get_file_sorting_enabled() -> bool:
    return bool(_load_library_settings().get("file_sorting_enabled", False))


def get_max_workers() -> int:
    return int(_load_library_settings().get("max_workers", PLATFORM_DEFAULTS["max_workers"]))


def get_warn_presupported_stl() -> bool:
    """Whether to warn when an archive contains supported STL files."""
    return bool(_load_library_settings().get("warn_presupported_stl", True))


def get_presupport_patterns() -> list[str]:
    """Return the regex patterns used to detect supported STL files."""
    from app.crawler.rules import (
        DEFAULT_PRESUPPORT_PATTERNS, LEGACY_PRESUPPORT_PATTERNS,
    )
    saved = _load_library_settings().get("presupport_patterns")
    if saved is None:
        return list(DEFAULT_PRESUPPORT_PATTERNS)
    if list(saved) == LEGACY_PRESUPPORT_PATTERNS:
        # These patterns used to drive the retired "Archive with supports"
        # warning and nothing else, so a list still identical to that default
        # was never a deliberate choice.  Upgrading it keeps the "presupported"
        # filename rule, which the old default has no pattern for.
        return list(DEFAULT_PRESUPPORT_PATTERNS)
    return list(saved)


def get_warn_no_images() -> bool:
    """Whether to warn when an archive contains no image files."""
    return bool(_load_library_settings().get("warn_no_images", True))


def get_warn_nested_archive() -> bool:
    """Whether to warn when an archive contains another compressed archive."""
    return bool(_load_library_settings().get("warn_nested_archive", True))


def get_warn_no_printable() -> bool:
    """Whether to warn when an archive contains no printable model files."""
    return bool(_load_library_settings().get("warn_no_printable", True))


def get_warn_low_compression() -> bool:
    """Whether to warn when an archive has a low compression rate."""
    return bool(_load_library_settings().get("warn_low_compression", False))


def get_low_compression_threshold() -> float:
    """Compression rate threshold above which the low-compression warning fires."""
    return float(_load_library_settings().get("low_compression_threshold", 0.95))


def get_logging_enabled() -> bool:
    """Whether file (and stderr) logging is active. Defaults to True."""
    return bool(_load_library_settings().get("logging_enabled", True))


def get_metadata_sidecar_enabled() -> bool:
    """Whether per-folder METADATA.7z sidecar files are created at all."""
    return bool(_load_library_settings().get("metadata_sidecar_enabled", True))


def get_metadata_thumbnails_enabled() -> bool:
    """Whether thumbnails are included in per-folder METADATA.7z sidecar files."""
    return bool(_load_library_settings().get("metadata_thumbnails", True))


def get_store_stl_file_list() -> bool:
    """Whether the crawler records the list of STL files contained in each archive."""
    return bool(_load_library_settings().get("store_stl_file_list", False))


def get_recurse_subdirs() -> bool:
    """Whether the crawler should recurse into subdirectories of the selected folder."""
    return bool(_load_library_settings().get("recurse_subdirs", True))


def get_ai_embeddings_enabled() -> bool:
    """Whether AI image embeddings (CLIP) are enabled for image similarity search."""
    return bool(_load_library_settings().get("ai_embeddings_enabled", True))


def get_ai_embeddings_during_crawl() -> bool:
    """Whether AI image embeddings are computed automatically during crawl."""
    return bool(_load_library_settings().get("ai_embeddings_during_crawl", False))


def get_ai_embeddings_batch_size() -> int:
    """Number of images per CLIP inference call (higher = better GPU utilisation)."""
    return int(_load_library_settings().get("ai_embeddings_batch_size", 32))


def get_embed_search_batch_size() -> int:
    return int(_load_library_settings().get("embed_search_batch_size", 1000))


def build_crawler_config(max_thumbnails: int | None = None, force_rescan: bool = False) -> CrawlerConfig:
    """Return a :class:`CrawlerConfig` populated from the current settings.

    Args:
        max_thumbnails: Override for the thumbnail count.  Pass ``0`` to
            disable thumbnail generation without changing the stored setting
            (used by the UI's "generate thumbnails" checkbox).  When ``None``
            the value is read from settings.
    """
    thumbs = max_thumbnails if max_thumbnails is not None else get_max_thumbnails()
    return CrawlerConfig(
        max_thumbnails=thumbs,
        max_thumb_size=get_max_thumb_size(),
        unrar_path=get_unrar_path(),
        max_workers=get_max_workers(),
        metadata_sidecar_enabled=get_metadata_sidecar_enabled(),
        metadata_thumbnails=get_metadata_thumbnails_enabled(),
        store_stl_file_list=get_store_stl_file_list(),
        warn_presupported_stl=get_warn_presupported_stl(),
        warn_no_images=get_warn_no_images(),
        warn_nested_archive=get_warn_nested_archive(),
        warn_no_printable=get_warn_no_printable(),
        presupport_patterns=get_presupport_patterns(),
        recurse_subdirs=get_recurse_subdirs(),
        force_rescan=force_rescan,
        warn_low_compression=get_warn_low_compression(),
        low_compression_threshold=get_low_compression_threshold(),
        embed_during_crawl=get_ai_embeddings_enabled() and get_ai_embeddings_during_crawl(),
    )
