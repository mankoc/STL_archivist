"""
Factory for building a :class:`~app.config.CrawlerConfig` from ``QSettings``
and the per-library JSON settings file.

This is the single point where UI-layer settings are translated into the
config dataclass that the crawler uses.  Business-logic modules import only
``app.config`` — never ``app.ui.*``.
"""
from app.config import CrawlerConfig
from app.ui.config_dialog import (
    get_max_thumbnails,
    get_max_thumb_size,
    get_max_workers,
    get_metadata_sidecar_enabled,
    get_metadata_thumbnails_enabled,
    get_store_stl_file_list,
    get_supports_patterns,
    get_unrar_path,
    get_warn_nested_archive,
    get_warn_no_images,
    get_warn_presupported_stl,
    get_warn_supports,
)


def build_crawler_config(max_thumbnails: int | None = None) -> CrawlerConfig:
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
        warn_supports=get_warn_supports(),
        support_patterns=get_supports_patterns(),
    )
