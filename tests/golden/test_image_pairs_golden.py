"""Golden test: the native model-pair aggregation (``stl_core::image_pairs``,
used by the server) against ``image_duplicates.model_pairs``."""
from itertools import combinations

import numpy as np
import pytest

stl_native = pytest.importorskip("stl_native")

import app.services.image_duplicates as D  # noqa: E402


@pytest.mark.parametrize("seed", range(200))
def test_model_pairs_match_python(seed):
    r = np.random.default_rng(seed)
    n_items, n_imgs = int(r.integers(2, 25)), int(r.integers(2, 90))
    max_models = int(r.choice([1, 2, 3, 5, 100]))
    img_item = r.integers(0, n_items, n_imgs)
    img_model = r.integers(100, 100 + int(r.integers(2, 16)), n_imgs)
    edges = [(u, v) for u, v in combinations(range(n_items), 2) if r.random() < 0.2] or [(0, 1)]
    a = np.array([e[0] for e in edges]); b = np.array([e[1] for e in edges])
    higher = bool(seed % 2)
    if higher:   # cosine similarities
        s = r.uniform(0.8, 1.0, len(edges)).astype(np.float32)
        perfect = 1.0
    else:        # Hamming distances
        s = r.integers(0, 12, len(edges)).astype(np.int16)
        perfect = 0
    ma, mb, pairs, best, skipped = D.model_pairs(n_items, img_item, img_model, a, b, s, perfect, higher, max_models)
    want = {(int(x), int(y)): (int(p), float(q)) for x, y, p, q in zip(ma, mb, pairs, best)}
    got_pairs, got_skipped = stl_native.model_pairs(
        n_items, img_item.tolist(), img_model.tolist(), a.tolist(), b.tolist(), [float(x) for x in s], float(perfect), higher, max_models)
    got = {(x, y): (p, q) for x, y, p, q in got_pairs}
    assert got_skipped == skipped
    assert got.keys() == want.keys()
    for k, (p, q) in want.items():
        assert got[k][0] == p and got[k][1] == pytest.approx(q, abs=1e-6), k
