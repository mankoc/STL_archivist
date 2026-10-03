"""Tests for the one-row model summary used by Find by Image's detail panel."""
from app.database.db import session_scope
from app.database.models import Collection, Image, STLModel, Sculptor, Subcollection, Tag
from app.services.model_summary import model_summary


def test_model_summary_fields():
    with session_scope() as s:
        col = Collection(name="Heroes")
        m = STLModel(
            title="Knight", archive_path="/lib/knight.7z", archive_hash="h",
            creator="Studio", release_year=2024, release_month=3, archive_size=2048,
            sculptor=Sculptor(name="Ana"), collection=col,
            subcollection=Subcollection(name="Paladins", collection=col),
            tags=[Tag(name="fantasy"), Tag(name="armor")],
            images=[Image(filename="a.jpg"), Image(filename="b.jpg")],
        )
        s.add(m)
        s.flush()
        info = model_summary(s, m.id)
    assert info["title"] == "Knight" and info["creator"] == "Studio"
    assert info["sculptor_name"] == "Ana"
    assert (info["collection_name"], info["subcollection_name"]) == ("Heroes", "Paladins")
    assert (info["release_year"], info["release_month"]) == (2024, 3)
    assert sorted(info["tags"].split(", ")) == ["armor", "fantasy"]
    assert info["image_count"] == 2
    assert info["archive_path"] == "/lib/knight.7z" and info["archive_size"] == 2048
    assert not info["is_missing"]


def test_model_summary_missing_model():
    with session_scope() as s:
        assert model_summary(s, 12345) is None
