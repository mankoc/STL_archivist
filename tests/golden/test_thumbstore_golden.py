"""Golden tests: the native thumbnail store and the py-lmdb one share one format.

A library must open with either backend (compatibility rule 6), so each test
writes with one backend, closes it (rule 3: never both open in a process) and
reads or rewrites with the other. Same payloads must produce the same keys.
"""
import lmdb
import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "ThumbStore"):
    pytest.skip("stl_native predates ThumbStore; rebuild it", allow_module_level=True)

from app import native
from app.utils import thumbnailer as T


@pytest.fixture
def use(monkeypatch):
    """use("native") / use("lmdb"): reopen the store with that backend."""
    def switch(backend, path):
        T.close_thumbnail_store()
        if backend == "lmdb":
            monkeypatch.setenv("STL_NATIVE", "0")
        else:
            monkeypatch.delenv("STL_NATIVE", raising=False)
        native.reload()
        T.init_thumbnail_store(path)
        assert T.store_backend() == backend
    yield switch
    T.close_thumbnail_store()
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()


PAYLOADS = {1: b"\xff\xd8red", 2: b"\xff\xd8red", 3: b"GIF89a-anim", 4: b"\xff\xd8" + bytes(range(256)) * 40}


def _dump(path):
    """Every key/value in the store, read with plain py-lmdb (store must be closed)."""
    env = lmdb.open(str(path), readonly=True, lock=False)
    try:
        with env.begin() as txn:
            return {bytes(k): bytes(v) for k, v in txn.cursor()}
    finally:
        env.close()


@pytest.mark.parametrize("writer,reader", [("lmdb", "native"), ("native", "lmdb")])
def test_store_written_by_one_backend_reads_with_the_other(tmp_path, use, writer, reader):
    path = tmp_path / "thumbs.lmdb"
    use(writer, path)
    T.write_raw_thumbnails_batch(list(PAYLOADS.items()))
    T._store.put_raw([(T._key(9), b"legacy-entry")])
    T.delete_thumbnails([4])
    stats = T.store_stats()

    use(reader, path)
    assert T.get_thumbnail_bytes_many([1, 2, 3, 4, 9]) == {1: PAYLOADS[1], 2: PAYLOADS[2],
                                                           3: PAYLOADS[3], 9: b"legacy-entry"}
    assert T.store_stats() == stats


def test_both_backends_write_identical_keys(tmp_path, use):
    dumps = {}
    for backend in ("lmdb", "native"):
        path = tmp_path / f"{backend}.lmdb"
        use(backend, path)
        T.write_raw_thumbnails_batch(list(PAYLOADS.items()))
        T.delete_thumbnails([3])
        T.close_thumbnail_store()
        dumps[backend] = _dump(path)
    assert dumps["lmdb"] == dumps["native"]
    assert len([k for k in dumps["native"] if k[:1] == b"D"]) == 3    # 1 and 2 share a blob


def test_dedup_and_compact_across_backends(tmp_path, use):
    path = tmp_path / "thumbs.lmdb"
    use("lmdb", path)
    for image_id in range(1, 31):                                      # a pre-dedup library
        T._store.put_raw([(T._key(image_id), PAYLOADS[1 + image_id % 3])])
    use("native", path)
    report = T.deduplicate_store()
    assert report["converted"] == 30 and report["blobs_after"] == 2   # payloads 1 and 2 are equal
    use("lmdb", path)
    assert T.store_stats()["legacy"] == 0
    assert T.get_thumbnail_bytes(30) == PAYLOADS[1]
