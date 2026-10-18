//! CLIP image preprocessing: a port of `_preprocess` in
//! `app/utils/image_embedding.py`. The encoder itself runs in ONNX Runtime
//! (the server's `embed` module); vectors from either side are L2-normalised
//! and stored under the same `EMBED_MODEL_ID`, so the preprocessing must stay
//! close to Python's (a golden test compares the tensors).

use fast_image_resize::images::Image as FirImage;
use fast_image_resize::{FilterType, PixelType, ResizeAlg, ResizeOptions, Resizer};

use crate::thumbnail::{ThumbError, decode_fitted};

/// The model's input edge (`_INPUT_SIZE`).
pub const INPUT: u32 = 224;
/// CLIP normalisation (`_MEAN`, `_STD`), digit for digit as in Python.
#[allow(clippy::excessive_precision)]
pub const MEAN: [f32; 3] = [0.481_454_66, 0.457_827_5, 0.408_210_73];
#[allow(clippy::excessive_precision)]
pub const STD: [f32; 3] = [0.268_629_54, 0.261_302_58, 0.275_777_11];

/// Python's `round()` for the resize target: half to even.
fn py_round(x: f64) -> u32 {
    let r = x.round();
    let r = if (x - x.trunc()).abs() == 0.5 && r % 2.0 != 0.0 { r - x.signum() } else { r };
    r.max(0.0) as u32
}

/// The `(3, 224, 224)` float32 tensor (channel-major) for an image: shorter
/// side scaled to 224 (bicubic), centre crop, CLIP-normalised RGB.
pub fn preprocess(data: &[u8]) -> Result<Vec<f32>, ThumbError> {
    let img = decode_fitted(data, u32::MAX)?;
    let (w, h) = (img.width, img.height);
    if w == 0 || h == 0 {
        return Err(ThumbError::Decode("empty image".into()));
    }
    let scale = f64::from(INPUT) / f64::from(w.min(h));
    let nw = INPUT.max(py_round(f64::from(w) * scale));
    let nh = INPUT.max(py_round(f64::from(h) * scale));
    let src = FirImage::from_vec_u8(w, h, img.data, PixelType::U8x3).map_err(|e| ThumbError::Decode(e.to_string()))?;
    let mut dst = FirImage::new(nw, nh, PixelType::U8x3);
    Resizer::new()
        .resize(&src, &mut dst, &ResizeOptions::new().resize_alg(ResizeAlg::Convolution(FilterType::CatmullRom)))
        .map_err(|e| ThumbError::Decode(e.to_string()))?;
    let pixels = dst.into_vec();
    let (left, top) = ((nw - INPUT) / 2, (nh - INPUT) / 2);
    let n = (INPUT * INPUT) as usize;
    let mut out = vec![0f32; 3 * n];
    for y in 0..INPUT {
        for x in 0..INPUT {
            let src = (((top + y) * nw + left + x) * 3) as usize;
            let dst = (y * INPUT + x) as usize;
            for c in 0..3 {
                out[c * n + dst] = (f32::from(pixels[src + c]) / 255.0 - MEAN[c]) / STD[c];
            }
        }
    }
    Ok(out)
}

/// L2-normalise `v` in place (unchanged when it is all zeros).
pub fn normalize(v: &mut [f32]) {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        v.iter_mut().for_each(|x| *x /= norm);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_half_to_even() {
        assert_eq!([0.5, 1.5, 2.5, 2.4, 2.6, 224.0].map(py_round), [0, 2, 2, 2, 3, 224]);
    }

    #[test]
    fn tensor_shape_and_values() {
        let img = image::RgbImage::from_pixel(300, 200, image::Rgb([255, 0, 128]));
        let mut png = std::io::Cursor::new(Vec::new());
        img.write_to(&mut png, image::ImageFormat::Png).unwrap();
        let t = preprocess(png.get_ref()).unwrap();
        assert_eq!(t.len(), 3 * 224 * 224);
        let n = 224 * 224;
        assert!((t[0] - (1.0 - MEAN[0]) / STD[0]).abs() < 1e-5);
        assert!((t[n] - (0.0 - MEAN[1]) / STD[1]).abs() < 1e-5);
        assert!((t[2 * n] - (128.0 / 255.0 - MEAN[2]) / STD[2]).abs() < 1e-5);
        let mut v = vec![3.0, 4.0];
        normalize(&mut v);
        assert_eq!(v, vec![0.6, 0.8]);
    }
}
