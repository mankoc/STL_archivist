//! Statistics and Find by Image over HTTP.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use common::{LOCAL, call, empty_library, send_from};
use serde_json::json;
use stl_server::accounts::Role;

fn png(seed: u8) -> Vec<u8> {
    let img = image::RgbImage::from_fn(96, 72, |x, y| image::Rgb([(x as u8).wrapping_mul(seed), (y as u8).wrapping_mul(3), seed]));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

#[tokio::test]
async fn statistics() {
    let lib = empty_library();
    let token = lib.user("vi", Role::Viewer);
    lib.state
        .db
        .with(|c| {
            c.execute_batch(
                "INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'root', '/');
                 INSERT INTO models (title, library_id, rel_path, archive_hash, is_missing, to_print, source, release_month, archive_size)
                 VALUES ('a', 1, 'a.7z', 'h1', 0, 0, 'patreon', 3, 100), ('b', 1, 'b.7z', 'h2', 0, 0, NULL, NULL, 50);",
            )
        })
        .unwrap();
    let v = call(&lib.state, "GET", "/api/stats/library", Some(&token), None).await.json();
    assert_eq!(v["scalars"]["total_models"], 2.0);
    assert_eq!(v["scalars"]["total_archive_size"], 150.0);
    assert!(v["rankings"]["by_source"].is_array());
    let v = call(&lib.state, "GET", "/api/stats/column?key=source", Some(&token), None).await.json();
    assert_eq!(v["total"], 2);
    // Ties are ordered by value, NULL first (as the desktop).
    assert_eq!(v["rows"], json!([{"label": "(unknown)", "count": 1}, {"label": "patreon", "count": 1}]));
    let v = call(&lib.state, "GET", "/api/stats/column?key=release_month", Some(&token), None).await.json();
    assert_eq!(v["rows"], json!([{"label": "March", "count": 1}]));
    assert_eq!(call(&lib.state, "GET", "/api/stats/column?key=nope", Some(&token), None).await.status, StatusCode::BAD_REQUEST);
}

fn upload(uri: &str, token: &str, data: &[u8]) -> Request<Body> {
    let boundary = "XboundaryX";
    let mut body = format!("--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"q.png\"\r\n\r\n").into_bytes();
    body.extend_from_slice(data);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    Request::post(uri)
        .header(header::AUTHORIZATION, format!("Bearer {token}"))
        .header(header::CONTENT_TYPE, format!("multipart/form-data; boundary={boundary}"))
        .body(Body::from(body))
        .unwrap()
}

#[tokio::test]
async fn find_by_image_dhash() {
    let lib = empty_library();
    let token = lib.user("vi", Role::Viewer);
    let (same, other) = (png(7), png(201));
    let h = |b: &[u8]| format!("2:{:032x}", stl_core::dhash::dhash(b).unwrap());
    let (hs, ho) = (h(&same), h(&other));
    lib.state
        .db
        .with(|c| {
            c.execute_batch(&format!(
                "INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'lib', 'F:/lib');
                 INSERT INTO models (id, title, library_id, rel_path, archive_hash, is_missing, to_print) VALUES
                   (1, 'Match', 1, 'match.zip', 'a', 0, 0), (2, 'Other', 1, 'other.zip', 'b', 0, 0);
                 INSERT INTO images (id, model_id, filename, thumbnail_path, dhash) VALUES
                   (10, 1, 'x', '10', '{hs}'), (11, 2, 'y', '11', '{ho}'), (12, 2, 'z', '12', 'ffffffffffffffff');"
            ))
        })
        .unwrap();
    lib.state.thumbs.put_many(&[(10, b"\xff\xd8a")]).unwrap();

    let r = send_from(&lib.state, LOCAL, upload("/api/search/image?max_distance=128", &token, &same)).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    let v = r.json();
    assert_eq!(v["outdated"], 1, "the old 16-character hash is not compared");
    assert_eq!(v["matches"][0]["model_id"], 1);
    assert_eq!(v["matches"][0]["score"], 0.0);
    assert_eq!(v["matches"][0]["archive"], "match.zip");
    assert!(v["matches"][0]["thumbnail"].as_str().unwrap().starts_with("/api/thumb/"));
    assert_eq!(v["matches"].as_array().unwrap().len(), 2);

    let v = send_from(&lib.state, LOCAL, upload("/api/search/image?max_distance=0", &token, &same)).await.json();
    assert_eq!(v["matches"].as_array().unwrap().len(), 1, "only the identical image");

    let r = send_from(&lib.state, LOCAL, upload("/api/search/image", &token, b"not an image")).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    let r = send_from(&lib.state, LOCAL, upload("/api/search/image?method=nope", &token, &same)).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);

    // Past axum's default 2 MB body limit: read, then refused as no image; past 64 MB: too large.
    let r = send_from(&lib.state, LOCAL, upload("/api/search/image", &token, &vec![b'x'; 3 << 20])).await;
    assert!(r.json()["error"].as_str().unwrap().starts_with("not a readable image"), "{:?}", r.json());
    let r = send_from(&lib.state, LOCAL, upload("/api/search/image", &token, &vec![b'x'; 65 << 20])).await;
    assert_eq!(r.status, StatusCode::PAYLOAD_TOO_LARGE);
}
