"""
Shared pytest fixtures.

Tests in this project follow two conventions:

* **Fresh database file per test** (``library_db``, autouse) with its
  thumbnail store beside it; build data with ``tests/factory.py``.
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
def native_follows_environment():
    """After each test, load or drop the native core to match ``STL_NATIVE`` again.

    Tests switch the native core with ``monkeypatch.setenv`` / ``delenv`` and
    ``native.reload()``.  A reload made before monkeypatch restores the
    environment would leave the next tests on the wrong implementation (in an
    ``STL_NATIVE=0`` run, on the native one).  Being autouse and defined here,
    this fixture is set up first and torn down last, after every monkeypatch.
    """
    yield
    from app import native
    native.reload()


@pytest.fixture(autouse=True)
def library_db(tmp_path):
    """A fresh library database file (and thumbnail store) per test.

    A file, not an in-memory database, so the native core opens it too;
    ``tests/factory.py`` builds test data in it with plain ``sqlite3``.
    """
    init_db(str(tmp_path / "stlLibrary.db"))
    yield tmp_path / "stlLibrary.db"
    from app.database import native_db
    from app.utils.thumbnailer import close_thumbnail_store
    native_db.reset()
    close_thumbnail_store()


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
