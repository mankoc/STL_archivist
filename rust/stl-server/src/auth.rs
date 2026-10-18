//! Request authentication: the session token from `Authorization: Bearer` or
//! the `stl_session` cookie, resolved to a [`CurrentUser`]; role checks; and
//! login throttling.

use std::collections::HashMap;
use std::sync::Mutex;

use axum::extract::FromRequestParts;
use axum::http::request::Parts;
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};

use crate::accounts::{self, Role, User};
use crate::{ApiError, AppState};

pub const COOKIE: &str = "stl_session";

/// Failed logins per name before the name is locked out for a while.
const MAX_FAILURES: u32 = 5;
const LOCKOUT_SECS: i64 = 15 * 60;

/// The token a request carries, if any.
pub fn request_token(headers: &HeaderMap) -> Option<String> {
    if let Some(v) = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok())
        && let Some(t) = v.strip_prefix("Bearer ")
    {
        return Some(t.trim().to_string());
    }
    headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(';'))
        .filter_map(|kv| kv.trim().split_once('='))
        .find(|(k, _)| *k == COOKIE)
        .map(|(_, v)| v.to_string())
}

/// `Set-Cookie` for a new session (`Secure` behind an HTTPS proxy).
pub fn session_cookie(token: &str, headers: &HeaderMap) -> HeaderValue {
    let secure = if forwarded_https(headers) { "; Secure" } else { "" };
    let v = format!("{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={}{secure}", accounts::SESSION_IDLE_SECS);
    HeaderValue::from_str(&v).expect("token is hex")
}

pub fn clear_cookie() -> HeaderValue {
    HeaderValue::from_static("stl_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
}

fn forwarded_https(headers: &HeaderMap) -> bool {
    headers.get("x-forwarded-proto").and_then(|v| v.to_str().ok()).is_some_and(|v| v.eq_ignore_ascii_case("https"))
}

/// Whether a reverse proxy forwarded the request (its peer address is then
/// the proxy's, not the client's).
pub fn proxied(headers: &HeaderMap) -> bool {
    ["forwarded", "x-forwarded-for", "x-real-ip"].iter().any(|h| headers.contains_key(*h))
}

/// The signed-in user of a request; rejects with 401 when there is none.
#[derive(Debug, Clone)]
pub struct CurrentUser {
    pub user: User,
    pub token: String,
}

impl CurrentUser {
    /// 403 unless the user has at least `role`.
    pub fn require(&self, role: Role) -> Result<(), ApiError> {
        if self.user.role >= role {
            Ok(())
        } else {
            Err(ApiError::new(StatusCode::FORBIDDEN, format!("this needs the {} role", role.as_str())))
        }
    }
}

impl FromRequestParts<AppState> for CurrentUser {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, lib: &AppState) -> Result<Self, ApiError> {
        let token = request_token(&parts.headers).ok_or_else(unauthorized)?;
        let t = token.clone();
        let user = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::session_user(c, &t))?)).await?;
        user.map(|user| CurrentUser { user, token }).ok_or_else(unauthorized)
    }
}

fn unauthorized() -> ApiError {
    ApiError::new(StatusCode::UNAUTHORIZED, "sign in first")
}

/// Failed logins per (lower-cased) name: `(failures, time of the last one)`.
#[derive(Default)]
pub struct LoginThrottle {
    failures: Mutex<HashMap<String, (u32, i64)>>,
}

impl LoginThrottle {
    /// Seconds until `name` may try again, when it is locked out.
    pub fn locked_for(&self, name: &str, now: i64) -> Option<i64> {
        let map = self.failures.lock().unwrap_or_else(|e| e.into_inner());
        let &(n, last) = map.get(&name.trim().to_lowercase())?;
        let left = last + LOCKOUT_SECS - now;
        (n >= MAX_FAILURES && left > 0).then_some(left)
    }

    pub fn failed(&self, name: &str, now: i64) {
        let mut map = self.failures.lock().unwrap_or_else(|e| e.into_inner());
        map.retain(|_, &mut (_, last)| now - last < LOCKOUT_SECS);
        let e = map.entry(name.trim().to_lowercase()).or_insert((0, now));
        *e = (e.0 + 1, now);
    }

    pub fn succeeded(&self, name: &str) {
        self.failures.lock().unwrap_or_else(|e| e.into_inner()).remove(&name.trim().to_lowercase());
    }
}

impl From<accounts::AccountError> for ApiError {
    fn from(e: accounts::AccountError) -> Self {
        use accounts::AccountError as E;
        match e {
            E::Invalid(m) => ApiError::bad_request(m),
            E::Conflict(m) => ApiError::new(StatusCode::CONFLICT, m),
            E::NotFound => ApiError::not_found("no such user"),
            E::Db(m) => {
                tracing::error!("accounts: {m}");
                ApiError::internal(format!("accounts: {m}"))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_from_header_or_cookie() {
        let mut h = HeaderMap::new();
        assert_eq!(request_token(&h), None);
        h.insert(header::COOKIE, HeaderValue::from_static("a=1; stl_session=abc; b=2"));
        assert_eq!(request_token(&h).as_deref(), Some("abc"));
        h.insert(header::AUTHORIZATION, HeaderValue::from_static("Bearer xyz"));
        assert_eq!(request_token(&h).as_deref(), Some("xyz"), "the header wins");
    }

    #[test]
    fn throttle_locks_after_repeated_failures() {
        let t = LoginThrottle::default();
        for i in 0..MAX_FAILURES {
            assert_eq!(t.locked_for("Bob", 100), None, "after {i} failures");
            t.failed("bob ", 100);
        }
        assert_eq!(t.locked_for("BOB", 100), Some(LOCKOUT_SECS));
        assert_eq!(t.locked_for("bob", 100 + LOCKOUT_SECS), None, "the lockout ends");
        t.succeeded("bob");
        assert_eq!(t.locked_for("bob", 101), None);
    }

    #[test]
    fn cookies_are_secure_behind_https() {
        let mut h = HeaderMap::new();
        assert!(!session_cookie("ab", &h).to_str().unwrap().contains("Secure"));
        h.insert("x-forwarded-proto", HeaderValue::from_static("https"));
        assert!(session_cookie("ab", &h).to_str().unwrap().ends_with("; Secure"));
        assert!(!proxied(&HeaderMap::new()));
    }
}
