//! Library settings over HTTP: defaults, admin-only changes, unknown keys kept.

mod common;

use axum::http::StatusCode;
use common::{call, empty_library};
use stl_server::accounts::Role;

#[tokio::test]
async fn settings_round_trip() {
    let lib = empty_library();
    let s = &lib.state;
    let admin = lib.user("admin", Role::Admin);
    let ed = lib.user("ed", Role::Editor);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"color_scheme": "Windows 11 Dark", "max_thumb_size": 256}"#).unwrap();

    let mut v = call(s, "GET", "/api/settings/library", Some(&ed), None).await.json();
    assert_eq!(v["max_thumb_size"], 256);
    assert_eq!(v["view_thumb_size"], 256, "view captures: the thumbnail size unless set");
    assert_eq!(v["max_thumbnails"], 5, "defaults filled in");
    assert_eq!(v["presupport_patterns"].as_array().unwrap().len(), 4);

    assert_eq!((v["allow_file_delete"].clone(), v["file_sorting_enabled"].clone(), v["ai_temperature"].clone()), (false.into(), false.into(), 0.2.into()));
    v["max_thumbnails"] = 8.into();
    v["warn_low_compression"] = true.into();
    v["allow_file_delete"] = true.into();
    v["trash_folder_path"] = "D:/Trash".into();
    assert_eq!(call(s, "PUT", "/api/settings/library", Some(&ed), Some(v.clone())).await.status, StatusCode::FORBIDDEN);
    let r = call(s, "PUT", "/api/settings/library", Some(&admin), Some(v.clone())).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(r.json()["max_thumbnails"], 8);
    let file: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(lib.dir.path().join("settings.json")).unwrap()).unwrap();
    assert_eq!(file["color_scheme"], "Windows 11 Dark", "the desktop's own keys are kept");
    assert_eq!(file["warn_low_compression"], true);
    assert_eq!((file["allow_file_delete"].clone(), file["trash_folder_path"].clone()), (true.into(), "D:/Trash".into()));
    let mut bad = v.clone();
    bad["ai_temperature"] = 3.0.into();
    assert_eq!(call(s, "PUT", "/api/settings/library", Some(&admin), Some(bad)).await.status, StatusCode::BAD_REQUEST);

    // Invalid values are refused, nothing is written.
    let mut bad = v.clone();
    bad["presupport_patterns"] = serde_json::json!([r"\w+_sup\.stl$"]);
    assert_eq!(call(s, "PUT", "/api/settings/library", Some(&admin), Some(bad)).await.status, StatusCode::BAD_REQUEST, "non-portable pattern");
    let mut bad = v.clone();
    bad["max_workers"] = 0.into();
    assert_eq!(call(s, "PUT", "/api/settings/library", Some(&admin), Some(bad)).await.status, StatusCode::BAD_REQUEST);
    let mut bad = v.clone();
    bad["view_thumb_size"] = 0.into();
    assert_eq!(call(s, "PUT", "/api/settings/library", Some(&admin), Some(bad)).await.status, StatusCode::BAD_REQUEST);
    let mut bad = v.clone();
    bad["surprise"] = 1.into();
    assert!(call(s, "PUT", "/api/settings/library", Some(&admin), Some(bad)).await.status.is_client_error());
    assert_eq!(call(s, "GET", "/api/settings/library", Some(&ed), None).await.json()["max_thumbnails"], 8);
}
