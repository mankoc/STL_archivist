"""Tests for app.services.lookup_admin (the lookup editors' operations)."""
from app.services import lookup_admin as LA
from tests import factory


def _lib():
    ids = {k: factory.lookup(t, n) for k, t, n in [
        ("comics", "collections", "Comics"), ("games", "collections", "Games"),
        ("busts", "categories", "Busts"), ("minis", "categories", "Minis"),
        ("dragon", "tags", "dragon"), ("elf", "tags", "Elf"), ("unused", "tags", "unused"),
        ("s28", "scales", "28mm")]}
    ids["marvel"] = factory.insert("subcollections", name="Marvel", collection_id=ids["comics"])
    ids["zelda"] = factory.insert("subcollections", name="Zelda", collection_id=ids["games"])
    ids["m1"] = factory.add_model("p1", "h1", title="a", creator="Sanix", collection_id=ids["comics"],
                                  subcollection_id=ids["marvel"], category_id=ids["busts"],
                                  tags=("dragon", "Elf"), scales=("28mm",))
    ids["m2"] = factory.add_model("p2", "h2", title="b", creator="sanix", collection_id=ids["games"],
                                  subcollection_id=ids["zelda"], category_id=ids["minis"],
                                  tags=("Elf",))
    factory.insert("creators", name="Sanix", full_name="S. Anix")
    return ids


def _model(mid):
    m = factory.row("models", mid)
    return (m["creator"], m["collection_id"], m["subcollection_id"], m["category_id"],
            sorted(factory.names(mid, "tags")))


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
