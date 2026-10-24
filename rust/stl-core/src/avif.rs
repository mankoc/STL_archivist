//! AVIF stills, decoded to 8-bit RGB for thumbnails: the container is read by
//! `avif-parse`, the AV1 image decoded by `rav1d` (the Rust port of dav1d,
//! built without its assembly, so no `nasm` is needed), and the YUV planes
//! converted here. The decoder calls follow `avif-decode`'s use of `rav1d`.
//!
//! Every pixel layout (4:0:0, 4:2:0, 4:2:2, 4:4:4), 8 to 12 bits, full and
//! limited range and the usual matrices are handled; alpha is dropped, as for
//! every thumbnail. Grid (tiled) images, which `avif-parse` does not read, are
//! [`Error::Unsupported`].

use std::mem::MaybeUninit;
use std::ptr::{self, NonNull};

use rav1d::include::dav1d::data::Dav1dData;
use rav1d::include::dav1d::dav1d::{Dav1dContext, Dav1dSettings};
use rav1d::include::dav1d::headers::*;
use rav1d::include::dav1d::picture::Dav1dPicture;
use rav1d::src::lib::{dav1d_close, dav1d_data_create, dav1d_data_unref, dav1d_default_settings, dav1d_get_picture};
use rav1d::src::lib::{dav1d_open, dav1d_picture_unref, dav1d_send_data};

#[derive(Debug)]
pub enum Error {
    /// Corrupt or truncated data.
    Decode(String),
    /// Valid AVIF this module does not read (grid images, unusual layouts).
    Unsupported(String),
}

/// The image's primary item as `(width, height, RGB bytes)`.
pub fn decode(data: &[u8]) -> Result<(u32, u32, Vec<u8>), Error> {
    let avif = avif_parse::read_avif(&mut &data[..]).map_err(|e| match e {
        avif_parse::Error::Unsupported(m) => Error::Unsupported(m.to_string()),
        other => Error::Decode(other.to_string()),
    })?;
    let mut decoder = Decoder::new()?;
    let pic = decoder.decode(&avif.primary_item)?;
    to_rgb(pic)
}

/// One `rav1d` context, closed (with its last picture released) on drop.
struct Decoder {
    ctx: Option<Dav1dContext>,
    picture: Option<Dav1dPicture>,
}

impl Decoder {
    fn new() -> Result<Self, Error> {
        unsafe {
            let mut settings = MaybeUninit::<Dav1dSettings>::uninit();
            dav1d_default_settings(NonNull::from(&mut settings).cast());
            let settings = settings.assume_init_mut();
            // Thumbnails are made in parallel already: one thread per image.
            settings.n_threads = 1;
            settings.max_frame_delay = 1;
            settings.apply_grain = 1;
            let mut ctx = None;
            ok(dav1d_open(Some(NonNull::from(&mut ctx)), Some(NonNull::from(settings))).0)?;
            Ok(Decoder { ctx, picture: None })
        }
    }

    /// Decode one AV1 temporal unit (an AVIF item); for a layered item, the
    /// last picture is the complete image.
    fn decode(&mut self, av1: &[u8]) -> Result<&Dav1dPicture, Error> {
        let ctx = self.ctx.ok_or_else(|| Error::Decode("decoder closed".into()))?;
        let mut last: Option<Dav1dPicture> = None;
        unsafe {
            let mut data = Dav1dData::default();
            let sent = (|| -> Result<(), Error> {
                let buf = NonNull::new(dav1d_data_create(Some(NonNull::from(&mut data)), av1.len()))
                    .ok_or_else(|| Error::Decode("out of memory".into()))?;
                ptr::copy_nonoverlapping(av1.as_ptr(), buf.as_ptr(), av1.len());
                loop {
                    let res = dav1d_send_data(Some(ctx), Some(NonNull::from(&mut data)));
                    if res.0 == 0 {
                        return Ok(());
                    }
                    // The decoder holds a frame: take it out so the data can go in.
                    let mut pending = Dav1dPicture::default();
                    if dav1d_get_picture(Some(ctx), Some(NonNull::from(&mut pending))).0 != 0 {
                        dav1d_picture_unref(Some(NonNull::from(&mut pending)));
                        return ok(res.0);
                    }
                    if let Some(mut prev) = last.replace(pending) {
                        dav1d_picture_unref(Some(NonNull::from(&mut prev)));
                    }
                }
            })();
            dav1d_data_unref(Some(NonNull::from(&mut data)));
            if let Err(e) = sent {
                if let Some(mut prev) = last.take() {
                    dav1d_picture_unref(Some(NonNull::from(&mut prev)));
                }
                return Err(e);
            }
            loop {
                let mut pic = Dav1dPicture::default();
                let res = dav1d_get_picture(Some(ctx), Some(NonNull::from(&mut pic)));
                if res.0 != 0 {
                    dav1d_picture_unref(Some(NonNull::from(&mut pic)));
                    let Some(pic) = last else {
                        ok(res.0)?;
                        return Err(Error::Decode("no frame decoded".into()));
                    };
                    return Ok(self.picture.insert(pic));
                }
                if let Some(mut prev) = last.replace(pic) {
                    dav1d_picture_unref(Some(NonNull::from(&mut prev)));
                }
            }
        }
    }
}

impl Drop for Decoder {
    fn drop(&mut self) {
        unsafe {
            if let Some(pic) = &mut self.picture {
                dav1d_picture_unref(Some(NonNull::from(pic)));
            }
            dav1d_close(Some(NonNull::from(&mut self.ctx)));
        }
    }
}

/// `rav1d` returns 0, or a negated `errno`.
fn ok(res: i32) -> Result<(), Error> {
    if res == 0 { Ok(()) } else { Err(Error::Decode(format!("AV1 decoder error {}", res.unsigned_abs()))) }
}

/// One plane's samples, widened to `u16`, with its size.
struct Plane {
    w: usize,
    h: usize,
    stride: usize,
    samples: Vec<u16>,
}

/// # Safety
///
/// `pic` must be a live picture from the decoder.
unsafe fn plane(pic: &Dav1dPicture, n: usize, ss: (usize, usize)) -> Result<Plane, Error> {
    let bad = |m: &str| Error::Decode(format!("AV1 picture: {m}"));
    let data = pic.data[n].ok_or_else(|| bad("missing plane"))?;
    let bytes = if pic.p.bpc <= 8 { 1 } else { 2 };
    let w = (pic.p.w as usize + ss.0) >> ss.0;
    let h = (pic.p.h as usize + ss.1) >> ss.1;
    let stride_bytes = pic.stride[usize::from(n != 0)];
    if stride_bytes <= 0 || !(stride_bytes as usize).is_multiple_of(bytes) || w == 0 {
        return Err(bad("stride"));
    }
    let stride = stride_bytes as usize / bytes;
    if stride < w {
        return Err(bad("stride"));
    }
    // rav1d allocates whole rows: `stride * h` samples are in the plane.
    let samples = unsafe {
        if bytes == 1 {
            std::slice::from_raw_parts(data.as_ptr().cast::<u8>(), stride * h).iter().map(|&v| v as u16).collect()
        } else {
            std::slice::from_raw_parts(data.as_ptr().cast::<u16>(), stride * h).to_vec()
        }
    };
    Ok(Plane { w, h, stride, samples })
}

/// `(Kr, Kb)` of a YUV matrix; `None` for identity (the planes are G, B, R).
fn matrix(mc: Option<Dav1dMatrixCoefficients>) -> Option<(f32, f32)> {
    Some(match mc {
        Some(DAV1D_MC_IDENTITY) => return None,
        Some(DAV1D_MC_BT709) => (0.2126, 0.0722),
        Some(DAV1D_MC_FCC) => (0.30, 0.11),
        Some(DAV1D_MC_SMPTE240) => (0.212, 0.087),
        Some(DAV1D_MC_BT2020_NCL | DAV1D_MC_BT2020_CL) => (0.2627, 0.0593),
        // BT.601 / BT.470BG, and unspecified (libavif's default).
        _ => (0.299, 0.114),
    })
}

fn to_rgb(pic: &Dav1dPicture) -> Result<(u32, u32, Vec<u8>), Error> {
    let (w, h, bpc) = (pic.p.w as usize, pic.p.h as usize, pic.p.bpc);
    if !(8..=12).contains(&bpc) {
        return Err(Error::Unsupported(format!("{bpc}-bit AVIF")));
    }
    let ss = match pic.p.layout {
        DAV1D_PIXEL_LAYOUT_I400 => None,
        DAV1D_PIXEL_LAYOUT_I420 => Some((1, 1)),
        DAV1D_PIXEL_LAYOUT_I422 => Some((1, 0)),
        DAV1D_PIXEL_LAYOUT_I444 => Some((0, 0)),
        other => return Err(Error::Unsupported(format!("AVIF pixel layout {other}"))),
    };
    let hdr = pic.seq_hdr.map(|h| unsafe { h.as_ref() });
    let full = hdr.is_some_and(|h| h.color_range != 0);
    let coeffs = matrix(hdr.map(|h| h.mtrx));
    let y = unsafe { plane(pic, 0, (0, 0))? };
    let uv = match ss {
        Some(s) => Some(unsafe { (plane(pic, 1, s)?, plane(pic, 2, s)?, s) }),
        None => None,
    };
    let max = ((1u32 << bpc) - 1) as f32;
    let scale = (1u32 << (bpc - 8)) as f32;
    // Normalised luma (0..1) and chroma (-0.5..0.5).
    let (y_off, y_span, c_span) = if full { (0.0, max, max) } else { (16.0 * scale, 219.0 * scale, 224.0 * scale) };
    let mid = (1u32 << (bpc - 1)) as f32;
    let byte = |v: f32| (v * 255.0).round().clamp(0.0, 255.0) as u8;
    let mut out = Vec::with_capacity(w * h * 3);
    for row in 0..h.min(y.h) {
        for col in 0..w.min(y.w) {
            let luma = (y.samples[row * y.stride + col] as f32 - y_off) / y_span;
            let Some((u, v, (sx, sy))) = &uv else {
                let g = byte(luma);
                out.extend([g, g, g]);
                continue;
            };
            let (cr, cc) = ((row >> sy).min(u.h - 1), (col >> sx).min(u.w - 1));
            let cb = (u.samples[cr * u.stride + cc] as f32 - mid) / c_span;
            let crv = (v.samples[cr * v.stride + cc] as f32 - mid) / c_span;
            let (r, g, b) = match coeffs {
                // Identity: Y is G, U is B, V is R (all full samples).
                None => (crv + 0.5, luma, cb + 0.5),
                Some((kr, kb)) => {
                    let r = luma + 2.0 * (1.0 - kr) * crv;
                    let b = luma + 2.0 * (1.0 - kb) * cb;
                    (r, (luma - kr * r - kb * b) / (1.0 - kr - kb), b)
                }
            };
            out.extend([byte(r), byte(g), byte(b)]);
        }
    }
    let (w, h) = (w.min(y.w) as u32, h.min(y.h) as u32);
    if out.len() != (w * h * 3) as usize {
        return Err(Error::Decode("short AV1 picture".into()));
    }
    Ok((w, h, out))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> Vec<u8> {
        std::fs::read(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/data").join(name)).unwrap()
    }

    /// The colours Pillow (libavif) decodes from the fixtures, made with Pillow
    /// as four 32 x 24 quadrants: red, green, blue, white.
    fn expect(name: &str, want: [[u8; 3]; 4]) {
        let (w, h, rgb) = decode(&fixture(name)).unwrap_or_else(|e| panic!("{name}: {e:?}"));
        assert_eq!((w, h), (64, 48), "{name}");
        for ((x, y), want) in [(8, 6), (40, 6), (8, 30), (40, 30)].into_iter().zip(want) {
            let i = (y * w as usize + x) * 3;
            let got = [rgb[i], rgb[i + 1], rgb[i + 2]];
            assert!(got.iter().zip(want).all(|(g, w)| g.abs_diff(w) <= 6), "{name} at ({x}, {y}): {got:?}, Pillow {want:?}");
        }
    }

    #[test]
    fn decodes_like_pillow() {
        let pillow = [[221, 30, 30], [30, 201, 39], [29, 40, 218], [250, 250, 250]];
        expect("quads_420.avif", pillow);
        expect("quads_444.avif", pillow);
        expect("quads_alpha.avif", pillow);
        expect("quads_limited.avif", [[220, 31, 29], [28, 200, 40], [28, 40, 217], [250, 250, 250]]);
    }

    #[test]
    fn refuses_what_is_no_avif() {
        assert!(matches!(decode(b"\0\0\0\x18ftypavif not really"), Err(Error::Decode(_))));
        assert!(decode(&fixture("quads_420.avif")[..100]).is_err(), "truncated");
    }
}
