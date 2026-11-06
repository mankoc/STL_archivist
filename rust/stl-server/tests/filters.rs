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

#[tokio::test(flavor = "multi_thread")]
async fn creators_match_exactly_as_the_pane_lists_them() {
    let (lib, ed) = fixture();
    assert_eq!(titles(&lib, &ed, json!({"include": {"creator": ["Élan Vital"]}})).await, ["no images"]);
    assert_eq!(titles(&lib, &ed, json!({"include": {"creator": ["Alice"]}})).await, ["nested"], "ALICE has a row of its own");
}
