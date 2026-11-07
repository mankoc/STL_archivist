//! String similarity: Levenshtein distance and all-pairs search.
//!
//! Distances count Unicode scalar values (Rust `char`), which matches Python's
//! per-code-point `str` indexing in `app/utils/string_utils.py`.

use rayon::prelude::*;

/// Edit distance between `a` and `b` if it is at most `max`, otherwise `None`.
///
/// Banded dynamic programming (Ukkonen): only cells with `|i - j| <= max` are
/// filled, and the scan stops as soon as a whole row exceeds `max`.
pub fn levenshtein_bounded(a: &[char], b: &[char], max: u32) -> Option<u32> {
    let (a, b) = if a.len() < b.len() { (b, a) } else { (a, b) };
    let (la, lb) = (a.len(), b.len());
    if (la - lb) as u64 > max as u64 {
        return None;
    }
    if lb == 0 {
        return Some(la as u32);
    }
    let big = max.saturating_add(1);
    let band = max as usize;
    let mut prev: Vec<u32> = (0..=lb as u32).map(|x| x.min(big)).collect();
    let mut curr = vec![big; lb + 1];
    for i in 1..=la {
        let lo = i.saturating_sub(band).max(1);
        let hi = (i + band).min(lb);
        curr[0] = (i as u32).min(big);
        curr[lo - 1] = if lo == 1 { curr[0] } else { big };
        let mut row_min = curr[lo - 1];
        let ca = a[i - 1];
        for j in lo..=hi {
            let cost = (ca != b[j - 1]) as u32;
            let v = (prev[j - 1] + cost).min(prev[j] + 1).min(curr[j - 1] + 1).min(big);
            curr[j] = v;
            row_min = row_min.min(v);
        }
        if hi < lb {
            curr[hi + 1] = big; // next row reads it as prev[hi + 1]
        }
        if row_min > max {
            return None;
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    let d = prev[lb];
    (d <= max).then_some(d)
}

/// Full edit distance.
pub fn levenshtein(a: &[char], b: &[char]) -> u32 {
    levenshtein_bounded(a, b, a.len().max(b.len()) as u32).unwrap_or(0)
}

/// All pairs `(i, j, distance)` with `i < j` and distance <= `max`, sorted by `(i, j)`.
///
/// Distance 0 (equal strings) is included; callers filter as they need.
pub fn levenshtein_pairs(values: &[String], max: u32) -> Vec<(u32, u32, u32)> {
    levenshtein_pairs_until(values, max, &|| false).unwrap_or_default()
}

/// [`levenshtein_pairs`], or `None` once `stop` says so: the pairs of 100,000 values
/// take a while on every core, and a job must be able to stop meanwhile.
pub fn levenshtein_pairs_until(values: &[String], max: u32, stop: &(dyn Fn() -> bool + Sync)) -> Option<Vec<(u32, u32, u32)>> {
    use std::sync::atomic::{AtomicBool, Ordering};
    let chars: Vec<Vec<char>> = values.iter().map(|s| s.chars().collect()).collect();
    let stopped = AtomicBool::new(false);
    let pairs = (0..chars.len())
        .into_par_iter()
        .flat_map_iter(|i| {
            // Each value is compared with every later one: asked between values.
            if stopped.load(Ordering::Relaxed) || stop() {
                stopped.store(true, Ordering::Relaxed);
                return Vec::new();
            }
            let a = &chars[i];
            (i + 1..chars.len())
                .filter_map(|j| {
                    let b = &chars[j];
                    if a.len().abs_diff(b.len()) as u64 > max as u64 {
                        return None;
                    }
                    levenshtein_bounded(a, b, max).map(|d| (i as u32, j as u32, d))
                })
                .collect()
        })
        .collect();
    (!stopped.into_inner()).then_some(pairs)
}

/// How names are compared by [`similar_pairs`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Likeness {
    /// At most this many edits, letter case aside.
    Spelling(u32),
    /// The same Soundex code.
    Sound,
    /// At least this percentage of the shorter name's words in common, in any
    /// order (`spectral warrior` and `warrior spectral`, `goblin` and `goblin warrior`).
    Words(u32),
}

/// Pairs `(i, j, score)`, `i < j`, of `names` that are alike, the closest first
/// (by Soundex code for [`Likeness::Sound`]). The score reads as the distance, the
/// code or a percentage. At most `limit + 1` pairs: one more tells that there are more.
pub fn similar_pairs(names: &[String], how: Likeness, limit: usize) -> Vec<(u32, u32, String)> {
    let mut out: Vec<(u32, u32, String)> = match how {
        Likeness::Spelling(max) => {
            let folded: Vec<String> = names.iter().map(|n| n.to_lowercase()).collect();
            let mut pairs = levenshtein_pairs(&folded, max);
            pairs.sort_by_key(|&(i, j, d)| (d, i, j));
            pairs.into_iter().take(limit + 1).map(|(i, j, d)| (i, j, d.to_string())).collect()
        }
        Likeness::Sound => {
            let mut groups: std::collections::BTreeMap<String, Vec<u32>> = Default::default();
            for (k, name) in names.iter().enumerate() {
                groups.entry(soundex(name)).or_default().push(k as u32);
            }
            groups.remove("0000"); // no letters to compare
            let pairs = groups.iter().flat_map(|(code, ks)| {
                ks.iter().enumerate().flat_map(move |(n, &i)| ks[n + 1..].iter().map(move |&j| (i, j, code.clone())))
            });
            pairs.take(limit + 1).collect()
        }
        Likeness::Words(min) => {
            let mut pairs = word_pairs(names, min);
            pairs.sort_by_key(|&(i, j, p)| (std::cmp::Reverse(p), i, j));
            pairs.into_iter().take(limit + 1).map(|(i, j, p)| (i, j, format!("{p}%"))).collect()
        }
    };
    out.truncate(limit + 1);
    out
}

/// [`Likeness::Words`]: the names sharing a word, through an index of the words.
fn word_pairs(names: &[String], min: u32) -> Vec<(u32, u32, u32)> {
    use std::collections::{HashMap, HashSet};
    let lower: Vec<String> = names.iter().map(|n| n.to_lowercase()).collect();
    let words: Vec<HashSet<&str>> = lower.iter().map(|n| n.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).collect()).collect();
    let mut index: HashMap<&str, Vec<u32>> = HashMap::new();
    for (k, set) in words.iter().enumerate() {
        for &w in set {
            index.entry(w).or_default().push(k as u32);
        }
    }
    (0..words.len())
        .into_par_iter()
        .flat_map_iter(|i| {
            let mut shared: HashMap<u32, u32> = HashMap::new();
            for w in &words[i] {
                for &j in index[w].iter().filter(|&&j| j > i as u32) {
                    *shared.entry(j).or_default() += 1;
                }
            }
            let words = &words;
            shared.into_iter().filter_map(move |(j, n)| {
                let percent = n * 100 / words[i].len().min(words[j as usize].len()) as u32;
                (percent >= min).then_some((i as u32, j, percent))
            })
        })
        .collect()
}

/// Soundex code of `s`, as `app.utils.string_utils.soundex`: NFKD-decompose,
/// uppercase, keep ASCII letters; `"0000"` when none are left.
///
/// Not the textbook algorithm, kept bit-compatible with the Python one: a vowel
/// (or H, W, Y) between two consonants of the same code does not separate them.
pub fn soundex(s: &str) -> String {
    use unicode_normalization::UnicodeNormalization;
    // A..Z -> digit, as `_SOUNDEX_CODE`.
    const CODE: &[u8; 26] = b"01230120022455012623010202";
    let letters: Vec<u8> = s
        .nfkd()
        .flat_map(char::to_uppercase)
        .filter(char::is_ascii_alphabetic)
        .map(|c| c as u8)
        .collect();
    let Some(&first) = letters.first() else { return "0000".into() };
    let digits: Vec<u8> = letters.iter().map(|&c| CODE[(c - b'A') as usize]).collect();
    let mut out = vec![first];
    let mut prev = digits[0];
    for &d in &digits[1..] {
        if d != b'0' && d != prev {
            out.push(d);
        }
        if d != b'0' {
            prev = d;
        }
        if out.len() == 4 {
            break;
        }
    }
    out.resize(4, b'0');
    String::from_utf8(out).expect("ASCII")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn similar_names() {
        let names: Vec<String> = ["Dragon", "dragons", "DRAGON", "goblin warrior", "warrior goblin", "goblin", "Smith", "Smyth", "123"].map(String::from).to_vec();
        let found = |how, limit| similar_pairs(&names, how, limit);
        let spelling = found(Likeness::Spelling(1), 100);
        assert_eq!(spelling[0], (0, 2, "0".to_string()), "letter case aside, the closest first");
        assert!(spelling.contains(&(0, 1, "1".to_string())) && spelling.contains(&(6, 7, "1".to_string())));
        assert!(found(Likeness::Sound, 100).iter().any(|p| (p.0, p.1) == (6, 7)), "Smith sounds like Smyth");
        assert!(found(Likeness::Sound, 100).iter().all(|p| p.0 != 8 && p.1 != 8), "digits have no sound");
        let words = found(Likeness::Words(100), 100);
        assert!(words.contains(&(3, 4, "100%".to_string())), "any order");
        assert!(words.contains(&(3, 5, "100%".to_string())), "all of the shorter name's words");
        assert!(found(Likeness::Words(100), 100).iter().all(|p| p.2 == "100%"));
        assert_eq!(found(Likeness::Spelling(1), 1).len(), 2, "one past the limit tells there are more");
    }

    fn c(s: &str) -> Vec<char> {
        s.chars().collect()
    }

    fn naive(a: &[char], b: &[char]) -> u32 {
        let mut prev: Vec<u32> = (0..=b.len() as u32).collect();
        for (i, ca) in a.iter().enumerate() {
            let mut curr = vec![i as u32 + 1];
            for (j, cb) in b.iter().enumerate() {
                curr.push((prev[j + 1] + 1).min(curr[j] + 1).min(prev[j] + (ca != cb) as u32));
            }
            prev = curr;
        }
        prev[b.len()]
    }

    #[test]
    fn known_distances() {
        assert_eq!(levenshtein(&c("kitten"), &c("sitting")), 3);
        assert_eq!(levenshtein(&c(""), &c("abc")), 3);
        assert_eq!(levenshtein(&c("Müller"), &c("Muller")), 1);
        assert_eq!(levenshtein(&c("ドラゴン"), &c("ドラコン")), 1);
        assert_eq!(levenshtein_bounded(&c("kitten"), &c("sitting"), 2), None);
        assert_eq!(levenshtein_bounded(&c("abc"), &c("abc"), 0), Some(0));
    }

    #[test]
    fn bounded_agrees_with_naive() {
        let words = ["", "a", "ab", "abc", "abd", "bca", "dragon", "dragoon", "drakon",
                     "wagon", "dragons bust", "dragon's bust", "ñandú", "nandu", "🐉 dragon"];
        for x in words {
            for y in words {
                let full = naive(&c(x), &c(y));
                for max in 0..6 {
                    let want = (full <= max).then_some(full);
                    assert_eq!(levenshtein_bounded(&c(x), &c(y), max), want, "{x:?} {y:?} {max}");
                }
            }
        }
    }

    #[test]
    fn pairs_sorted_and_complete() {
        let v: Vec<String> = ["dragon", "dragoon", "x", "drakon", "dragon"]
            .iter().map(|s| s.to_string()).collect();
        assert_eq!(
            levenshtein_pairs(&v, 1),
            vec![(0, 1, 1), (0, 3, 1), (0, 4, 0), (1, 4, 1), (3, 4, 1)]
        );
    }

    #[test]
    fn pairs_can_be_stopped() {
        let v: Vec<String> = (0..2000).map(|i| format!("model {i}")).collect();
        let asked = std::sync::atomic::AtomicUsize::new(0);
        let stop = || asked.fetch_add(1, std::sync::atomic::Ordering::Relaxed) >= 10;
        assert_eq!(levenshtein_pairs_until(&v, 1, &stop), None);
        assert!(asked.into_inner() < v.len(), "it stopped early");
        assert_eq!(levenshtein_pairs_until(&v[..5], 1, &|| false).map(|p| p.len()), Some(10), "model 0 to model 4: all one edit apart");
    }
}
