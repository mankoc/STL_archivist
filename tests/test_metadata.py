"""
Basic tests for metadata inference (no archive files needed).
"""
from pathlib import Path
from unittest.mock import patch

from app.crawler.metadata import _infer_from_path


def test_infer_title_from_stem():
    p = Path("/mnt/f/STL/SomeAuthor/cyborg_hulk.7z")
    meta = _infer_from_path(p)
    assert meta.title == "cyborg hulk"


def test_infer_author_from_parent():
    p = Path("/mnt/f/STL/CalebNefzen/Cyborg-Hulk.7z")
    meta = _infer_from_path(p)
    assert meta.author == "CalebNefzen"


def test_infer_patreon_type():
    p = Path("/mnt/f/STL/Patreon/SomeArtist/model.zip")
    meta = _infer_from_path(p)
    assert meta.distributor == "Patreon"


def test_infer_date():
    p = Path("/mnt/f/STL/2024-03/SomeModel.7z")
    meta = _infer_from_path(p)
    assert meta.release_date == "2024-03"
