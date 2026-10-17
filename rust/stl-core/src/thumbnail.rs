//! Thumbnails: decode, shrink to fit a square, encode as JPEG (or GIF when animated).
//!
//! Replaces Pillow in `app.utils.thumbnailer.make_thumbnail`. The output does not
//! have to match Pillow's bytes (thumbnails are display data, and the dHash
//! computed from them gets a new format in Phase 2c), but it keeps Pillow's
//! visible behaviour:
//!
//! * the output size is `Image.thumbnail`'s (same aspect rounding, never enlarged);
//! * alpha is dropped, not composited, as `convert("RGB")` does;
//! * EXIF orientation is ignored, as it always was;
//! * an animated GIF stays an animated GIF (at most [`GIF_MAX_FRAMES`] frames and
//!   [`GIF_MAX_BYTES`]), otherwise its first frame becomes a JPEG.
//!
//! JPEGs are decoded with DCT scaling to the smallest size of at least twice the
//! target, which Pillow never did here (`convert` loads the full image first).
//! Formats this module does not decode (AVIF, CMYK JPEG, ...) return
//! [`ThumbError::Unsupported`] and the caller uses Pillow.

use std::fmt;
use std::io::Cursor;

use fast_image_resize::images::Image as FirImage;
use fast_image_resize::{FilterType, PixelType, ResizeAlg, ResizeOptions, Resizer};
use image::{AnimationDecoder, ImageFormat};

/// JPEG quality, as `THUMB_QUALITY` in `thumbnailer.py`.
pub const QUALITY: u8 = 75;
/// Animated thumbnails with more frames are stored as a still JPEG.
pub const GIF_MAX_FRAMES: usize = 60;
/// Animated thumbnails larger than this are stored as a still JPEG.
pub const GIF_MAX_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug)]
pub enum ThumbError {
    /// Corrupt or truncated image data.
    Decode(String),
    /// Valid data in a format or variant handled by the Python fallback.
    Unsupported(String),
}

impl fmt::Display for ThumbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ThumbError::Decode(m) => write!(f, "cannot decode image: {m}"),
            ThumbError::Unsupported(m) => write!(f, "unsupported image: {m}"),
        }
    }
}

impl std::error::Error for ThumbError {}

fn decode_err(e: impl fmt::Display) -> ThumbError {
    ThumbError::Decode(e.to_string())
}

/// An 8-bit RGB image.
pub(crate) struct Rgb {
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) data: Vec<u8>,
    /// Size of the encoded image before any DCT scaling. The thumbnail size is
    /// computed from it, as Pillow does; the scaled size can round differently.
    source: (u32, u32),
}

impl Rgb {
    fn new(width: u32, height: u32, data: Vec<u8>) -> Self {
        Rgb { width, height, data, source: (width, height) }
    }
}

/// `Image.thumbnail`'s target size for a `width` x `height` image fitting in
/// `max` x `max`, or `None` when it already fits (no resize).
pub fn fit_size(width: u32, height: u32, max: u32) -> Option<(u32, u32)> {
    let (x, y) = (max as f64, max as f64);
    if max >= width && max >= height {
        return None;
    }
    // round_aspect: floor or ceil, whichever keeps the aspect closer (floor on ties).
    fn round_aspect(number: f64, key: impl Fn(f64) -> f64) -> u32 {
        let (lo, hi) = (number.floor(), number.ceil());
        let best = if key(hi) < key(lo) { hi } else { lo };
        best.max(1.0) as u32
    }
    let aspect = width as f64 / height as f64;
    Some(if x / y >= aspect {
        (round_aspect(y * aspect, |n| (aspect - n / y).abs()), max)
    } else {
        (max, round_aspect(x / aspect, |n| if n == 0.0 { 0.0 } else { (aspect - x / n).abs() }))
    })
}

/// Thumbnail bytes for `data`: JPEG, or GIF for an animated GIF when `animate`.
pub fn make_thumbnail(data: &[u8], max_size: u32, animate: bool) -> Result<Vec<u8>, ThumbError> {
    if max_size == 0 {
        return Err(ThumbError::Unsupported("max_size 0".into()));
    }
    if animate
        && image::guess_format(data).ok() == Some(ImageFormat::Gif)
        && let Some(gif) = animated_gif(data, max_size)?
    {
        return Ok(gif);
    }
    encode_jpeg(&decode_fitted(data, max_size)?)
}

/// The still image (first frame) shrunk to fit `max_size`, as RGB.
pub(crate) fn decode_fitted(data: &[u8], max_size: u32) -> Result<Rgb, ThumbError> {
    let format = image::guess_format(data).map_err(|_| ThumbError::Unsupported("unknown format".into()))?;
    let img = match format {
        ImageFormat::Jpeg => decode_jpeg(data, max_size)?,
        ImageFormat::Png | ImageFormat::Gif | ImageFormat::WebP | ImageFormat::Bmp | ImageFormat::Tiff => {
            decode_generic(data, format)?
        }
        other => return Err(ThumbError::Unsupported(format!("{other:?}"))),
    };
    shrink(img, max_size)
}

fn decode_jpeg(data: &[u8], max_size: u32) -> Result<Rgb, ThumbError> {
    let mut dec = jpeg_decoder::Decoder::new(Cursor::new(data));
    dec.read_info().map_err(decode_err)?;
    let info = dec.info().ok_or_else(|| ThumbError::Decode("no frame header".into()))?;
    let source = (info.width as u32, info.height as u32);
    if let Some((w, h)) = fit_size(source.0, source.1, max_size) {
        // Smallest DCT scale (1/8 .. 1) that keeps at least twice the target size,
        // like Pillow's reducing_gap=2.0; the Lanczos pass does the rest.
        let rw = (w * 2).min(u16::MAX as u32) as u16;
        let rh = (h * 2).min(u16::MAX as u32) as u16;
        dec.scale(rw, rh).map_err(decode_err)?;
    }
    let pixels = dec.decode().map_err(decode_err)?;
    let info = dec.info().ok_or_else(|| ThumbError::Decode("no frame header".into()))?;
    let (width, height) = (info.width as u32, info.height as u32);
    let data = match info.pixel_format {
        jpeg_decoder::PixelFormat::RGB24 => pixels,
        jpeg_decoder::PixelFormat::L8 => pixels.iter().flat_map(|&v| [v, v, v]).collect(),
        other => return Err(ThumbError::Unsupported(format!("JPEG pixel format {other:?}"))),
    };
    if data.len() < (width as usize) * (height as usize) * 3 {
        return Err(ThumbError::Decode("short JPEG pixel data".into()));
    }
    Ok(Rgb { width, height, data, source })
}

fn decode_generic(data: &[u8], format: ImageFormat) -> Result<Rgb, ThumbError> {
    let img = image::load_from_memory_with_format(data, format).map_err(decode_err)?;
    let rgb = img.into_rgb8(); // drops alpha, as convert("RGB")
    Ok(Rgb::new(rgb.width(), rgb.height(), rgb.into_raw()))
}

/// Lanczos3 down to `Image.thumbnail`'s size; unchanged when it already fits.
fn shrink(img: Rgb, max_size: u32) -> Result<Rgb, ThumbError> {
    let Some((w, h)) = fit_size(img.source.0, img.source.1, max_size) else {
        return Ok(img);
    };
    if (w, h) == (img.width, img.height) {
        return Ok(img);
    }
    let src = FirImage::from_vec_u8(img.width, img.height, img.data, PixelType::U8x3).map_err(decode_err)?;
    let mut dst = FirImage::new(w, h, PixelType::U8x3);
    Resizer::new()
        .resize(&src, &mut dst, &ResizeOptions::new().resize_alg(ResizeAlg::Convolution(FilterType::Lanczos3)))
        .map_err(decode_err)?;
    Ok(Rgb::new(w, h, dst.into_vec()))
}

fn encode_jpeg(img: &Rgb) -> Result<Vec<u8>, ThumbError> {
    let (w, h) = (u16::try_from(img.width), u16::try_from(img.height));
    let (Ok(w), Ok(h)) = (w, h) else {
        return Err(ThumbError::Unsupported("image too large for JPEG".into()));
    };
    let mut out = Vec::new();
    let mut enc = jpeg_encoder::Encoder::new(&mut out, QUALITY);
    enc.set_sampling_factor(jpeg_encoder::SamplingFactor::F_2_2); // 4:2:0, Pillow's default
    enc.set_optimized_huffman_tables(true); // optimize=True
    enc.encode(&img.data, w, h, jpeg_encoder::ColorType::Rgb).map_err(decode_err)?;
    Ok(out)
}

/// `_make_animated_gif`: the shrunk animation, or `None` when it has a single
/// frame or exceeds [`GIF_MAX_FRAMES`] / [`GIF_MAX_BYTES`].
fn animated_gif(data: &[u8], max_size: u32) -> Result<Option<Vec<u8>>, ThumbError> {
    use image::codecs::gif::{GifDecoder, GifEncoder, Repeat};
    use image::{Frame, RgbaImage};

    let decoder = GifDecoder::new(Cursor::new(data)).map_err(decode_err)?;
    let mut frames = Vec::new();
    for frame in decoder.into_frames() {
        if frames.len() >= GIF_MAX_FRAMES {
            return Ok(None);
        }
        let frame = frame.map_err(decode_err)?;
        let delay = frame.delay();
        let rgba = frame.into_buffer();
        let rgb = Rgb::new(rgba.width(), rgba.height(), rgba.pixels().flat_map(|p| [p[0], p[1], p[2]]).collect());
        frames.push((shrink(rgb, max_size)?, delay));
    }
    if frames.len() < 2 {
        return Ok(None);
    }
    let mut out = Vec::new();
    {
        let mut enc = GifEncoder::new_with_speed(&mut out, 10);
        enc.set_repeat(Repeat::Infinite).map_err(decode_err)?;
        for (img, delay) in frames {
            let rgba: Vec<u8> = img.data.as_chunks::<3>().0.iter().flat_map(|p| [p[0], p[1], p[2], 255]).collect();
            let buf = RgbaImage::from_raw(img.width, img.height, rgba)
                .ok_or_else(|| ThumbError::Decode("frame buffer".into()))?;
            enc.encode_frame(Frame::from_parts(buf, 0, 0, delay)).map_err(decode_err)?;
        }
    }
    Ok((out.len() <= GIF_MAX_BYTES).then_some(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fit_size_matches_pillow_thumbnail() {
        // (width, height, max) -> Image.thumbnail size, from Pillow 12.2.
        let cases = [
            ((1000, 500, 512), (512, 256)),
            ((500, 1000, 512), (256, 512)),
            ((513, 512, 512), (512, 511)),
            ((512, 513, 512), (511, 512)),
            ((3000, 7, 512), (512, 1)),
            ((7, 3000, 512), (1, 512)),
            ((1023, 1024, 100), (100, 100)),
            ((641, 481, 640), (640, 480)),
            ((1, 10000, 512), (1, 512)),
            ((999, 1001, 333), (332, 333)),
        ];
        for ((w, h, m), want) in cases {
            assert_eq!(fit_size(w, h, m), Some(want), "{w}x{h} in {m}");
        }
        assert_eq!(fit_size(640, 480, 640), None);
    }

    fn jpeg_size(data: &[u8]) -> (u32, u32) {
        let mut dec = jpeg_decoder::Decoder::new(data);
        dec.read_info().unwrap();
        let info = dec.info().unwrap();
        (info.width as u32, info.height as u32)
    }

    fn png(width: u32, height: u32) -> Vec<u8> {
        let img = image::RgbaImage::from_fn(width, height, |x, y| image::Rgba([x as u8, y as u8, 7, 128]));
        let mut out = Cursor::new(Vec::new());
        img.write_to(&mut out, ImageFormat::Png).unwrap();
        out.into_inner()
    }

    #[test]
    fn png_becomes_fitted_jpeg() {
        let thumb = make_thumbnail(&png(300, 200), 100, true).unwrap();
        let img = jpeg_size(&thumb);
        assert_eq!(image::guess_format(&thumb).unwrap(), ImageFormat::Jpeg);
        assert_eq!(img, (100, 67));
    }

    #[test]
    fn jpeg_is_dct_scaled_and_fitted() {
        let big = Rgb::new(2000, 1000, vec![90; 2000 * 1000 * 3]);
        let jpeg = encode_jpeg(&big).unwrap();
        let thumb = make_thumbnail(&jpeg, 128, true).unwrap();
        let img = jpeg_size(&thumb);
        assert_eq!(img, (128, 64));
    }

    #[test]
    fn dct_scaled_size_comes_from_the_source_size() {
        // Odd sizes round differently once DCT-scaled; the output must not.
        for (w, h) in [(2049, 1366), (3001, 2001), (1537, 1025), (999, 4001)] {
            let jpeg = encode_jpeg(&Rgb::new(w, h, vec![128; (w * h * 3) as usize])).unwrap();
            let thumb = make_thumbnail(&jpeg, 512, true).unwrap();
            assert_eq!(Some(jpeg_size(&thumb)), fit_size(w, h, 512), "{w}x{h}");
        }
    }

    #[test]
    fn garbage_and_unknown_formats_fail() {
        assert!(matches!(make_thumbnail(b"not an image", 100, true), Err(ThumbError::Unsupported(_))));
        assert!(matches!(make_thumbnail(b"\x89PNG\r\n\x1a\n broken", 100, true), Err(ThumbError::Decode(_))));
    }
}
