"""
Shared configuration dataclass for the crawler and archive modules.

Populated from ``app.ui.settings_loader.build_crawler_config()`` at startup
and passed into the crawler so that business-logic code has no direct
dependency on the UI layer.
"""
import os
from dataclasses import dataclass, field


def _default_workers() -> int:
    """Return half the logical CPU count, clamped to [2, 8]."""
    cpu = os.cpu_count() or 2
    return max(2, min(cpu // 2, 8))


def _default_presupport_patterns() -> list[str]:
    from app.crawler.rules import DEFAULT_PRESUPPORT_PATTERNS
    return list(DEFAULT_PRESUPPORT_PATTERNS)



@dataclass
class CrawlerConfig:
    """Runtime configuration for the crawler and archive processing pipeline.

    Attributes:
        max_thumbnails: Maximum number of preview images stored per archive.
        max_thumb_size: Maximum thumbnail dimension in pixels (applied to both
            width and height — the image is scaled to fit within a square of
            this size while preserving aspect ratio).
        unrar_path: Absolute path to the ``unrar`` or ``rar`` executable.
            Empty string means the tool must be on ``PATH``.
        max_workers: Number of parallel worker threads used for hashing and
            archive extraction.  Defaults to half the logical CPU count,
            clamped to [2, 8].
        metadata_sidecar_enabled: Write per-folder METADATA.7z sidecars after
            each crawl.
        metadata_thumbnails: Include thumbnail data in METADATA.7z sidecars.
        store_stl_file_list: Record the list of STL filenames contained in
            each archive.
        warn_supported_stl: Warn when an archive contains supported STL files.
        presupport_patterns: Regex patterns applied to member filenames to
            detect supported STL files. Defaults to
            ``DEFAULT_PRESUPPORT_PATTERNS``.
        warn_no_images: Warn when an archive contains no image files.
        warn_nested_archive: Warn when an archive contains another compressed
            archive.
    """

    max_thumbnails: int = 5
    max_thumb_size: int = 512
    unrar_path: str = ""
    max_workers: int = field(default_factory=_default_workers)

    # Sidecar / STL-list flags
    metadata_sidecar_enabled: bool = True
    metadata_thumbnails: bool = True
    store_stl_file_list: bool = False

    # Warning flags
    warn_supported_stl: bool = True
    presupport_patterns: list[str] = field(default_factory=_default_presupport_patterns)
    warn_no_images: bool = True
    warn_nested_archive: bool = True
    # Whether to recurse into subdirectories of the selected source folder.
    # When False the crawler only processes archives directly inside the selected
    # directory (no recursive walk).
    recurse_subdirs: bool = True
