//! All-pairs cosine-similarity search over CLIP image embeddings.
//!
//! Used by Find Duplicate Archives → Images (AI embedding). Vectors must be
//! L2-normalised, so the dot product is the cosine similarity. Each row block
//! is multiplied against the column tiles to its right with `gemm`, which picks
//! AVX2/AVX-512/FMA kernels at run time; rayon spreads row blocks over cores.

use gemm::{gemm, Parallelism};
use rayon::prelude::*;

use crate::control::{Control, Stopped};
use crate::pairs::{triangle_work, Pairs};

const ROW_BLOCK: usize = 256;
const COL_TILE: usize = 4096;

/// Pairs `(i, j)`, `i < j`, whose vectors have dot product >= `threshold`.
///
/// `vecs` holds `n` row-major vectors of length `dim`. Progress is reported to
/// `ctl` as pairs examined out of `n(n+1)/2`.
pub fn near_pairs_cosine(
    vecs: &[f32],
    dim: usize,
    threshold: f32,
    ctl: &Control,
) -> Result<Pairs<f32>, Stopped> {
    assert!(dim > 0 && vecs.len().is_multiple_of(dim), "vecs length must be a multiple of dim");
    let n = vecs.len() / dim;
    ctl.set_total(triangle_work(0..n, n));

    let starts: Vec<usize> = (0..n).step_by(ROW_BLOCK).collect();
    let parts = starts
        .par_iter()
        .map(|&r0| {
            let r1 = (r0 + ROW_BLOCK).min(n);
            let m = r1 - r0;
            let mut tile = vec![0f32; m * COL_TILE];
            let mut out = Pairs::default();
            let mut c0 = r0;
            while c0 < n {
                ctl.check()?;
                let c1 = (c0 + COL_TILE).min(n);
                let w = c1 - c0;
                // tile[m x w] = rows[r0..r1] (m x dim) * cols[c0..c1]^T (dim x w)
                // SAFETY: all pointers address in-bounds, non-overlapping
                // buffers with the strides below; `tile` is exclusively ours.
                unsafe {
                    gemm(
                        m, w, dim,
                        tile.as_mut_ptr(), 1, w as isize,
                        false,
                        vecs.as_ptr().add(r0 * dim), 1, dim as isize,
                        vecs.as_ptr().add(c0 * dim), dim as isize, 1,
                        0.0, 1.0,
                        false, false, false,
                        Parallelism::None,
                    );
                }
                for li in 0..m {
                    let i = r0 + li;
                    let row = &tile[li * w..li * w + w];
                    let from = (i + 1).saturating_sub(c0).min(w);
                    for (lj, &s) in row.iter().enumerate().skip(from) {
                        if s >= threshold {
                            out.push(i, c0 + lj, s);
                        }
                    }
                }
                c0 = c1;
            }
            ctl.advance(triangle_work(r0..r1, n));
            Ok(out)
        })
        .collect::<Result<Vec<_>, Stopped>>()?;
    Ok(Pairs::concat(parts))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn normalised(n: usize, dim: usize) -> Vec<f32> {
        let mut x: u32 = 12345;
        let mut v: Vec<f32> = (0..n * dim)
            .map(|_| {
                x = x.wrapping_mul(1_103_515_245).wrapping_add(12345);
                (x >> 8) as f32 / (1u32 << 24) as f32 - 0.5
            })
            .collect();
        // plant near-duplicates
        for k in (5..n).step_by(9) {
            for d in 0..dim {
                v[k * dim + d] = v[(k - 5) * dim + d] + if d == 0 { 0.01 } else { 0.0 };
            }
        }
        for row in v.chunks_mut(dim) {
            let norm = row.iter().map(|a| a * a).sum::<f32>().sqrt();
            row.iter_mut().for_each(|a| *a /= norm);
        }
        v
    }

    #[test]
    fn matches_naive_dot_products() {
        let (n, dim) = (700, 24); // > ROW_BLOCK and crosses the diagonal tile
        let v = normalised(n, dim);
        let got = near_pairs_cosine(&v, dim, 0.95, &Control::new()).unwrap();
        let mut want = vec![];
        for i in 0..n {
            for j in i + 1..n {
                let s: f32 = (0..dim).map(|d| v[i * dim + d] * v[j * dim + d]).sum();
                if s >= 0.95 {
                    want.push((i as u32, j as u32, s));
                }
            }
        }
        let mut have: Vec<_> = (0..got.len()).map(|k| (got.i[k], got.j[k], got.score[k])).collect();
        have.sort_by_key(|h| (h.0, h.1));
        assert_eq!(have.len(), want.len());
        assert!(want.len() > 50);
        for (h, w) in have.iter().zip(&want) {
            assert_eq!((h.0, h.1), (w.0, w.1));
            assert!((h.2 - w.2).abs() < 1e-5);
        }
    }
}
