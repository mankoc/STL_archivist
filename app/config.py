"""
Shared configuration dataclass for the crawler and archive modules.

Populated from the UI config dialog at startup and passed into the crawler
so that business-logic code has no direct dependency on the UI layer.
"""
import os
from dataclasses import dataclass, field


def _default_workers() -> int:
    """Half the logical CPU count, clamped to [2, 8]."""
    cpu = os.cpu_count() or 2
    return max(2, min(cpu // 2, 8))


@dataclass
class CrawlerConfig:
    max_thumbnails: int = 5
    max_thumb_size: int = 512
    unrar_path: str = ""
    max_workers: int = field(default_factory=_default_workers)
