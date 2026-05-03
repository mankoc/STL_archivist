"""
Shared pytest fixtures.

Tests in this project follow two conventions:

* **Fresh in-memory DB per test** — most modules touch the SQLAlchemy session,
  so we re-initialise an in-memory SQLite database before every test.  This
  is wired up as autouse so individual tests don't need to opt in.
* **Per-test LMDB thumbnail store** — modules that exercise thumbnail I/O
  ask for the ``thumb_store`` fixture which points at a tmpdir.  It is *not*
  autouse so tests that don't touch thumbnails skip the LMDB cost.

Tests that need both (e.g. METADATA.7z roundtrips) request ``tmp_library``
which provides a tmpdir with both a fresh DB and a fresh LMDB store rooted
inside it.
"""
import pytest

from app.database.db import init_db
from app.utils.thumbnailer import init_thumbnail_store


@pytest.fixture(autouse=True)
def in_memory_db():
    """Reset the global SQLAlchemy engine to an empty in-memory SQLite."""
    init_db(":memory:")
    yield


@pytest.fixture
def thumb_store(tmp_path):
    """Open a fresh LMDB thumbnail store under *tmp_path* for the test.

    The store lives at ``tmp_path / "thumbnails.lmdb"`` so callers can
    pass that exact path as a ``thumb_base_dir`` argument to functions
    like :func:`app.crawler.folder_metadata.export_folder_metadata`.
    """
    store_dir = tmp_path / "thumbnails.lmdb"
    init_thumbnail_store(store_dir)
    return store_dir


@pytest.fixture
def tmp_library(tmp_path, thumb_store):
    """Tmpdir with a fresh DB *and* a fresh LMDB thumbnail store inside it."""
    return tmp_path
