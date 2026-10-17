//! Archive fingerprints.
//!
//! An archive's fingerprint is derived from the CRCs already stored in its
//! catalog, so no member is decompressed. It must stay **bit-identical** to
//! `app.crawler.archive.archive_hash` in the Python app: the stored value
//! decides whether the crawler re-extracts an archive, so any difference makes
//! every affected archive look changed (compatibility rule 1 in
//! `docs/rust-migration-plan.md`).

use xxhash_rust::xxh64::Xxh64;

/// One archive member as listed in the archive catalog.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Member {
    pub name: String,
    pub crc: u32,
}

/// Fingerprint of an archive from its members' names and CRCs.
///
/// Members are sorted by name, then by CRC for equal names, so the result does
/// not depend on catalog order. Each member contributes its UTF-8 name bytes
/// followed by its CRC as 4 little-endian bytes to an xxh64 (seed 0) digest,
/// returned as 16 lowercase hex characters.
///
/// Python sorts the `(name, crc)` tuples by code point; Rust's `String` order
/// compares UTF-8 bytes, which gives the same order for valid UTF-8.
pub fn archive_fingerprint(members: &mut [Member]) -> String {
    members.sort_unstable();
    let mut hasher = Xxh64::new(0);
    for m in members.iter() {
        hasher.update(m.name.as_bytes());
        hasher.update(&m.crc.to_le_bytes());
    }
    format!("{:016x}", hasher.digest())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn members(items: &[(&str, u32)]) -> Vec<Member> {
        items.iter().map(|(n, c)| Member { name: n.to_string(), crc: *c }).collect()
    }

    // Expected values computed with the Python implementation (xxhash.xxh64).
    #[test]
    fn matches_python_reference_values() {
        assert_eq!(archive_fingerprint(&mut members(&[])), "ef46db3751d8e999");
        assert_eq!(
            archive_fingerprint(&mut members(&[("model.stl", 0x1234_5678), ("render.jpg", 42)])),
            PY_TWO_MEMBERS
        );
        assert_eq!(
            archive_fingerprint(&mut members(&[("Ñandú/ドラゴン 🐉.stl", u32::MAX)])),
            PY_UNICODE
        );
    }

    #[test]
    fn order_independent() {
        let a = archive_fingerprint(&mut members(&[("b", 2), ("a", 1), ("a", 0)]));
        let b = archive_fingerprint(&mut members(&[("a", 0), ("b", 2), ("a", 1)]));
        assert_eq!(a, b);
    }

    const PY_TWO_MEMBERS: &str = "e2787269290203d2";
    const PY_UNICODE: &str = "3a482560ab559c35";
}
