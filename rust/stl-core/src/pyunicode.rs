//! Python's notion of decimal digits (`str.isdecimal`, `re`'s `\d`, `int()`),
//! so dates and numbers typed with any script's digits parse as in Python.
//!
//! The table holds the zero of every run of ten Unicode decimal digits
//! (category Nd), generated from Python's `unicodedata` (Unicode 16.0).

/// First code point (value 0) of each run of decimal digits.
const ZEROS: [u32; 76] = [0x30, 0x660, 0x6F0, 0x7C0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66, 0xDE6, 0xE50, 0xED0, 0xF20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90, 0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620, 0xA8D0, 0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10, 0x104A0, 0x10D30, 0x10D40, 0x11066, 0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450, 0x114D0, 0x11650, 0x116C0, 0x116D0, 0x116DA, 0x11730, 0x118E0, 0x11950, 0x11BF0, 0x11C50, 0x11D50, 0x11DA0, 0x11F50, 0x16130, 0x16A60, 0x16AC0, 0x16B50, 0x16D70, 0x1CCF0, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6, 0x1E140, 0x1E2F0, 0x1E4F0, 0x1E5F1, 0x1E950, 0x1FBF0];

/// The decimal value of `c` (`unicodedata.decimal`), or `None`.
pub fn decimal(c: char) -> Option<u32> {
    let cp = c as u32;
    let k = ZEROS.partition_point(|&z| z <= cp);
    if k == 0 {
        return None;
    }
    let off = cp - ZEROS[k - 1];
    (off < 10).then_some(off)
}

/// Python's `int(s)` for a stripped string: optional sign, decimal digits of
/// any script, single underscores between digits. `None` where `int()`
/// raises `ValueError` (or the value exceeds `i64`).
pub fn py_int(s: &str) -> Option<i64> {
    let (neg, body) = match s.strip_prefix('-') {
        Some(b) => (true, b),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    };
    if body.is_empty() || body.starts_with('_') || body.ends_with('_') || body.contains("__") {
        return None;
    }
    let mut v: i64 = 0;
    for c in body.chars() {
        if c == '_' {
            continue;
        }
        let d = decimal(c)? as i64;
        v = v.checked_mul(10)?.checked_add(d)?;
    }
    Some(if neg { -v } else { v })
}

/// `len(s) == n` and every character a decimal digit.
pub fn is_digits(s: &str, n: std::ops::RangeInclusive<usize>) -> bool {
    let count = s.chars().count();
    n.contains(&count) && s.chars().all(|c| decimal(c).is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digits_like_python() {
        assert_eq!(decimal('7'), Some(7));
        assert_eq!(decimal('٣'), Some(3)); // ARABIC-INDIC DIGIT THREE
        assert_eq!(decimal('३'), Some(3)); // DEVANAGARI DIGIT THREE
        assert_eq!(decimal('x'), None);
        assert_eq!(decimal('²'), None); // superscript: numeric, not decimal
        assert_eq!(py_int("1_000"), Some(1000));
        assert_eq!(py_int("-٢٠"), Some(-20));
        assert_eq!(py_int("1__0"), None);
        assert_eq!(py_int(""), None);
        assert!(is_digits("٢٠٢٤", 4..=4));
    }
}
