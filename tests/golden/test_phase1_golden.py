"""Golden tests for the Phase 1 ports: Rust and Python must give the same results.

Hamming and Levenshtein results must be identical; cosine similarities may
differ in the last float bits (different summation order), so pairs within
1e-5 of the threshold are allowed to differ. Skipped when stl_native is absent.
"""
import random

import numpy as np
import pytest

stl_native = pytest.importorskip("stl_native")

from app.services.image_duplicates import _near_dhash_pairs_py, _near_embedding_pairs_py
from app.utils.string_utils import _levenshtein_pairs_py, levenshtein


def _noop(*_a):
    pass


def _never():
    return False


def _triples(i, j, s):
    return {(int(a), int(b), s_) for a, b, s_ in zip(i, j, s)}


def test_hamming_pairs_identical_to_numpy():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 2**63, size=(1500, 2), dtype=np.uint64)
    near = base[:300].copy()
    near[:, 1] ^= np.uint64(0b1011)                      # 3-bit variants
    hashes = np.vstack([base, near, base[:50]])          # plus exact copies
    hi, lo = np.ascontiguousarray(hashes[:, 0]), np.ascontiguousarray(hashes[:, 1])
    for max_dist in (0, 3, 12, 40):
        ri, rj, rd = stl_native.hamming_pairs(hi, lo, max_dist)
        rust = _triples(np.frombuffer(ri, np.uint32), np.frombuffer(rj, np.uint32),
                        np.frombuffer(rd, np.uint8).tolist())
        py = _triples(*_near_dhash_pairs_py(hi, lo, max_dist, _noop, _never, 4))
        assert rust == {(a, b, int(d)) for a, b, d in py}, max_dist


def test_cosine_pairs_match_numpy_within_rounding():
    rng = np.random.default_rng(2)
    v = rng.standard_normal((900, 64)).astype(np.float32)
    v[700:] = v[:200] + 0.05 * rng.standard_normal((200, 64)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    thr = 0.9
    ri, rj, rs = stl_native.cosine_pairs(v, 64, thr)
    ri, rj, rs = (np.frombuffer(ri, np.uint32), np.frombuffer(rj, np.uint32),
                  np.frombuffer(rs, np.float32))
    pi, pj, ps = _near_embedding_pairs_py(v, thr, _noop, _never, 2)
    rust = {(int(a), int(b)) for a, b in zip(ri, rj)}
    py = {(int(a), int(b)) for a, b in zip(pi, pj)}
    assert len(py) > 150
    for a, b in rust ^ py:                       # only threshold-edge disagreements
        assert abs(float(v[a] @ v[b]) - thr) < 1e-5
    exact = (v[ri] * v[rj]).sum(1)
    assert np.allclose(rs, exact, atol=1e-5)


def test_stop_before_start():
    hi = lo = np.zeros(10, np.uint64)
    with pytest.raises(stl_native.Stopped):
        stl_native.hamming_pairs(hi, lo, 1, should_stop=lambda: True)


def test_progress_reaches_total():
    hi = lo = np.zeros(400, np.uint64)
    seen = []
    stl_native.hamming_pairs(hi, lo, 0, progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (400 * 401 // 2, 400 * 401 // 2)


WORDS = ["dragon", "dragoon", "drakon", "Dragon", "wagon", "ドラゴン", "ドラコン",
         "ñandú", "nandu", "🐉 bust", "🐉 busts", "", "a", "ab", "kitten", "sitting",
         "éclair", "éclair", "dragon's bust", "dragons bust"]


def test_levenshtein_pairs_identical_to_python():
    rnd = random.Random(3)
    values = WORDS + ["".join(rnd.choice("abcdeñü龍 ") for _ in range(rnd.randint(0, 12)))
                      for _ in range(400)]
    for max_d in (0, 1, 2, 4):
        assert stl_native.levenshtein_pairs(values, max_d) == _levenshtein_pairs_py(values, max_d)


def test_levenshtein_distances_match_full_algorithm():
    for i, j, d in stl_native.levenshtein_pairs(WORDS, 30):
        assert d == levenshtein(WORDS[i], WORDS[j])
