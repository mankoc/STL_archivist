//! Whether GitHub has a newer release. Asked only for users who turned the check
//! on; the answer is kept for a day, so startup and daily checks cost one request.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

const LATEST_RELEASE: &str = "https://api.github.com/repos/mankoc/STL_archivist/releases/latest";
const RECHECK: Duration = Duration::from_secs(24 * 60 * 60);
const RETRY: Duration = Duration::from_secs(60 * 60);
const TIMEOUT: Duration = Duration::from_secs(10);
const INSTALLER_SUFFIX: &str = "_x64-setup.exe";

#[derive(Debug, Clone, Serialize, ToSchema, PartialEq)]
pub struct UpdateInfo {
    pub current: String,
    /// The newer version, when there is one.
    pub latest: Option<String>,
    /// The newer release's page.
    pub release_url: Option<String>,
    /// The newer release's Windows installer.
    pub installer_url: Option<String>,
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    html_url: String,
    #[serde(default)]
    assets: Vec<Asset>,
}

#[derive(Deserialize)]
struct Asset {
    name: String,
    browser_download_url: String,
}

#[derive(Default)]
pub struct UpdateChecker {
    cached: Mutex<Option<(Instant, Duration, UpdateInfo)>>,
}

impl UpdateChecker {
    /// The kept answer, or GitHub's when it is too old. Blocking: holding the lock
    /// through the request keeps concurrent callers to one request.
    pub fn check(&self, current: &str) -> UpdateInfo {
        let mut cached = self.cached.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((at, keep, info)) = cached.as_ref()
            && at.elapsed() < *keep
        {
            return info.clone();
        }
        let (keep, info) = match fetch() {
            Ok(release) => {
                let info = compare(current, release);
                tracing::info!(current, latest = ?info.latest, "checked GitHub for updates");
                (RECHECK, info)
            }
            Err(e) => {
                tracing::warn!("update check failed: {e}");
                (RETRY, compare(current, Release { tag_name: current.to_string(), html_url: String::new(), assets: Vec::new() }))
            }
        };
        *cached = Some((Instant::now(), keep, info.clone()));
        info
    }
}

fn fetch() -> Result<Release, String> {
    let agent: ureq::Agent = ureq::Agent::config_builder().timeout_global(Some(TIMEOUT)).build().into();
    let mut resp = agent
        .get(LATEST_RELEASE)
        .header("User-Agent", concat!("StlArchivist/", env!("CARGO_PKG_VERSION")))
        .header("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| e.to_string())?;
    let text = resp.body_mut().read_to_string().map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

fn compare(current: &str, release: Release) -> UpdateInfo {
    let latest = release.tag_name.trim_start_matches('v');
    if !is_newer(latest, current) {
        return UpdateInfo { current: current.to_string(), latest: None, release_url: None, installer_url: None };
    }
    let installer = release.assets.into_iter().find(|a| a.name.ends_with(INSTALLER_SUFFIX));
    UpdateInfo {
        current: current.to_string(),
        latest: Some(latest.to_string()),
        release_url: Some(release.html_url),
        installer_url: installer.map(|a| a.browser_download_url),
    }
}

fn is_newer(latest: &str, current: &str) -> bool {
    let parts = |v: &str| v.split('.').map(str::parse::<u32>).collect::<Result<Vec<_>, _>>();
    matches!((parts(latest), parts(current)), (Ok(l), Ok(c)) if l > c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_by_number() {
        assert!(is_newer("2026.46.0", "2026.45.12"));
        assert!(is_newer("2026.45.13", "2026.45.12"));
        assert!(is_newer("2027.1.0", "2026.53.9"));
        assert!(!is_newer("2026.45.12", "2026.45.12"));
        assert!(!is_newer("2026.45.9", "2026.45.12"));
        assert!(!is_newer("nightly", "2026.45.12"));
    }

    #[test]
    fn a_newer_release_names_its_installer() {
        let release = Release {
            tag_name: "v2026.46.0".into(),
            html_url: "https://example.com/r".into(),
            assets: vec![
                Asset { name: "notes.txt".into(), browser_download_url: "https://example.com/n".into() },
                Asset { name: "StlArchivist_2026.46.0_x64-setup.exe".into(), browser_download_url: "https://example.com/i".into() },
            ],
        };
        let info = compare("2026.45.12", release);
        assert_eq!(info.latest.as_deref(), Some("2026.46.0"));
        assert_eq!(info.installer_url.as_deref(), Some("https://example.com/i"));
    }

    #[test]
    fn the_same_release_is_no_update() {
        let release = Release { tag_name: "v2026.45.12".into(), html_url: "https://example.com/r".into(), assets: Vec::new() };
        assert_eq!(compare("2026.45.12", release).latest, None);
    }
}
