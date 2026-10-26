//! Thumbnail store: the content-addressed LMDB environment `thumbnails.lmdb`.
//!
//! The on-disk format older versions (py-lmdb) wrote, unchanged (compatibility
//! rule 6):
//!
//! ```text
//! R<image_id: u64 BE>        -> blob key of that image's thumbnail
//! D<xxh3-128 BE>[<slot: u8>] -> the JPEG/GIF bytes (slot only after a hash collision)
//! <image_id: u64 BE>         -> legacy, pre-dedup entry: the bytes themselves
//! ```
//!
//! The prefixes are ASCII letters while a legacy key starts with 0x00 for every
//! id below 2^56, so the layouts never clash. Rule 3: a process opens the store
//! once.
//!
//! A blob's digest (the key without its `D` prefix) is a content address: the
//! server serves it at `/api/thumb/<hex>` as an immutable resource.
//!
//! LMDB's map has a fixed size; a write that fills it grows the map and retries.
//! Growing requires that no transaction is open in the process, so every
//! transaction holds a shared lock and growing takes it exclusively, as does
//! compacting (which closes the environment, swaps the file and reopens it).

use std::collections::HashSet;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::{RwLock, RwLockReadGuard};

use heed::types::Bytes;
use heed::{CompactionOption, Database, Env, EnvOpenOptions, MdbError, RwTxn};

use crate::control::Control;

/// Initial map size (address space, not disk: the file is sparse).
pub const MAP_SIZE: usize = 10 * 1024 * 1024 * 1024;
/// How much the map grows each time a write fills it.
pub const GROW_STEP: usize = 2 * 1024 * 1024 * 1024;

const REF_PREFIX: u8 = b'R';
const BLOB_PREFIX: u8 = b'D';

pub type Result<T> = std::result::Result<T, heed::Error>;

/// What the store holds; see `store_stats` in `thumbnailer.py`.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Stats {
    /// Live image references (content-addressed plus legacy).
    pub thumbnails: u64,
    /// Distinct stored payloads.
    pub blobs: u64,
    /// Entries still in the pre-dedup layout.
    pub legacy: u64,
    /// Bytes across blobs and legacy entries.
    pub payload_bytes: u64,
}

/// The open environment and its (unnamed) database.
struct Inner {
    env: Env,
    db: Database<Bytes, Bytes>,
}

pub struct ThumbStore {
    /// Shared by every transaction, exclusive while the map grows or the file
    /// is compacted; `None` only when reopening after a compaction failed.
    inner: RwLock<Option<Inner>>,
    dir: PathBuf,
    /// Content digest of a payload; replaceable in tests to force collisions.
    digest: fn(&[u8]) -> [u8; 16],
}

/// A shared hold on the open environment.
struct Shared<'a>(RwLockReadGuard<'a, Option<Inner>>);

impl Deref for Shared<'_> {
    type Target = Inner;
    fn deref(&self) -> &Inner {
        self.0.as_ref().expect("checked when taken")
    }
}

fn closed() -> heed::Error {
    heed::Error::Io(std::io::Error::other("the thumbnail store is closed"))
}

fn xxh3_digest(data: &[u8]) -> [u8; 16] {
    // xxhash.xxh3_128_digest() is the canonical (big-endian) form.
    xxhash_rust::xxh3::xxh3_128(data).to_be_bytes()
}

fn ref_key(image_id: u64) -> [u8; 9] {
    let mut k = [REF_PREFIX; 9];
    k[1..].copy_from_slice(&image_id.to_be_bytes());
    k
}

fn legacy_key(image_id: u64) -> [u8; 8] {
    image_id.to_be_bytes()
}

fn blob_key(digest: &[u8; 16], slot: u8) -> Vec<u8> {
    let mut k = Vec::with_capacity(18);
    k.push(BLOB_PREFIX);
    k.extend_from_slice(digest);
    if slot != 0 {
        k.push(slot);
    }
    k
}

fn page_align(n: usize) -> usize {
    const PAGE: usize = 64 * 1024; // a multiple of every page size LMDB runs on
    n.div_ceil(PAGE) * PAGE
}

/// Open (creating if needed) the environment in `dir`; see [`ThumbStore::open`].
fn open_env(dir: &Path) -> Result<Inner> {
    std::fs::create_dir_all(dir)?;
    // SAFETY: the file is only modified through LMDB; rule 3 keeps py-lmdb
    // from opening the same environment in this process. LMDB raises the
    // map to the pages in use when it is smaller.
    let env = unsafe { EnvOpenOptions::new().map_size(page_align(MAP_SIZE)).max_dbs(0).open(dir)? };
    // The unnamed main database always exists; opening it needs no write.
    let rtxn = env.read_txn()?;
    let db = env.open_database(&rtxn, None)?;
    rtxn.commit()?;
    let db = match db {
        Some(db) => db,
        None => {
            let mut wtxn = env.write_txn()?;
            let db = env.create_database(&mut wtxn, None)?;
            wtxn.commit()?;
            db
        }
    };
    let rtxn = env.read_txn()?;
    let used = (env.info().last_page_number + 1) * db.stat(&rtxn)?.page_size as usize;
    rtxn.commit()?;
    if env.info().map_size < used + GROW_STEP {
        // SAFETY: no transaction is open on this environment yet.
        unsafe { env.resize(page_align(used + GROW_STEP))? };
    }
    Ok(Inner { env, db })
}

impl ThumbStore {
    /// Open (creating if needed) the store in directory `dir`.
    ///
    /// The map then holds at least [`GROW_STEP`] beyond the pages in use, so a
    /// store that already grew past [`MAP_SIZE`] does not fill on the first
    /// write. It is sized from the pages in use, not from the file: on Windows
    /// LMDB extends `data.mdb` to the map size, so sizing from the file grew the
    /// map (and the file) by a step on every open.
    pub fn open(dir: &Path) -> Result<Self> {
        let inner = open_env(dir)?;
        Ok(ThumbStore { inner: RwLock::new(Some(inner)), dir: dir.to_path_buf(), digest: xxh3_digest })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Hold the environment for a transaction (map growth and compaction wait).
    fn shared(&self) -> Result<Shared<'_>> {
        let guard = self.inner.read().unwrap_or_else(|e| e.into_inner());
        if guard.is_none() {
            return Err(closed());
        }
        Ok(Shared(guard))
    }

    pub fn map_size(&self) -> usize {
        self.shared().map(|s| s.env.info().map_size).unwrap_or(0)
    }

    /// Close the environment and wait until LMDB has released it.
    pub fn close(self) {
        if let Some(inner) = self.inner.into_inner().unwrap_or_else(|e| e.into_inner()) {
            inner.env.prepare_for_closing().wait();
        }
    }

    /// Run `f` in a write transaction and commit, growing the map on MDB_MAP_FULL.
    fn write<T>(&self, mut f: impl FnMut(&mut RwTxn, Database<Bytes, Bytes>) -> Result<T>) -> Result<T> {
        loop {
            let outcome = {
                let s = self.shared()?;
                let mut txn = s.env.write_txn()?;
                match f(&mut txn, s.db) {
                    Ok(v) => txn.commit().map(|_| v),
                    Err(e) => Err(e), // dropping txn aborts it
                }
            };
            match outcome {
                Err(heed::Error::Mdb(MdbError::MapFull)) => {
                    let mut exclusive = self.inner.write().unwrap_or_else(|e| e.into_inner());
                    let inner = exclusive.as_mut().ok_or_else(closed)?;
                    let new_size = page_align(inner.env.info().map_size + GROW_STEP);
                    // SAFETY: the exclusive lock means no transaction is open.
                    unsafe { inner.env.resize(new_size)? };
                }
                other => return other,
            }
        }
    }

    /// Store `data` once (deduplicated) and return its blob key.
    ///
    /// xxh3-128 is not collision-resistant by construction, so a digest hit is
    /// confirmed by comparing bytes; a genuine collision takes the next slot.
    fn put_blob(&self, txn: &mut RwTxn, db: Database<Bytes, Bytes>, data: &[u8]) -> Result<Vec<u8>> {
        let digest = (self.digest)(data);
        for slot in 0..=255u8 {
            let key = blob_key(&digest, slot);
            match db.get(txn, &key)? {
                None => {
                    db.put(txn, &key, data)?;
                    return Ok(key);
                }
                Some(existing) if existing == data => return Ok(key),
                Some(_) => {}
            }
        }
        // 256 distinct payloads on one digest: store uncollapsed rather than fail.
        let key = blob_key(&digest, 255);
        db.put(txn, &key, data)?;
        Ok(key)
    }

    /// Point each image id at its bytes, in one transaction. Empty payloads are skipped.
    pub fn put_many(&self, items: &[(u64, &[u8])]) -> Result<()> {
        if items.iter().all(|(_, d)| d.is_empty()) {
            return Ok(());
        }
        self.write(|txn, db| {
            for &(id, data) in items {
                if data.is_empty() {
                    continue;
                }
                let blob = self.put_blob(txn, db, data)?;
                db.put(txn, &ref_key(id), &blob)?;
                db.delete(txn, &legacy_key(id))?; // drop the pre-dedup entry, if any
            }
            Ok(())
        })
    }

    /// Thumbnails of `ids` that exist, in one read transaction.
    pub fn get_many(&self, ids: &[u64]) -> Result<Vec<(u64, Vec<u8>)>> {
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        let mut out = Vec::with_capacity(ids.len());
        for &id in ids {
            let data = match s.db.get(&txn, &ref_key(id))? {
                Some(blob) => s.db.get(&txn, blob)?,
                None => s.db.get(&txn, &legacy_key(id))?,
            };
            if let Some(d) = data {
                out.push((id, d.to_vec()));
            }
        }
        Ok(out)
    }

    /// The digest each existing id's thumbnail is stored under: `Some(digest)`
    /// (16 bytes, plus a slot byte after a hash collision), or `None` for a
    /// legacy entry, which has no content address yet. Missing ids are left out.
    pub fn digests(&self, ids: &[u64]) -> Result<Vec<(u64, Option<Vec<u8>>)>> {
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        let mut out = Vec::with_capacity(ids.len());
        for &id in ids {
            if let Some(blob) = s.db.get(&txn, &ref_key(id))? {
                out.push((id, Some(blob[1..].to_vec())));
            } else if s.db.get(&txn, &legacy_key(id))?.is_some() {
                out.push((id, None));
            }
        }
        Ok(out)
    }

    /// The bytes stored under `digest` (see [`Self::digests`]), if any.
    pub fn blob(&self, digest: &[u8]) -> Result<Option<Vec<u8>>> {
        if !(digest.len() == 16 || digest.len() == 17) {
            return Ok(None);
        }
        let mut key = Vec::with_capacity(18);
        key.push(BLOB_PREFIX);
        key.extend_from_slice(digest);
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        Ok(s.db.get(&txn, &key)?.map(<[u8]>::to_vec))
    }

    /// Remove the references of `ids` (blobs stay; [`Self::delete_orphan_blobs`] sweeps them).
    pub fn delete_many(&self, ids: &[u64]) -> Result<()> {
        if ids.is_empty() {
            return Ok(());
        }
        self.write(|txn, db| {
            for &id in ids {
                db.delete(txn, &ref_key(id))?;
                db.delete(txn, &legacy_key(id))?;
            }
            Ok(())
        })
    }

    pub fn stats(&self) -> Result<Stats> {
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        let mut st = Stats::default();
        for item in s.db.iter(&txn)? {
            let (k, v) = item?;
            match k.first() {
                Some(&REF_PREFIX) => st.thumbnails += 1,
                Some(&BLOB_PREFIX) => {
                    st.blobs += 1;
                    st.payload_bytes += v.len() as u64;
                }
                _ => {
                    st.legacy += 1;
                    st.thumbnails += 1;
                    st.payload_bytes += v.len() as u64;
                }
            }
        }
        Ok(st)
    }

    /// Delete the blobs no reference points at, finding them in the same write
    /// transaction (so no write can start pointing at one meanwhile). Returns
    /// how many were deleted.
    pub fn delete_orphan_blobs(&self) -> Result<usize> {
        self.write(|txn, db| {
            let mut live = std::collections::HashSet::new();
            for item in db.prefix_iter(txn, &[REF_PREFIX])? {
                live.insert(item?.1.to_vec());
            }
            let mut orphans = Vec::new();
            for item in db.prefix_iter(txn, &[BLOB_PREFIX])? {
                let (k, _) = item?;
                if !live.contains(k) {
                    orphans.push(k.to_vec());
                }
            }
            for k in &orphans {
                db.delete(txn, k)?;
            }
            Ok(orphans.len())
        })
    }

    /// Every blob's key without its prefix (the digest, plus a slot byte after
    /// a collision): what a sync's receiver already holds.
    pub fn blob_keys(&self) -> Result<Vec<Vec<u8>>> {
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        let mut out = Vec::new();
        for item in s.db.prefix_iter(&txn, &[BLOB_PREFIX])? {
            out.push(item?.0[1..].to_vec());
        }
        Ok(out)
    }

    /// Copy into `dest` every image reference and legacy entry, and the blobs
    /// whose key ([`Self::blob_keys`]) is not in `skip`: a sync's store, whose
    /// missing blobs the receiver has. Stops when `ctl` is stopped. Returns
    /// `(blobs copied, blobs skipped)`.
    pub fn copy_partial(&self, dest: &ThumbStore, skip: &HashSet<Vec<u8>>, ctl: &Control) -> Result<(u64, u64)> {
        const CHUNK_BYTES: usize = 32 << 20;
        let s = self.shared()?;
        let txn = s.env.read_txn()?;
        let (mut copied, mut skipped) = (0, 0);
        let (mut chunk, mut bytes) = (Vec::new(), 0);
        for item in s.db.iter(&txn)? {
            let (k, v) = item?;
            if k.first() == Some(&BLOB_PREFIX) {
                if skip.contains(&k[1..]) {
                    skipped += 1;
                    continue;
                }
                copied += 1;
            }
            bytes += k.len() + v.len();
            chunk.push((k.to_vec(), v.to_vec()));
            if bytes >= CHUNK_BYTES {
                dest.put_raw(&std::mem::take(&mut chunk))?;
                bytes = 0;
                if ctl.is_stopped() {
                    return Err(heed::Error::Io(std::io::Error::other("stopped")));
                }
            }
        }
        if !chunk.is_empty() {
            dest.put_raw(&chunk)?;
        }
        Ok((copied, skipped))
    }

    /// Copy from `from` the blobs this store's references point at but it
    /// lacks (a sync's store, completed from the receiver's own). Returns
    /// `(blobs filled, blobs still missing)`.
    pub fn fill_missing_blobs(&self, from: &ThumbStore) -> Result<(u64, u64)> {
        let missing: Vec<Vec<u8>> = {
            let s = self.shared()?;
            let txn = s.env.read_txn()?;
            let mut keys = HashSet::new();
            for item in s.db.prefix_iter(&txn, &[REF_PREFIX])? {
                let (_, blob) = item?;
                if s.db.get(&txn, blob)?.is_none() {
                    keys.insert(blob.to_vec());
                }
            }
            keys.into_iter().collect()
        };
        let (mut filled, mut lost) = (0, 0);
        for keys in missing.chunks(1000) {
            let found = {
                let s = from.shared()?;
                let txn = s.env.read_txn()?;
                let mut found = Vec::with_capacity(keys.len());
                for k in keys {
                    if let Some(v) = s.db.get(&txn, k)? {
                        found.push((k.clone(), v.to_vec()));
                    }
                }
                found
            };
            filled += found.len() as u64;
            lost += (keys.len() - found.len()) as u64;
            if !found.is_empty() {
                self.put_raw(&found)?;
            }
        }
        Ok((filled, lost))
    }

    /// Write raw key/value pairs (tests and tools: e.g. a legacy-layout entry).
    pub fn put_raw(&self, items: &[(Vec<u8>, Vec<u8>)]) -> Result<()> {
        self.write(|txn, db| {
            for (k, v) in items {
                db.put(txn, k, v)?;
            }
            Ok(())
        })
    }

    /// Write a compacted copy of the environment to the file `path` (`data.mdb`).
    pub fn copy_compacted(&self, path: &Path) -> Result<()> {
        let s = self.shared()?;
        s.env.copy_to_path(path, CompactionOption::Enabled)?;
        Ok(())
    }

    /// Rewrite `data.mdb` without its free pages (LMDB reuses them but never
    /// shrinks the file): a compacted copy beside it replaces it while the
    /// environment is closed. Every other use of the store waits meanwhile.
    /// Returns the file's size before and after, in bytes.
    pub fn compact(&self) -> Result<(u64, u64)> {
        let data = self.dir.join("data.mdb");
        let tmp = self.dir.join("data.mdb.compact");
        let size = |p: &Path| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
        let before = size(&data);
        let mut exclusive = self.inner.write().unwrap_or_else(|e| e.into_inner());
        let inner = exclusive.take().ok_or_else(closed)?;
        let _ = std::fs::remove_file(&tmp);
        let copied = inner.env.copy_to_path(&tmp, CompactionOption::Enabled);
        inner.env.prepare_for_closing().wait();
        // Replaced only by a complete copy; the store reopens either way.
        let swapped = copied.and_then(|_| Ok(std::fs::rename(&tmp, &data)?));
        let _ = std::fs::remove_file(&tmp);
        *exclusive = Some(open_env(&self.dir)?);
        swapped?;
        Ok((before, size(&data)))
    }

    /// Replace every thumbnail with those of `data`, another store's
    /// `data.mdb` (a backup's, for one), moved into place while the
    /// environment is closed. Every other use of the store waits meanwhile.
    pub fn replace_data(&self, data: &Path) -> Result<()> {
        let mut exclusive = self.inner.write().unwrap_or_else(|e| e.into_inner());
        let inner = exclusive.take().ok_or_else(closed)?;
        inner.env.prepare_for_closing().wait();
        let moved = std::fs::rename(data, self.dir.join("data.mdb"));
        // The store reopens either way (on the old data when the move failed).
        *exclusive = Some(open_env(&self.dir)?);
        Ok(moved?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, ThumbStore) {
        let dir = tempfile::tempdir().unwrap();
        let s = ThumbStore::open(&dir.path().join("t.lmdb")).unwrap();
        (dir, s)
    }

    fn get(s: &ThumbStore, id: u64) -> Option<Vec<u8>> {
        s.get_many(&[id]).unwrap().pop().map(|(_, d)| d)
    }

    #[test]
    fn digest_is_xxhash_canonical() {
        // xxhash.xxh3_128_digest(b"abc").hex() in Python.
        let hex: String = xxh3_digest(b"abc").iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(hex, "06b05ab6733a618578af5f94892f3950");
    }

    #[test]
    fn partial_copies_complete_from_another_store() {
        let (dir, s) = store();
        s.put_many(&[(1, b"shared"), (2, b"only here"), (3, b"shared")]).unwrap();
        s.put_raw(&[(legacy_key(9).to_vec(), b"legacy".to_vec())]).unwrap();
        assert_eq!(s.blob_keys().unwrap().len(), 2);

        // The receiver already has "shared": only "only here" travels.
        let theirs = ThumbStore::open(&dir.path().join("theirs.lmdb")).unwrap();
        theirs.put_many(&[(50, b"shared")]).unwrap();
        let have: HashSet<Vec<u8>> = theirs.blob_keys().unwrap().into_iter().collect();
        let partial = ThumbStore::open(&dir.path().join("partial.lmdb")).unwrap();
        assert_eq!(s.copy_partial(&partial, &have, &Control::new()).unwrap(), (1, 1));
        assert_eq!(get(&partial, 2).as_deref(), Some(&b"only here"[..]));
        assert_eq!(get(&partial, 1), None, "its blob stayed behind");
        assert_eq!(get(&partial, 9).as_deref(), Some(&b"legacy"[..]), "legacy entries always travel");

        assert_eq!(partial.fill_missing_blobs(&theirs).unwrap(), (1, 0));
        assert_eq!(get(&partial, 1).as_deref(), Some(&b"shared"[..]));
        assert_eq!(get(&partial, 3).as_deref(), Some(&b"shared"[..]));
        let empty = ThumbStore::open(&dir.path().join("empty.lmdb")).unwrap();
        partial.delete_many(&[1]).unwrap();
        let lone = ThumbStore::open(&dir.path().join("lone.lmdb")).unwrap();
        s.copy_partial(&lone, &have, &Control::new()).unwrap();
        assert_eq!(lone.fill_missing_blobs(&empty).unwrap(), (0, 1), "a blob neither side has stays missing");
    }

    #[test]
    fn replaced_data_brings_its_thumbnails() {
        let (dir, s) = store();
        s.put_many(&[(1, b"mine")]).unwrap();
        let theirs = ThumbStore::open(&dir.path().join("theirs.lmdb")).unwrap();
        theirs.put_many(&[(2, b"theirs")]).unwrap();
        theirs.close();
        s.replace_data(&dir.path().join("theirs.lmdb").join("data.mdb")).unwrap();
        assert_eq!(get(&s, 1), None);
        assert_eq!(get(&s, 2).as_deref(), Some(&b"theirs"[..]));
        s.put_many(&[(3, b"after")]).unwrap();
        assert_eq!(get(&s, 3).as_deref(), Some(&b"after"[..]), "the store is writable again");
    }

    #[test]
    fn identical_payloads_share_a_blob() {
        let (_d, s) = store();
        s.put_many(&[(1, b"same"), (2, b"same"), (3, b"other")]).unwrap();
        assert_eq!(get(&s, 2).as_deref(), Some(&b"same"[..]));
        let st = s.stats().unwrap();
        assert_eq!((st.thumbnails, st.blobs, st.payload_bytes), (3, 2, 9));
    }

    #[test]
    fn digests_address_blobs() {
        let (_d, s) = store();
        s.put_many(&[(1, b"same"), (2, b"same")]).unwrap();
        s.put_raw(&[(legacy_key(3).to_vec(), b"old".to_vec())]).unwrap();
        let d = s.digests(&[1, 2, 3, 4]).unwrap();
        assert_eq!(d.len(), 3, "missing id 4 is left out");
        assert_eq!(d[0].1, d[1].1, "shared payload, one address");
        assert_eq!(d[0].1.as_deref(), Some(&xxh3_digest(b"same")[..]));
        assert_eq!(d[2], (3, None), "legacy entry has no address");
        assert_eq!(s.blob(d[0].1.as_ref().unwrap()).unwrap().as_deref(), Some(&b"same"[..]));
        assert_eq!(s.blob(&[0; 16]).unwrap(), None);
        assert_eq!(s.blob(b"short").unwrap(), None);
    }

    #[test]
    fn delete_keeps_shared_blob_until_swept() {
        let (_d, s) = store();
        s.put_many(&[(1, b"a"), (2, b"a"), (3, b"b")]).unwrap();
        s.delete_many(&[1, 3]).unwrap();
        assert_eq!(get(&s, 2).as_deref(), Some(&b"a"[..]));
        assert_eq!(get(&s, 1), None);
        assert_eq!(s.delete_orphan_blobs().unwrap(), 1);
        assert_eq!(s.stats().unwrap().blobs, 1);
    }

    #[test]
    fn legacy_entries_read_and_are_replaced() {
        let (_d, s) = store();
        s.put_raw(&[(7u64.to_be_bytes().to_vec(), b"old".to_vec()), (8u64.to_be_bytes().to_vec(), b"old".to_vec())])
            .unwrap();
        assert_eq!(get(&s, 7).as_deref(), Some(&b"old"[..]));
        assert_eq!(s.stats().unwrap().legacy, 2);
        s.put_many(&[(7, b"new".as_slice())]).unwrap(); // drops 7's legacy entry
        let st = s.stats().unwrap();
        assert_eq!((st.legacy, st.blobs, st.thumbnails), (1, 1, 2));
        assert_eq!(get(&s, 7).as_deref(), Some(&b"new"[..]));
        assert_eq!(get(&s, 8).as_deref(), Some(&b"old"[..]));
    }

    #[test]
    fn collisions_take_separate_slots() {
        let (_d, mut s) = store();
        s.digest = |_| [0; 16];
        s.put_many(&[(1, b"red"), (2, b"green"), (3, b"red")]).unwrap();
        assert_eq!(get(&s, 1).as_deref(), Some(&b"red"[..]));
        assert_eq!(get(&s, 2).as_deref(), Some(&b"green"[..]));
        assert_eq!(s.stats().unwrap().blobs, 2);
    }

    #[test]
    fn reopening_does_not_grow_the_map() {
        let dir = tempfile::tempdir().unwrap();
        let first = ThumbStore::open(dir.path()).unwrap();
        first.put_many(&[(1, b"x".as_slice())]).unwrap();
        let size = first.map_size();
        first.close();
        for _ in 0..3 {
            let s = ThumbStore::open(dir.path()).unwrap();
            assert_eq!(s.map_size(), size);
            s.close();
        }
        let file = std::fs::metadata(dir.path().join("data.mdb")).unwrap().len() as usize;
        assert!(file <= size, "{file} > {size}");
    }

    #[test]
    fn map_grows_when_full() {
        let dir = tempfile::tempdir().unwrap();
        let s = ThumbStore::open(&dir.path().join("t.lmdb")).unwrap();
        // Shrink the map so a modest write fills it.
        {
            let mut x = s.inner.write().unwrap();
            unsafe { x.as_mut().unwrap().env.resize(page_align(1024 * 1024)).unwrap() };
        }
        let big: Vec<Vec<u8>> = (0..40u8).map(|k| vec![k; 100_000]).collect();
        let items: Vec<(u64, &[u8])> = big.iter().enumerate().map(|(i, d)| (i as u64, d.as_slice())).collect();
        s.put_many(&items).unwrap();
        assert!(s.map_size() > 1024 * 1024);
        assert_eq!(get(&s, 39).unwrap(), big[39]);
    }

    #[test]
    fn compacted_copy_reopens() {
        let (d, s) = store();
        s.put_many(&[(1, b"keep"), (2, b"gone")]).unwrap();
        s.delete_many(&[2]).unwrap();
        let out = d.path().join("copy");
        std::fs::create_dir_all(&out).unwrap();
        s.copy_compacted(&out.join("data.mdb")).unwrap();
        let c = ThumbStore::open(&out).unwrap();
        assert_eq!(get(&c, 1).as_deref(), Some(&b"keep"[..]));
        c.close();
        s.close();
    }

    #[test]
    fn compacting_shrinks_the_file_and_keeps_the_rest() {
        let (_d, s) = store();
        let big: Vec<Vec<u8>> = (0..60u8).map(|k| vec![k; 200_000]).collect();
        let items: Vec<(u64, &[u8])> = big.iter().enumerate().map(|(i, d)| (i as u64, d.as_slice())).collect();
        s.put_many(&items).unwrap();
        s.put_many(&[(200, big[0].as_slice())]).unwrap(); // shares id 0's blob
        s.delete_many(&(1..60).collect::<Vec<u64>>()).unwrap();
        assert_eq!(s.delete_orphan_blobs().unwrap(), 59, "id 0's blob is still used");
        assert_eq!(s.delete_orphan_blobs().unwrap(), 0);
        let (before, after) = s.compact().unwrap();
        assert!(after < before / 4, "{before} -> {after}");
        assert_eq!(get(&s, 0).unwrap(), big[0], "kept after the swap");
        assert_eq!(get(&s, 1), None);
        s.put_many(&[(100, b"new".as_slice())]).unwrap();
        assert_eq!(get(&s, 100).as_deref(), Some(&b"new"[..]), "writable after the swap");
        assert!(!s.dir().join("data.mdb.compact").exists());
        s.close();
    }
}
