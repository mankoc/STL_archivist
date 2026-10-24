//! Byte cipher + MessagePack used by `scene.bin` (V2+) and the islands members.

use crate::error::{LoadError, Result};

/// The fixed application id used as the repeating cipher key by every Lychee build.
pub const FILE_APP_ID: &[u8] = b"a8ee1146-8d03-4b69-8a67-59009a3f9ee7";

/// Undo the cipher: `b[i] - (key[i % len] % 255)`, wrapping in 0..=255.
pub fn decipher(raw: &[u8]) -> Vec<u8> {
    raw.iter().zip(FILE_APP_ID.iter().cycle()).map(|(&b, &k)| b.wrapping_sub(k % 255)).collect()
}

/// Decode `scene.bin` (or an islands member) into a MessagePack value, deciphering it first when `ciphered`
/// (MNGO files; ZIP files are plain).
pub fn decode(raw: &[u8], ciphered: bool) -> Result<rmpv::Value> {
    let deciphered;
    let mut data = if ciphered {
        deciphered = decipher(raw);
        &deciphered[..]
    } else {
        raw
    };
    rmpv::decode::read_value(&mut data)
        .map_err(|e| LoadError::format(format!("scene data is not valid MessagePack ({e})")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cipher_roundtrip() {
        let data: Vec<u8> = (0..=255u8).cycle().take(256 * 3).collect();
        let enciphered: Vec<u8> =
            data.iter().zip(FILE_APP_ID.iter().cycle()).map(|(&b, &k)| b.wrapping_add(k % 255)).collect();
        assert_eq!(decipher(&enciphered), data);
    }
}
