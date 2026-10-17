//! All-pairs Hamming search over 128-bit perceptual hashes (dHash).
//!
//! Used by Find Duplicate Archives → Images. Each hash is two `u64` words
//! (high, low). Every pair is compared: in real libraries near-identical hashes
//! cluster, so index-based shortcuts (pigeonhole / multi-index hashing) degrade
//! badly; see `app/services/image_duplicates.py`.

use rayon::prelude::*;

use crate::control::{Control, Stopped};
use crate::pairs::{triangle_work, Pairs};

/// Rows per parallel task and columns per cache tile. Small tiles keep both
/// hash slices in L1/L2; measured fastest for the NumPy version too.
const ROW_BLOCK: usize = 64;
const COL_TILE: usize = 4096;

/// Pairs `(i, j)`, `i < j`, whose hashes differ in at most `max_dist` bits.
///
/// `hi[k]` and `lo[k]` are the high and low 64 bits of hash `k`. Progress is
/// reported to `ctl` as pairs examined out of `n(n+1)/2`.
pub fn near_pairs_128(
    hi: &[u64],
    lo: &[u64],
    max_dist: u32,
    ctl: &Control,
) -> Result<Pairs<u8>, Stopped> {
    assert_eq!(hi.len(), lo.len(), "hi and lo must have the same length");
    let n = hi.len();
    ctl.set_total(triangle_work(0..n, n));

    let starts: Vec<usize> = (0..n).step_by(ROW_BLOCK).collect();
    let parts = starts
        .par_iter()
        .map(|&r0| {
            let r1 = (r0 + ROW_BLOCK).min(n);
            let mut out = Pairs::default();
            let mut c0 = r0;
            while c0 < n {
                ctl.check()?;
                let c1 = (c0 + COL_TILE).min(n);
                for i in r0..r1 {
                    let start = c0.max(i + 1);
                    if start >= c1 {
                        continue;
                    }
                    let (h, l) = (hi[i], lo[i]);
                    for (off, (&hj, &lj)) in hi[start..c1].iter().zip(&lo[start..c1]).enumerate() {
                        let d = (h ^ hj).count_ones() + (l ^ lj).count_ones();
                        if d <= max_dist {
                            out.push(i, start + off, d as u8);
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

    fn brute(hi: &[u64], lo: &[u64], max: u32) -> Vec<(u32, u32, u8)> {
        let mut v = vec![];
        for i in 0..hi.len() {
            for j in i + 1..hi.len() {
                let d = (hi[i] ^ hi[j]).count_ones() + (lo[i] ^ lo[j]).count_ones();
                if d <= max {
                    v.push((i as u32, j as u32, d as u8));
                }
            }
        }
        v
    }

    fn sorted(p: Pairs<u8>) -> Vec<(u32, u32, u8)> {
        let mut v: Vec<_> = (0..p.len()).map(|k| (p.i[k], p.j[k], p.score[k])).collect();
        v.sort();
        v
    }

    #[test]
    fn matches_brute_force_across_tiles() {
        // Deterministic pseudo-random hashes with planted near-duplicates; more
        // rows than one tile so block and tile boundaries are crossed.
        let mut x: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut next = || {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            x
        };
        let (mut hi, mut lo) = (vec![], vec![]);
        for k in 0..5000 {
            if k % 7 == 0 && k > 0 {
                hi.push(hi[k - 1] ^ (1 << (k % 64)));
                lo.push(lo[k - 1]);
            } else {
                hi.push(next());
                lo.push(next());
            }
        }
        for max in [0, 1, 3, 40] {
            let got = sorted(near_pairs_128(&hi, &lo, max, &Control::new()).unwrap());
            assert_eq!(got, brute(&hi, &lo, max), "max_dist {max}");
        }
    }

    #[test]
    fn reports_full_progress_and_stops() {
        let hi = vec![0u64; 300];
        let lo = vec![0u64; 300];
        let ctl = Control::new();
        near_pairs_128(&hi, &lo, 0, &ctl).unwrap();
        assert_eq!(ctl.progress(), (300 * 301 / 2, 300 * 301 / 2));
        let stopped = Control::new();
        stopped.request_stop();
        assert_eq!(near_pairs_128(&hi, &lo, 0, &stopped), Err(Stopped));
    }
}
