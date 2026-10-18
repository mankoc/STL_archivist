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
use stl_core::storage::library;
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

/// Read access to the library database for the browser and editors
/// (`storage::library`): a small pool of connections, each opened with the
/// same pragmas as the app's. Every method releases the GIL.
///
/// A `FilterSpec` crosses as a dict: `{"include": {facet: [values]}, "exclude":
/// {...}, "has_value": [...], "no_value": [...], "ids": [...] | None}` with
/// values `str`, `int` or `(year, month | None)`.
/// A maintenance result row: `(id, title, path, hash, size, status or match path)`.
type MaintRow<T> = (i64, String, String, String, Option<i64>, T);

#[pyclass(frozen, name = "LibraryDb")]
struct PyLibraryDb {
    path: std::path::PathBuf,
    pool: std::sync::Mutex<Vec<rusqlite::Connection>>,
}

fn db_err(e: rusqlite::Error) -> PyErr {
    PyRuntimeError::new_err(format!("library database: {e}"))
}

impl PyLibraryDb {
    fn with<T: Send>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send,
    ) -> PyResult<T> {
        py.detach(|| {
            let conn = self.pool.lock().unwrap_or_else(|e| e.into_inner()).pop();
            let conn = match conn {
                Some(c) => c,
                None => storage::open(&self.path).map_err(db_err)?,
            };
            let out = f(&conn).map_err(db_err);
            let mut pool = self.pool.lock().unwrap_or_else(|e| e.into_inner());
            if pool.len() < 8 {
                pool.push(conn);
            }
            out
        })
    }
}

impl PyLibraryDb {
    /// Run `f` in one `BEGIN IMMEDIATE` transaction on a pooled connection;
    /// commits on `Ok`, rolls back on `Err`.
    fn with_tx<T: Send, E: Send>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&rusqlite::Transaction) -> Result<T, E> + Send,
        err: fn(E) -> PyErr,
    ) -> PyResult<T> {
        py.detach(|| {
            let conn = self.pool.lock().unwrap_or_else(|e| e.into_inner()).pop();
            let mut conn = match conn {
                Some(c) => c,
                None => storage::open(&self.path).map_err(db_err)?,
            };
            let out = (|| {
                let tx = conn
                    .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
                    .map_err(db_err)?;
                let v = f(&tx).map_err(err)?;
                tx.commit().map_err(db_err)?;
                Ok(v)
            })();
            let mut pool = self.pool.lock().unwrap_or_else(|e| e.into_inner());
            if pool.len() < 8 {
                pool.push(conn);
            }
            out
        })
    }
}

fn edit_err(e: stl_core::storage::edit::EditError) -> PyErr {
    use stl_core::storage::edit::EditError;
    match e {
        EditError::Db(e) => db_err(e),
        EditError::Unsupported(m) => PyValueError::new_err(m),
        EditError::UnknownField(f) => pyo3::exceptions::PyKeyError::new_err(f),
    }
}

fn filter_value(v: &Bound<'_, PyAny>) -> PyResult<library::FilterValue> {
    if let Ok((y, m)) = v.extract::<(i64, Option<i64>)>() {
        return Ok(library::FilterValue::Date(y, m));
    }
    if !v.is_instance_of::<pyo3::types::PyBool>()
        && let Ok(i) = v.extract::<i64>()
    {
        return Ok(library::FilterValue::Int(i));
    }
    Ok(library::FilterValue::Text(v.extract::<String>()?))
}

fn filter_spec(d: Option<&Bound<'_, PyDict>>) -> PyResult<Option<library::FilterSpec>> {
    let Some(d) = d else { return Ok(None) };
    let mut spec = library::FilterSpec::default();
    for (key, target) in [("include", &mut spec.include), ("exclude", &mut spec.exclude)] {
        if let Some(m) = d.get_item(key)? {
            for (facet, values) in m.cast::<PyDict>()?.iter() {
                let facet: String = facet.extract()?;
                if !library::FACETS.contains(&facet.as_str()) {
                    return Err(PyValueError::new_err(format!("unknown facet {facet}")));
                }
                let vals: Vec<library::FilterValue> =
                    values.try_iter()?.map(|v| filter_value(&v?)).collect::<PyResult<_>>()?;
                if !vals.is_empty() {
                    target.insert(facet, vals);
                }
            }
        }
    }
    for (key, target) in [("has_value", &mut spec.has_value), ("no_value", &mut spec.no_value)] {
        if let Some(v) = d.get_item(key)? {
            for g in v.try_iter()? {
                target.insert(g?.extract()?);
            }
        }
    }
    if let Some(ids) = d.get_item("ids")?
        && !ids.is_none()
    {
        spec.ids = Some(ids.extract()?);
    }
    Ok(Some(spec))
}

fn key_to_py<'py>(py: Python<'py>, k: &library::Key) -> PyResult<Bound<'py, PyAny>> {
    use library::Key;
    Ok(match k {
        Key::Text(kind, s) => (kind.as_str(), s.as_str()).into_pyobject(py)?.into_any(),
        Key::Int(kind, i) => (kind.as_str(), *i).into_pyobject(py)?.into_any(),
        Key::Month(y, m) => ("date_month", (*y, *m)).into_pyobject(py)?.into_any(),
        Key::Year(y) => ("date_year", *y).into_pyobject(py)?.into_any(),
        Key::Group(g) => ("group", g.as_str()).into_pyobject(py)?.into_any(),
    })
}

fn counts_to_py<'py>(
    py: Python<'py>,
    counts: &std::collections::HashMap<library::Key, i64>,
) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    for (k, v) in counts {
        out.set_item(key_to_py(py, k)?, v)?;
    }
    Ok(out)
}

fn sql_value_to_py<'py>(py: Python<'py>, v: &rusqlite::types::Value) -> PyResult<Bound<'py, PyAny>> {
    use rusqlite::types::Value;
    Ok(match v {
        Value::Null => py.None().into_bound(py),
        Value::Integer(i) => i.into_pyobject(py)?.into_any(),
        Value::Real(f) => f.into_pyobject(py)?.into_any(),
        Value::Text(s) => s.into_pyobject(py)?.into_any(),
        Value::Blob(b) => PyBytes::new(py, b).into_any(),
    })
}

fn ranking_to_py<'py>(
    py: Python<'py>,
    rows: &[(rusqlite::types::Value, i64)],
) -> PyResult<Vec<Bound<'py, PyAny>>> {
    rows.iter()
        .map(|(v, n)| Ok((sql_value_to_py(py, v)?, *n).into_pyobject(py)?.into_any()))
        .collect()
}

#[pymethods]
impl PyLibraryDb {
    #[new]
    fn new(path: std::path::PathBuf) -> Self {
        PyLibraryDb { path, pool: std::sync::Mutex::new(Vec::new()) }
    }

    /// `library_query.table_ids`; `sort` is `(column, descending)` or `None`.
    #[pyo3(signature = (spec, search="", sort=None))]
    fn table_ids(
        &self,
        py: Python<'_>,
        spec: &Bound<'_, PyDict>,
        search: &str,
        sort: Option<(String, bool)>,
    ) -> PyResult<Vec<i64>> {
        let spec = filter_spec(Some(spec))?.unwrap_or_default();
        let search = search.to_string();
        self.with(py, |c| library::table_ids(c, &spec, &search, sort.as_ref().map(|(s, d)| (s.as_str(), *d))))
    }

    /// `library_query.page_rows` without the pair index: tuples of
    /// `(id, title, creator, source, release_year, release_month, archive_path,
    /// presupports, warning, archive_size, compression_rate, sequence,
    /// created_at, sculptor_name, collection_name, subcollection_name,
    /// category_name, release_name, scales, tags, to_print)`; `created_at` is text.
    fn page_rows<'py>(&self, py: Python<'py>, ids: Vec<i64>) -> PyResult<Vec<Bound<'py, PyAny>>> {
        let rows = self.with(py, |c| library::page_rows(c, &ids))?;
        rows.into_iter()
            .map(|r| {
                let head = (
                    r.id, r.title, r.creator, r.source, r.release_year, r.release_month, r.archive_path,
                    r.presupports, r.warning, r.archive_size,
                );
                let tail = (
                    r.compression_rate, r.sequence, r.created_at, r.sculptor_name, r.collection_name,
                    r.subcollection_name, r.category_name, r.release_name, r.scales, r.tags, r.to_print,
                );
                Ok((head, tail).into_pyobject(py)?.into_any())
            })
            .collect()
    }

    /// `filter_tree.filter_tree_data` (lists as Python lists; `new_dirs` instead of
    /// the frozenset; `scoped` flag).
    fn filter_tree<'py>(
        &self,
        py: Python<'py>,
        spec: Option<&Bound<'py, PyDict>>,
        show: Vec<String>,
        show_counts: bool,
    ) -> PyResult<Bound<'py, PyDict>> {
        let spec = filter_spec(spec)?;
        let show = library::Show(show.into_iter().collect());
        let ft = self.with(py, |c| library::filter_tree(c, spec.as_ref(), &show, show_counts))?;
        let out = PyDict::new(py);
        out.set_item("counts", counts_to_py(py, &ft.counts)?)?;
        out.set_item("path_counts", ft.path_counts)?;
        out.set_item("scoped_counts", counts_to_py(py, &ft.scoped_counts)?)?;
        out.set_item("sc_path_counts", ft.sc_path_counts)?;
        out.set_item("creators", ft.creators)?;
        out.set_item("tags", ft.tags)?;
        out.set_item("sources", ft.sources)?;
        out.set_item("dates", ft.dates)?;
        out.set_item("sculptors", ft.sculptors)?;
        out.set_item("collections", ft.collections)?;
        out.set_item("sub_by_col", ft.sub_by_col)?;
        out.set_item("categories", ft.categories)?;
        out.set_item("releases", ft.releases)?;
        out.set_item("scales", ft.scales)?;
        out.set_item("presupports", ft.presupports)?;
        out.set_item("filetypes", ft.filetypes)?;
        out.set_item("sorted_dirs", ft.sorted_dirs)?;
        out.set_item("scoped_dirs", ft.scoped_dirs)?;
        out.set_item("new_dirs", ft.new_dirs)?;
        out.set_item("scoped", ft.scoped)?;
        Ok(out)
    }

    /// `lookups.usage_counts`.
    fn usage_counts(&self, py: Python<'_>, kind: String) -> PyResult<std::collections::HashMap<i64, i64>> {
        self.with(py, |c| library::usage_counts(c, &kind))
    }

    /// `model_summary.model_summary`: a dict, or `None` when the model is gone.
    fn model_summary<'py>(&self, py: Python<'py>, model_id: i64) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(row) = self.with(py, |c| library::model_summary(c, model_id))? else { return Ok(None) };
        let out = PyDict::new(py);
        for (k, v) in &row {
            out.set_item(k, sql_value_to_py(py, v)?)?;
        }
        Ok(Some(out))
    }

    /// `editing.apply_cell_edit` for `[(model_id, field, text | None)]`, in order,
    /// in one transaction. Returns the archive paths of the models that exist.
    /// Raises `ValueError` for input Python handles differently (non-ASCII
    /// digits) and `KeyError` for an unknown field; nothing is written then.
    fn apply_cell_edits(&self, py: Python<'_>, edits: Vec<(i64, String, Option<String>)>) -> PyResult<Vec<String>> {
        use stl_core::storage::edit;
        self.with_tx(py, |tx| edit::apply_cell_edits(tx, &edits), edit_err)
    }

    /// `moves.relocate`.
    #[pyo3(signature = (model_id, new_path, archive_hash, size, mtime_ns, compression=None))]
    #[allow(clippy::too_many_arguments)] // mirrors moves.relocate
    fn relocate(
        &self,
        py: Python<'_>,
        model_id: i64,
        new_path: String,
        archive_hash: String,
        size: Option<i64>,
        mtime_ns: Option<i64>,
        compression: Option<f64>,
    ) -> PyResult<()> {
        use stl_core::storage::edit;
        self.with_tx(py, |tx| edit::relocate(tx, model_id, &new_path, &archive_hash, size, mtime_ns, compression), db_err)
    }

    /// `moves.delete_model`: returns the thumbnail references to delete after commit.
    fn delete_model(&self, py: Python<'_>, model_id: i64) -> PyResult<Vec<String>> {
        use stl_core::storage::edit;
        self.with_tx(py, |tx| edit::delete_model(tx, model_id), db_err)
    }

    /// `moves.merge_into_missing` by ids: returns the deleted record's thumbnail references.
    fn merge_into_missing(&self, py: Python<'_>, old_id: i64, new_id: i64) -> PyResult<Vec<String>> {
        use stl_core::storage::edit;
        self.with_tx(py, |tx| edit::merge_into_missing(tx, old_id, new_id), db_err)
    }

    /// `helpers.prune_unused_lookups`: `{table: rows deleted}` for non-zero counts.
    fn prune_unused_lookups(&self, py: Python<'_>) -> PyResult<std::collections::HashMap<&'static str, usize>> {
        use stl_core::storage::edit;
        Ok(self.with_tx(py, edit::prune_unused_lookups, db_err)?.into_iter().collect())
    }

    /// `vectors.read_all`: `(image ids as int64 bytes, float32 matrix as bytearray)`.
    fn vectors_read_all<'py>(&self, py: Python<'py>, dim: usize) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyAny>)> {
        let (ids, mat) = self.with(py, |c| stl_core::storage::vectors::read_all(c, dim))?;
        Ok((bytes(py, &ids), pyo3::types::PyByteArray::new(py, bytemuck::cast_slice(&mat)).into_any()))
    }

    /// `image_similarity.load_model_vectors`: like `vectors_read_all`, with model ids.
    fn load_model_vectors<'py>(&self, py: Python<'py>, dim: usize) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyAny>)> {
        let (ids, mat) = self.with(py, |c| stl_core::storage::vectors::load_model_vectors(c, dim))?;
        Ok((bytes(py, &ids), pyo3::types::PyByteArray::new(py, bytemuck::cast_slice(&mat)).into_any()))
    }

    /// `image_similarity.find_similar_models`: `([(similarity, model_id, title,
    /// archive_name, thumbnail_path)], capped)`.
    fn find_similar_models(
        &self,
        py: Python<'_>,
        query: Vec<u8>,
        threshold: f64,
        limit: usize,
    ) -> PyResult<(Vec<stl_core::storage::vectors::Similar>, bool)> {
        self.with(py, |c| stl_core::storage::vectors::find_similar_models(c, &query, threshold, limit))
    }

    /// `maintenance.verify_archives` core: `(total, [(id, title, path, hash,
    /// size, status)], stopped)`; clears the missing flag of files that came back.
    #[pyo3(signature = (progress=None, should_stop=None))]
    fn verify_archives<'py>(
        &self,
        py: Python<'py>,
        progress: Option<Bound<'py, PyAny>>,
        should_stop: Option<Bound<'py, PyAny>>,
    ) -> PyResult<(usize, Vec<MaintRow<&'static str>>, bool, usize)> {
        use stl_core::storage::maintenance;
        let conn = self.pool.lock().unwrap_or_else(|e| e.into_inner()).pop();
        let conn = match conn {
            Some(c) => c,
            None => storage::open(&self.path).map_err(db_err)?,
        };
        let outcome = run_job(py, progress.as_ref(), should_stop.as_ref(), move |ctl| {
            let v = maintenance::verify(&conn, ctl);
            Ok((v, conn))
        });
        let (verified, conn) = match outcome {
            Ok(x) => x,
            // Stopped before the first file: nothing checked, as the Python loop.
            Err(e) if e.is_instance_of::<Stopped>(py) => {
                let total = self.with(py, maintenance::model_count)?;
                return Ok((total, Vec::new(), true, 0));
            }
            Err(e) => return Err(e),
        };
        self.pool.lock().unwrap_or_else(|e| e.into_inner()).push(conn);
        let v = verified.map_err(db_err)?;
        let cleared = if v.reappeared.is_empty() {
            0
        } else {
            let ids = v.reappeared.clone();
            self.with_tx(py, move |tx| maintenance::clear_missing(tx, &ids), db_err)?
        };
        let rows = v.results.into_iter().map(|r| (r.id, r.title, r.path, r.hash, r.size, r.status)).collect();
        Ok((v.total, rows, v.stopped, cleared))
    }

    /// `maintenance.find_duplicate_entries` core: `[(id, title, path, hash, size, match_path)]`.
    fn duplicate_entries(&self, py: Python<'_>) -> PyResult<Vec<MaintRow<String>>> {
        let rows = self.with(py, stl_core::storage::maintenance::duplicate_entries)?;
        Ok(rows.into_iter().map(|r| (r.id, r.title, r.path, r.hash, r.size, r.match_path)).collect())
    }

    /// `prune_images`: model ids with more than `keep` images.
    fn images_over(&self, py: Python<'_>, keep: i64) -> PyResult<Vec<i64>> {
        self.with(py, |c| stl_core::storage::maintenance::models_over(c, keep))
    }

    /// `prune_images`, one batch: `(models pruned, excess image ids)`.
    fn excess_images(&self, py: Python<'_>, model_ids: Vec<i64>, keep: usize) -> PyResult<(usize, Vec<i64>)> {
        self.with(py, |c| stl_core::storage::maintenance::excess_images(c, &model_ids, keep))
    }

    /// Delete images by id (and, through the TEMP trigger, their vectors).
    fn delete_images(&self, py: Python<'_>, ids: Vec<i64>) -> PyResult<usize> {
        self.with_tx(py, move |tx| stl_core::storage::maintenance::delete_images(tx, &ids), db_err)
    }

    // ── model editing (services.model_edit) ───────────────────────────────────

    fn edit_editor_choices<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let lists = self.with(py, stl_core::storage::model_edit::editor_choices)?;
        let out = PyDict::new(py);
        for (k, v) in lists {
            out.set_item(k, v)?;
        }
        Ok(out)
    }

    fn edit_subcollection_names(&self, py: Python<'_>, collection_name: String) -> PyResult<Vec<String>> {
        self.with(py, |c| stl_core::storage::model_edit::subcollection_names(c, &collection_name))
    }

    fn edit_model_for_editor<'py>(&self, py: Python<'py>, model_id: i64) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(m) = self.with(py, |c| stl_core::storage::model_edit::model_for_editor(c, model_id))? else {
            return Ok(None);
        };
        let out = PyDict::new(py);
        out.set_item("archive_path", m.archive_path)?;
        out.set_item("title", m.row.title)?;
        out.set_item("creator", m.row.creator)?;
        out.set_item("source", m.row.source)?;
        out.set_item("release_year", m.row.release_year)?;
        out.set_item("release_month", m.row.release_month)?;
        out.set_item("release", m.release)?;
        out.set_item("sculptor", m.sculptor)?;
        out.set_item("collection", m.collection)?;
        out.set_item("subcollection", m.subcollection)?;
        out.set_item("category", m.category)?;
        out.set_item("sequence", m.row.sequence)?;
        out.set_item("presupports", m.row.presupports)?;
        out.set_item("url", m.row.url)?;
        out.set_item("description", m.row.description)?;
        out.set_item("scales", m.scales)?;
        out.set_item("tags", m.tags)?;
        out.set_item("images", m.images)?;
        Ok(Some(out))
    }

    /// Returns the archive path (`None` when the model is gone or has none).
    fn edit_save_model_editor(&self, py: Python<'_>, model_id: i64, form: &Bound<'_, PyDict>) -> PyResult<Option<String>> {
        use stl_core::storage::model_edit::{EditorForm, save_model_editor};
        let get = |k: &str| -> PyResult<String> { Ok(form.get_item(k)?.map(|v| v.extract()).transpose()?.unwrap_or_default()) };
        let f = EditorForm {
            title: get("title")?,
            creator: get("creator")?,
            source: get("source")?,
            date: get("date")?,
            presupports: get("presupports")?,
            url: get("url")?,
            description: get("description")?,
            sequence: form.get_item("sequence")?.map(|v| v.extract()).transpose()?.unwrap_or(0),
            release: get("release")?,
            sculptor: get("sculptor")?,
            collection: get("collection")?,
            subcollection: get("subcollection")?,
            category: get("category")?,
            scales: form.get_item("scales")?.map(|v| v.extract()).transpose()?.unwrap_or_default(),
            tags: form.get_item("tags")?.map(|v| v.extract()).transpose()?.unwrap_or_default(),
        };
        let out = self.with_tx(py, move |tx| save_model_editor(tx, model_id, &f), edit_err)?;
        Ok(out.flatten())
    }

    fn edit_ai_model_inputs<'py>(&self, py: Python<'py>, model_id: i64) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(a) = self.with(py, |c| stl_core::storage::model_edit::ai_model_inputs(c, model_id))? else {
            return Ok(None);
        };
        let out = PyDict::new(py);
        out.set_item("archive_path", a.archive_path)?;
        out.set_item("title", a.title)?;
        out.set_item("meta", a.meta)?;
        out.set_item("tags", a.tags)?;
        out.set_item("images", a.images)?;
        out.set_item("files", a.files)?;
        Ok(Some(out))
    }

    fn edit_ai_commit(
        &self,
        py: Python<'_>,
        model_id: i64,
        fields: std::collections::HashMap<String, String>,
        year: Option<i64>,
        tags: Vec<String>,
        overwrite: bool,
    ) -> PyResult<bool> {
        use stl_core::storage::model_edit::{AiFields, ai_commit};
        let g = |k: &str| fields.get(k).cloned().unwrap_or_default();
        let f = AiFields {
            title: g("title"),
            creator: g("creator"),
            description: g("description"),
            source: g("source"),
            sculptor: g("sculptor"),
            collection: g("collection"),
            subcollection: g("subcollection"),
            scale: g("scale"),
            year,
        };
        self.with_tx(py, move |tx| ai_commit(tx, model_id, &f, &tags, overwrite), edit_err)
    }

    fn edit_mass_choices<'py>(&self, py: Python<'py>, model_ids: Vec<i64>) -> PyResult<Bound<'py, PyDict>> {
        let lists = self.with(py, |c| stl_core::storage::model_edit::mass_choices(c, &model_ids))?;
        let out = PyDict::new(py);
        for (k, v) in lists {
            out.set_item(k, v)?;
        }
        Ok(out)
    }

    fn edit_archive_paths(&self, py: Python<'_>, model_ids: Vec<i64>) -> PyResult<std::collections::HashMap<i64, String>> {
        self.with(py, |c| stl_core::storage::model_edit::archive_paths(c, &model_ids))
    }

    /// `model_edit.mass_save`; raises `ValueError` (nothing written) for what the
    /// native port leaves to Python.
    fn edit_mass_save(&self, py: Python<'_>, plan: &Bound<'_, PyDict>) -> PyResult<()> {
        use stl_core::storage::model_edit::{Find, MassPlan, mass_save};
        let s = |k: &str| -> PyResult<String> { Ok(plan.get_item(k)?.map(|v| v.extract()).transpose()?.unwrap_or_default()) };
        let mut p = MassPlan {
            model_ids: plan.get_item("model_ids")?.map(|v| v.extract()).transpose()?.unwrap_or_default(),
            overwrite: plan.get_item("overwrite")?.map(|v| v.extract()).transpose()?.unwrap_or(false),
            scale: s("scale")?,
            release: s("release")?,
            collection: s("collection")?,
            subcollection: s("subcollection")?,
            category: s("category")?,
            sculptor: s("sculptor")?,
            tags: s("tags")?,
            trim: plan.get_item("trim")?.map(|v| v.extract()).transpose()?.unwrap_or(false),
            presupports: plan.get_item("presupports")?.map(|v| v.extract()).transpose()?.flatten(),
            ..Default::default()
        };
        if let Some(c) = plan.get_item("clearing")? {
            for x in c.try_iter()? {
                p.clearing.insert(x?.extract()?);
            }
        }
        if let Some(m) = plan.get_item("manual")? {
            for (k, v) in m.cast::<PyDict>()?.iter() {
                p.manual.push((k.extract()?, v.extract()?));
            }
        }
        if let Some(pr) = plan.get_item("path_results")? {
            for (k, v) in pr.cast::<PyDict>()?.iter() {
                let fields: Vec<(String, String)> = v
                    .cast::<PyDict>()?
                    .iter()
                    .map(|(a, b)| Ok((a.extract()?, b.extract()?)))
                    .collect::<PyResult<_>>()?;
                p.path_results.insert(k.extract()?, fields);
            }
        }
        if let Some(f) = plan.get_item("find")?
            && !f.is_none()
        {
            let f = f.cast::<PyDict>()?;
            let g = |k: &str| -> PyResult<Bound<'_, PyAny>> {
                f.get_item(k)?.ok_or_else(|| PyValueError::new_err(format!("find.{k} missing")))
            };
            p.find = Some(Find {
                find: g("find")?.extract()?,
                replace: g("replace")?.extract()?,
                case: g("case")?.extract()?,
                regex: g("regex")?.extract()?,
                fields: g("fields")?.extract()?,
            });
        }
        self.with_tx(py, move |tx| mass_save(tx, &p), edit_err)
    }

    // ── lookup administration (services.lookup_admin) ─────────────────────────

    /// `(id, name, models)`, or `(id, name, models, collection_id, collection_name)`
    /// for subcollections.
    fn admin_list<'py>(&self, py: Python<'py>, kind: String) -> PyResult<Vec<Bound<'py, PyAny>>> {
        use stl_core::storage::admin;
        let rows = self.with(py, |c| admin::list(c, &kind))?;
        rows.into_iter()
            .map(|(id, name, n, cid, cname)| {
                Ok(match (cid, cname) {
                    (Some(cid), Some(cname)) => (id, name, n, cid, cname).into_pyobject(py)?.into_any(),
                    _ => (id, name, n).into_pyobject(py)?.into_any(),
                })
            })
            .collect()
    }

    fn admin_tags_for_scan(&self, py: Python<'_>) -> PyResult<Vec<(i64, String, i64)>> {
        self.with(py, stl_core::storage::admin::tags_for_scan)
    }

    #[pyo3(signature = (kind, name, collection_name=None))]
    fn admin_add(&self, py: Python<'_>, kind: String, name: String, collection_name: Option<String>) -> PyResult<bool> {
        self.with_tx(py, move |tx| stl_core::storage::admin::add(tx, &kind, &name, collection_name.as_deref()), db_err)
    }

    fn admin_name_of(&self, py: Python<'_>, kind: String, id: i64) -> PyResult<Option<(String, Option<i64>)>> {
        self.with(py, |c| stl_core::storage::admin::name_of(c, &kind, id))
    }

    fn admin_rename(&self, py: Python<'_>, kind: String, id: i64, new_name: String) -> PyResult<bool> {
        self.with_tx(py, move |tx| stl_core::storage::admin::rename(tx, &kind, id, &new_name), db_err)
    }

    fn admin_delete_info(&self, py: Python<'_>, kind: String, id: i64) -> PyResult<Option<(String, i64, i64)>> {
        self.with(py, |c| stl_core::storage::admin::delete_info(c, &kind, id))
    }

    fn admin_delete(&self, py: Python<'_>, kind: String, id: i64) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::delete(tx, &kind, id), db_err)
    }

    fn admin_merge(&self, py: Python<'_>, kind: String, keep_id: i64, delete_id: i64) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::merge(tx, &kind, keep_id, delete_id), db_err)
    }

    fn admin_unused_tag_count(&self, py: Python<'_>) -> PyResult<i64> {
        self.with(py, stl_core::storage::admin::unused_tag_count)
    }

    fn admin_delete_unused_tags(&self, py: Python<'_>) -> PyResult<usize> {
        self.with_tx(py, stl_core::storage::admin::delete_unused_tags, db_err)
    }

    fn admin_merge_tags(&self, py: Python<'_>, tag_ids: Vec<i64>, target: String) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::merge_tags(tx, &tag_ids, &target), db_err)
    }

    fn admin_author_counts(&self, py: Python<'_>) -> PyResult<Vec<(String, i64)>> {
        self.with(py, stl_core::storage::admin::author_counts)
    }

    fn admin_author_model_count(&self, py: Python<'_>, name: String) -> PyResult<i64> {
        self.with(py, |c| stl_core::storage::admin::author_model_count(c, &name))
    }

    fn admin_rename_author(&self, py: Python<'_>, old_name: String, new_name: String) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::rename_author(tx, &old_name, &new_name), db_err)
    }

    fn admin_delete_author(&self, py: Python<'_>, name: String) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::delete_author(tx, &name), db_err)
    }

    fn admin_merge_authors(&self, py: Python<'_>, keep_name: String, delete_name: String) -> PyResult<()> {
        self.with_tx(py, move |tx| stl_core::storage::admin::merge_authors(tx, &keep_name, &delete_name), db_err)
    }

    fn admin_creator_profile<'py>(&self, py: Python<'py>, name: String) -> PyResult<Option<Bound<'py, PyDict>>> {
        use stl_core::storage::admin::{PROFILE_FIELDS, creator_profile};
        let Some(vals) = self.with(py, |c| creator_profile(c, &name))? else { return Ok(None) };
        let out = PyDict::new(py);
        for (f, v) in PROFILE_FIELDS.iter().zip(vals) {
            out.set_item(f, v)?;
        }
        Ok(Some(out))
    }

    fn admin_save_creator_profile(&self, py: Python<'_>, name: String, values: &Bound<'_, PyDict>) -> PyResult<()> {
        use stl_core::storage::admin::{PROFILE_FIELDS, save_creator_profile};
        let mut vals: [Option<String>; 5] = Default::default();
        for (slot, f) in vals.iter_mut().zip(PROFILE_FIELDS) {
            *slot = values.get_item(f)?.map(|v| v.extract()).transpose()?.flatten();
        }
        self.with_tx(py, move |tx| save_creator_profile(tx, &name, &vals), db_err)
    }

    // ── statistics (services.library_stats) ───────────────────────────────────

    fn stats_library<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let st = self.with(py, stl_core::storage::stats::library)?;
        let out = PyDict::new(py);
        for (k, v) in &st.scalars {
            out.set_item(k, sql_value_to_py(py, v)?)?;
        }
        for (k, rows) in &st.rankings {
            out.set_item(k, ranking_to_py(py, rows)?)?;
        }
        Ok(out)
    }

    #[pyo3(signature = (key, top_n=None))]
    fn stats_column<'py>(
        &self,
        py: Python<'py>,
        key: String,
        top_n: Option<i64>,
    ) -> PyResult<(Vec<Bound<'py, PyAny>>, i64)> {
        let (rows, total) = self.with(py, |c| stl_core::storage::stats::column(c, &key, top_n))?;
        Ok((ranking_to_py(py, &rows)?, total))
    }

    // ── browser panel and sort dialog (services.browser_data) ─────────────────

    fn browser_lookup_names(&self, py: Python<'_>, kind: String) -> PyResult<Vec<String>> {
        self.with(py, |c| stl_core::storage::browser::lookup_names(c, &kind))
    }

    fn browser_tag_names(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        self.with(py, stl_core::storage::browser::tag_names)
    }

    #[pyo3(signature = (model_id, creator=None))]
    fn browser_model_panel(
        &self,
        py: Python<'_>,
        model_id: i64,
        creator: Option<String>,
    ) -> PyResult<stl_core::storage::browser::Panel> {
        self.with(py, |c| stl_core::storage::browser::model_panel(c, model_id, creator.as_deref()))
    }

    fn browser_model_images(&self, py: Python<'_>, model_id: i64) -> PyResult<Vec<(i64, Option<String>)>> {
        self.with(py, |c| stl_core::storage::browser::model_images(c, model_id))
    }

    fn browser_add_images(&self, py: Python<'_>, model_id: i64, filenames: Vec<String>) -> PyResult<Vec<i64>> {
        self.with_tx(py, |tx| stl_core::storage::browser::add_images(tx, model_id, &filenames), db_err)
    }

    fn browser_set_image_thumbnails(&self, py: Python<'_>, items: Vec<(i64, String)>) -> PyResult<()> {
        self.with_tx(py, |tx| stl_core::storage::browser::set_image_thumbnails(tx, &items), db_err)
    }

    fn browser_add_tag(&self, py: Python<'_>, name: String) -> PyResult<bool> {
        self.with_tx(py, |tx| stl_core::storage::browser::add_tag(tx, &name), db_err)
    }

    /// `moves.delete_model` for each id in one transaction: the thumbnail references.
    fn browser_delete_models(&self, py: Python<'_>, ids: Vec<i64>) -> PyResult<Vec<String>> {
        self.with_tx(py, |tx| stl_core::storage::browser::delete_models(tx, &ids), db_err)
    }

    /// `{id: fields}` for the sort dialog's path pattern.
    fn browser_sort_sources<'py>(&self, py: Python<'py>, ids: Vec<i64>) -> PyResult<Bound<'py, PyDict>> {
        let rows = self.with(py, |c| stl_core::storage::browser::sort_sources(c, &ids))?;
        let out = PyDict::new(py);
        for r in rows {
            let d = PyDict::new(py);
            d.set_item("archive_path", r.archive_path)?;
            d.set_item("title", r.title)?;
            d.set_item("source", r.source)?;
            d.set_item("creator", r.creator)?;
            d.set_item("release_year", sql_value_to_py(py, &r.release_year)?)?;
            d.set_item("release_month", sql_value_to_py(py, &r.release_month)?)?;
            d.set_item("url", r.url)?;
            d.set_item("sequence", sql_value_to_py(py, &r.sequence)?)?;
            d.set_item("sculptor", r.sculptor)?;
            d.set_item("collection", r.collection)?;
            d.set_item("subcollection", r.subcollection)?;
            d.set_item("release", r.release)?;
            out.set_item(r.id, d)?;
        }
        Ok(out)
    }

    fn browser_deletion_rows(&self, py: Python<'_>, ids: Vec<i64>) -> PyResult<Vec<stl_core::storage::browser::DeletionRow>> {
        self.with(py, |c| stl_core::storage::browser::deletion_rows(c, &ids))
    }

    fn browser_set_archive_paths(&self, py: Python<'_>, items: Vec<(i64, String)>) -> PyResult<()> {
        self.with_tx(py, |tx| stl_core::storage::browser::set_archive_paths(tx, &items), db_err)
    }

    /// Close the pooled connections (they reopen on the next call).
    fn close(&self, py: Python<'_>) {
        py.detach(|| self.pool.lock().unwrap_or_else(|e| e.into_inner()).clear());
    }
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
    m.add_class::<PyLibraryDb>()?;
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
