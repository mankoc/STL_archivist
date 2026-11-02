//! Import from a web page: fetching a model's page and its images for
//! `api::web` (reading them is `stl_core::web_page`). Only http(s) addresses
//! outside this computer and its network (unless `web_allow_private`), with
//! redirects followed here so that every hop is checked, and bounded sizes and
//! times. A site that answers with a bot check or a sign-in (Cloudflare,
//! Patreon) is reported as such: the desktop app reads it in a window instead.

use std::net::{IpAddr, ToSocketAddrs};
use std::time::Duration;

use url::{Host, Url};

/// Largest page read.
pub const MAX_PAGE_BYTES: u64 = 16 << 20;
/// Largest image downloaded (as an uploaded one).
pub const MAX_IMAGE_BYTES: u64 = crate::api::edit::MAX_IMAGE_BYTES as u64;
const MAX_REDIRECTS: usize = 5;

/// Why a page or image could not be fetched.
#[derive(Debug)]
pub enum FetchError {
    /// Not an address this server fetches (the client's mistake).
    Refused(String),
    /// The site could not be reached, or answered with an error.
    Failed(String),
}

impl std::fmt::Display for FetchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FetchError::Refused(m) | FetchError::Failed(m) => f.write_str(m),
        }
    }
}

/// A fetched page, or why only a browser can read it.
pub enum Fetched {
    /// Its address after redirects, and its HTML.
    Page { url: String, html: String },
    Blocked(String),
}

/// An http(s) address the server may be asked to fetch (no credentials in it).
pub fn parse_url(s: &str) -> Result<Url, FetchError> {
    let u = Url::parse(s.trim()).map_err(|_| FetchError::Refused(format!("{s:?} is not a web address")))?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() || !u.username().is_empty() || u.password().is_some() {
        return Err(FetchError::Refused(format!("{s:?} is not an http(s) address")));
    }
    Ok(u)
}

/// Whether `ip` is on the internet (not this computer, its network, or a reserved range).
fn public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(a) => {
            let [first, second, ..] = a.octets();
            !(a.is_loopback()
                || a.is_private()
                || a.is_link_local()
                || a.is_unspecified()
                || a.is_broadcast()
                || a.is_documentation()
                || a.is_multicast()
                || first == 0
                // Carrier-grade NAT, 100.64.0.0/10.
                || (first == 100 && second & 0xc0 == 64))
        }
        IpAddr::V6(a) => match a.to_ipv4_mapped() {
            Some(v4) => public(IpAddr::V4(v4)),
            // Unique local fc00::/7, link-local fe80::/10.
            None => !(a.is_loopback() || a.is_unspecified() || a.is_multicast() || a.segments()[0] & 0xfe00 == 0xfc00 || a.segments()[0] & 0xffc0 == 0xfe80),
        },
    }
}

/// The server's web client: one agent for a page or an import's images.
pub struct Client {
    agent: ureq::Agent,
    allow_private: bool,
}

type Resp = ureq::http::Response<ureq::Body>;

impl Client {
    pub fn new(allow_private: bool) -> Client {
        let agent = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .max_redirects(0)
            .max_redirects_will_error(false)
            .timeout_connect(Some(Duration::from_secs(20)))
            .timeout_global(Some(Duration::from_secs(90)))
            .user_agent(concat!("Mozilla/5.0 (compatible; StlArchivist/", env!("CARGO_PKG_VERSION"), ")"))
            .build()
            .into();
        Client { agent, allow_private }
    }

    /// Refuse a host on this computer or its network (unless allowed).
    fn check_host(&self, u: &Url) -> Result<(), FetchError> {
        if self.allow_private {
            return Ok(());
        }
        let host = u.host_str().unwrap_or_default();
        let addrs: Vec<IpAddr> = match u.host() {
            Some(Host::Ipv4(a)) => vec![a.into()],
            Some(Host::Ipv6(a)) => vec![a.into()],
            _ => (host, u.port_or_known_default().unwrap_or(443))
                .to_socket_addrs()
                .map_err(|e| FetchError::Failed(format!("{host} cannot be found: {e}")))?
                .map(|s| s.ip())
                .collect(),
        };
        if addrs.is_empty() || !addrs.into_iter().all(public) {
            return Err(FetchError::Refused(format!("{host} is on this computer or its local network")));
        }
        Ok(())
    }

    /// `GET` with redirects followed, each hop checked; the final address and response.
    fn get(&self, mut url: Url, accept: &str) -> Result<(Url, Resp), FetchError> {
        for _ in 0..=MAX_REDIRECTS {
            self.check_host(&url)?;
            let host = url.host_str().unwrap_or_default().to_string();
            let resp = self
                .agent
                .get(url.as_str())
                .header("Accept", accept)
                .header("Accept-Language", "en")
                .call()
                .map_err(|e| FetchError::Failed(format!("{host} cannot be reached: {e}")))?;
            let location = resp.headers().get("location").and_then(|v| v.to_str().ok());
            match location.filter(|_| resp.status().is_redirection()) {
                Some(next) => {
                    url = url.join(next).map_err(|e| FetchError::Failed(format!("{host} redirects to {next:?}: {e}")))?;
                    if !matches!(url.scheme(), "http" | "https") {
                        return Err(FetchError::Failed(format!("{host} redirects to {url}")));
                    }
                }
                None => return Ok((url, resp)),
            }
        }
        Err(FetchError::Failed(format!("{url} redirects too many times")))
    }

    /// A page's HTML, or why only a browser can read it.
    pub fn page(&self, url: &str) -> Result<Fetched, FetchError> {
        let (url, mut resp) = self.get(parse_url(url)?, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")?;
        let host = url.host_str().unwrap_or_default().to_string();
        let status = resp.status().as_u16();
        let challenge = resp.headers().contains_key("cf-mitigated");
        let kind = resp.headers().get("content-type").and_then(|v| v.to_str().ok()).unwrap_or("").to_ascii_lowercase();
        let body = resp
            .body_mut()
            .with_config()
            .limit(MAX_PAGE_BYTES)
            .read_to_vec()
            .map_err(|e| FetchError::Failed(format!("reading {url}: {e}")))?;
        let html = String::from_utf8_lossy(&body).into_owned();
        if matches!(status, 404 | 410) {
            return Err(FetchError::Failed(format!("{url} was not found ({status})")));
        }
        if challenge || bot_check(&html) || matches!(status, 401 | 403 | 429 | 503) || (status == 202 && html.trim().is_empty()) {
            return Ok(Fetched::Blocked(format!("{host} answers only a browser (a bot check or a sign-in, {status})")));
        }
        if !(200..300).contains(&status) {
            return Err(FetchError::Failed(format!("{host} answered {status}")));
        }
        if !kind.is_empty() && !kind.contains("html") && !kind.contains("xml") {
            return Err(FetchError::Refused(format!("{url} is not a web page ({kind})")));
        }
        Ok(Fetched::Page { url: url.to_string(), html })
    }

    /// An image's bytes.
    pub fn image(&self, url: &str) -> Result<Vec<u8>, FetchError> {
        let (url, mut resp) = self.get(parse_url(url)?, "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8")?;
        let status = resp.status();
        if !status.is_success() {
            return Err(FetchError::Failed(format!("{} answered {}", url.host_str().unwrap_or_default(), status.as_u16())));
        }
        resp.body_mut().with_config().limit(MAX_IMAGE_BYTES).read_to_vec().map_err(|e| FetchError::Failed(format!("reading {url}: {e}")))
    }
}

/// A bot check's page (Cloudflare's "Just a moment…").
fn bot_check(html: &str) -> bool {
    html.len() < 512 * 1024 && (html.contains("<title>Just a moment") || html.contains("/cdn-cgi/challenge-platform/") || html.contains("cf-chl-"))
}

/// An image's name for the library: its address's file name.
pub fn image_name(url: &str) -> String {
    let name = Url::parse(url).ok().and_then(|u| u.path_segments().and_then(|mut s| s.next_back().map(str::to_string))).unwrap_or_default();
    let name: String = name.chars().take(120).collect();
    if name.is_empty() { "image".into() } else { name }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_public_http_addresses() {
        assert!(parse_url("https://cults3d.com/en/3d-model/x").is_ok());
        for bad in ["ftp://x.org/a", "file:///C:/x", "https://user:pw@x.org/", "x.org/a", "javascript:alert(1)"] {
            assert!(matches!(parse_url(bad), Err(FetchError::Refused(_))), "{bad}");
        }
        let client = Client::new(false);
        for local in ["http://127.0.0.1:8770/", "http://[::1]/", "http://192.168.1.10/", "http://10.0.0.2/", "http://169.254.169.254/latest", "http://100.64.0.1/", "http://[fd00::1]/", "http://[::ffff:127.0.0.1]/"] {
            assert!(matches!(client.check_host(&Url::parse(local).unwrap()), Err(FetchError::Refused(_))), "{local}");
        }
        assert!(client.check_host(&Url::parse("http://93.184.215.14/").unwrap()).is_ok());
        assert!(Client::new(true).check_host(&Url::parse("http://127.0.0.1/").unwrap()).is_ok());
    }

    #[test]
    fn image_names_and_bot_checks() {
        assert_eq!(image_name("https://x.org/a/ettercap-b.jpg?token=1"), "ettercap-b.jpg");
        assert_eq!(image_name("https://x.org/"), "image");
        assert!(bot_check("<html><head><title>Just a moment...</title>"));
        assert!(!bot_check("<html><head><title>Dragon</title>"));
    }
}
