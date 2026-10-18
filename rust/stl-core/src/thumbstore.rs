//! Thumbnail store: the content-addressed LMDB environment `thumbnails.lmdb`.
//!
//! Same on-disk format as the py-lmdb implementation in `app/utils/thumbnailer.py`
//! (compatibility rule 6), so a library opens with either:
//!
//! ```text
//! R<image_id: u64 BE>        -> blob key of that image's thumbnail
//! D<xxh3-128 BE>[<slot: u8>] -> the JPEG/GIF bytes (slot only after a hash collision)
//! <image_id: u64 BE>         -> legacy, pre-dedup entry: the bytes themselves
//! ```
//!
//! The prefixes are ASCII letters while a legacy key starts with 0x00 for every
//! id below 2^56, so the layouts never clash. Rule 3: a process opens the store
//! with this module **or** py-lmdb, never both.
//!
//! LMDB's map has a fixed size; a write that fills it grows the map and retries.
//! Growing requires that no transaction is open in the process, so every
//! transaction holds a shared lock and growing takes it exclusively.

use std::path::{Path, PathBuf};
use std::sync::RwLock;

use heed::types::Bytes;
use heed::{CompactionOption, Database, Env, EnvOpenOptions, MdbError, RwTxn};

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

pub struct ThumbStore {
    env: Env,
    db: Database<Bytes, Bytes>,
    dir: PathBuf,
    /// Shared by every transaction, exclusive while the map is resized.
    resize: RwLock<()>,
    /// Content digest of a payload; replaceable in tests to force collisions.
    digest: fn(&[u8]) -> [u8; 16],
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

fn is_legacy(key: &[u8]) -> bool {
    !matches!(key.first(), Some(&REF_PREFIX) | Some(&BLOB_PREFIX))
}

fn page_align(n: usize) -> usize {
    const PAGE: usize = 64 * 1024; // a multiple of every page size LMDB runs on
    n.div_ceil(PAGE) * PAGE
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
            // SAFETY: no transaction is open in this process yet.
            unsafe { env.resize(page_align(used + GROW_STEP))? };
        }
        Ok(ThumbStore { env, db, dir: dir.to_path_buf(), resize: RwLock::new(()), digest: xxh3_digest })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn map_size(&self) -> usize {
        self.env.info().map_size
    }

    /// Close the environment and wait until LMDB has released it.
    pub fn close(self) {
        let ThumbStore { env, .. } = self;
        env.prepare_for_closing().wait();
    }

    /// Run `f` in a write transaction and commit, growing the map on MDB_MAP_FULL.
    fn write<T>(&self, mut f: impl FnMut(&mut RwTxn, Database<Bytes, Bytes>) -> Result<T>) -> Result<T> {
        loop {
            let outcome = {
                let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
                let mut txn = self.env.write_txn()?;
                match f(&mut txn, self.db) {
                    Ok(v) => txn.commit().map(|_| v),
                    Err(e) => Err(e), // dropping txn aborts it
                }
            };
            match outcome {
                Err(heed::Error::Mdb(MdbError::MapFull)) => {
                    let _exclusive = self.resize.write().unwrap_or_else(|e| e.into_inner());
                    let new_size = page_align(self.map_size() + GROW_STEP);
                    // SAFETY: the exclusive lock means no transaction is open.
                    unsafe { self.env.resize(new_size)? };
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
        let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
        let txn = self.env.read_txn()?;
        let mut out = Vec::with_capacity(ids.len());
        for &id in ids {
            let data = match self.db.get(&txn, &ref_key(id))? {
                Some(blob) => self.db.get(&txn, blob)?,
                None => self.db.get(&txn, &legacy_key(id))?,
            };
            if let Some(d) = data {
                out.push((id, d.to_vec()));
            }
        }
        Ok(out)
    }

    /// Remove the references of `ids` (blobs stay; [`Self::orphan_blobs`] finds them).
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
        let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
        let txn = self.env.read_txn()?;
        let mut s = Stats::default();
        for item in self.db.iter(&txn)? {
            let (k, v) = item?;
            match k.first() {
                Some(&REF_PREFIX) => s.thumbnails += 1,
                Some(&BLOB_PREFIX) => {
                    s.blobs += 1;
                    s.payload_bytes += v.len() as u64;
                }
                _ => {
                    s.legacy += 1;
                    s.thumbnails += 1;
                    s.payload_bytes += v.len() as u64;
                }
            }
        }
        Ok(s)
    }

    /// Convert up to `limit` legacy entries to the content-addressed layout.
    /// Returns how many were converted (0 when none are left).
    pub fn convert_legacy(&self, limit: usize) -> Result<usize> {
        let batch: Vec<(u64, Vec<u8>)> = {
            let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
            let txn = self.env.read_txn()?;
            let mut batch = Vec::new();
            for item in self.db.iter(&txn)? {
                let (k, v) = item?;
                if is_legacy(k) && k.len() == 8 {
                    batch.push((u64::from_be_bytes(k.try_into().unwrap()), v.to_vec()));
                    if batch.len() >= limit {
                        break;
                    }
                }
            }
            batch
        };
        let items: Vec<(u64, &[u8])> = batch.iter().map(|(id, d)| (*id, d.as_slice())).collect();
        self.put_many(&items)?;
        Ok(batch.len())
    }

    /// Keys of blobs no reference points at.
    pub fn orphan_blobs(&self) -> Result<Vec<Vec<u8>>> {
        let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
        let txn = self.env.read_txn()?;
        let mut live = std::collections::HashSet::new();
        for item in self.db.prefix_iter(&txn, &[REF_PREFIX])? {
            live.insert(item?.1.to_vec());
        }
        let mut orphans = Vec::new();
        for item in self.db.prefix_iter(&txn, &[BLOB_PREFIX])? {
            let (k, _) = item?;
            if !live.contains(k) {
                orphans.push(k.to_vec());
            }
        }
        Ok(orphans)
    }

    /// Delete raw keys (orphan blobs) in one transaction.
    pub fn delete_keys(&self, keys: &[Vec<u8>]) -> Result<()> {
        if keys.is_empty() {
            return Ok(());
        }
        self.write(|txn, db| {
            for k in keys {
                db.delete(txn, k)?;
            }
            Ok(())
        })
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
        let _shared = self.resize.read().unwrap_or_else(|e| e.into_inner());
        self.env.copy_to_path(path, CompactionOption::Enabled)?;
        Ok(())
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
    fn identical_payloads_share_a_blob() {
        let (_d, s) = store();
        s.put_many(&[(1, b"same"), (2, b"same"), (3, b"other")]).unwrap();
        assert_eq!(get(&s, 2).as_deref(), Some(&b"same"[..]));
        let st = s.stats().unwrap();
        assert_eq!((st.thumbnails, st.blobs, st.payload_bytes), (3, 2, 9));
    }

    #[test]
    fn delete_keeps_shared_blob_until_swept() {
        let (_d, s) = store();
        s.put_many(&[(1, b"a"), (2, b"a"), (3, b"b")]).unwrap();
        s.delete_many(&[1, 3]).unwrap();
        assert_eq!(get(&s, 2).as_deref(), Some(&b"a"[..]));
        assert_eq!(get(&s, 1), None);
        let orphans = s.orphan_blobs().unwrap();
        assert_eq!(orphans.len(), 1);
        s.delete_keys(&orphans).unwrap();
        assert_eq!(s.stats().unwrap().blobs, 1);
    }

    #[test]
    fn legacy_entries_read_and_convert() {
        let (_d, s) = store();
        s.put_raw(&[(7u64.to_be_bytes().to_vec(), b"old".to_vec()), (8u64.to_be_bytes().to_vec(), b"old".to_vec())])
            .unwrap();
        assert_eq!(get(&s, 7).as_deref(), Some(&b"old"[..]));
        assert_eq!(s.stats().unwrap().legacy, 2);
        assert_eq!(s.convert_legacy(1).unwrap(), 1);
        assert_eq!(s.convert_legacy(10).unwrap(), 1);
        assert_eq!(s.convert_legacy(10).unwrap(), 0);
        let st = s.stats().unwrap();
        assert_eq!((st.legacy, st.blobs, st.thumbnails), (0, 1, 2));
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
            let _x = s.resize.write().unwrap();
            unsafe { s.env.resize(page_align(1024 * 1024)).unwrap() };
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
}
