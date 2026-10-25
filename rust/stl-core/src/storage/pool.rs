//! A small pool of library connections, each opened by [`super::open`] (same
//! pragmas, sqlite-vec and the vec0 delete trigger). Shared by the PyO3
//! bindings and the server; connections are reused, not shared between threads.

use std::ops::{Deref, DerefMut};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use rusqlite::{Connection, Transaction, TransactionBehavior};

/// Idle connections kept for reuse; more may be open at once.
const MAX_IDLE: usize = 8;

pub struct Pool {
    path: PathBuf,
    idle: Mutex<Vec<Connection>>,
}

/// A connection borrowed from a [`Pool`]; returned to it when dropped.
pub struct Pooled<'a> {
    pool: &'a Pool,
    conn: Option<Connection>,
}

impl Pool {
    pub fn new(path: &Path) -> Self {
        Pool { path: path.to_path_buf(), idle: Mutex::new(Vec::new()) }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// An idle connection, or a new one.
    pub fn get(&self) -> super::Result<Pooled<'_>> {
        let conn = self.idle.lock().unwrap_or_else(|e| e.into_inner()).pop();
        let conn = match conn {
            Some(c) => c,
            None => super::open(&self.path)?,
        };
        Ok(Pooled { pool: self, conn: Some(conn) })
    }

    /// Run `f` on a pooled connection.
    pub fn with<T, E: From<rusqlite::Error>>(&self, f: impl FnOnce(&Connection) -> Result<T, E>) -> Result<T, E> {
        let conn = self.get()?;
        f(&conn)
    }

    /// Run `f` in one `BEGIN IMMEDIATE` transaction; commits on `Ok`, rolls back on `Err`.
    pub fn with_tx<T, E: From<rusqlite::Error>>(&self, f: impl FnOnce(&Transaction) -> Result<T, E>) -> Result<T, E> {
        let mut conn = self.get()?;
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let v = f(&tx)?;
        tx.commit()?;
        Ok(v)
    }

    /// Close the idle connections; borrowed ones come back to the pool as usual.
    pub fn close(&self) {
        self.idle.lock().unwrap_or_else(|e| e.into_inner()).clear();
    }

    fn give_back(&self, conn: Connection) {
        let mut idle = self.idle.lock().unwrap_or_else(|e| e.into_inner());
        if idle.len() < MAX_IDLE {
            idle.push(conn);
        }
    }
}

impl Deref for Pooled<'_> {
    type Target = Connection;
    fn deref(&self) -> &Connection {
        self.conn.as_ref().expect("connection present until drop")
    }
}

impl DerefMut for Pooled<'_> {
    fn deref_mut(&mut self) -> &mut Connection {
        self.conn.as_mut().expect("connection present until drop")
    }
}

impl Drop for Pooled<'_> {
    fn drop(&mut self) {
        if let Some(conn) = self.conn.take() {
            self.pool.give_back(conn);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reuses_connections_and_rolls_back_on_error() {
        let dir = tempfile::tempdir().unwrap();
        let pool = Pool::new(&dir.path().join("p.db"));
        pool.with(|c| c.execute_batch("CREATE TABLE t (x INTEGER)")).unwrap();
        let r: Result<(), rusqlite::Error> = pool.with_tx(|tx| {
            tx.execute("INSERT INTO t VALUES (1)", [])?;
            Err(rusqlite::Error::InvalidQuery)
        });
        assert!(r.is_err());
        pool.with_tx(|tx| tx.execute("INSERT INTO t VALUES (2)", []).map(|_| ())).unwrap();
        let n: i64 = pool.with(|c| c.query_row("SELECT sum(x) FROM t", [], |r| r.get(0))).unwrap();
        assert_eq!(n, 2);
        assert_eq!(pool.idle.lock().unwrap().len(), 1, "one connection, reused");
    }
}
