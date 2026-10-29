//! Monitored folders: whether crawling a folder would find anything, from file
//! sizes and dates alone (no archive is opened), so a server can check often
//! and crawl only when something changed. "Changed" is what a crawl treats as
//! changed: a new path, or a known one whose size or mtime differs from the
//! record's (with `size_only`, its size); "gone" is a record under the folder, not yet missing, whose file
//! no longer exists (the crawl marks it missing).

use std::collections::HashMap;
use std::path::Path;

use rusqlite::{Connection, Result};

use crate::storage::crawl;
use crate::winpath::WinPath;

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Changes {
    pub new: usize,
    pub modified: usize,
    pub gone: usize,
}

impl Changes {
    pub fn any(&self) -> bool {
        self.new + self.modified + self.gone > 0
    }
}

/// `LIKE` pattern for paths under `folder` (`/` form, `!` escapes).
fn under(folder: &str) -> String {
    let posix = WinPath::new(folder).as_posix();
    let escaped: String = posix.chars().flat_map(|c| if matches!(c, '%' | '_' | '!') { vec!['!', c] } else { vec![c] }).collect();
    format!("{}/%", escaped.trim_end_matches('/'))
}

/// What a crawl of `folder` (`recurse` and `size_only` as the crawl would) would find.
pub fn changes(conn: &Connection, folder: &str, recurse: bool, size_only: bool) -> Result<Changes> {
    let roots = crate::storage::libraries::Roots::load(conn)?;
    let inner: Vec<String> = roots.locate_root(folder).map(|id| roots.inner(id).into_iter().map(|(_, r)| r).collect()).unwrap_or_default();
    // One listing gives the archives with their sizes and times; the rest is compared in memory.
    let listing = crate::crawler::list_archives_stat(folder, recurse, &inner);
    let files: Vec<String> = listing.iter().map(|(p, _)| p.clone()).collect();
    let mut out = Changes::default();
    let known: HashMap<String, (Option<i64>, Option<i64>)> =
        crawl::existing(conn, &files)?.into_iter().map(|(p, _, size, mtime)| (p, (size, mtime))).collect();
    for (f, stat) in &listing {
        match known.get(f) {
            None => out.new += 1,
            Some(&(size, mtime)) => {
                if stat.or_else(|| crawl::stat(f)).is_some_and(|now| !crawl::unchanged(size, mtime, now, size_only)) {
                    out.modified += 1;
                }
            }
        }
    }
    // A library's own records (not those of libraries inside it), or for a
    // folder that is no library, every record below it.
    const PRESENT: &str = "SELECT path_join(l.root, m.rel_path) FROM models m JOIN libraries l ON l.id = m.library_id \
                           WHERE NOT coalesce(m.is_missing, 0)";
    let recorded: Vec<String> = match roots.locate_root(folder) {
        Some(id) => conn.prepare(&format!("{PRESENT} AND m.library_id = ?1"))?.query_map([id], |r| r.get(0))?.collect::<Result<_>>()?,
        None => conn
            .prepare(&format!("{PRESENT} AND posix_join(l.root, m.rel_path) LIKE ?1 ESCAPE '!'"))?
            .query_map([under(folder)], |r| r.get(0))?
            .collect::<Result<_>>()?,
    };
    // A recursive listing covers every record counted here; otherwise each is looked up.
    let listed: std::collections::HashSet<String> = files.iter().map(|p| crate::relpath::path_key(p)).collect();
    out.gone = if recurse {
        recorded.iter().filter(|p| !listed.contains(&crate::relpath::path_key(p))).count()
    } else {
        recorded.iter().filter(|p| !Path::new(&crate::relpath::on_disk(p)).exists()).count()
    };
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn finds_new_modified_and_gone_archives() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("lib_100%");
        std::fs::create_dir_all(root.join("sub")).unwrap();
        let path = |n: &str| crate::winpath::native(&root.join(n).to_string_lossy());
        std::fs::write(root.join("a.zip"), b"aaaa").unwrap();
        std::fs::write(root.join("sub").join("b.zip"), b"bbbb").unwrap();
        std::fs::write(root.join("notes.txt"), b"not an archive").unwrap();
        let db = dir.path().join("lib.db");
        migrate(&db, &Vectors::default()).unwrap();
        let conn = open(&db).unwrap();
        let folder = crate::winpath::native(&root.to_string_lossy());
        assert_eq!(changes(&conn, &folder, true, false).unwrap(), Changes { new: 2, modified: 0, gone: 0 });

        // Recorded as they are: nothing to do.
        // The monitored folder is a library; a path outside it gets its own.
        let insert = |p: &str, size: i64, mtime: i64| {
            let roots = crate::storage::libraries::Roots::load(&conn).unwrap();
            let (lib, rel) = roots.locate(p).unwrap_or_else(|| {
                let parent = crate::winpath::native(&WinPath::new(p).parent().as_posix());
                conn.execute("INSERT INTO libraries (uuid, name, root) VALUES (?1, ?1, ?1)", [&parent]).unwrap();
                crate::storage::libraries::Roots::load(&conn).unwrap().locate(p).unwrap()
            });
            conn.execute(
                "INSERT INTO models (title, library_id, rel_path, rel_folder, archive_hash, archive_size, archive_mtime_ns, is_missing, to_print) \
                 VALUES ('x', ?1, ?2, ?3, ?2, ?4, ?5, 0, 0)",
                rusqlite::params![lib, rel.as_str(), rel.folder(), size, mtime],
            )
            .unwrap();
        };
        conn.execute("INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'lib', ?1)", [&folder]).unwrap();
        for n in ["a.zip", "sub/b.zip"] {
            let p = path(n);
            let (s, m) = crawl::stat(&p).unwrap();
            insert(&p, s, m);
        }
        assert!(!changes(&conn, &folder, true, false).unwrap().any());
        assert_eq!(changes(&conn, &folder, false, false).unwrap(), Changes::default(), "a non-recursive check sees a.zip only");

        // A rewritten archive, a deleted one, a new one.
        std::fs::write(root.join("a.zip"), b"aaaa-longer").unwrap();
        std::fs::remove_file(root.join("sub").join("b.zip")).unwrap();
        std::fs::write(root.join("c.7z"), b"c").unwrap();
        assert_eq!(changes(&conn, &folder, true, false).unwrap(), Changes { new: 1, modified: 1, gone: 1 });

        // Records elsewhere, or already missing, are not this folder's business.
        insert(&crate::winpath::native(&dir.path().join("other").join("x.zip").to_string_lossy()), 1, 1);
        conn.execute("UPDATE models SET is_missing = 1 WHERE rel_path LIKE '%b.zip'", []).unwrap();
        assert_eq!(changes(&conn, &folder, true, false).unwrap(), Changes { new: 1, modified: 1, gone: 0 });

        // Only a time that differs: changed, unless sizes alone count.
        let a = path("a.zip");
        let (s, m) = crawl::stat(&a).unwrap();
        conn.execute("UPDATE models SET archive_size = ?1, archive_mtime_ns = ?2 WHERE rel_path = 'a.zip'", [s, m - 1]).unwrap();
        assert_eq!(changes(&conn, &folder, true, false).unwrap().modified, 1);
        assert_eq!(changes(&conn, &folder, true, true).unwrap().modified, 0);
    }
}
