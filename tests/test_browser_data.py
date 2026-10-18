"""Functional tests for services.library_stats and services.browser_data (Python path)."""
import pytest

from app.database.db import init_db, session_scope
from app.database.models import Collection, Image, Sculptor, STLModel, Tag
from app.services import browser_data as BD
from app.services import library_stats as LS
from app.utils.path_pattern import render_path_pattern


@pytest.fixture
def lib():
    init_db(":memory:")
    with session_scope() as s:
        alice = Sculptor(name="Alice")
        comics = Collection(name="Comics")
        s.add_all([
            STLModel(title="A", archive_path="F:/x/a.7z", archive_hash="1", release_year=2024,
                     release_month=3, source="patreon", sculptor=alice, collection=comics),
            STLModel(title="B", archive_path="F:/x/b.7z", archive_hash="2", release_year=2024,
                     source=None, presupports="Lychee", sculptor=alice),
            STLModel(title="C", archive_path="F:/y/c.7z", archive_hash="3", source="patreon",
                     presupports="No"),
        ])
        s.add(Tag(name="dragon"))


def test_column_labels(lib):
    data, total = LS.column_distribution("source", None)
    assert total == 3 and data == [("patreon", 2), ("(unknown)", 1)]
    assert LS.column_distribution("release_month", None)[0] == [("March", 1)]
    assert LS.column_distribution("presupports", 1)[0] == [("(not set)", 1)]
    assert LS.column_distribution("sculptor", None)[0] == [("Alice", 2)]
    assert LS.column_distribution("nope", None) == ([], 3)


def test_library_statistics(lib):
    st = LS.library_statistics()
    assert st["total_models"] == 3 and st["presupported"] == 1 and st["no_images"] == 3
    assert st["by_source"] == [("patreon", 2), (None, 1)]
    assert st["by_year"] == [(2024, 2)]
    assert st["total_file_size"] == 0


def test_browser_writes(lib):
    assert BD.add_tag("elf") and not BD.add_tag("dragon")
    assert BD.tag_names() == ["dragon", "elf"]
    failed = BD.add_images(1, [("a.png", b"ok"), ("b.png", b"bad")],
                           lambda i, data: None if data == b"bad" else str(i))
    assert failed == ["b.png"]
    imgs = BD.model_images(1)
    assert len(imgs) == 1 and imgs[0][1] == str(imgs[0][0])
    assert BD.delete_image(imgs[0][0]) and not BD.delete_image(imgs[0][0])
    BD.set_archive_paths([(1, "G:\\sorted\\a.7z")])
    with session_scope() as s:
        m = s.get(STLModel, 1)
        assert (m.archive_path, m.archive_folder) == ("G:\\sorted\\a.7z", "G:/sorted")
        assert s.query(Image).count() == 0
    assert BD.lookup_names("source") == ["patreon"]


def test_render_path_pattern_takes_fields(lib):
    fields = BD.sort_sources([1])[1]
    with session_scope() as s:
        m = s.get(STLModel, 1)
        pattern = "{sculptor}/{collection}/{date} {name:1}/{release}"
        assert render_path_pattern(pattern, fields) == render_path_pattern(pattern, m)
    assert render_path_pattern(pattern, fields) == "Alice/Comics/2024-03 A/_unknown"
