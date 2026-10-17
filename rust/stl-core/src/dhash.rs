//! Perceptual hash, format 2: a 128-bit difference hash (dHash).
//!
//! Replaces the Pillow hash in `app.utils.image_hash._dhash` (compatibility rule 2
//! in `docs/rust-migration-plan.md`: a new format, recomputed once, computed only
//! here). The bit layout is unchanged, so stored hashes still split into the same
//! `hi`/`lo` words for the Hamming scans:
//!
//! * bits 0..64: horizontal gradient, row-major over the 9 x 9 grid's first 8 rows,
//!   set when a cell is brighter than its right neighbour;
//! * bits 64..128: vertical gradient, set when a cell is brighter than the one below.
//!
//! The method is Pillow's (8-bit BT.601 luma, Lanczos3 down to 9 x 9, strict
//! integer comparison); only the decoders and the resampler differ, which is why
//! the bits do and the format number changed. Two additions: the image is first
//! shrunk as a thumbnail would be, so hashing an original and hashing its
//! thumbnail agree, and any format `thumbnail` decodes is accepted.
//!
//! Variants measured on 2,000 reference-library thumbnails (exact area averaging
//! in floating point, with or without rounding) were less robust to re-encoding
//! and separated unrelated images less well; the 8-bit rounding acts as a dead
//! zone for nearly equal neighbours.

use fast_image_resize::images::Image as FirImage;
use fast_image_resize::{FilterType, PixelType, ResizeAlg, ResizeOptions, Resizer};
use rayon::prelude::*;

use crate::thumbnail::{ThumbError, decode_fitted};

/// Cells per side of the comparison grid (one more than the bits per row).
const GRID: usize = 9;
/// Images are shrunk to fit this size before hashing, like a default thumbnail.
pub const SOURCE_SIZE: u32 = 512;

/// Format-2 dHash of an encoded image (any format `thumbnail` decodes).
pub fn dhash(data: &[u8]) -> Result<u128, ThumbError> {
    let img = decode_fitted(data, SOURCE_SIZE)?;
    if img.width == 0 || img.height == 0 {
        return Err(ThumbError::Decode("empty image".into()));
    }
    // Pillow's convert("L"): L = (299 R + 587 G + 114 B) / 1000, rounded.
    let luma: Vec<u8> = img
        .data
        .as_chunks::<3>()
        .0
        .iter()
        .map(|p| ((p[0] as u32 * 299 + p[1] as u32 * 587 + p[2] as u32 * 114 + 500) / 1000) as u8)
        .collect();
    let src = FirImage::from_vec_u8(img.width, img.height, luma, PixelType::U8)
        .map_err(|e| ThumbError::Decode(e.to_string()))?;
    let mut grid = FirImage::new(GRID as u32, GRID as u32, PixelType::U8);
    Resizer::new()
        .resize(&src, &mut grid, &ResizeOptions::new().resize_alg(ResizeAlg::Convolution(FilterType::Lanczos3)))
        .map_err(|e| ThumbError::Decode(e.to_string()))?;
    Ok(hash_grid(grid.buffer()))
}

/// [`dhash`] over many images in parallel; `None` where an image fails.
pub fn dhash_many(items: &[&[u8]]) -> Vec<Option<u128>> {
    items.par_iter().map(|d| dhash(d).ok()).collect()
}

/// Bits from a 9 x 9 grid (row-major), in the layout described above.
fn hash_grid(g: &[u8]) -> u128 {
    let at = |row: usize, col: usize| g[row * GRID + col];
    let mut out = 0u128;
    let mut bit = 0;
    for row in 0..GRID - 1 {
        for col in 0..GRID - 1 {
            if at(row, col) > at(row, col + 1) {
                out |= 1 << bit;
            }
            bit += 1;
        }
    }
    for row in 0..GRID - 1 {
        for col in 0..GRID - 1 {
            if at(row, col) > at(row + 1, col) {
                out |= 1 << bit;
            }
            bit += 1;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::thumbnail::make_thumbnail;
    use std::io::Cursor;

    fn png(w: u32, h: u32, f: impl Fn(u32, u32) -> [u8; 3]) -> Vec<u8> {
        let img = image::RgbImage::from_fn(w, h, |x, y| image::Rgb(f(x, y)));
        let mut out = Cursor::new(Vec::new());
        img.write_to(&mut out, image::ImageFormat::Png).unwrap();
        out.into_inner()
    }

    #[test]
    fn grid_bit_layout() {
        // Each row darker to the right: all horizontal bits; rows equal: no vertical bit.
        let mut g = [0u8; GRID * GRID];
        for (k, v) in g.iter_mut().enumerate() {
            *v = 200 - 10 * (k % GRID) as u8;
        }
        assert_eq!(hash_grid(&g), u64::MAX as u128);
        // Only cell (0, 0) brighter: bit 0 (right neighbour) and bit 64 (below).
        let mut g = [50u8; GRID * GRID];
        g[0] = 51;
        assert_eq!(hash_grid(&g), 1 | 1 << 64);
    }

    #[test]
    fn gradients_set_the_expected_bits() {
        let left = dhash(&png(90, 90, |x, _| [255 - x as u8 * 2; 3])).unwrap();
        assert_eq!(left, u64::MAX as u128);
        let top = dhash(&png(90, 90, |_, y| [255 - y as u8 * 2; 3])).unwrap();
        assert_eq!(top, (u64::MAX as u128) << 64);
        assert_eq!(dhash(&png(40, 30, |_, _| [77, 77, 77])).unwrap(), 0);
    }

    #[test]
    fn original_and_its_thumbnail_hash_alike() {
        let big = png(1600, 1000, |x, y| [(x / 7) as u8, (y / 5) as u8, ((x * y) % 251) as u8]);
        let thumb = make_thumbnail(&big, 256, false).unwrap();
        let d = (dhash(&big).unwrap() ^ dhash(&thumb).unwrap()).count_ones();
        assert!(d <= 4, "distance {d}");
    }

    #[test]
    fn many_matches_one_by_one() {
        let a = png(50, 50, |x, y| [x as u8 * 5, y as u8 * 5, 0]);
        let b = png(64, 20, |x, _| [x as u8 * 4; 3]);
        let got = dhash_many(&[&a, b"junk", &b]);
        assert_eq!(got, vec![Some(dhash(&a).unwrap()), None, Some(dhash(&b).unwrap())]);
    }
}
