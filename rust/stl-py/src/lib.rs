//! `stl_native`: the Rust core exposed to the Python app.
//!
//! Thin wrappers only: argument conversion, GIL release and progress relay.
//! All logic lives in `stl-core`. The Python side imports this module through
//! `app/native.py`, which falls back to the Python implementation when it is
//! missing.

use std::thread;
use std::time::{Duration, Instant};

use pyo3::buffer::{Element, PyBuffer};
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use stl_core::control::{Control, Stopped as CoreStopped};
use stl_core::fingerprint::{self, Member};
use stl_core::archive::{self, ArchiveError};
use stl_core::{embedding, hamming, path_pattern, rules, storage, strings, thumbnail, thumbstore};

create_exception!(
    stl_native,
    Stopped,
    PyException,
    "Raised when a long operation stops because should_stop() returned True."
);

/// How often a running job reports progress and polls `should_stop`.
const POLL: Duration = Duration::from_millis(100);

/// Run `job` on a worker thread while this thread, holding the GIL only
/// briefly, relays progress to `progress(done, total)` and polls
/// `should_stop()` every [`POLL`]. Callbacks never run on worker threads.
fn run_job<T: Send>(
    py: Python<'_>,
    progress: Option<&Bound<'_, PyAny>>,
    should_stop: Option<&Bound<'_, PyAny>>,
    job: impl FnOnce(&Control) -> Result<T, CoreStopped> + Send,
) -> PyResult<T> {
    // A stop requested before the start must win even if the job is quicker
    // than the first poll.
    if let Some(cb) = should_stop
        && cb.call0()?.is_truthy()?
    {
        return Err(Stopped::new_err("stopped"));
    }
    let ctl = Control::new();
    let outcome = thread::scope(|s| {
        let handle = s.spawn(|| job(&ctl));
        let relay = || -> PyResult<()> {
            if let Some(cb) = progress {
                let (done, total) = ctl.progress();
                cb.call1((done, total))?;
            }
            if let Some(cb) = should_stop
                && cb.call0()?.is_truthy()?
            {
                ctl.request_stop();
            }
            Ok(())
        };
        while !handle.is_finished() {
            py.detach(|| {
                let start = Instant::now();
                while !handle.is_finished() && start.elapsed() < POLL {
                    thread::sleep(Duration::from_millis(5));
                }
            });
            if let Err(e) = relay() {
                ctl.request_stop(); // let the worker finish quickly, then re-raise
                let _ = handle.join();
                return Err(e);
            }
        }
        let result = handle
            .join()
            .map_err(|_| PyRuntimeError::new_err("native worker thread panicked"))?;
        relay()?; // final 100 % report
        Ok(result)
    })?;
    outcome.map_err(|_| Stopped::new_err("stopped"))
}

/// Borrow a C-contiguous buffer (e.g. a NumPy array) as a slice without copying.
fn contiguous<'a, T: Element>(buf: &'a PyBuffer<T>, what: &str) -> PyResult<&'a [T]> {
    if !buf.is_c_contiguous() {
        return Err(PyValueError::new_err(format!("{what} must be C-contiguous")));
    }
    // SAFETY: the buffer is C-contiguous, holds `item_count` elements of `T`
    // (checked by PyBuffer::get), and stays alive and unmodified while `buf`
    // is held; the caller does not mutate it during the call.
    Ok(unsafe { std::slice::from_raw_parts(buf.buf_ptr() as *const T, buf.item_count()) })
}

fn bytes<'py, T: bytemuck::Pod>(py: Python<'py>, v: &[T]) -> Bound<'py, PyBytes> {
    PyBytes::new(py, bytemuck::cast_slice(v))
}

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

fn archive_err(e: ArchiveError) -> PyErr {
    match e {
        ArchiveError::Io(io) => io.into(), // OSError subclass, like Python's own readers
        other => PyValueError::new_err(other.to_string()),
    }
}

/// Catalog of a `.zip` or `.7z` archive: `[(name, crc, size), ...]`.
///
/// Same names and CRCs as the Python readers in `app.crawler.archive`.
/// Raises `OSError` or `ValueError` for anything it cannot read exactly; the
/// caller then falls back to the Python reader (and always does for `.rar`).
#[pyfunction]
fn archive_members(py: Python<'_>, path: std::path::PathBuf) -> PyResult<Vec<(String, u32, Option<u64>)>> {
    let members = py.detach(|| archive::list_members(&path)).map_err(archive_err)?;
    Ok(members.into_iter().map(|m| (m.name, m.crc, m.size)).collect())
}

/// `archive_hash` of a `.zip` or `.7z` archive, computed in one native call.
#[pyfunction]
fn archive_hash_file(py: Python<'_>, path: std::path::PathBuf) -> PyResult<String> {
    py.detach(|| archive::fingerprint(&path)).map_err(archive_err)
}

/// Decompress the named members of a `.zip` or `.7z` archive: `{name: bytes}`.
///
/// Same result as the Python readers' `read_batch`: missing, empty and
/// unreadable members are left out. Raises `OSError` or `ValueError` when the
/// archive needs the Python reader instead.
#[pyfunction]
fn read_archive_members<'py>(
    py: Python<'py>,
    path: std::path::PathBuf,
    names: Vec<String>,
) -> PyResult<Bound<'py, PyDict>> {
    let members = py.detach(|| archive::read_members(&path, &names)).map_err(archive_err)?;
    let out = PyDict::new(py);
    for (name, data) in members {
        out.set_item(name, PyBytes::new(py, &data))?;
    }
    Ok(out)
}

/// Thumbnail of an encoded image: JPEG bytes, or GIF bytes for an animated GIF
/// when `animate`. Same size rules as `Image.thumbnail`.
///
/// Raises `ValueError` for data it cannot decode or a format it does not handle
/// (AVIF, CMYK JPEG, ...); the caller then uses Pillow.
#[pyfunction]
#[pyo3(signature = (data, max_size, animate=true))]
fn make_thumbnail<'py>(
    py: Python<'py>,
    data: &[u8],
    max_size: u32,
    animate: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let out = py
        .detach(|| thumbnail::make_thumbnail(data, max_size, animate))
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &out))
}

/// Perceptual hash (format 2) of an encoded image, as a 128-bit int.
///
/// Raises `ValueError` for data it cannot decode.
#[pyfunction]
fn dhash(py: Python<'_>, data: &[u8]) -> PyResult<u128> {
    py.detach(|| stl_core::dhash::dhash(data)).map_err(|e| PyValueError::new_err(e.to_string()))
}

/// `dhash` over a list of encoded images, in parallel: an int or `None` per item.
#[pyfunction]
fn dhash_many(py: Python<'_>, items: Vec<Bound<'_, PyBytes>>) -> Vec<Option<u128>> {
    let slices: Vec<&[u8]> = items.iter().map(|b| b.as_bytes()).collect();
    py.detach(|| stl_core::dhash::dhash_many(&slices))
}

/// Bring the database file at `path` to the current schema (`storage::migrate`).
///
/// Returns what changed as a dict (`renamed`, `tables_created`, `columns_added`,
/// `dhash_rebuilt`, `embeddings_moved`, `vectors_reset`, `scales_copied`).
/// Opens, migrates and closes its own connection. Raises `RuntimeError`.
///
/// `embed_model_id` / `embed_dim`: the embedding model stored vectors must come
/// from (`app.database.vectors`); `vectors=False` skips the vector step, as the
/// Python migration does when it cannot load sqlite-vec.
#[pyfunction]
#[pyo3(signature = (path, embed_model_id, embed_dim, vectors=true))]
fn migrate_database<'py>(
    py: Python<'py>,
    path: std::path::PathBuf,
    embed_model_id: String,
    embed_dim: i64,
    vectors: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let v = storage::Vectors { model_id: embed_model_id, dim: embed_dim, enabled: vectors };
    let r = py
        .detach(|| storage::migrate(&path, &v))
        .map_err(|e| PyRuntimeError::new_err(format!("database migration: {e}")))?;
    let out = PyDict::new(py);
    out.set_item("renamed", r.renamed)?;
    out.set_item("tables_created", r.tables_created)?;
    out.set_item("columns_added", r.columns_added)?;
    out.set_item("dhash_rebuilt", r.dhash_rebuilt)?;
    out.set_item("embeddings_moved", r.embeddings_moved)?;
    out.set_item("vectors_reset", r.vectors_reset)?;
    out.set_item("scales_copied", r.scales_copied)?;
    Ok(out)
}

fn rules_err(e: rules::RulesError) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn checks(presupported: bool, no_images: bool, nested_archive: bool, no_printable: bool) -> rules::Checks {
    rules::Checks { presupported, no_images, nested_archive, no_printable }
}

/// `app.crawler.rules.detect_presupport`.
#[pyfunction]
fn detect_presupport(py: Python<'_>, names: Vec<String>) -> &'static str {
    py.detach(|| rules::detect_presupport(&names))
}

/// `app.crawler.rules.detect_warnings`. Raises `ValueError` for a presupport
/// pattern the native regex engine rejects (the caller then uses Python).
#[pyfunction]
#[pyo3(signature = (names, check_presupported=true, check_no_images=true, check_nested_archive=true,
                    check_no_printable=true, presupport_patterns=None))]
fn detect_warnings(
    py: Python<'_>,
    names: Vec<String>,
    check_presupported: bool,
    check_no_images: bool,
    check_nested_archive: bool,
    check_no_printable: bool,
    presupport_patterns: Option<Vec<String>>,
) -> PyResult<String> {
    py.detach(|| {
        let pats = rules::PresupportPatterns::new(presupport_patterns.as_deref())?;
        rules::detect_warnings(
            &names,
            checks(check_presupported, check_no_images, check_nested_archive, check_no_printable),
            &pats,
        )
    })
    .map_err(rules_err)
}

/// `app.crawler.rules.explain_warnings`, as `[(warning, [members]), ...]`.
#[pyfunction]
#[pyo3(signature = (names, check_presupported=true, check_no_images=true, check_nested_archive=true,
                    check_no_printable=true, presupport_patterns=None))]
fn explain_warnings(
    py: Python<'_>,
    names: Vec<String>,
    check_presupported: bool,
    check_no_images: bool,
    check_nested_archive: bool,
    check_no_printable: bool,
    presupport_patterns: Option<Vec<String>>,
) -> PyResult<Vec<(&'static str, Vec<String>)>> {
    py.detach(|| {
        let pats = rules::PresupportPatterns::new(presupport_patterns.as_deref())?;
        rules::explain_warnings(
            &names,
            checks(check_presupported, check_no_images, check_nested_archive, check_no_printable),
            &pats,
        )
    })
    .map_err(rules_err)
}

/// `app.crawler.rules.detect_scale`.
#[pyfunction]
fn detect_scale(py: Python<'_>, names: Vec<String>) -> Vec<String> {
    py.detach(|| rules::detect_scale(&names))
}

/// `app.utils.path_pattern.parse_path`: `{field: value}`.
#[pyfunction]
fn parse_path(py: Python<'_>, pattern: String, path: String) -> Vec<(String, String)> {
    py.detach(|| path_pattern::parse_path(&pattern, &path))
}

/// The token substitution of `app.utils.path_pattern.render_path_pattern`.
#[pyfunction]
fn render_path_pattern(pattern: String, values: std::collections::HashMap<String, String>) -> String {
    path_pattern::render(&pattern, &values)
}

/// `app.utils.string_utils.soundex`.
#[pyfunction]
fn soundex(s: &str) -> String {
    strings::soundex(s)
}

/// The thumbnail store (`thumbnails.lmdb`), same format as the py-lmdb one.
///
/// A process uses this **or** py-lmdb for a store, never both (LMDB forbids
/// opening one environment twice in a process). Every method releases the GIL.
/// After `close()` every method raises `ValueError`.
#[pyclass(frozen, name = "ThumbStore")]
struct PyThumbStore {
    inner: std::sync::RwLock<Option<thumbstore::ThumbStore>>,
}

fn store_err(e: heed::Error) -> PyErr {
    match e {
        heed::Error::Io(io) => io.into(),
        other => PyRuntimeError::new_err(format!("thumbnail store: {other}")),
    }
}

impl PyThumbStore {
    fn with<T: Send>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&thumbstore::ThumbStore) -> heed::Result<T> + Send,
    ) -> PyResult<T> {
        py.detach(|| {
            let guard = self.inner.read().unwrap_or_else(|e| e.into_inner());
            match guard.as_ref() {
                Some(s) => f(s).map_err(store_err),
                None => Err(PyValueError::new_err("thumbnail store is closed")),
            }
        })
    }
}

#[pymethods]
impl PyThumbStore {
    #[new]
    fn new(py: Python<'_>, path: std::path::PathBuf) -> PyResult<Self> {
        let store = py.detach(|| thumbstore::ThumbStore::open(&path)).map_err(store_err)?;
        Ok(PyThumbStore { inner: std::sync::RwLock::new(Some(store)) })
    }

    /// Store `(image_id, bytes)` pairs in one transaction (deduplicated).
    fn put_many(&self, py: Python<'_>, items: Vec<(u64, Vec<u8>)>) -> PyResult<()> {
        let refs: Vec<(u64, &[u8])> = items.iter().map(|(id, d)| (*id, d.as_slice())).collect();
        self.with(py, |s| s.put_many(&refs))
    }

    /// `{image_id: bytes}` for the ids that have a thumbnail.
    fn get_many<'py>(&self, py: Python<'py>, ids: Vec<u64>) -> PyResult<Bound<'py, PyDict>> {
        let found = self.with(py, |s| s.get_many(&ids))?;
        let out = PyDict::new(py);
        for (id, data) in found {
            out.set_item(id, PyBytes::new(py, &data))?;
        }
        Ok(out)
    }

    fn get<'py>(&self, py: Python<'py>, image_id: u64) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let found = self.with(py, |s| s.get_many(&[image_id]))?;
        Ok(found.into_iter().next().map(|(_, d)| PyBytes::new(py, &d)))
    }

    fn delete_many(&self, py: Python<'_>, ids: Vec<u64>) -> PyResult<()> {
        self.with(py, |s| s.delete_many(&ids))
    }

    /// `{"thumbnails", "blobs", "legacy", "payload_bytes"}`.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let st = self.with(py, |s| s.stats())?;
        let out = PyDict::new(py);
        out.set_item("thumbnails", st.thumbnails)?;
        out.set_item("blobs", st.blobs)?;
        out.set_item("legacy", st.legacy)?;
        out.set_item("payload_bytes", st.payload_bytes)?;
        Ok(out)
    }

    /// Convert up to `limit` legacy entries; returns how many were converted.
    fn convert_legacy(&self, py: Python<'_>, limit: usize) -> PyResult<usize> {
        self.with(py, |s| s.convert_legacy(limit))
    }

    /// Keys of blobs nothing references.
    fn orphan_blobs<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        let keys = self.with(py, |s| s.orphan_blobs())?;
        Ok(keys.iter().map(|k| PyBytes::new(py, k)).collect())
    }

    fn delete_keys(&self, py: Python<'_>, keys: Vec<Vec<u8>>) -> PyResult<()> {
        self.with(py, |s| s.delete_keys(&keys))
    }

    /// Write raw `(key, value)` pairs (tests and tools).
    fn put_raw(&self, py: Python<'_>, items: Vec<(Vec<u8>, Vec<u8>)>) -> PyResult<()> {
        self.with(py, |s| s.put_raw(&items))
    }

    /// Write a compacted copy of the store to the file `path`.
    fn copy_compacted(&self, py: Python<'_>, path: std::path::PathBuf) -> PyResult<()> {
        self.with(py, |s| s.copy_compacted(&path))
    }

    fn map_size(&self, py: Python<'_>) -> PyResult<usize> {
        self.with(py, |s| Ok(s.map_size()))
    }

    /// Close the environment (waits until LMDB has released it). Idempotent.
    fn close(&self, py: Python<'_>) {
        py.detach(|| {
            let store = self.inner.write().unwrap_or_else(|e| e.into_inner()).take();
            if let Some(s) = store {
                s.close();
            }
        })
    }
}

/// Pairs of 128-bit hashes within `max_dist` bits.
///
/// `hi` / `lo`: uint64 arrays (high and low halves). Returns `(i, j, dist)` as
/// bytes of uint32, uint32 and uint8 for `np.frombuffer`. Raises `Stopped`.
#[pyfunction]
#[pyo3(signature = (hi, lo, max_dist, progress=None, should_stop=None))]
fn hamming_pairs<'py>(
    py: Python<'py>,
    hi: PyBuffer<u64>,
    lo: PyBuffer<u64>,
    max_dist: u32,
    progress: Option<Bound<'py, PyAny>>,
    should_stop: Option<Bound<'py, PyAny>>,
) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    let (h, l) = (contiguous(&hi, "hi")?, contiguous(&lo, "lo")?);
    if h.len() != l.len() {
        return Err(PyValueError::new_err("hi and lo must have the same length"));
    }
    let p = run_job(py, progress.as_ref(), should_stop.as_ref(), |ctl| {
        hamming::near_pairs_128(h, l, max_dist, ctl)
    })?;
    Ok((bytes(py, &p.i), bytes(py, &p.j), bytes(py, &p.score)))
}

/// Pairs of L2-normalised vectors with cosine similarity >= `threshold`.
///
/// `vecs`: float32 array of shape (n, dim). Returns `(i, j, similarity)` as
/// bytes of uint32, uint32 and float32. Raises `Stopped`.
#[pyfunction]
#[pyo3(signature = (vecs, dim, threshold, progress=None, should_stop=None))]
fn cosine_pairs<'py>(
    py: Python<'py>,
    vecs: PyBuffer<f32>,
    dim: usize,
    threshold: f32,
    progress: Option<Bound<'py, PyAny>>,
    should_stop: Option<Bound<'py, PyAny>>,
) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    let v = contiguous(&vecs, "vecs")?;
    if dim == 0 || !v.len().is_multiple_of(dim) {
        return Err(PyValueError::new_err("vecs size must be a multiple of dim"));
    }
    let p = run_job(py, progress.as_ref(), should_stop.as_ref(), |ctl| {
        embedding::near_pairs_cosine(v, dim, threshold, ctl)
    })?;
    Ok((bytes(py, &p.i), bytes(py, &p.j), bytes(py, &p.score)))
}

/// All `(i, j, distance)` with `i < j` and Levenshtein distance <= `max_distance`,
/// sorted by `(i, j)`; distance 0 (equal strings) included.
#[pyfunction]
fn levenshtein_pairs(py: Python<'_>, values: Vec<String>, max_distance: u32) -> Vec<(u32, u32, u32)> {
    py.detach(|| strings::levenshtein_pairs(&values, max_distance))
}

#[pymodule]
fn stl_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", stl_core::VERSION)?;
    m.add("Stopped", m.py().get_type::<Stopped>())?;
    m.add_function(wrap_pyfunction!(archive_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(archive_members, m)?)?;
    m.add_function(wrap_pyfunction!(archive_hash_file, m)?)?;
    m.add_function(wrap_pyfunction!(read_archive_members, m)?)?;
    m.add_function(wrap_pyfunction!(make_thumbnail, m)?)?;
    m.add_function(wrap_pyfunction!(dhash, m)?)?;
    m.add_function(wrap_pyfunction!(dhash_many, m)?)?;
    m.add_class::<PyThumbStore>()?;
    for f in [
        wrap_pyfunction!(detect_presupport, m)?,
        wrap_pyfunction!(detect_warnings, m)?,
        wrap_pyfunction!(explain_warnings, m)?,
        wrap_pyfunction!(detect_scale, m)?,
        wrap_pyfunction!(parse_path, m)?,
        wrap_pyfunction!(render_path_pattern, m)?,
        wrap_pyfunction!(soundex, m)?,
        wrap_pyfunction!(migrate_database, m)?,
    ] {
        m.add_function(f)?;
    }
    m.add_function(wrap_pyfunction!(hamming_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_pairs, m)?)?;
    m.add_function(wrap_pyfunction!(levenshtein_pairs, m)?)?;
    Ok(())
}
