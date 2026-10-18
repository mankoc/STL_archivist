//! Find Duplicate Archives → Images, aggregation: a port of `shared_items`
//! and `model_pairs` in `app/services/image_duplicates.py`.
//!
//! Images are first collapsed into *items* (identical hashes or vectors);
//! near pairs of items come from `hamming::near_pairs_128` or
//! `embedding::near_pairs_cosine`. This module turns item pairs into archive
//! (model) pairs: how many image pairs two models share, and their best score.
//! Items shared by more than `max_models` models (banners, logos), counting
//! their near variants, are left out.

use std::collections::{HashMap, HashSet};

/// One model pair: `(model_a, model_b, image_pairs, best_score)`, `model_a < model_b`.
pub type ModelPair = (i64, i64, i64, f64);

/// `shared_items`: which items are shared by more than `max_models` models.
/// `entries[item]` holds the item's distinct models with their image counts.
fn shared_items(entries: &[Vec<(i64, i64)>], edges: &[(usize, usize, f64)], max_models: usize) -> Vec<bool> {
    let cnt: Vec<usize> = entries.iter().map(Vec::len).collect();
    let over: Vec<bool> = cnt.iter().map(|&c| c > max_models).collect();
    let mut shared = over.clone();
    // A near variant of an item over the limit on its own is over it too.
    let mut rest = Vec::new();
    for &(a, b, _) in edges {
        if over[a] || over[b] {
            shared[a] = true;
            shared[b] = true;
        } else {
            rest.push((a, b));
        }
    }
    // The rest: the exact distinct-model count over an item and its remaining
    // neighbours, only where the simple upper bound could exceed the limit.
    let mut bound = cnt.clone();
    for &(a, b) in &rest {
        bound[a] += cnt[b];
        bound[b] += cnt[a];
    }
    let maybe: Vec<bool> = (0..entries.len()).map(|i| bound[i] > max_models && !shared[i]).collect();
    if !maybe.iter().any(|&m| m) {
        return shared;
    }
    let mut models: HashMap<usize, HashSet<i64>> = HashMap::new();
    for (i, m) in maybe.iter().enumerate() {
        if *m {
            models.insert(i, entries[i].iter().map(|&(model, _)| model).collect());
        }
    }
    for &(a, b) in &rest {
        if maybe[a] {
            models.get_mut(&a).expect("maybe item").extend(entries[b].iter().map(|&(m, _)| m));
        }
        if maybe[b] {
            models.get_mut(&b).expect("maybe item").extend(entries[a].iter().map(|&(m, _)| m));
        }
    }
    for (i, set) in models {
        if set.len() > max_models {
            shared[i] = true;
        }
    }
    shared
}

/// `model_pairs`: aggregate item matches into model pairs.
///
/// `img_item[k]` / `img_model[k]`: the item and model of image `k`. `edges`:
/// near item pairs `(u, v, score)`. Identical images (an item paired with
/// itself across models) score `perfect`. Returns the pairs sorted by
/// `(model_a, model_b)`, and how many items were left out as shared.
pub fn model_pairs(
    n_items: usize,
    img_item: &[usize],
    img_model: &[i64],
    edges: &[(usize, usize, f64)],
    perfect: f64,
    higher_is_better: bool,
    max_models: usize,
) -> (Vec<ModelPair>, usize) {
    // Distinct (item, model) entries with image counts, models in order.
    let mut entries: Vec<Vec<(i64, i64)>> = vec![Vec::new(); n_items];
    {
        let mut counts: HashMap<(usize, i64), i64> = HashMap::new();
        for (&item, &model) in img_item.iter().zip(img_model) {
            *counts.entry((item, model)).or_default() += 1;
        }
        for ((item, model), n) in counts {
            entries[item].push((model, n));
        }
        for e in &mut entries {
            e.sort_unstable();
        }
    }
    let shared = shared_items(&entries, edges, max_models);
    let better = |a: f64, b: f64| if higher_is_better { a.max(b) } else { a.min(b) };
    let mut acc: HashMap<(i64, i64), (i64, f64)> = HashMap::new();
    let mut add = |u: usize, v: usize, score: f64, is_self: bool| {
        for &(ma, ca) in &entries[u] {
            for &(mb, cb) in &entries[v] {
                if (is_self && ma >= mb) || (!is_self && ma == mb) {
                    continue;
                }
                let key = (ma.min(mb), ma.max(mb));
                let e = acc.entry(key).or_insert((0, score));
                e.0 += ca * cb;
                e.1 = better(e.1, score);
            }
        }
    };
    for &(u, v, s) in edges {
        if !shared[u] && !shared[v] {
            add(u, v, s, false);
        }
    }
    for (u, e) in entries.iter().enumerate() {
        if e.len() > 1 && !shared[u] {
            add(u, u, perfect, true);
        }
    }
    let mut out: Vec<ModelPair> = acc.into_iter().map(|((a, b), (w, s))| (a, b, w, s)).collect();
    out.sort_unstable_by_key(|&(a, b, ..)| (a, b));
    (out, shared.iter().filter(|&&s| s).count())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_and_near_images() {
        // Items: 0 shared by models 1 and 2 (identical image); 1 in model 3 is
        // near item 0 (score 3).
        let (pairs, skipped) = model_pairs(2, &[0, 0, 1], &[1, 2, 3], &[(0, 1, 3.0)], 0.0, false, 10);
        assert_eq!(skipped, 0);
        assert_eq!(pairs, vec![(1, 2, 1, 0.0), (1, 3, 1, 3.0), (2, 3, 1, 3.0)]);
    }

    #[test]
    fn banners_are_left_out() {
        // Item 0 in four models with max_models 3: shared, and its near variant too.
        let (pairs, skipped) = model_pairs(2, &[0, 0, 0, 0, 1], &[1, 2, 3, 4, 5], &[(0, 1, 1.0)], 0.0, false, 3);
        assert_eq!(skipped, 2);
        assert!(pairs.is_empty());
    }

    #[test]
    fn variants_add_up_to_the_limit() {
        // Two items, two models each, near each other: four models together > 3.
        let (pairs, skipped) = model_pairs(2, &[0, 0, 1, 1], &[1, 2, 3, 4], &[(0, 1, 2.0)], 0.0, false, 3);
        assert_eq!((pairs.len(), skipped), (0, 2));
    }

    #[test]
    fn weights_count_image_pairs_and_higher_scores_win() {
        // Model 1 has the item twice, model 2 once; a near edge scores 0.9 and 0.95.
        let (pairs, _) = model_pairs(3, &[0, 0, 1, 2], &[1, 1, 2, 2], &[(0, 1, 0.9), (0, 2, 0.95)], 1.0, true, 10);
        assert_eq!(pairs, vec![(1, 2, 4, 0.95)]);
    }
}
