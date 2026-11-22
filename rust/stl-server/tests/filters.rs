//! Include and exclude filters: exact values, and what an exclusion keeps.

mod common;

use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

struct Row {
    title: &'static str,
    creator: Option<&'static str>,
    warning: Option<&'static str>,
    year: Option<i64>,
}

fn seed(lib: &TestLib, rows: &[Row]) {
    let root = lib.dir.path().join("archives").to_string_lossy().into_owned();
    lib.state
        .db
        .with_tx(|tx| {
            tx.execute("INSERT INTO library_groups (id, uuid, name) VALUES (1, 'g', 'G')", [])?;
            tx.execute("INSERT INTO libraries (id, uuid, name, root, group_id) VALUES (1, 'l', 'L', ?1, 1)", [root])?;
            for (i, r) in rows.iter().enumerate() {
                tx.execute(
                    "INSERT INTO models (title, creator, warning, release_year, library_id, rel_path, rel_folder, archive_hash, is_missing, to_print) \
                     VALUES (?1, ?2, ?3, ?4, 1, ?5, '', 'h', 0, 0)",
                    rusqlite::params![r.title, r.creator, r.warning, r.year, format!("{i}.zip")],
                )?;
            }
            Ok::<_, rusqlite::Error>(())
        })
        .unwrap();
}

/// The titles a filter leaves, sorted.
async fn titles(lib: &TestLib, token: &str, filter: Value) -> Vec<String> {
    let r = call(&lib.state, "POST", "/api/models/ids", Some(token), Some(json!({"filter": filter}))).await;
    let ids = r.json()["ids"].clone();
    let r = call(&lib.state, "POST", "/api/models/rows", Some(token), Some(json!({"ids": ids}))).await;
    let mut out: Vec<String> = r.json()["rows"].as_array().unwrap().iter().map(|r| r["title"].as_str().unwrap().to_string()).collect();
    out.sort();
    out
}

fn fixture() -> (TestLib, String) {
    let lib = empty_library();
    let token = lib.user("ed", Role::Editor);
    seed(
        &lib,
        &[
            Row { title: "clean", creator: None, warning: None, year: None },
            Row { title: "nested", creator: Some("Alice"), warning: Some("Nested archives"), year: Some(2024) },
            Row { title: "no images", creator: Some("Élan Vital"), warning: Some("Archive without images"), year: Some(2025) },
            Row { title: "shouting", creator: Some("ALICE"), warning: None, year: Some(2024) },
        ],
    );
    (lib, token)
}

#[tokio::test(flavor = "multi_thread")]
async fn an_exclusion_keeps_models_without_a_value() {
    let (lib, ed) = fixture();
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"warning": ["Nested archives"]}})).await, ["clean", "no images", "shouting"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"creator": ["Alice"]}})).await, ["clean", "no images", "shouting"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"date": [{"year": 2024}]}})).await, ["clean", "no images"]);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_filtered_tree_has_the_library_s_counts_and_its_own() {
    let (lib, ed) = fixture();
    let tree = |filter: Value| {
        let body = json!({"filter": filter, "show": ["author", "path"], "counts": true});
        let state = lib.state.clone();
        let token = ed.clone();
        async move { call(&state, "POST", "/api/filter-tree", Some(&token), Some(body)).await.json() }
    };
    let all = tree(json!({})).await;
    let filtered = tree(json!({"include": {"date": [{"year": 2024}]}})).await;
    assert_eq!(filtered["counts"], all["counts"], "over the whole library, under any filter");
    assert_eq!(filtered["path_counts"], all["path_counts"]);
    assert_eq!(filtered["scoped"], true);
    assert_eq!(filtered["scoped_counts"]["creator"], json!({"ALICE": 1, "Alice": 1}));
    assert_eq!(filtered["creators"], json!(["ALICE", "Alice"]));
}

/// Models with tags, scales, warnings and archive types (the fixture's have none
/// of the first two, and all are `.zip`).
fn linked_fixture() -> (TestLib, String) {
    let lib = empty_library();
    let token = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives").to_string_lossy().into_owned();
    lib.state
        .db
        .with_tx(|tx| {
            tx.execute_batch(
                "INSERT INTO library_groups (id, uuid, name) VALUES (1, 'g', 'G');
                 INSERT INTO tags (id, name) VALUES (1, 'dragon'), (2, 'bust');
                 INSERT INTO scales (id, name) VALUES (1, '32 mm'), (2, '75 mm');",
            )?;
            tx.execute("INSERT INTO libraries (id, uuid, name, root, group_id) VALUES (1, 'l', 'L', ?1, 1)", [root])?;
            for (id, title, rel_path, warning) in [
                (1, "dragon", "dragon.zip", Some("Nested archives\nArchive without images")),
                (2, "knight", "knight.7z", Some("Nested archives")),
                (3, "plain", "plain.rar", None),
            ] {
                tx.execute(
                    "INSERT INTO models (id, title, warning, library_id, rel_path, rel_folder, archive_hash, is_missing, to_print) \
                     VALUES (?1, ?2, ?3, 1, ?4, '', 'h', 0, 0)",
                    rusqlite::params![id, title, warning, rel_path],
                )?;
            }
            // dragon: both tags and 32 mm; knight: bust and 75 mm; plain: none.
            tx.execute_batch(
                "INSERT INTO model_tags (model_id, tag_id) VALUES (1, 1), (1, 2), (2, 2);
                 INSERT INTO model_scales (model_id, scale_id) VALUES (1, 1), (2, 2);",
            )?;
            Ok::<_, rusqlite::Error>(())
        })
        .unwrap();
    (lib, token)
}

#[tokio::test(flavor = "multi_thread")]
async fn excluding_tags_and_scales_keeps_models_without_them() {
    let (lib, ed) = linked_fixture();
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"tag": ["dragon"]}})).await, ["knight", "plain"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"tag": ["dragon", "bust"]}})).await, ["plain"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"scale": [1]}})).await, ["knight", "plain"]);
    assert_eq!(titles(&lib, &ed, json!({"include": {"scale": [1, 2]}})).await, ["dragon", "knight"]);
}

#[tokio::test(flavor = "multi_thread")]
async fn excluding_several_warnings_excludes_each() {
    let (lib, ed) = linked_fixture();
    let both = json!(["Archive without images", "Nested archives"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"warning": both.clone()}})).await, ["plain"], "knight has one of them");
    assert_eq!(titles(&lib, &ed, json!({"include": {"warning": both}})).await, ["dragon", "knight"]);
}

#[tokio::test(flavor = "multi_thread")]
async fn excluding_archive_types_excludes_each() {
    let (lib, ed) = linked_fixture();
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"filetype": [".7z", ".rar"]}})).await, ["dragon"]);
    assert_eq!(titles(&lib, &ed, json!({"exclude": {"filetype": [".zip"]}})).await, ["knight", "plain"]);
    assert_eq!(titles(&lib, &ed, json!({"include": {"filetype": [".7z", ".rar"]}})).await, ["knight", "plain"]);
}

/// The filter tree's counts scoped by `filter` (each section counts the models the
/// other sections' filters leave).
async fn scoped_counts(lib: &TestLib, token: &str, filter: Value) -> Value {
    let body = json!({"filter": filter, "show": ["tags", "scale"], "counts": true});
    let tree = call(&lib.state, "POST", "/api/filter-tree", Some(token), Some(body)).await.json();
    assert_eq!(tree["scoped"], true);
    tree["scoped_counts"].clone()
}

#[tokio::test(flavor = "multi_thread")]
async fn scoped_counts_of_scales_warnings_and_archive_types() {
    let (lib, ed) = linked_fixture();
    // Without the dragon tag: knight (75 mm, nested, .7z) and plain (nothing, .rar).
    let c = scoped_counts(&lib, &ed, json!({"exclude": {"tag": ["dragon"]}})).await;
    assert_eq!(c["scale"], json!({"2": 1}));
    assert_eq!(c["group"]["scale"], 1, "models with a scale");
    assert_eq!(c["warning"]["Nested archives"], 1);
    assert_eq!(c["warning"]["Archive without images"], 0);
    assert_eq!(c["group"]["warning"], 1, "models with a warning");
    assert_eq!(c["filetype"], json!({".7z": 1, ".rar": 1}));
    assert_eq!(c["group"]["filetype"], 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn scoped_counts_of_tags() {
    let (lib, ed) = linked_fixture();
    // At 32 mm: dragon alone, with both tags.
    let c = scoped_counts(&lib, &ed, json!({"include": {"scale": [1]}})).await;
    assert_eq!(c["tag"], json!({"bust": 1, "dragon": 1}));
    assert_eq!(c["group"]["tag"], 1, "models with a tag, not tags");
}

#[tokio::test(flavor = "multi_thread")]
async fn creators_match_exactly_as_the_pane_lists_them() {
    let (lib, ed) = fixture();
    assert_eq!(titles(&lib, &ed, json!({"include": {"creator": ["Élan Vital"]}})).await, ["no images"]);
    assert_eq!(titles(&lib, &ed, json!({"include": {"creator": ["Alice"]}})).await, ["nested"], "ALICE has a row of its own");
}
