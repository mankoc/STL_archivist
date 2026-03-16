"""
Extract and resize thumbnail images from archive contents.
Returns JPEG bytes ready for storing in the DB.
"""
import io
from pathlib import Path
from typing import Optional

from PIL import Image

THUMB_SIZE_DEFAULT = 512
THUMB_QUALITY = 75


def make_thumbnail(image_bytes: bytes, max_size: int = THUMB_SIZE_DEFAULT) -> Optional[bytes]:
    """
    Convert raw image bytes to a JPEG thumbnail capped at max_size × max_size.
    Returns None if conversion fails.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def pixmap_from_bytes(thumbnail_bytes: bytes):
    """
    Convert stored thumbnail bytes to a QPixmap for display.
    Import is deferred so this module can be imported without PyQt.
    """
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import QByteArray

    ba = QByteArray(thumbnail_bytes)
    pixmap = QPixmap()
    pixmap.loadFromData(ba, "JPEG")
    return pixmap
