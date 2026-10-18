//! Accounts over HTTP: setup, sign-in, roles, administration, settings.

mod common;

use axum::http::{StatusCode, header};
use common::{LOCAL, REMOTE, call, empty_library, request, send_from};
use serde_json::json;
use stl_server::accounts::Role;

#[tokio::test]
async fn first_run_setup_only_from_the_server_machine() {
    let lib = empty_library();
    let s = &lib.state;
    let v = call(s, "GET", "/api/auth/status", None, None).await.json();
    assert_eq!(v["setup_required"], true);
    assert_eq!(v["user"], json!(null));

    let body = json!({"name": "root", "password": "long password"});
    let r = send_from(s, REMOTE, request("POST", "/api/auth/setup", None, Some(body.clone()))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN, "remote peer");
    let mut proxied = request("POST", "/api/auth/setup", None, Some(body.clone()));
    proxied.headers_mut().insert("x-forwarded-for", "192.0.2.7".parse().unwrap());
    assert_eq!(send_from(s, LOCAL, proxied).await.status, StatusCode::FORBIDDEN, "through a proxy");

    let r = call(s, "POST", "/api/auth/setup", None, Some(body.clone())).await;
    assert_eq!(r.status, StatusCode::OK);
    let token = r.json()["token"].as_str().unwrap().to_string();
    assert_eq!(r.json()["user"]["role"], "admin");
    let cookie = r.headers[header::SET_COOKIE].to_str().unwrap();
    assert!(cookie.starts_with(&format!("stl_session={token};")) && cookie.contains("HttpOnly") && cookie.contains("SameSite=Strict"));

    assert_eq!(call(s, "POST", "/api/auth/setup", None, Some(body)).await.status, StatusCode::CONFLICT, "only once");
    let v = call(s, "GET", "/api/auth/status", Some(&token), None).await.json();
    assert_eq!((v["setup_required"].clone(), v["user"]["name"].clone()), (json!(false), json!("root")));
}

#[tokio::test]
async fn login_logout_and_cookies() {
    let lib = empty_library();
    let s = &lib.state;
    lib.user("alice", Role::Editor);
    let r = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "Alice", "password": "password123"}))).await;
    assert_eq!(r.status, StatusCode::OK);
    let token = r.json()["token"].as_str().unwrap().to_string();

    // The cookie alone authenticates (what the web client uses).
    let mut req = request("GET", "/api/system/info", None, None);
    req.headers_mut().insert(header::COOKIE, format!("theme=dark; stl_session={token}").parse().unwrap());
    assert_eq!(send_from(s, LOCAL, req).await.status, StatusCode::OK);

    let r = call(s, "POST", "/api/auth/logout", Some(&token), None).await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    assert!(r.headers[header::SET_COOKIE].to_str().unwrap().contains("Max-Age=0"));
    assert_eq!(call(s, "GET", "/api/system/info", Some(&token), None).await.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn repeated_failures_lock_the_name_out() {
    let lib = empty_library();
    let s = &lib.state;
    lib.user("bob", Role::Viewer);
    for _ in 0..5 {
        let r = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "bob", "password": "wrong-password"}))).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED);
        assert_eq!(r.json()["error"], "wrong name or password");
    }
    let r = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "BOB", "password": "password123"}))).await;
    assert_eq!(r.status, StatusCode::TOO_MANY_REQUESTS, "locked out even with the right password");
    let r = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "nobody", "password": "password123"}))).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED, "other names are unaffected");
}

#[tokio::test]
async fn only_admins_manage_users() {
    let lib = empty_library();
    let s = &lib.state;
    let admin = lib.user("admin", Role::Admin);
    let editor = lib.user("ed", Role::Editor);
    let new = json!({"name": "vic", "password": "password456", "role": "viewer"});

    assert_eq!(call(s, "GET", "/api/users", Some(&editor), None).await.status, StatusCode::FORBIDDEN);
    assert_eq!(call(s, "POST", "/api/users", Some(&editor), Some(new.clone())).await.status, StatusCode::FORBIDDEN);

    let r = call(s, "POST", "/api/users", Some(&admin), Some(new.clone())).await;
    assert_eq!(r.status, StatusCode::CREATED);
    let vic = r.json()["id"].as_i64().unwrap();
    assert_eq!(call(s, "POST", "/api/users", Some(&admin), Some(new)).await.status, StatusCode::CONFLICT);
    let bad = json!({"name": "x", "password": "short", "role": "viewer"});
    assert_eq!(call(s, "POST", "/api/users", Some(&admin), Some(bad)).await.status, StatusCode::BAD_REQUEST);
    let bad_role = json!({"name": "y", "password": "password456", "role": "owner"});
    assert!(call(s, "POST", "/api/users", Some(&admin), Some(bad_role)).await.status.is_client_error());

    let names: Vec<String> = call(s, "GET", "/api/users", Some(&admin), None).await.json().as_array().unwrap()
        .iter().map(|u| u["name"].as_str().unwrap().to_string()).collect();
    assert_eq!(names, ["admin", "ed", "vic"]);

    // Promote, disable (ends sessions), delete.
    let r = call(s, "PATCH", &format!("/api/users/{vic}"), Some(&admin), Some(json!({"role": "editor"}))).await;
    assert_eq!(r.json()["role"], "editor");
    let vic_token = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "vic", "password": "password456"})))
        .await.json()["token"].as_str().unwrap().to_string();
    call(s, "PATCH", &format!("/api/users/{vic}"), Some(&admin), Some(json!({"disabled": true}))).await;
    assert_eq!(call(s, "GET", "/api/system/info", Some(&vic_token), None).await.status, StatusCode::UNAUTHORIZED);
    assert_eq!(call(s, "DELETE", &format!("/api/users/{vic}"), Some(&admin), None).await.status, StatusCode::NO_CONTENT);
    assert_eq!(call(s, "DELETE", &format!("/api/users/{vic}"), Some(&admin), None).await.status, StatusCode::NOT_FOUND);

    // The last administrator stays.
    let admin_id = call(s, "GET", "/api/auth/status", Some(&admin), None).await.json()["user"]["id"].as_i64().unwrap();
    let r = call(s, "PATCH", &format!("/api/users/{admin_id}"), Some(&admin), Some(json!({"role": "viewer"}))).await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    assert_eq!(call(s, "DELETE", &format!("/api/users/{admin_id}"), Some(&admin), None).await.status, StatusCode::CONFLICT);
}

#[tokio::test]
async fn own_password_and_settings() {
    let lib = empty_library();
    let s = &lib.state;
    let token = lib.user("carol", Role::Viewer);

    let r = call(s, "PUT", "/api/me/settings/table.columns", Some(&token), Some(json!(["title", "tags"]))).await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    call(s, "PUT", "/api/me/settings/theme", Some(&token), Some(json!("dark"))).await;
    let v = call(s, "GET", "/api/me/settings", Some(&token), None).await.json();
    assert_eq!(v, json!({"table.columns": ["title", "tags"], "theme": "dark"}));
    call(s, "DELETE", "/api/me/settings/theme", Some(&token), None).await;
    assert_eq!(call(s, "GET", "/api/me/settings", Some(&token), None).await.json(), json!({"table.columns": ["title", "tags"]}));
    assert_eq!(call(s, "PUT", "/api/me/settings/bad%20key", Some(&token), Some(json!(1))).await.status, StatusCode::BAD_REQUEST);

    // Another user's settings are separate.
    let other = lib.user("dave", Role::Viewer);
    assert_eq!(call(s, "GET", "/api/me/settings", Some(&other), None).await.json(), json!({}));

    let wrong = json!({"current": "nope", "new": "brand new password"});
    assert_eq!(call(s, "PUT", "/api/auth/password", Some(&token), Some(wrong)).await.status, StatusCode::BAD_REQUEST);
    let ok = json!({"current": "password123", "new": "brand new password"});
    assert_eq!(call(s, "PUT", "/api/auth/password", Some(&token), Some(ok)).await.status, StatusCode::NO_CONTENT);
    assert_eq!(call(s, "GET", "/api/me/settings", Some(&token), None).await.status, StatusCode::UNAUTHORIZED, "sessions end");
    let r = call(s, "POST", "/api/auth/login", None, Some(json!({"name": "carol", "password": "brand new password"}))).await;
    assert_eq!(r.status, StatusCode::OK);
}
