"""
Shared configuration dataclass for the crawler and archive modules.

Populated from the UI config dialog at startup and passed into the crawler
so that business-logic code has no direct dependency on the UI layer.
"""
from dataclasses import dataclass, field


@dataclass
class CrawlerConfig:
    max_thumbnails: int = 5
    max_thumb_size: int = 512
    unrar_path: str = ""
