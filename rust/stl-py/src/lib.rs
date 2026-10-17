//! `stl_native`: the Rust core exposed to the Python app.
//!
//! Thin wrappers only: argument conversion and GIL release. All logic lives in
//! `stl-core`. The Python side imports this module through `app/native.py`,
//! which falls back to the Python implementation when it is missing.

use pyo3::prelude::*;
use stl_core::fingerprint::{self, Member};

/// Archive fingerprint from `(name, crc)` pairs; same result as
/// `app.crawler.archive.archive_hash` computes from an archive's catalog.
///
/// Raises `TypeError` for a non-`str` name or a CRC outside 0..2**32, and
/// `UnicodeEncodeError` for a name with lone surrogates; callers fall back to
/// the Python implementation in those cases.
#[pyfunction]
fn archive_fingerprint(py: Python<'_>, members: Vec<(String, u32)>) -> String {
    let mut members: Vec<Member> =
        members.into_iter().map(|(name, crc)| Member { name, crc }).collect();
    py.detach(|| fingerprint::archive_fingerprint(&mut members))
}

#[pymodule]
fn stl_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", stl_core::VERSION)?;
    m.add_function(wrap_pyfunction!(archive_fingerprint, m)?)?;
    Ok(())
}
