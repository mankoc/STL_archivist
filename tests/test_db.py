"""
Tests for the database module: init_db, clear_db, the schema's cascades and
the archive_folder backfill.
"""
import pytest

from tests import factory


def test_init_db_creates_the_schema(library_db):
    tables = {r[0] for r in factory.rows("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    assert {"models", "tags", "model_tags", "images", "sculptors", "collections",
            "subcollections", "categories", "releases", "scales", "stl_files"} <= tables


def test_init_db_reopens_an_existing_database(library_db):
    from app.database.db import init_db
    mid = factory.add_model(title="Kept")
    init_db(str(library_db))
    assert factory.row("models", mid)["title"] == "Kept"


def test_database_path_before_init_raises(monkeypatch):
    import app.database.db as db_module
    monkeypatch.setattr(db_module, "_db_path", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        db_module.database_path()


def test_clear_db_removes_all_rows():
    factory.add_model("/a.7z", "x", title="A", tags=("t",))
    factory.add_model("/b.7z", "y", title="B")

    from app.database.db import clear_db
    clear_db()

    assert factory.count("models") == 0
    assert factory.count("tags") == 0


def test_clear_db_schema_intact_after_clear():
    """Tables still exist after clear_db — we can insert new rows immediately."""
    from app.database.db import clear_db
    clear_db()
    factory.add_model("/c.7z", "z", title="C")
    assert factory.count("models") == 1


def test_cascade_delete_collection_removes_subcollections():
    cid = factory.lookup("collections", "Comics")
    factory.insert("subcollections", name="Marvel", collection_id=cid)
    factory.execute("DELETE FROM collections WHERE id = ?", cid)
    assert factory.count("subcollections", "name = 'Marvel'") == 0


def test_deleting_a_model_removes_its_images_and_tag_links():
    mid = factory.add_model(tags=("a", "b"))
    factory.add_image(mid)
    factory.execute("DELETE FROM models WHERE id = ?", mid)
    assert factory.count("images") == 0
    assert factory.count("model_tags") == 0
    assert factory.count("tags") == 2


def test_multiple_tags_on_model():
    mid = factory.add_model(title="Multi", tags=("a", "b", "c"))
    assert factory.names(mid, "tags") == ["a", "b", "c"]


def test_backfill_archive_folder():
    from app.database.db import backfill_archive_folder
    a = factory.add_model("F:\\lib\\x\\a.7z")
    b = factory.add_model("F:\\lib\\y\\b.7z", archive_folder="F:\\lib\\y")
    assert backfill_archive_folder() == 1
    assert factory.row("models", a)["archive_folder"] == "F:/lib/x"
    assert factory.row("models", b)["archive_folder"] == "F:/lib/y"
