//! Result type for all-pairs similarity scans.

/// Matching item pairs `(i[k], j[k])` with `i[k] < j[k]` and their score.
///
/// Stored as three parallel vectors so callers can hand them to NumPy (or any
/// columnar consumer) without per-pair objects. Order is unspecified.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Pairs<S> {
    pub i: Vec<u32>,
    pub j: Vec<u32>,
    pub score: Vec<S>,
}

impl<S> Pairs<S> {
    pub fn len(&self) -> usize {
        self.i.len()
    }

    pub fn is_empty(&self) -> bool {
        self.i.is_empty()
    }

    pub(crate) fn push(&mut self, i: usize, j: usize, score: S) {
        self.i.push(i as u32);
        self.j.push(j as u32);
        self.score.push(score);
    }

    /// Concatenate per-thread results.
    pub(crate) fn concat(parts: Vec<Pairs<S>>) -> Pairs<S> {
        let total = parts.iter().map(Pairs::len).sum();
        let mut out = Pairs {
            i: Vec::with_capacity(total),
            j: Vec::with_capacity(total),
            score: Vec::with_capacity(total),
        };
        for p in parts {
            out.i.extend(p.i);
            out.j.extend(p.j);
            out.score.extend(p.score);
        }
        out
    }
}

/// Number of pairs `(i, j)` with `i <= j < n` for rows `rows.start..rows.end`,
/// i.e. the work a row block covers in an upper-triangle scan.
pub(crate) fn triangle_work(rows: std::ops::Range<usize>, n: usize) -> u64 {
    rows.map(|i| (n - i) as u64).sum()
}
