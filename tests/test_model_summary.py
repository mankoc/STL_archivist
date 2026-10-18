"""Tests for the one-row model summary used by Find by Image's detail panel."""
from app.services.model_summary import model_summary
from tests import factory


def test_model_summary_fields():
    mid = factory.add_model(
        title="Knight", archive_path="/lib/knight.7z", archive_hash="h",
        creator="Studio", release_year=2024, release_month=3, archive_size=2048,
        sculptor="Ana", collection="Heroes", subcollection="Paladins", tags=("fantasy", "armor"),
    )
    factory.add_image(mid, "a.jpg")
    factory.add_image(mid, "b.jpg")
    info = model_summary(None, mid)
    assert info["title"] == "Knight" and info["creator"] == "Studio"
    assert info["sculptor_name"] == "Ana"
    assert (info["collection_name"], info["subcollection_name"]) == ("Heroes", "Paladins")
    assert (info["release_year"], info["release_month"]) == (2024, 3)
    assert sorted(info["tags"].split(", ")) == ["armor", "fantasy"]
    assert info["image_count"] == 2
    assert info["archive_path"] == "/lib/knight.7z" and info["archive_size"] == 2048
    assert not info["is_missing"]


def test_model_summary_missing_model():
    assert model_summary(None, 12345) is None
