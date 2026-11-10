//! Editing over HTTP: cell edits, the editor, the mass editor, deletes,
//! images, lookups; roles and sidecar refreshes.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use common::{LOCAL, TestLib, call, empty_library, send_from};
use serde_json::{Value, json};
use stl_server::accounts::Role;

struct Lib {
    lib: TestLib,
    editor: String,
    viewer: String,
    /// Model ids: a.zip and b.zip in `<dir>/lib/Studio/030 - …`.
    ids: Vec<i64>,
    folder: std::path::PathBuf,
}

fn library() -> Lib {
    let lib = empty_library();
    let folder = lib.dir.path().join("lib").join("Studio");
    std::fs::create_dir_all(&folder).unwrap();
    let mut ids = Vec::new();
    for (i, name) in ["030 - Red riding hood.zip", "031 - Blue coat.zip"].iter().enumerate() {
        let path = folder.join(name);
        std::fs::write(&path, b"not really a zip").unwrap();
        let id = lib
            .state
            .db
            .with_tx(|c| {
                let root = lib.dir.path().join("lib").to_string_lossy().into_owned();
                let (library, rel, folder) = common::located(c, &root, &path.to_string_lossy());
                c.execute(
                    "INSERT INTO models (title, library_id, rel_path, rel_folder, archive_hash, is_missing, to_print) VALUES (?1, ?2, ?3, ?4, ?5, 0, 0)",
                    rusqlite::params![format!("old {i}"), library, rel, folder, format!("h{i}")],
                )?;
                Ok::<_, rusqlite::Error>(c.last_insert_rowid())
            })
            .unwrap();
        ids.push(id);
    }
    let editor = lib.user("ed", Role::Editor);
    let viewer = lib.user("vi", Role::Viewer);
    Lib { lib, editor, viewer, ids, folder }
}

impl Lib {
    async fn call(&self, method: &str, uri: &str, body: Option<Value>) -> common::Reply {
        call(&self.lib.state, method, uri, Some(&self.editor), body).await
    }

    fn scalar<T: rusqlite::types::FromSql>(&self, sql: &str) -> T {
        self.lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
    }

    fn title(&self, id: i64) -> Option<String> {
        self.scalar(&format!("SELECT title FROM models WHERE id = {id}"))
    }
}

#[tokio::test]
async fn viewers_cannot_edit() {
    let l = library();
    let s = &l.lib.state;
    let id = l.ids[0];
    for (method, uri, body) in [
        ("PATCH", "/api/models/cells".to_string(), json!({"edits": [{"id": id, "field": "title", "value": "x"}]})),
        ("PUT", format!("/api/models/{id}/editor"), json!({})),
        ("POST", "/api/models/mass-edit".into(), json!({"ids": [id]})),
        ("POST", "/api/models/delete".into(), json!({"ids": [id]})),
        ("POST", "/api/models/clear-warnings".into(), json!({"ids": [id]})),
        ("POST", "/api/lookups/tag".into(), json!({"name": "x"})),
        ("POST", "/api/authors".into(), json!({"action": "delete", "name": "x"})),
    ] {
        let r = call(s, method, &uri, Some(&l.viewer), Some(body)).await;
        assert_eq!(r.status, StatusCode::FORBIDDEN, "{method} {uri}");
    }
    assert_eq!(l.title(id).as_deref(), Some("old 0"));
    // Reading is fine.
    assert_eq!(call(s, "GET", &format!("/api/models/{id}/editor"), Some(&l.viewer), None).await.status, StatusCode::OK);
}

#[tokio::test]
async fn cell_edits_and_sidecars() {
    let l = library();
    let id = l.ids[0];
    let r = l
        .call("PATCH", "/api/models/cells", Some(json!({"edits": [
            {"id": id, "field": "title", "value": "  Dragon  "},
            {"id": id, "field": "tags", "value": "Dragon, fantasy , DRAGON"},
            {"id": id, "field": "collection", "value": "Comics"},
            {"id": id, "field": "subcollection", "value": "Marvel"},
            {"id": 999, "field": "title", "value": "nobody"},
        ]})))
        .await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.json()["models"], 1);
    assert_eq!(l.title(id).as_deref(), Some("Dragon"));
    let tags: String = l.scalar(&format!(
        "SELECT group_concat(t.name, ',') FROM model_tags mt JOIN tags t ON t.id = mt.tag_id WHERE mt.model_id = {id}"
    ));
    assert_eq!(tags, "dragon,fantasy");
    let r = l.call("PATCH", "/api/models/cells", Some(json!({"edits": [{"id": id, "field": "archive_path", "value": "/evil"}]}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);

    // The folder's sidecar is queued; exporting it writes METADATA.7z.
    let pending = l.lib.state.sidecars.pending();
    assert_eq!(pending, vec![l.folder.to_string_lossy().into_owned()]);
    l.lib.state.export_pending_sidecars();
    assert!(l.folder.join("METADATA.7z").is_file());
    assert!(l.lib.state.sidecars.pending().is_empty());
}

/// Sculptors and releases have what the other lookups have.
#[tokio::test]
async fn sculptors_and_releases_are_lookups() {
    let l = library();
    let (one, two) = (l.ids[0], l.ids[1]);
    for (kind, names) in [("sculptor", ["Ann", "Anne"]), ("release", ["May", "Mai"])] {
        let r = l
            .call("PATCH", "/api/models/cells", Some(json!({"edits": [
                {"id": one, "field": kind, "value": names[0]},
                {"id": two, "field": kind, "value": names[1]},
            ]})))
            .await;
        assert_eq!(r.status, StatusCode::OK);
        let rows = l.call("GET", &format!("/api/lookups/{kind}"), None).await.json();
        let id = |name: &str| rows.as_array().unwrap().iter().find(|r| r["name"] == name).unwrap()["id"].as_i64().unwrap();
        let (keep, remove) = (id(names[0]), id(names[1]));
        assert_eq!(l.call("PATCH", &format!("/api/lookups/{kind}/{keep}"), Some(json!({"name": "Kept"}))).await.status, StatusCode::NO_CONTENT);
        l.lib.state.export_pending_sidecars();
        let merge = l.call("POST", &format!("/api/lookups/{kind}/merge"), Some(json!({"keep": keep, "remove": remove}))).await;
        assert_eq!(merge.status, StatusCode::NO_CONTENT);
        assert_eq!(l.lib.state.sidecars.pending().len(), 1, "{kind}");
        let rows = l.call("GET", &format!("/api/lookups/{kind}"), None).await.json();
        assert_eq!(rows.as_array().unwrap().len(), 1, "{rows}");
        assert_eq!(rows[0]["models"], 2);
        assert_eq!(l.call("DELETE", &format!("/api/lookups/{kind}/{keep}"), None).await.status, StatusCode::NO_CONTENT);
        let used: i64 = l.scalar(&format!("SELECT count(*) FROM models WHERE {kind}_id IS NOT NULL"));
        assert_eq!(used, 0);
    }
}

/// A title isn't in the filter pane: the tree stays valid, and clients reload only rows.
#[tokio::test]
async fn edits_outside_the_filter_pane_keep_the_tree() {
    let l = library();
    let id = l.ids[0];
    let generation = || l.lib.state.generation.load(std::sync::atomic::Ordering::SeqCst);
    let mut events = l.lib.state.jobs.events.subscribe();
    let before = generation();
    for field in ["title", "sequence", "to_print"] {
        let value = if field == "title" { "New title" } else { "1" };
        let r = l.call("PATCH", "/api/models/cells", Some(json!({"edits": [{"id": id, "field": field, "value": value}]}))).await;
        assert_eq!(r.status, StatusCode::OK);
        let event = serde_json::to_value(events.recv().await.unwrap()).unwrap();
        assert_eq!(event["what"], "rows", "{field}");
    }
    assert_eq!(generation(), before);

    let r = l.call("PATCH", "/api/models/cells", Some(json!({"edits": [
        {"id": id, "field": "title", "value": "Newer"},
        {"id": id, "field": "tags", "value": "dragon"},
    ]}))).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(serde_json::to_value(events.recv().await.unwrap()).unwrap()["what"], "models");
    assert_eq!(generation(), before + 1);
}

/// Sidecars hold the names, so whatever changes a name queues the models' folders.
#[tokio::test]
async fn name_changes_refresh_sidecars() {
    let l = library();
    let id = l.ids[0];
    let r = l
        .call("PATCH", "/api/models/cells", Some(json!({"edits": [
            {"id": id, "field": "tags", "value": "one, two"},
            {"id": id, "field": "collection", "value": "Comics"},
            {"id": id, "field": "creator", "value": "Ann"},
        ]})))
        .await;
    assert_eq!(r.status, StatusCode::OK);
    let tag = |name: &str| -> i64 { l.scalar(&format!("SELECT id FROM tags WHERE name = '{name}'")) };
    let collection: i64 = l.scalar("SELECT id FROM collections WHERE name = 'Comics'");
    let (one, two) = (tag("one"), tag("two"));
    let steps = [
        ("PATCH", format!("/api/lookups/tag/{one}"), Some(json!({"name": "uno"}))),
        ("POST", "/api/lookups/tag/merge".to_string(), Some(json!({"keep": two, "remove": one}))),
        ("POST", "/api/tags/merge".to_string(), Some(json!({"ids": [two], "target": "final"}))),
        ("POST", "/api/authors".to_string(), Some(json!({"action": "rename", "name": "Ann", "to": "Bea"}))),
        ("POST", "/api/authors".to_string(), Some(json!({"action": "merge", "name": "Bea", "to": "Cy"}))),
        ("POST", "/api/authors".to_string(), Some(json!({"action": "delete", "name": "Cy"}))),
        ("DELETE", format!("/api/lookups/collection/{collection}"), None),
    ];
    for (method, uri, body) in steps {
        l.lib.state.export_pending_sidecars();
        let r = l.call(method, &uri, body.clone()).await;
        assert_eq!(r.status, StatusCode::NO_CONTENT, "{method} {uri} {body:?}");
        assert_eq!(l.lib.state.sidecars.pending(), vec![l.folder.to_string_lossy().into_owned()], "{method} {uri} {body:?}");
    }
    let last = tag("final");
    l.lib.state.export_pending_sidecars();
    assert_eq!(l.call("DELETE", &format!("/api/lookups/tag/{last}"), None).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.lib.state.sidecars.pending().len(), 1, "deleting a tag");
}

#[tokio::test]
async fn sidecars_follow_the_library_setting() {
    let l = library();
    std::fs::write(l.lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    l.call("PATCH", "/api/models/cells", Some(json!({"edits": [{"id": l.ids[0], "field": "title", "value": "x"}]}))).await;
    assert!(l.lib.state.sidecars.pending().is_empty());
}

#[tokio::test]
async fn editor_round_trip() {
    let l = library();
    let id = l.ids[1];
    let form = json!({
        "title": "Blue coat", "creator": "Onicron", "date": "2024-05", "sequence": 31,
        "sculptor": "Onicron", "collection": "Fairy tales", "subcollection": "Coats",
        "scales": ["32mm", "75mm"], "tags": ["Coat", "coat", "blue"], "url": "  ", "presupports": "Lychee"
    });
    assert_eq!(l.call("PUT", &format!("/api/models/{id}/editor"), Some(form)).await.status, StatusCode::NO_CONTENT);
    let v = l.call("GET", &format!("/api/models/{id}/editor"), None).await.json();
    assert_eq!(v["title"], "Blue coat");
    assert_eq!(v["date"], "2024-05");
    assert_eq!(v["sequence"], 31);
    assert_eq!(v["subcollection"], "Coats");
    assert_eq!(v["scales"], json!(["32mm", "75mm"]));
    assert_eq!(v["tags"], json!(["coat", "blue"]), "in the order they were created, as the desktop editor");
    assert_eq!(v["url"], Value::Null, "blank clears");
    assert_eq!(l.call("PUT", "/api/models/999/editor", Some(json!({}))).await.status, StatusCode::NOT_FOUND);
    assert_eq!(l.call("GET", "/api/models/999/editor", None).await.status, StatusCode::NOT_FOUND);

    let c = l.call("GET", "/api/editor/choices", None).await.json();
    assert!(c["sculptors"].as_array().unwrap().contains(&json!("Onicron")));
    let subs = l.call("GET", "/api/editor/subcollections?collection=Fairy%20tales", None).await.json();
    assert_eq!(subs, json!(["Coats"]));
}

#[tokio::test]
async fn parse_paths_previews_a_pattern() {
    let l = library();
    let r = l.call("POST", "/api/models/parse-paths", Some(json!({"ids": l.ids, "pattern": "{release} - {title}"}))).await;
    assert_eq!(r.status, StatusCode::OK);
    let v = r.json();
    assert_eq!(v[0]["fields"], json!([["release_name", "030"], ["title", "Red riding hood"]]));
    let v = l.call("POST", "/api/models/parse-paths", Some(json!({"ids": l.ids, "pattern": "ZZZ_{title}"}))).await.json();
    assert_eq!(v[1]["fields"], json!([]));
    assert_eq!(l.title(l.ids[0]).as_deref(), Some("old 0"), "nothing saved");
}

#[tokio::test]
async fn mass_edit_with_pattern_and_regex() {
    let l = library();
    let r = l
        .call("POST", "/api/models/mass-edit", Some(json!({
            "ids": l.ids, "pattern": "{release} - {title}", "overwrite": true,
            "manual": {"creator": "Onicron"}, "tags": "fairy tale",
            "find": {"find": "(\\w+) (\\w+)$", "replace": "\\2 \\1", "regex": true, "fields": ["title"]}
        })))
        .await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(r.json(), json!({"models": 2, "pattern_matches": 2}));
    assert_eq!(l.title(l.ids[0]).as_deref(), Some("Red hood riding"));
    assert_eq!(l.title(l.ids[1]).as_deref(), Some("coat Blue"));
    let creators: i64 = l.scalar("SELECT count(*) FROM models WHERE creator = 'Onicron'");
    assert_eq!(creators, 2);
    let release: String = l.scalar(&format!("SELECT r.name FROM models m JOIN releases r ON r.id = m.release_id WHERE m.id = {}", l.ids[0]));
    assert_eq!(release, "030");

    let bad = json!({"ids": l.ids, "find": {"find": "(", "replace": "", "regex": true, "fields": ["title"]}});
    let r = l.call("POST", "/api/models/mass-edit", Some(bad)).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST, "an invalid regex is reported");
    assert_eq!(l.title(l.ids[1]).as_deref(), Some("coat Blue"), "nothing saved");
    let r = l.call("POST", "/api/models/mass-edit", Some(json!({"ids": l.ids, "manual": {"archive_path": "x"}}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn mass_edit_adds_and_clears_tags() {
    let l = library();
    let tags = |id: i64| -> String {
        l.scalar(&format!(
            "SELECT coalesce(group_concat(name, ','), '') FROM (SELECT t.name FROM model_tags mt JOIN tags t ON t.id = mt.tag_id WHERE mt.model_id = {id} ORDER BY t.name)"
        ))
    };
    async fn mass(l: &Lib, body: Value) {
        let r = l.call("POST", "/api/models/mass-edit", Some(body)).await;
        assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    }
    mass(&l, json!({"ids": [l.ids[0]], "tags": "old"})).await;
    mass(&l, json!({"ids": l.ids, "tags": "new, old"})).await;
    assert_eq!((tags(l.ids[0]), tags(l.ids[1])), ("new,old".into(), "new,old".into()), "added, never twice");
    mass(&l, json!({"ids": [l.ids[0]], "clearing": ["tags"], "tags": "only"})).await;
    assert_eq!(tags(l.ids[0]), "only", "cleared first, then added");
    mass(&l, json!({"ids": l.ids, "clearing": ["tags"]})).await;
    assert_eq!((tags(l.ids[0]), tags(l.ids[1])), (String::new(), String::new()));
}

fn png() -> Vec<u8> {
    let img = image::RgbImage::from_pixel(64, 48, image::Rgb([200, 30, 30]));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

fn multipart(files: &[(&str, &[u8])]) -> (String, Vec<u8>) {
    let boundary = "XboundaryX";
    let mut body = Vec::new();
    for (name, data) in files {
        body.extend_from_slice(format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n\
             Content-Type: application/octet-stream\r\n\r\n"
        ).as_bytes());
        body.extend_from_slice(data);
        body.extend_from_slice(b"\r\n");
    }
    body.extend_from_slice(format!("--{boundary}--\r\n").as_bytes());
    (format!("multipart/form-data; boundary={boundary}"), body)
}

#[tokio::test]
async fn images_upload_and_delete() {
    let l = library();
    let id = l.ids[0];
    let (ct, body) = multipart(&[("render.png", &png()), ("notes.txt", b"not an image")]);
    let req = Request::post(format!("/api/models/{id}/images"))
        .header(header::AUTHORIZATION, format!("Bearer {}", l.editor))
        .header(header::CONTENT_TYPE, ct)
        .body(Body::from(body))
        .unwrap();
    let r = send_from(&l.lib.state, LOCAL, req).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    let v = r.json();
    assert_eq!(v["failed"], json!(["notes.txt"]));
    let added = v["added"].as_array().unwrap();
    assert_eq!(added.len(), 1);
    let image_id = added[0]["id"].as_i64().unwrap();
    let url = added[0]["thumbnail"].as_str().unwrap();
    let thumb = l.call("GET", url, None).await;
    assert_eq!(thumb.status, StatusCode::OK);
    assert!(thumb.body.starts_with(&[0xff, 0xd8]), "stored as a JPEG thumbnail");
    let tp: String = l.scalar(&format!("SELECT thumbnail_path FROM images WHERE id = {image_id}"));
    assert_eq!(tp, image_id.to_string());

    assert_eq!(l.call("DELETE", &format!("/api/images/{image_id}"), None).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.call("DELETE", &format!("/api/images/{image_id}"), None).await.status, StatusCode::NOT_FOUND);
    assert_eq!(l.call("GET", &format!("/api/images/{image_id}/thumbnail"), None).await.status, StatusCode::NOT_FOUND);
}

/// Only the file's own name is kept: a path would make the image's original a file of
/// the server (`/api/images/{id}/original` reads absolute names from disk).
#[tokio::test]
async fn uploaded_images_keep_only_their_file_name() {
    let l = library();
    let id = l.ids[0];
    let (ct, body) = multipart(&[("/home/someone/private/render.png", &png())]);
    let req = Request::post(format!("/api/models/{id}/images"))
        .header(header::AUTHORIZATION, format!("Bearer {}", l.editor))
        .header(header::CONTENT_TYPE, ct)
        .body(Body::from(body))
        .unwrap();
    let r = send_from(&l.lib.state, LOCAL, req).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    let image_id = r.json()["added"][0]["id"].as_i64().unwrap();
    let name: String = l.scalar(&format!("SELECT filename FROM images WHERE id = {image_id}"));
    assert_eq!(name, "render.png");
}

/// A view captured in the model viewer, or any image added by hand, is the model's own:
/// it is no longer "without images".
#[tokio::test]
async fn an_added_image_clears_the_no_images_warning() {
    let l = library();
    let id = l.ids[0];
    l.lib.state.db.with(|c| c.execute("UPDATE models SET warning = 'Archive without images\nNested archives' WHERE id = ?1", [id])).unwrap();
    let (ct, body) = multipart(&[("view.png", &png())]);
    let req = Request::post(format!("/api/models/{id}/images?view=true"))
        .header(header::AUTHORIZATION, format!("Bearer {}", l.editor))
        .header(header::CONTENT_TYPE, ct)
        .body(Body::from(body))
        .unwrap();
    assert_eq!(send_from(&l.lib.state, LOCAL, req).await.status, StatusCode::OK);
    let warning: Option<String> = l.scalar(&format!("SELECT warning FROM models WHERE id = {id}"));
    assert_eq!(warning.as_deref(), Some("Nested archives"), "only that warning goes");
}

#[tokio::test]
async fn view_captures_take_their_own_thumbnail_size() {
    let l = library();
    let id = l.ids[0];
    std::fs::write(l.lib.dir.path().join("settings.json"), r#"{"view_thumb_size": 16}"#).unwrap();
    let width = |query: &'static str| {
        let (ct, body) = multipart(&[("view.png", &png())]);
        let req = Request::post(format!("/api/models/{id}/images{query}"))
            .header(header::AUTHORIZATION, format!("Bearer {}", l.editor))
            .header(header::CONTENT_TYPE, ct)
            .body(Body::from(body))
            .unwrap();
        async {
            let r = send_from(&l.lib.state, LOCAL, req).await;
            assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
            let thumb = l.call("GET", r.json()["added"][0]["thumbnail"].as_str().unwrap(), None).await.body;
            let mut dec = jpeg_decoder::Decoder::new(std::io::Cursor::new(&thumb));
            dec.read_info().unwrap();
            dec.info().unwrap().width
        }
    };
    assert_eq!(width("?view=true").await, 16, "64 x 48 fitted into 16");
    assert_eq!(width("").await, 64, "other images: the thumbnail size (512)");
    let settings = l.call("GET", "/api/settings/library", None).await.json();
    assert_eq!((settings["view_thumb_size"].clone(), settings["max_thumb_size"].clone()), (json!(16), json!(512)));
}

#[tokio::test]
async fn images_upload_past_two_megabytes() {
    // axum's default body limit (2 MB) is raised for this route: a large render (the model
    // viewer's "Add this view", a photo) is read.
    let l = library();
    let id = l.ids[0];
    let big = vec![b'x'; 3 << 20];
    let (ct, body) = multipart(&[("view.png", &png()), ("big.png", &big)]);
    let req = Request::post(format!("/api/models/{id}/images"))
        .header(header::AUTHORIZATION, format!("Bearer {}", l.editor))
        .header(header::CONTENT_TYPE, ct)
        .body(Body::from(body))
        .unwrap();
    let r = send_from(&l.lib.state, LOCAL, req).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    let v = r.json();
    assert_eq!((v["added"].as_array().unwrap().len(), v["failed"].clone()), (1, json!(["big.png"])));
}

#[tokio::test]
async fn deleting_models_removes_their_thumbnails() {
    let l = library();
    let id = l.ids[0];
    l.lib.state.db.with(|c| c.execute("INSERT INTO images (id, model_id, filename, thumbnail_path) VALUES (50, ?1, 'x', '50')", [id])).unwrap();
    l.lib.state.thumbs.put_many(&[(50, b"\xff\xd8x")]).unwrap();
    let r = l.call("POST", "/api/models/delete", Some(json!({"ids": [id, 999]}))).await;
    assert_eq!(r.json()["models"], 1);
    assert_eq!(l.scalar::<i64>("SELECT count(*) FROM models"), 1);
    assert!(l.lib.state.thumbs.get_many(&[50]).unwrap().is_empty());
    assert!(l.folder.join("030 - Red riding hood.zip").is_file(), "the archive file stays");
}

#[tokio::test]
async fn clearing_warnings() {
    let l = library();
    let (a, b) = (l.ids[0], l.ids[1]);
    l.lib.state.db.with(|c| c.execute("UPDATE models SET warning = 'No images found' WHERE id = ?1", [a])).unwrap();
    let r = l.call("POST", "/api/models/clear-warnings", Some(json!({"ids": [a, b, 999]}))).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(r.json()["models"], 1, "only the model that had a warning");
    assert_eq!(l.scalar::<i64>("SELECT count(warning) FROM models"), 0);
}

#[tokio::test]
async fn similar_names_are_found_and_merged() {
    let l = library();
    l.call("PATCH", "/api/models/cells", Some(json!({"edits": [
        {"id": l.ids[0], "field": "tags", "value": "dragons, goblin warrior"},
        {"id": l.ids[1], "field": "tags", "value": "dragon, warrior goblin, dragons"},
        {"id": l.ids[0], "field": "creator", "value": "Sanix"},
        {"id": l.ids[1], "field": "creator", "value": "sanix"},
        {"id": l.ids[0], "field": "scale", "value": "32mm"},
        {"id": l.ids[1], "field": "scale", "value": "35mm"},
    ]}))).await;

    let tags = l.call("GET", "/api/lookups/tag/similar?threshold=1", None).await.json();
    assert_eq!(tags["pairs"].as_array().unwrap().len(), 1, "{tags}");
    let pair = &tags["pairs"][0];
    assert_eq!((pair["a"]["name"].as_str(), pair["b"]["name"].as_str(), pair["score"].as_str()), (Some("dragon"), Some("dragons"), Some("1")));
    assert_eq!((pair["a"]["models"].as_i64(), pair["b"]["models"].as_i64(), tags["truncated"].as_bool()), (Some(1), Some(2), Some(false)));
    let words = l.call("GET", "/api/lookups/tag/similar?method=words&threshold=100", None).await.json();
    assert_eq!(words["pairs"][0]["score"], "100%", "the same words in another order: {words}");

    let creators = l.call("GET", "/api/lookups/creator/similar", None).await.json();
    assert_eq!(creators["pairs"][0]["score"], "0", "only the letter case differs: {creators}");
    assert_eq!(creators["pairs"][0]["a"]["id"], 0);

    // Tags and scales merge as the others do: a model with both keeps one link.
    let (keep, remove) = (pair["a"]["id"].as_i64().unwrap(), pair["b"]["id"].as_i64().unwrap());
    assert_eq!(l.call("POST", "/api/lookups/tag/merge", Some(json!({"keep": keep, "remove": remove}))).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.scalar::<i64>(&format!("SELECT count(*) FROM model_tags WHERE tag_id = {keep}")), 2);
    assert_eq!(l.scalar::<i64>("SELECT count(*) FROM tags WHERE name = 'dragons'"), 0);
    assert!(l.call("GET", "/api/lookups/tag/similar?threshold=1", None).await.json()["pairs"].as_array().unwrap().is_empty());
    let scales = l.call("GET", "/api/lookups/scale/similar?threshold=1", None).await.json();
    let (a, b) = (scales["pairs"][0]["a"]["id"].as_i64().unwrap(), scales["pairs"][0]["b"]["id"].as_i64().unwrap());
    assert_eq!(l.call("POST", "/api/lookups/scale/merge", Some(json!({"keep": a, "remove": b}))).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.scalar::<i64>(&format!("SELECT count(*) FROM model_scales WHERE scale_id = {a}")), 2);

    // Subcollections are compared within their collection.
    for (kind, body) in [
        ("collection", json!({"name": "Busts"})),
        ("collection", json!({"name": "Minis"})),
        ("subcollection", json!({"name": "Heroes", "collection": "Busts"})),
        ("subcollection", json!({"name": "Heroes", "collection": "Minis"})),
    ] {
        assert_eq!(l.call("POST", &format!("/api/lookups/{kind}"), Some(body)).await.status, StatusCode::CREATED);
    }
    assert!(l.call("GET", "/api/lookups/subcollection/similar", None).await.json()["pairs"].as_array().unwrap().is_empty());
    l.call("POST", "/api/lookups/subcollection", Some(json!({"name": "Hero", "collection": "Minis"}))).await;
    assert_eq!(l.call("GET", "/api/lookups/subcollection/similar", None).await.json()["pairs"].as_array().unwrap().len(), 1);

    assert_eq!(l.call("GET", "/api/lookups/tag/similar?method=nope", None).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(l.call("GET", "/api/lookups/nope/similar", None).await.status, StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread")]
async fn lookups_and_authors() {
    let l = library();
    for kind in ["category", "collection"] {
        assert_eq!(l.call("POST", &format!("/api/lookups/{kind}"), Some(json!({"name": "Busts"}))).await.status, StatusCode::CREATED);
    }
    assert_eq!(l.call("POST", "/api/lookups/category", Some(json!({"name": "Busts"}))).await.status, StatusCode::CONFLICT);
    assert_eq!(l.call("POST", "/api/lookups/subcollection", Some(json!({"name": "DC", "collection": "Busts"}))).await.status, StatusCode::CREATED);
    assert_eq!(l.call("POST", "/api/lookups/subcollection", Some(json!({"name": "DC"}))).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(l.call("GET", "/api/lookups/nope", None).await.status, StatusCode::NOT_FOUND);

    l.call("PATCH", "/api/models/cells", Some(json!({"edits": [
        {"id": l.ids[0], "field": "category", "value": "Busts"},
        {"id": l.ids[1], "field": "category", "value": "Minis"},
        {"id": l.ids[0], "field": "creator", "value": "Sanix"},
        {"id": l.ids[1], "field": "creator", "value": "sanix"},
    ]}))).await;
    let cats = l.call("GET", "/api/lookups/category", None).await.json();
    let id_of = |name: &str| cats.as_array().unwrap().iter().find(|r| r["name"] == name).unwrap()["id"].as_i64().unwrap();
    let (busts, minis) = (id_of("Busts"), id_of("Minis"));
    assert_eq!(cats.as_array().unwrap().iter().map(|r| r["models"].as_i64().unwrap()).sum::<i64>(), 2);

    assert_eq!(l.call("PATCH", &format!("/api/lookups/category/{minis}"), Some(json!({"name": "Busts"}))).await.status, StatusCode::CONFLICT);
    assert_eq!(l.call("PATCH", &format!("/api/lookups/category/{minis}"), Some(json!({"name": "Miniatures"}))).await.status, StatusCode::NO_CONTENT);
    let info = l.call("GET", &format!("/api/lookups/category/{busts}"), None).await.json();
    assert_eq!(info, json!({"name": "Busts", "models": 1, "subcollections": 0}));
    assert_eq!(l.call("POST", "/api/lookups/category/merge", Some(json!({"keep": busts, "remove": minis}))).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.scalar::<i64>(&format!("SELECT count(*) FROM models WHERE category_id = {busts}")), 2);
    assert_eq!(l.call("DELETE", &format!("/api/lookups/category/{busts}"), None).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.call("DELETE", &format!("/api/lookups/category/{busts}"), None).await.status, StatusCode::NOT_FOUND);
    assert_eq!(l.scalar::<i64>("SELECT count(*) FROM models WHERE category_id IS NOT NULL"), 0);

    // Tags.
    l.call("POST", "/api/lookups/tag", Some(json!({"name": "orphan"}))).await;
    assert_eq!(l.call("GET", "/api/tags/unused", None).await.json()["count"], 1);
    assert_eq!(l.call("DELETE", "/api/tags/unused", None).await.json()["count"], 1);

    // Authors and profiles.
    let authors = l.call("GET", "/api/authors", None).await.json();
    assert_eq!(authors, json!([{"name": "Sanix", "models": 1}, {"name": "sanix", "models": 1}]));
    let r = l.call("PUT", "/api/authors/profile?name=Sanix", Some(json!({"full_name": " S. Anix ", "patreon_url": " "}))).await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    let p = l.call("GET", "/api/authors/profile?name=Sanix", None).await.json();
    assert_eq!((p["full_name"].clone(), p["patreon_url"].clone()), (json!("S. Anix"), Value::Null));
    assert_eq!(l.call("POST", "/api/authors", Some(json!({"action": "merge", "name": "sanix", "to": "Sanix"}))).await.status, StatusCode::NO_CONTENT);
    assert_eq!(l.call("GET", "/api/authors", None).await.json(), json!([{"name": "Sanix", "models": 2}]));
    assert_eq!(l.call("POST", "/api/authors", Some(json!({"action": "rename", "name": "Sanix"}))).await.status, StatusCode::BAD_REQUEST);
    l.call("POST", "/api/authors", Some(json!({"action": "rename", "name": "Sanix", "to": "Sanix Studio"}))).await;
    assert_eq!(l.call("GET", "/api/authors/profile?name=Sanix%20Studio", None).await.json()["full_name"], "S. Anix");
    l.call("POST", "/api/authors", Some(json!({"action": "delete", "name": "Sanix Studio"}))).await;
    assert_eq!(l.call("GET", "/api/authors", None).await.json(), json!([]));
}

#[tokio::test]
async fn the_background_worker_exports_after_the_quiet_period() {
    let l = library();
    l.call("PATCH", "/api/models/cells", Some(json!({"edits": [{"id": l.ids[0], "field": "title", "value": "x"}]}))).await;
    assert!(!l.folder.join("METADATA.7z").exists(), "not before the quiet period");
    for _ in 0..50 {
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        if l.folder.join("METADATA.7z").is_file() {
            break;
        }
    }
    assert!(l.folder.join("METADATA.7z").is_file(), "exported in the background");
    assert!(l.lib.state.sidecars.pending().is_empty());
}
