//! Moving a library between servers: importing a package, and sync.

use super::*;

// ── importing a library ──────────────────────────────────────────────────────

/// Where packages are taken apart (`<data dir>/import/<name>/`).
pub(super) const IMPORT_DIR: &str = "import";
/// Marks a package taken apart, its database brought to this version.
pub(super) const STAGED: &str = "staged";
/// The thumbnails a package from before libraries leaves behind (JSON ids).
pub(super) const DROPPED: &str = "dropped_thumbnails.json";

/// Clears [`Library::importing`] when the import ends, however it ends.
struct Importing<'a>(&'a Library);

impl Drop for Importing<'_> {
    fn drop(&mut self) {
        self.0.importing.store(false, Ordering::SeqCst);
    }
}

/// Take package `name` apart (once; another package's leftovers go) and bring
/// its database to this version. Returns its folder.
pub(super) fn stage_package(lib: &Library, job: &Job, src: &std::path::Path, name: &str) -> Result<std::path::PathBuf, String> {
    use stl_core::backup::{self, Parts};
    let staging = lib.config.data_dir.join(IMPORT_DIR);
    let dir = staging.join(name);
    if dir.join(STAGED).is_file() {
        return Ok(dir);
    }
    let _ = std::fs::remove_dir_all(&staging);
    lib.job_log(job, format!("Reading {name}…"));
    backup::extract(src, &dir, Parts::Database).map_err(|e| e.to_string())?;
    let delta = dir.join(backup::DELTA_FILE);
    if delta.is_file() && !dir.join(backup::DB_FILE).is_file() {
        // A smart sync's package: this library with the changes it carries.
        let built = (|| {
            let base = dir.join(backup::DB_FILE);
            lib.db.with(|c| c.backup(rusqlite::MAIN_DB, &base, None)).map_err(db_err)?;
            storage::delta::apply_delta(&base, &delta).map_err(|e| e.to_string())
        })();
        let _ = std::fs::remove_file(&delta);
        let built = built.inspect_err(|_| {
            let _ = std::fs::remove_dir_all(&staging);
        })?;
        lib.job_log(job, format!("Applied the changes: {} row(s) in {} block(s).", built.rows, built.blocks));
    }
    let report = storage::migrate(&dir.join(backup::DB_FILE), &storage::Vectors::default()).map_err(|e| format!("the package's database: {e}"))?;
    if let Some(p) = report.paths {
        lib.job_log(job, format!("The package is from before libraries: its archive paths were moved to {} librar(ies).", p.libraries));
        let ids: Vec<u64> = stl_core::storage::crawl::ref_ids(&p.thumbnail_refs);
        std::fs::write(dir.join(DROPPED), serde_json::to_string(&ids).expect("ids serialise")).map_err(|e| e.to_string())?;
    }
    std::fs::write(dir.join(STAGED), b"").map_err(|e| e.to_string())?;
    Ok(dir)
}

/// Pre-import backups kept: older ones go after each import.
pub(super) const KEEP_PRE_IMPORT: usize = 3;

pub(super) fn yes() -> bool {
    true
}

/// Check package `name`'s folders at their roots here (`roots`: folder uuid →
/// root, else the first of `rules` that matches) and, with `apply`, replace
/// this library with it (backed up first with `backup_first`).
pub(super) fn import_library(
    lib: &Library,
    job: &Job,
    name: &str,
    roots: &BTreeMap<String, String>,
    rules: &[crate::sync::RootRule],
    apply: bool,
    backup_first: bool,
) -> Result<String, String> {
    use stl_core::backup;
    use storage::libraries::{self, Folder};
    let src = crate::api::backups::package(&lib.config.data_dir, name).map_err(|e| e.message)?;
    let dir = stage_package(lib, job, &src, name)?;
    let db = dir.join(backup::DB_FILE);
    let mut conn = storage::open(&db).map_err(db_err)?;
    let folders = libraries::list(&conn).map_err(db_err)?;
    let groups = libraries::groups(&conn).map_err(db_err)?;
    if let Some(uuid) = roots.keys().find(|u| !folders.iter().any(|f| &f.uuid == *u)) {
        return Err(format!("the package has no folder {uuid}"));
    }
    // The outermost folders: the folders inside them move along.
    let inside = |outer: &Folder, f: &Folder| f.id != outer.id && libraries::foreign_rel(&outer.root, &f.root).is_some();
    let outer: Vec<&Folder> = folders.iter().filter(|f| !folders.iter().any(|o| inside(o, f))).collect();
    // A folder neither given nor matched by a rule keeps its root when that is a folder here.
    let map: Vec<(i64, String)> = outer
        .iter()
        .filter_map(|f| {
            let given = roots.get(&f.uuid).map(|r| r.trim()).filter(|r| !r.is_empty()).map(str::to_string);
            given
                .or_else(|| crate::sync::apply_rules(rules, &f.root))
                .or_else(|| std::path::Path::new(&f.root).is_dir().then(|| f.root.clone()))
                .map(|r| (f.id, r))
        })
        .collect();
    let plan = libraries::import_plan(&conn, &map)?;

    let models: i64 = folders.iter().map(|f| f.models).sum();
    job.ctl.set_total(models.max(0) as u64);
    let mut rows = Vec::new();
    for f in &outer {
        let new_root = plan.iter().find(|p| p.id == f.id).map_or_else(|| f.root.clone(), |p| p.new.clone());
        let mut row = crate::jobs::ImportFolder {
            uuid: f.uuid.clone(),
            name: f.name.clone(),
            library: groups.iter().find(|g| Some(g.id) == f.group_id).map(|g| g.name.clone()).unwrap_or_default(),
            root: f.root.clone(),
            root_exists: std::path::Path::new(&new_root).is_dir(),
            new_root,
            models: 0,
            inner: 0,
            found: 0,
            case_only: 0,
            missing: 0,
            missing_examples: Vec::new(),
        };
        for p in &plan {
            let Some(rel) = libraries::foreign_rel(&f.root, &p.old) else { continue };
            let check = libraries::count_found(&conn, p.id, &p.new, None)?;
            row.models += check.total as i64;
            row.inner += usize::from(p.id != f.id);
            row.found += check.found;
            row.case_only += check.case_only.len();
            row.missing += check.missing;
            let room = 20usize.saturating_sub(row.missing_examples.len());
            row.missing_examples.extend(check.missing_examples.into_iter().take(room).map(|m| if rel.is_empty() { m } else { format!("{rel}/{m}") }));
            job.ctl.advance(check.total as u64);
            if job.stopped() {
                return Err("stopped".into());
            }
        }
        lib.job_log(job, format!("{}: {} of {} archive(s) found under {}", f.name, row.found + row.case_only, row.models, row.new_root));
        rows.push(row);
    }
    let found: usize = rows.iter().map(|r| r.found + r.case_only).sum();
    let summary = format!("{} folder(s), {found} of {models} archive(s) found", rows.len());
    let mut preview = crate::jobs::ImportPreview { name: name.to_string(), folders: rows, models, applied: false };
    if !apply || job.stopped() {
        job.set_result(crate::jobs::JobResult::Import(preview));
        return Ok(format!("{summary} (checked only)"));
    }

    lib.importing.store(true, Ordering::SeqCst);
    let _importing = Importing(lib);
    let pre = backup_first.then(|| crate::api::backups::new_name().replace(".7z", "_pre_import.7z"));
    if let Some(pre) = &pre {
        lib.job_log(job, format!("Backing up this library to {pre}…"));
        let settings = lib.config.data_dir.join(backup::SETTINGS_FILE);
        let dest = crate::api::backups::dir(&lib.config.data_dir).join(pre);
        lib.db
            .with(|c| Ok::<_, rusqlite::Error>(backup::write(c, &lib.thumbs, Some(&settings), &dest, &job.ctl)))
            .map_err(db_err)?
            .map_err(|e| format!("could not back this library up ({e}); nothing was changed"))?;
    }
    lib.job_log(job, "Reading the thumbnails…".into());
    backup::extract(&src, &dir, backup::Parts::Thumbnails).map_err(|e| e.to_string())?;
    {
        // Opening it makes an empty store for a package without thumbnails.
        let staged = stl_core::thumbstore::ThumbStore::open(&dir.join(backup::THUMB_DIR)).map_err(|e| format!("the package's thumbnails: {e}"))?;
        if let Some(ids) = std::fs::read_to_string(dir.join(DROPPED)).ok().and_then(|t| serde_json::from_str::<Vec<u64>>(&t).ok()) {
            staged.delete_many(&ids).map_err(|e| format!("the package's thumbnails: {e}"))?;
        }
        // A sync's package leaves out the images this server has: take them from here.
        let filled = staged.fill_missing_blobs(&lib.thumbs);
        staged.close();
        let (filled, lost) = filled.map_err(|e| format!("the package's thumbnails: {e}"))?;
        if filled > 0 {
            lib.job_log(job, format!("{filled} thumbnail image(s) taken from this server's store."));
        }
        if lost > 0 {
            lib.job_log(job, format!("{lost} thumbnail image(s) are in neither (Generate thumbnails makes them again)."));
        }
    }
    {
        let tx = conn.transaction().map_err(db_err)?;
        libraries::import_roots(&tx, &map)?;
        tx.commit().map_err(db_err)?;
    }
    drop(conn);
    if job.stopped() {
        return Err("stopped".into());
    }

    lib.job_log(job, "Replacing the library…".into());
    let replaced = (|| {
        let mut live = lib.db.get().map_err(db_err)?;
        backup::replace_live(&mut live, &db, &job.ctl).map_err(|e| e.to_string())
    })();
    replaced.map_err(|e| format!("{e}; nothing was changed"))?;
    lib.db.close();
    let rest = (|| {
        lib.thumbs.replace_data(&dir.join(backup::THUMB_DIR).join("data.mdb")).map_err(|e| format!("thumbnail store: {e}"))?;
        import_settings(&lib.config.data_dir, &dir)
    })();
    lib.sidecars.clear();
    lib.jobs.send(ServerEvent::Resync);
    let before = pre.as_deref().map_or_else(String::new, |p| format!("; the library before the import is in {p}"));
    rest.map_err(|e| format!("{e}{before}"))?;
    let _ = std::fs::remove_dir_all(lib.config.data_dir.join(IMPORT_DIR));
    prune_pre_import(&lib.config.data_dir);
    preview.applied = true;
    job.set_result(crate::jobs::JobResult::Import(preview));
    lib.job_log(job, format!("Imported{before}."));
    Ok(format!("{summary}; imported"))
}

// ── sync ─────────────────────────────────────────────────────────────────────

/// Pull from or push to remote `remote`; the outcome is noted on the remote.
pub(super) fn sync(lib: &Library, job: &Job, remote: i64, direction: crate::sync::Direction, backup_first: Option<bool>) -> Result<String, String> {
    use crate::sync::{Direction, Remote};
    let mut stored = lib.accounts.with(|c| crate::sync::get(c, remote)).map_err(db_err)?.ok_or_else(|| format!("no remote server {remote}"))?;
    if let Some(b) = backup_first {
        stored.backup_first = b;
    }
    let client = Remote::new(&stored.name, &stored.url, Some(&stored.token)).not_this_server(&lib.instance);
    let result = (|| {
        let (version,) = client.status()?;
        crate::sync::check_versions(direction, &stored.name, &version)?;
        match direction {
            Direction::Pull => sync_pull(lib, job, &stored, &client),
            Direction::Push => sync_push(lib, job, &stored, &client),
        }
    })();
    let note = match &result {
        Ok(m) => m.clone(),
        Err(e) => format!("failed: {e}"),
    };
    let _ = lib.accounts.with(|c| crate::sync::record(c, remote, direction, &note));
    result
}

/// This library becomes a copy of the remote's.
pub(super) fn sync_pull(lib: &Library, job: &Job, r: &crate::sync::Stored, client: &crate::sync::Remote) -> Result<String, String> {
    lib.job_log(job, format!("Pulling the library of {} ({})…", r.name, r.url));
    // Only the changed rows when this library can be fingerprinted and the
    // remote can make a delta; else (or when the changes do not fit) all of it.
    let fingerprints = lib.db.with(|c| {
        let tx = c.unchecked_transaction()?;
        Ok::<_, rusqlite::Error>(storage::delta::state(&tx))
    });
    let blocks = match fingerprints.map_err(db_err)? {
        Ok(state) => client.send_blocks(&state)?,
        Err(e) => {
            lib.job_log(job, format!("Sending the whole library: {e}."));
            None
        }
    };
    match pull_once(lib, job, r, client, blocks.as_deref()) {
        Err(e) if blocks.is_some() && e.contains(crate::sync::DIVERGED) => {
            lib.job_log(job, format!("{e}: pulling the whole library instead."));
            pull_once(lib, job, r, client, None)
        }
        other => other,
    }
}

/// One pull: with `blocks` (the fingerprints' id there), only the changes.
pub(super) fn pull_once(lib: &Library, job: &Job, r: &crate::sync::Stored, client: &crate::sync::Remote, blocks: Option<&str>) -> Result<String, String> {
    use crate::api::backups;
    let keys = lib.thumbs.blob_keys().map_err(|e| format!("thumbnail store: {e}"))?;
    let have = client.send_keys(&keys)?;
    let mut request = serde_json::json!({"kind": "sync_export", "have": have});
    if let Some(b) = blocks {
        request["blocks"] = b.into();
    }
    let detail = client.run_job(&request, &|l| lib.job_log(job, l), &|| job.stopped())?;
    let message = detail["job"]["message"].as_str().unwrap_or_default();
    let name = message.split(": ").next().filter(|n| backups::valid(n)).ok_or_else(|| format!("{}: no package in {message:?}", r.name))?.to_string();
    let local = backups::new_name().replace(".7z", "_pull.7z");
    let dest = backups::dir(&lib.config.data_dir).join(&local);
    lib.job_log(job, format!("Downloading {name}…"));
    let got = client.download(&format!("/api/backups/{name}"), &dest, &job.ctl);
    let _ = client.delete(&format!("/api/backups/{name}"));
    let size = got?;
    lib.job_log(job, format!("Downloaded {:.1} MB.", size as f64 / (1024.0 * 1024.0)));
    let rules: Vec<_> = r.rules.iter().map(|p| p.toward(crate::sync::Direction::Pull)).collect();
    let imported = import_library(lib, job, &local, &BTreeMap::new(), &rules, true, r.backup_first);
    let _ = std::fs::remove_file(&dest);
    Ok(format!("pulled from {}: {}", r.name, imported?))
}

/// The remote's library becomes a copy of this one.
pub(super) fn sync_push(lib: &Library, job: &Job, r: &crate::sync::Stored, client: &crate::sync::Remote) -> Result<String, String> {
    lib.job_log(job, format!("Pushing this library to {} ({})…", r.name, r.url));
    let theirs = client.blocks()?;
    if theirs.is_none() {
        lib.job_log(job, format!("{} cannot take only the changes: sending the whole library.", r.name));
    }
    match push_once(lib, job, r, client, theirs.as_ref()) {
        Err(e) if theirs.is_some() && e.contains(crate::sync::DIVERGED) => {
            lib.job_log(job, format!("{e}: pushing the whole library instead."));
            push_once(lib, job, r, client, None)
        }
        other => other,
    }
}

/// One push: with `theirs` (the remote's fingerprints), only the changes.
pub(super) fn push_once(lib: &Library, job: &Job, r: &crate::sync::Stored, client: &crate::sync::Remote, theirs: Option<&storage::delta::State>) -> Result<String, String> {
    use crate::api::backups;
    let have = client.blob_keys()?;
    let name = backups::new_name().replace(".7z", "_push.7z");
    let src = backups::dir(&lib.config.data_dir).join(&name);
    lib.job_log(job, format!("Packing the library ({} thumbnail image(s) are there already)…", have.len()));
    let settings = lib.config.data_dir.join(stl_core::backup::SETTINGS_FILE);
    let packed = lib
        .db
        .with(|c| Ok::<_, rusqlite::Error>(stl_core::backup::write_sync(c, &lib.thumbs, Some(&settings), &src, &have, theirs, &job.ctl)))
        .map_err(db_err)?
        .map_err(|e| e.to_string());
    let p = packed?;
    lib.job_log(job, format!("{}. Uploading…", package_text(&p)));
    let uploaded = client.upload("/api/backups/upload", &src, &job.ctl);
    let _ = std::fs::remove_file(&src);
    let uploaded = uploaded?;
    let there = uploaded["name"].as_str().ok_or_else(|| format!("{}: no package name in the answer", r.name))?.to_string();
    let rules: Vec<_> = r.rules.iter().map(|p| p.toward(crate::sync::Direction::Push)).collect();
    let request = serde_json::json!({
        "kind": "import_library", "name": there, "rules": rules, "apply": true, "confirm": "REPLACE", "backup_first": r.backup_first,
    });
    let detail = client.run_job(&request, &|l| lib.job_log(job, l), &|| job.stopped());
    let _ = client.delete(&format!("/api/backups/{there}"));
    let detail = detail?;
    Ok(format!("pushed to {}: {}", r.name, detail["job"]["message"].as_str().unwrap_or("imported")))
}

/// Pack this library for a pulling server: everything but the thumbnails it has.
pub(super) fn sync_export(lib: &Library, job: &Job, have: &str, blocks: Option<&str>) -> Result<String, String> {
    use crate::sync::{Upload, sent_path};
    let path = sent_path(&lib.config.data_dir, Upload::Have, have)?;
    let keys = crate::sync::decode_keys(&std::fs::read(&path).map_err(|e| e.to_string())?)?;
    let theirs = match blocks {
        Some(id) => {
            let p = sent_path(&lib.config.data_dir, Upload::Blocks, id)?;
            let state = storage::delta::decode(&std::fs::read(&p).map_err(|e| e.to_string())?);
            let _ = std::fs::remove_file(&p);
            Some(state?)
        }
        None => None,
    };
    let name = crate::api::backups::new_name().replace(".7z", "_sync.7z");
    let dest = crate::api::backups::dir(&lib.config.data_dir).join(&name);
    lib.job_log(job, format!("Packing the library for a sync ({} thumbnail image(s) are there already)…", keys.len()));
    let settings = lib.config.data_dir.join(stl_core::backup::SETTINGS_FILE);
    let packed = lib
        .db
        .with(|c| Ok::<_, rusqlite::Error>(stl_core::backup::write_sync(c, &lib.thumbs, Some(&settings), &dest, &keys, theirs.as_ref(), &job.ctl)))
        .map_err(db_err)?
        .map_err(|e| e.to_string());
    let _ = std::fs::remove_file(&path);
    Ok(format!("{name}: {}", package_text(&packed?)))
}

/// What a sync's package holds, for the log.
pub(super) fn package_text(p: &stl_core::backup::SyncPackage) -> String {
    let database = match p.delta {
        Some(d) => format!("only the changes ({} of {} row(s))", d.rows, d.total),
        None => "the whole database".into(),
    };
    format!("{:.1} MB, {database}, {} of {} thumbnail image(s) sent", p.size as f64 / (1024.0 * 1024.0), p.sent, p.sent + p.skipped)
}

/// Keep the newest [`KEEP_PRE_IMPORT`] pre-import backups (a sync makes one each time).
pub(super) fn prune_pre_import(data_dir: &std::path::Path) {
    let folder = crate::api::backups::dir(data_dir);
    let mut names: Vec<String> = std::fs::read_dir(&folder)
        .map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| crate::api::backups::valid(n) && n.ends_with("_pre_import.7z")).collect())
        .unwrap_or_default();
    names.sort_by(|a, b| b.cmp(a));
    for old in names.iter().skip(KEEP_PRE_IMPORT) {
        let _ = std::fs::remove_file(folder.join(old));
    }
}

/// The package's library settings (when it has them), keeping this server's
/// trash folder, a path here.
pub(super) fn import_settings(data_dir: &std::path::Path, staged: &std::path::Path) -> Result<(), String> {
    use serde_json::{Map, Value};
    const TRASH: &str = "trash_folder_path";
    let file = stl_core::backup::SETTINGS_FILE;
    let Ok(text) = std::fs::read_to_string(staged.join(file)) else { return Ok(()) };
    let mut doc: Map<String, Value> = serde_json::from_str(&text).map_err(|e| format!("the package's settings: {e}"))?;
    let path = data_dir.join(file);
    let here = std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Map<String, Value>>(&t).ok());
    match here.and_then(|mut h| h.remove(TRASH)) {
        Some(trash) => doc.insert(TRASH.into(), trash),
        None => doc.remove(TRASH),
    };
    // Write beside, then replace: a crash never leaves half a file.
    let tmp = path.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(&doc).expect("json serialises");
    std::fs::write(&tmp, text).and_then(|_| std::fs::rename(&tmp, &path)).map_err(|e| e.to_string())
}
