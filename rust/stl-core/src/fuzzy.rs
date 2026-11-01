//! Typo-tolerant matching for the browser's search: every word of the query
//! must appear somewhere in the text, ignoring case and accents, with a few
//! edits allowed in longer words (`drgon` finds "Red Dragon", `mjolnir` finds
//! "Mjölnir").

use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

/// Lowercase letters and digits without accents; anything else a space.
pub fn fold(s: &str) -> String {
    s.nfd()
        .filter(|c| !is_combining_mark(*c))
        .flat_map(char::to_lowercase)
        .map(|c| if c.is_alphanumeric() { c } else { ' ' })
        .collect()
}

/// Edits allowed in a query word of `n` characters.
fn allowed(n: usize) -> usize {
    match n {
        0..=3 => 0,
        4..=6 => 1,
        _ => 2,
    }
}

/// Whether some substring of `text` is within `k` edits of `word` (Sellers'
/// algorithm: an edit distance whose first row is all zeros).
fn within(word: &[char], text: &[char], k: usize) -> bool {
    let m = word.len();
    let mut col: Vec<usize> = (0..=m).collect();
    if col[m] <= k {
        return true;
    }
    for &t in text {
        let mut diag = 0;
        for i in 1..=m {
            let up = col[i];
            let cost = usize::from(word[i - 1] != t);
            col[i] = (diag + cost).min(up + 1).min(col[i - 1] + 1);
            diag = up;
        }
        if col[m] <= k {
            return true;
        }
    }
    false
}

/// Whether every word of `query` matches `text` (see the module).
pub fn matches(text: &str, query: &str) -> bool {
    let text = fold(text);
    let chars: Vec<char> = text.chars().collect();
    fold(query).split_whitespace().all(|w| {
        if text.contains(w) {
            return true;
        }
        let word: Vec<char> = w.chars().collect();
        let k = allowed(word.len());
        k > 0 && within(&word, &chars, k)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tolerates_typos_case_and_accents() {
        assert!(matches("Red Dragon Bust", "drgon"));
        assert!(matches("Red Dragon Bust", "DRAGNO red"));
        assert!(matches("Mjölnir (supported)", "mjolnir"));
        assert!(matches("Warhammer_Orks", "orks warhamer"));
        assert!(matches("anything", ""));
    }

    #[test]
    fn needs_every_word_and_few_edits() {
        assert!(!matches("Red Dragon Bust", "dragon elf"));
        assert!(!matches("Red Dragon Bust", "wyvern"));
        // Short words must match exactly.
        assert!(!matches("Red Dragon Bust", "elf"));
        assert!(matches("Red Dragon Bust", "bus"));
    }
}
