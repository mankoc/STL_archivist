"""
Shared configuration dataclass for the crawler and archive modules.

Populated from the UI config dialog at startup and passed into the crawler
so that business-logic code has no direct dependency on the UI layer.
"""
import os
from dataclasses import dataclass, field


def _default_workers() -> int:
    """Return half the logical CPU count, clamped to [2, 8]."""
    cpu = os.cpu_count() or 2
    return max(2, min(cpu // 2, 8))


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
    """

    max_thumbnails: int = 5
    max_thumb_size: int = 512
    unrar_path: str = ""
    max_workers: int = field(default_factory=_default_workers)
