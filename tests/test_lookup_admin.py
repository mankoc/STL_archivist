"""Tests for app.services.lookup_admin (the lookup editors' operations)."""
from app.database.db import session_scope
from app.database.models import (
    Category, Collection, Creator, Scale, STLModel, Subcollection, Tag,
)
from app.services import lookup_admin as LA


def _lib():
    with session_scope() as s:
        comics, games = Collection(name="Comics"), Collection(name="Games")
        busts, minis = Category(name="Busts"), Category(name="Minis")
        dragon, elf, unused = Tag(name="dragon"), Tag(name="Elf"), Tag(name="unused")
        s28 = Scale(name="28mm")
        s.add_all([comics, games, busts, minis, dragon, elf, unused, s28])
        s.flush()
        marvel = Subcollection(name="Marvel", collection_id=comics.id)
        zelda = Subcollection(name="Zelda", collection_id=games.id)
        s.add_all([marvel, zelda])
        s.flush()
        m1 = STLModel(title="a", archive_path="p1", archive_hash="h1", creator="Sanix",
                      collection=comics, subcollection=marvel, category=busts)
        m2 = STLModel(title="b", archive_path="p2", archive_hash="h2", creator="sanix",
                      collection=games, subcollection=zelda, category=minis)
        m1.tags.extend([dragon, elf])
        m2.tags.append(elf)
        m1.scales.append(s28)
        s.add_all([m1, m2, Creator(name="Sanix", full_name="S. Anix")])
        s.flush()
        return dict(comics=comics.id, games=games.id, busts=busts.id, minis=minis.id,
                    dragon=dragon.id, elf=elf.id, unused=unused.id, s28=s28.id,
                    marvel=marvel.id, zelda=zelda.id, m1=m1.id, m2=m2.id)


def _model(mid):
    with session_scope() as s:
        m = s.get(STLModel, mid)
        return (m.creator, m.collection_id, m.subcollection_id, m.category_id,
                sorted(t.name for t in m.tags))


def test_listing():
    ids = _lib()
    assert LA.list_lookups("tag") == [(ids["elf"], "Elf", 2), (ids["dragon"], "dragon", 1), (ids["unused"], "unused", 0)]
    assert LA.list_lookups("subcollection") == [
        (ids["marvel"], "Marvel", 1, ids["comics"], "Comics"),
        (ids["zelda"], "Zelda", 1, ids["games"], "Games")]
    assert LA.collection_names() == ["Comics", "Games"]
    assert LA.tags_for_merge_scan() == [(ids["dragon"], "dragon", 1), (ids["elf"], "Elf", 2), (ids["unused"], "unused", 0)]
    assert LA.author_counts() == [("Sanix", 1), ("sanix", 1)]


def test_add_rename_and_conflicts():
    ids = _lib()
    assert LA.add("category", "Terrain") and not LA.add("category", "Busts")
    assert LA.add("subcollection", "DC", "Comics")
    assert not LA.add("subcollection", "DC", "Comics")
    assert LA.add("subcollection", "Marvel", "Games")            # same name, other parent
    assert not LA.add("subcollection", "X", "No such collection")
    assert LA.name_of("subcollection", ids["marvel"]) == ("Marvel", ids["comics"])
    assert not LA.rename("subcollection", ids["zelda"], "Marvel")  # taken in Games now
    assert LA.rename("category", ids["busts"], "Bustos")
    assert not LA.rename("category", ids["minis"], "Bustos")
    assert LA.delete_info("collection", ids["comics"]) == ("Comics", 1, 2)


def test_delete_semantics():
    ids = _lib()
    LA.delete("collection", ids["comics"])                         # takes Marvel and DC along
    assert _model(ids["m1"])[1:3] == (None, None)
    assert [r[1] for r in LA.list_lookups("subcollection")] == ["Zelda"]
    LA.delete("tag", ids["elf"])
    assert _model(ids["m2"])[4] == []
    LA.delete("category", ids["minis"])
    assert _model(ids["m2"])[3] is None


def test_merges():
    ids = _lib()
    LA.merge("collection", ids["games"], ids["comics"])
    assert _model(ids["m1"])[1:3] == (ids["games"], None)
    LA.merge_tags([ids["dragon"], ids["elf"]], "ELF")             # matches "Elf"
    assert _model(ids["m1"])[4] == ["Elf"] and _model(ids["m2"])[4] == ["Elf"]
    LA.merge_tags([ids["unused"]], "brand new")
    assert "brand new" in [r[1] for r in LA.list_lookups("tag")]
    assert LA.unused_tag_count() == 1 and LA.delete_unused_tags() == 1


def test_authors_and_profiles():
    ids = _lib()
    LA.rename_author("Sanix", "Sanix Studio")
    assert _model(ids["m1"])[0] == "Sanix Studio"
    assert LA.creator_profile("Sanix Studio")["full_name"] == "S. Anix"
    LA.save_creator_profile("Sanix Studio", {"patreon_url": " https://p ", "full_name": ""})
    prof = LA.creator_profile("Sanix Studio")
    assert prof["patreon_url"] == "https://p" and prof["full_name"] is None
    LA.merge_authors("sanix", "Sanix Studio")
    assert LA.author_counts() == [("sanix", 2)] and LA.creator_profile("Sanix Studio") is None
    LA.delete_author("sanix")
    assert LA.author_counts() == [] and LA.author_model_count("sanix") == 0
