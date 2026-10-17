"""Find archive pairs whose thumbnails look alike (Find Duplicate Archives → Images).

Both matching methods share one pipeline:

1. **Load** the images of present (not missing) models, each with an *item*:
   its 128-bit dHash or its CLIP embedding.
2. **Collapse identical items.**  Libraries repeat renders across releases; in
   the reference library 363k images held only 190k distinct items, so the
   quadratic scan does a quarter of the work.
3. **Find near pairs** among distinct items with a cache-tiled brute-force scan
   (dHash: XOR + popcount; embeddings: float32 matrix product), in threads.
   The scan always covers every pair: an earlier version stopped once a few
   chunks found nothing new, silently missing later duplicates.
4. **Skip shared images.**  An item that appears in more than *max_models*
   models, directly or through a near-identical variant, is a banner, logo or
   promo render shipped with many releases, not evidence that two archives are
   copies.  Without this, seven renders found in 749 archives each produced
   millions of meaningless pairs.
5. **Aggregate** image pairs into model pairs (number of matching image pairs
   and best score) with NumPy, then attach titles, paths and stored sizes.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import text

logger = logging.getLogger(__name__)

Progress   = Callable[[int, int], None]
Log        = Callable[[str], None]
ShouldStop = Callable[[], bool]

# Default for "skip images shared by more than N models".
DEFAULT_MAX_MODELS_PER_IMAGE = 10
# Rows handed to the result table; best matches first.
MAX_RESULTS = 100_000

# Tile shapes: small enough that a tile's temporaries stay in cache and the
# total buffer memory stays bounded whatever the library size.
_DHASH_TILE = (64, 2048)      # measured fastest of 32-256 rows x 2k-16k columns
_EMBED_TILE = (1024, 16384)


def _noop(*_a) -> None:
    pass


def _never() -> bool:
    return False


class Stopped(Exception):
    """Raised inside a scan when *should_stop* returns True."""


@dataclass
class ScanReport:
    """Result of a duplicate scan plus the counts the dialog reports."""
    results: list[dict] = field(default_factory=list)
    images: int = 0              # images with an item (hash or embedding)
    items: int = 0               # distinct items after collapsing identical ones
    near_pairs: int = 0          # item pairs within the threshold
    shared_skipped: int = 0      # distinct items skipped as shared images
    truncated: bool = False      # results cut to MAX_RESULTS
    seconds: float = 0.0


# ── Loading ──────────────────────────────────────────────────────────────────

def _present_images(session, extra_col: str = "") -> list[tuple]:
    sql = (f"SELECT i.id, i.model_id{', ' + extra_col if extra_col else ''} "
           "FROM images i JOIN models m ON m.id = i.model_id "
           "WHERE i.thumbnail_path IS NOT NULL AND m.is_missing IS NOT 1")
    return session.connection().exec_driver_sql(sql).all()


def load_dhash_items(session, log: Log = _noop, progress: Progress = _noop,
                     should_stop: ShouldStop = _never):
    """``(img_model, hi, lo)``: model id per image and its dHash halves.

    Stored hashes in the current 32-hex-char format are used as is; missing or
    outdated ones are computed from the stored thumbnail (in threads), as
    Database Maintenance → Recompute Image Hashes would.
    """
    from app.utils.image_hash import DHASH_HEX_LEN, _dhash, format_dhash, split_dhashes
    from app.utils.thumbnailer import get_thumbnail_bytes

    rows = _present_images(session, "i.dhash")
    good = [(mid, h) for _iid, mid, h in rows if isinstance(h, str) and len(h) == DHASH_HEX_LEN]
    todo = [(iid, mid) for iid, mid, h in rows if not (isinstance(h, str) and len(h) == DHASH_HEX_LEN)]
    if todo:
        log(f"Computing {len(todo):,} missing perceptual hash(es)…")

        def one(row):
            iid, mid = row
            jpeg = get_thumbnail_bytes(iid)
            h = _dhash(jpeg) if jpeg else None
            return mid, (format_dhash(h) if h is not None else None)

        with ThreadPoolExecutor(min(os.cpu_count() or 1, 8)) as pool:
            for k, (mid, h) in enumerate(pool.map(one, todo, chunksize=64), 1):
                if h is not None:
                    good.append((mid, h))
                if k % 200 == 0:
                    if should_stop():
                        raise Stopped
                    progress(k, len(todo))
    if not good:
        return np.empty(0, np.int64), np.empty(0, np.uint64), np.empty(0, np.uint64)
    img_model = np.fromiter((m for m, _h in good), np.int64, len(good))
    hi, lo = split_dhashes([h for _m, h in good])
    return img_model, hi, lo


def load_embedding_items(session):
    """``(img_model, matrix)``: model id per image and its CLIP vector (float32)."""
    from app.services.image_similarity import load_model_vectors
    return load_model_vectors(session)


# ── Collapsing identical items ───────────────────────────────────────────────

def _collapse(keys: np.ndarray):
    """Unique rows of *keys* (2-D, fixed width): ``(first_index, inverse)``."""
    keys = np.ascontiguousarray(keys)
    as_void = keys.view(np.dtype((np.void, keys.dtype.itemsize * keys.shape[1]))).ravel()
    _u, first, inverse = np.unique(as_void, return_index=True, return_inverse=True)
    return first, inverse.ravel()


# ── Near-pair scans ──────────────────────────────────────────────────────────

def _tiled_upper(n: int, tile: tuple[int, int], scan_tile, progress: Progress,
                 should_stop: ShouldStop, workers: int):
    """Run *scan_tile(r0, r1, c0, c1, buffers)* over the upper triangle in threads.

    Each task owns one row block and walks its column tiles left to right, so
    work per task shrinks along the triangle; progress is reported as the share
    of pairs covered.  Returns concatenated ``(i, j, score)`` with ``i < j``.
    """
    rb, cb = tile
    starts = list(range(0, n, rb))
    total = n * (n + 1) // 2 or 1
    done = 0

    def task(r0: int):
        r1 = min(r0 + rb, n)
        out_i, out_j, out_s = [], [], []
        buffers: dict = {}
        for c0 in range(r0, n, cb):
            if should_stop():
                raise Stopped
            c1 = min(c0 + cb, n)
            i, j, s = scan_tile(r0, r1, c0, c1, buffers)
            if len(i):
                keep = (c0 + j) > (r0 + i)          # strict upper triangle
                out_i.append(r0 + i[keep]); out_j.append(c0 + j[keep]); out_s.append(s[keep])
        work = (r1 - r0) * (n - r0)
        return out_i, out_j, out_s, work

    parts_i, parts_j, parts_s = [], [], []
    with ThreadPoolExecutor(workers) as pool:
        for out_i, out_j, out_s, work in pool.map(task, starts):
            parts_i += out_i; parts_j += out_j; parts_s += out_s
            done += work
            progress(min(done, total), total)
    if not parts_i:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.float32)
    return np.concatenate(parts_i), np.concatenate(parts_j), np.concatenate(parts_s)


def _native_pairs(name: str, args: tuple, score_dtype, progress: Progress,
                  should_stop: ShouldStop):
    """Run ``stl_native.<name>`` if available: ``(i, j, score)`` arrays, or None.

    The native scans (``rust/stl-core/src/{hamming,embedding}.rs``) return the
    same pairs as the NumPy scans below; golden tests in ``tests/golden/``
    check that. None means "use the NumPy path".
    """
    from app import native
    mod = native.module()
    if mod is None:
        return None
    try:
        i, j, s = getattr(mod, name)(*args, progress=progress, should_stop=should_stop)
    except mod.Stopped:
        raise Stopped from None
    except (TypeError, ValueError, AttributeError):
        return None          # stale build or unexpected input: NumPy path
    return (np.frombuffer(i, np.uint32).astype(np.int64),
            np.frombuffer(j, np.uint32).astype(np.int64),
            np.frombuffer(s, score_dtype))


def near_dhash_pairs(hi, lo, max_dist: int, progress: Progress = _noop,
                     should_stop: ShouldStop = _never, workers: int | None = None):
    """Item pairs ``(i, j, distance)`` with Hamming distance <= *max_dist*, ``i < j``.

    Native core when available (6.6x faster on 190k hashes: 2.1 s vs 13.9 s),
    otherwise the NumPy scan.
    """
    out = _native_pairs("hamming_pairs",
                        (np.ascontiguousarray(hi, np.uint64), np.ascontiguousarray(lo, np.uint64),
                         int(max_dist)),
                        np.uint8, progress, should_stop)
    if out is not None:
        return out
    return _near_dhash_pairs_py(hi, lo, max_dist, progress, should_stop, workers)


def _near_dhash_pairs_py(hi, lo, max_dist, progress, should_stop, workers):
    from app.utils.image_hash import popcount64

    if hasattr(np, "bitwise_count"):
        def popcount_into(x, out):
            np.bitwise_count(x, out=out)
    else:                                            # NumPy < 2.0
        def popcount_into(x, out):
            out[...] = popcount64(x)

    def scan_tile(r0, r1, c0, c1, buf):
        shape = (r1 - r0, c1 - c0)
        if buf.get("shape") != shape:
            buf.update(shape=shape, x=np.empty(shape, np.uint64),
                       p=np.empty(shape, np.uint8), q=np.empty(shape, np.uint8))
        x, p, q = buf["x"], buf["p"], buf["q"]
        np.bitwise_xor(lo[r0:r1, None], lo[None, c0:c1], out=x)
        popcount_into(x, p)
        np.bitwise_xor(hi[r0:r1, None], hi[None, c0:c1], out=x)
        popcount_into(x, q)
        np.add(p, q, out=p)
        i, j = np.nonzero(p <= max_dist)
        return i, j, p[i, j]

    return _tiled_upper(len(hi), _DHASH_TILE, scan_tile, progress, should_stop,
                        workers or os.cpu_count() or 1)


def near_embedding_pairs(vecs, threshold: float, progress: Progress = _noop,
                         should_stop: ShouldStop = _never, workers: int = 4):
    """Item pairs ``(i, j, cosine similarity)`` with similarity >= *threshold*, ``i < j``.

    *vecs* must be L2-normalised float32.  Native core when available (3x
    faster on 190k vectors: 29 s vs 87 s); pairs within float rounding of the
    threshold (about 1e-6) can differ between the two paths.
    """
    vecs = np.ascontiguousarray(vecs, np.float32)
    if vecs.ndim == 2 and len(vecs):
        out = _native_pairs("cosine_pairs", (vecs, vecs.shape[1], float(threshold)),
                            np.float32, progress, should_stop)
        if out is not None:
            return out
    return _near_embedding_pairs_py(vecs, threshold, progress, should_stop, workers)


def _near_embedding_pairs_py(vecs, threshold, progress, should_stop, workers):
    """NumPy fallback: tiles multiplied in BLAS (itself multithreaded, so few workers)."""
    def scan_tile(r0, r1, c0, c1, buf):
        shape = (r1 - r0, c1 - c0)
        if buf.get("shape") != shape:
            buf.update(shape=shape, out=np.empty(shape, np.float32))
        out = buf["out"]
        np.matmul(vecs[r0:r1], vecs[c0:c1].T, out=out)
        i, j = np.nonzero(out >= threshold)
        return i, j, out[i, j]

    return _tiled_upper(len(vecs), _EMBED_TILE, scan_tile, progress, should_stop, workers)


# ── Shared images ────────────────────────────────────────────────────────────

def _expand(src, dst, off, cnt, ent_model):
    """For each edge, one row per model of *dst*: ``(src repeated, model)``."""
    sizes = cnt[dst]
    total = int(sizes.sum())
    if total == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    rep = np.repeat(np.arange(len(dst)), sizes)
    within = np.arange(total) - np.repeat(np.cumsum(sizes) - sizes, sizes)
    return src[rep], ent_model[off[dst][rep] + within]


def shared_items(n_items: int, ent_item, ent_model, a, b, max_models: int) -> np.ndarray:
    """Boolean mask of items shared by more than *max_models* models.

    An item counts the distinct models of itself and of its near-identical
    variants, so a banner re-encoded slightly differently in each release is
    caught even when no single variant crosses the limit.
    *ent_item*/*ent_model* are the distinct (item, model) pairs sorted by item.
    """
    cnt = np.bincount(ent_item, minlength=n_items)
    over = cnt > max_models            # over the limit on its own
    shared = over.copy()
    if len(a) == 0:
        return shared
    # A near variant of an item over the limit on its own is over it too.
    touch = over[a] | over[b]
    shared[a[touch]] = True
    shared[b[touch]] = True
    # The rest: exact distinct-model count over the item and its neighbours,
    # only where a simple upper bound could exceed the limit.  Every remaining
    # neighbour has at most max_models models, so the expansion is bounded.
    # (A neighbour marked shared above still contributes its own models.)
    keep = ~touch
    a, b = a[keep], b[keep]
    bound = cnt.copy()
    np.add.at(bound, a, cnt[b])
    np.add.at(bound, b, cnt[a])
    maybe = np.flatnonzero((bound > max_models) & ~shared)
    if len(maybe) == 0:
        return shared
    off = np.r_[0, np.cumsum(cnt)[:-1]]
    is_maybe = np.zeros(n_items, bool)
    is_maybe[maybe] = True
    src = np.r_[a, b]
    dst = np.r_[b, a]
    sel = is_maybe[src]
    nb_item, nb_model = _expand(src[sel], dst[sel], off, cnt, ent_model)
    own = is_maybe[ent_item]
    pairs = np.stack([np.r_[nb_item, ent_item[own]], np.r_[nb_model, ent_model[own]]], 1)
    pairs = np.unique(pairs, axis=0)
    shared |= np.bincount(pairs[:, 0], minlength=n_items) > max_models
    return shared


# ── Aggregation ──────────────────────────────────────────────────────────────

def _reduce(keys, weights, scores, higher_is_better: bool):
    """Per distinct key: summed weight and best score."""
    if len(keys) == 0:
        return keys, weights, scores
    order = np.lexsort((-scores if higher_is_better else scores, keys))
    k = keys[order]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    return k[starts], np.add.reduceat(weights[order], starts), scores[order][starts]


def model_pairs(n_items: int, img_item, img_model, a, b, score, perfect,
                higher_is_better: bool, max_models: int, chunk: int = 2_000_000):
    """Aggregate item matches into ``(model_a, model_b, image_pairs, best)`` arrays.

    Image pairs between two models count every pair of their images that
    match, including identical images (the item paired with itself, scored
    *perfect*).  Items shared by more than *max_models* models are left out.
    Returns the arrays plus the number of skipped items.
    """
    ent, ent_count = np.unique(np.stack([img_item, img_model], 1), axis=0, return_counts=True)
    ent_item, ent_model = ent[:, 0], ent[:, 1]
    shared = shared_items(n_items, ent_item, ent_model, a, b, max_models)

    cnt = np.bincount(ent_item, minlength=n_items)
    off = np.r_[0, np.cumsum(cnt)[:-1]]
    keep = ~(shared[a] | shared[b])
    self_items = np.flatnonzero((cnt > 1) & ~shared)
    eu = np.r_[a[keep], self_items]
    ev = np.r_[b[keep], self_items]
    es = np.r_[score[keep], np.full(len(self_items), perfect, dtype=score.dtype)]
    is_self = np.r_[np.zeros(keep.sum(), bool), np.ones(len(self_items), bool)]

    acc_k, acc_w, acc_s = [], [], []
    # Each edge expands to |models(u)| x |models(v)| rows; take edges in
    # chunks of about *chunk* rows so memory stays bounded.
    csum = np.cumsum(cnt[eu] * cnt[ev])
    start = 0
    while start < len(eu):
        base = int(csum[start - 1]) if start else 0
        end = max(start + 1, int(np.searchsorted(csum, base + chunk, side="right")))
        u, v, s, sf = eu[start:end], ev[start:end], es[start:end], is_self[start:end]
        start = end
        na, nb = cnt[u], cnt[v]
        n = na * nb
        total = int(n.sum())
        if total == 0:
            continue
        e = np.repeat(np.arange(len(u)), n)
        pos = np.arange(total) - np.repeat(np.cumsum(n) - n, n)
        ia = off[u][e] + pos // nb[e]
        ib = off[v][e] + pos % nb[e]
        ma, mb = ent_model[ia], ent_model[ib]
        ok = np.where(sf[e], ma < mb, ma != mb)
        ma, mb = ma[ok], mb[ok]
        w = (ent_count[ia] * ent_count[ib])[ok]
        keys = np.minimum(ma, mb) << 32 | np.maximum(ma, mb)
        k, wsum, best = _reduce(keys, w, s[e][ok], higher_is_better)
        acc_k.append(k); acc_w.append(wsum); acc_s.append(best)

    if not acc_k:
        empty = np.empty(0, np.int64)
        return empty, empty, empty, np.empty(0, score.dtype), int(shared.sum())
    k, w, s = _reduce(np.concatenate(acc_k), np.concatenate(acc_w), np.concatenate(acc_s),
                      higher_is_better)
    return k >> 32, k & 0xFFFFFFFF, w, s, int(shared.sum())


# ── Entry points ─────────────────────────────────────────────────────────────

def _results(session, ma, mb, pairs, best, img_model, higher_is_better: bool):
    """Result rows for the dialog, best matches first, capped at MAX_RESULTS."""
    order = np.lexsort((-pairs, -best if higher_is_better else best))
    truncated = len(order) > MAX_RESULTS
    order = order[:MAX_RESULTS]
    imgs = dict(zip(*np.unique(img_model, return_counts=True)))
    info = {mid: (title, path, size) for mid, title, path, size in session.connection()
            .exec_driver_sql("SELECT id, title, archive_path, archive_size FROM models")}
    rows = []
    for k in order.tolist():
        a, b = int(ma[k]), int(mb[k])
        ia, ib = info.get(a), info.get(b)
        if ia is None or ib is None:
            continue
        rows.append({
            "id_a": a, "title_a": ia[0] or Path(ia[1] or "").stem, "path_a": ia[1] or "",
            "size_a": ia[2], "imgs_a": int(imgs.get(a, 0)),
            "id_b": b, "title_b": ib[0] or Path(ib[1] or "").stem, "path_b": ib[1] or "",
            "size_b": ib[2], "imgs_b": int(imgs.get(b, 0)),
            "pairs": int(pairs[k]),
            # float32 rounding can put an identical pair's cosine at 1.0000007
            "best": min(float(best[k]), 1.0) if higher_is_better else int(best[k]),
        })
    return rows, truncated


def find_dhash_duplicates(session, max_dist: int,
                          max_models: int = DEFAULT_MAX_MODELS_PER_IMAGE,
                          progress: Progress = _noop, log: Log = _noop,
                          should_stop: ShouldStop = _never) -> ScanReport:
    """Archive pairs whose thumbnails' dHashes differ in at most *max_dist* bits."""
    t0 = time.perf_counter()
    report = ScanReport()
    log("Loading image hashes…")
    img_model, hi, lo = load_dhash_items(session, log, progress, should_stop)
    report.images = len(img_model)
    if report.images < 2:
        return report
    first, img_item = _collapse(np.stack([hi, lo], 1))
    hi, lo = np.ascontiguousarray(hi[first]), np.ascontiguousarray(lo[first])
    report.items = len(first)
    log(f"Comparing {report.items:,} distinct image(s) ({report.images:,} images)…")
    a, b, d = near_dhash_pairs(hi, lo, max_dist, progress, should_stop)
    report.near_pairs = len(a)
    log("Grouping matches by archive…")
    ma, mb, pairs, best, report.shared_skipped = model_pairs(
        report.items, img_item, img_model, a, b, d.astype(np.int16), 0, False, max_models)
    report.results, report.truncated = _results(session, ma, mb, pairs, best, img_model, False)
    report.seconds = time.perf_counter() - t0
    return report


def find_embedding_duplicates(session, threshold: float,
                              max_models: int = DEFAULT_MAX_MODELS_PER_IMAGE,
                              progress: Progress = _noop, log: Log = _noop,
                              should_stop: ShouldStop = _never) -> ScanReport:
    """Archive pairs whose thumbnails' CLIP embeddings have cosine similarity >= *threshold*."""
    t0 = time.perf_counter()
    report = ScanReport()
    log("Loading image embeddings…")
    img_model, mat = load_embedding_items(session)
    report.images = len(img_model)
    if report.images < 2:
        return report
    first, img_item = _collapse(mat)
    vecs = mat[first]
    del mat
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = np.ascontiguousarray(vecs / np.where(norms > 0, norms, 1), dtype=np.float32)
    report.items = len(first)
    log(f"Comparing {report.items:,} distinct image(s) ({report.images:,} images)…")
    a, b, s = near_embedding_pairs(vecs, threshold, progress, should_stop)
    report.near_pairs = len(a)
    log("Grouping matches by archive…")
    ma, mb, pairs, best, report.shared_skipped = model_pairs(
        report.items, img_item, img_model, a, b, s.astype(np.float32), 1.0, True, max_models)
    report.results, report.truncated = _results(session, ma, mb, pairs, best, img_model, True)
    report.seconds = time.perf_counter() - t0
    return report
