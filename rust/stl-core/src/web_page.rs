//! Import from a web page: what a model's page on a store or a creator's site
//! (Cults3D, MyMiniFactory, Patreon, Gambody, …) says about it. It is read
//! from the markup such pages carry for search engines and link previews:
//! schema.org JSON-LD (`Product`, `CreativeWork`, …), OpenGraph and Twitter
//! `<meta>` tags, tag links and the canonical link. No site's layout is
//! assumed beyond Patreon's title convention, so a redesign rarely breaks it.
//! Fetching is the server's (`stl-server/src/web.rs`), or the desktop app's
//! for pages that only answer a browser (it adds the images it showed large).

use std::sync::LazyLock;

use regex::Regex;
use scraper::{ElementRef, Html, Node, Selector};
use serde_json::{Map, Value};
use url::Url;

/// What a model's page says; texts are trimmed, absent ones `None`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WebPage {
    /// The page's canonical address on the same site, else the one read.
    pub url: String,
    pub title: Option<String>,
    pub creator: Option<String>,
    /// The site, spelled as the crawler spells sources (`Cults3D`, `MyMiniFactory`, …).
    pub source: Option<String>,
    pub description: Option<String>,
    /// Lowercase, without `#`, de-duplicated, in page order.
    pub tags: Vec<String>,
    /// Absolute http(s) addresses, the page's own first, de-duplicated.
    pub images: Vec<String>,
}

/// Most images kept from one page.
pub const MAX_IMAGES: usize = 60;
/// Most tags kept from one page.
pub const MAX_TAGS: usize = 40;
/// Longest tag kept (longer ones are sentences, not tags).
const MAX_TAG_CHARS: usize = 40;

/// Sites by domain (and its subdomains), named as the crawler names sources.
const SITES: &[(&str, &str)] = &[
    ("cults3d.com", "Cults3D"),
    ("myminifactory.com", "MyMiniFactory"),
    ("patreon.com", "Patreon"),
    ("thingiverse.com", "Thingiverse"),
    ("printables.com", "Printables"),
    ("cgtrader.com", "CGTrader"),
    ("gambody.com", "Gambody"),
    ("artstation.com", "ArtStation"),
    ("gumroad.com", "Gumroad"),
    ("kickstarter.com", "Kickstarter"),
    ("etsy.com", "Etsy"),
    ("thangs.com", "Thangs"),
    ("makerworld.com", "MakerWorld"),
    ("stlflix.com", "STLFlix"),
];

/// The main entity's schema.org types, most specific first.
const MAIN_TYPES: &[&str] = &[
    "Product",
    "3DModel",
    "VisualArtwork",
    "CreativeWork",
    "SocialMediaPosting",
    "DiscussionForumPosting",
    "BlogPosting",
    "Article",
    "ItemPage",
    "WebPage",
];

/// Store wording around a model's name ("… 3D print model", "STL files for 3D printing").
static TITLE_NOISE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:3d[ -]?print(?:ing|able)?\s+(?:models?|files?|miniatures?)|stl\s+files?(?:\s+for\s+3d\s+printing)?|for\s+3d\s+printing|3d\s+printable|3d\s+models?)\b")
        .expect("built-in pattern")
});
/// Separators between a name and what a site appends ("Name | Site", "Name - STL files").
static TITLE_TAIL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+[|\-–—·]\s+").expect("built-in pattern"));
/// Patreon's titles: "Post title | Creator on Patreon".
static PATREON_TITLE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(.*?)\s*\|\s*(.+?)\s+on Patreon\s*$").expect("built-in pattern"));
/// Links to a tag's page (`/tags/dragon`, `?tag=dragon`, `?filters[tag]=dragon`).
static TAG_LINK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)(?:/tags?/[^/?#]+/?$|[?&][^=&#]*tag[^=&#]*=)").expect("built-in pattern"));
static HTML_TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"</?[a-zA-Z][^>]*>").expect("built-in pattern"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\S\n]+").expect("built-in pattern"));
static BLANK_LINES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n{3,}").expect("built-in pattern"));

fn sel(s: &str) -> Selector {
    Selector::parse(s).expect("built-in selector")
}

/// Read the page at `url` from its HTML. `seen` are images a browser showed
/// large on it (the desktop app's capture), kept after the page's own.
pub fn read(url: &str, html: &str, seen: &[String]) -> WebPage {
    let doc = Html::parse_document(html);
    let base = Url::parse(url).ok();
    let meta = Meta::of(&doc);
    let ld = json_ld(&doc);
    let main = MAIN_TYPES.iter().find_map(|t| ld.iter().find(|o| types(o).any(|x| x == *t)));
    let host = base.as_ref().and_then(|u| u.host_str()).map(|h| h.trim_start_matches("www.").to_ascii_lowercase()).unwrap_or_default();
    let source = site_name(&host).map(str::to_string).or_else(|| meta.first(&["og:site_name"]).map(str::to_string));

    let page_title = doc.select(&sel("title")).next().map(|t| collapse(&t.text().collect::<String>()));
    let patreon = [meta.first(&["og:title"]).map(str::to_string), page_title.clone()]
        .into_iter()
        .flatten()
        .find_map(|t| PATREON_TITLE.captures(&t).map(|c| (c[1].to_string(), c[2].to_string())));
    let h1 = || doc.select(&sel("h1")).next().map(|h| collapse(&h.text().collect::<String>()));
    let title = patreon
        .as_ref()
        .map(|(t, _)| t.clone())
        .or_else(|| main.and_then(|m| ld_text(m.get("name"))))
        .or_else(|| meta.first(&["og:title", "twitter:title"]).map(str::to_string))
        .or_else(h1)
        .or(page_title)
        .map(|t| clean_title(&t, source.as_deref()))
        .filter(|t| !t.is_empty());

    let is_site = |name: &str| source.as_deref().is_some_and(|s| s.eq_ignore_ascii_case(name.trim()));
    let creator = patreon
        .map(|(_, c)| c)
        .or_else(|| main.and_then(|m| ["author", "creator", "brand", "manufacturer"].iter().find_map(|k| ld_text(m.get(*k)))))
        .or_else(|| meta.first(&["author", "article:author"]).filter(|a| !a.starts_with("http")).map(str::to_string))
        .map(|c| collapse(&c))
        .filter(|c| !c.is_empty() && !is_site(c));

    let description = [main.and_then(|m| ld_text(m.get("description"))), meta.first(&["og:description", "twitter:description", "description"]).map(str::to_string)]
        .into_iter()
        .flatten()
        .map(|d| clean_text(&d))
        .filter(|d| !d.is_empty())
        .max_by_key(|d| d.chars().count());

    let resolve = |href: &str| -> Option<String> {
        let u = match &base {
            Some(b) => b.join(href.trim()).ok()?,
            None => Url::parse(href.trim()).ok()?,
        };
        matches!(u.scheme(), "http" | "https").then(|| u.to_string())
    };
    let mut images: Vec<String> = Vec::new();
    let candidates = main
        .map(|m| ld_images(m.get("image")))
        .unwrap_or_default()
        .into_iter()
        .chain(meta.all(&["og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"]).map(str::to_string))
        .chain(seen.iter().cloned());
    for c in candidates {
        if let Some(u) = resolve(&c)
            && !images.contains(&u)
            && images.len() < MAX_IMAGES
        {
            images.push(u);
        }
    }

    let same_site = |u: &str| Url::parse(u).ok().and_then(|u| u.host_str().map(|h| h.trim_start_matches("www.").to_ascii_lowercase())).is_some_and(|h| h == host);
    let canonical = doc
        .select(&sel(r#"link[rel="canonical"]"#))
        .find_map(|l| l.value().attr("href").map(str::to_string))
        .or_else(|| meta.first(&["og:url"]).map(str::to_string))
        .or_else(|| main.and_then(|m| ld_text(m.get("url"))))
        .and_then(|u| resolve(&u))
        .filter(|u| same_site(u));

    WebPage {
        url: canonical.unwrap_or_else(|| url.trim().to_string()),
        title,
        creator,
        source,
        description,
        tags: tags(&doc, &meta, main),
        images,
    }
}

/// The site's name for a host (`www.` removed, lowercase): its own or a subdomain of a known one.
fn site_name(host: &str) -> Option<&'static str> {
    SITES.iter().find(|(d, _)| host == *d || host.ends_with(&format!(".{d}"))).map(|(_, n)| *n)
}

/// The page's `<meta>` values by `property` or `name` (lowercase), in page order.
struct Meta(Vec<(String, String)>);

impl Meta {
    fn of(doc: &Html) -> Meta {
        Meta(
            doc.select(&sel("meta[content]"))
                .filter_map(|m| {
                    let e = m.value();
                    let key = e.attr("property").or_else(|| e.attr("name"))?.trim().to_ascii_lowercase();
                    let content = e.attr("content")?.trim();
                    (!content.is_empty()).then(|| (key, content.to_string()))
                })
                .collect(),
        )
    }

    /// The first value of the first key that has one.
    fn first(&self, keys: &[&str]) -> Option<&str> {
        keys.iter().find_map(|k| self.0.iter().find(|(m, _)| m == k).map(|(_, v)| v.as_str()))
    }

    /// Every value of these keys, in page order.
    fn all<'a>(&'a self, keys: &'a [&str]) -> impl Iterator<Item = &'a str> + 'a {
        self.0.iter().filter(|(m, _)| keys.contains(&m.as_str())).map(|(_, v)| v.as_str())
    }
}

/// Every JSON-LD object of the page (`@graph`s and lists flattened); scripts
/// that are not JSON are skipped.
fn json_ld(doc: &Html) -> Vec<Map<String, Value>> {
    fn flatten(v: Value, out: &mut Vec<Map<String, Value>>) {
        match v {
            Value::Array(items) => items.into_iter().for_each(|i| flatten(i, out)),
            Value::Object(mut o) => {
                if let Some(graph) = o.remove("@graph") {
                    flatten(graph, out);
                }
                out.push(o);
            }
            _ => {}
        }
    }
    let mut out = Vec::new();
    for script in doc.select(&sel(r#"script[type="application/ld+json"]"#)) {
        let text: String = script.text().collect();
        let text = text.trim().trim_start_matches("<!--").trim_end_matches("-->").trim().trim_end_matches(';');
        if let Ok(v) = serde_json::from_str::<Value>(text) {
            flatten(v, &mut out);
        }
    }
    out
}

fn types(o: &Map<String, Value>) -> impl Iterator<Item = &str> {
    let t = o.get("@type");
    let one = t.and_then(Value::as_str);
    let many = t.and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str);
    one.into_iter().chain(many)
}

/// A JSON-LD value as text: a string or number, an object's `name`, a list's first.
fn ld_text(v: Option<&Value>) -> Option<String> {
    match v? {
        Value::String(s) => Some(s.trim().to_string()).filter(|s| !s.is_empty()),
        Value::Number(n) => Some(n.to_string()),
        Value::Object(o) => ld_text(o.get("name")),
        Value::Array(items) => items.iter().find_map(|i| ld_text(Some(i))),
        _ => None,
    }
}

/// A JSON-LD `image`: an address, an `ImageObject` (`url`, `contentUrl`), or a list of them.
fn ld_images(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Object(o)) => o.get("url").or_else(|| o.get("contentUrl")).and_then(Value::as_str).map(str::to_string).into_iter().collect(),
        Some(Value::Array(items)) => items.iter().flat_map(|i| ld_images(Some(i))).collect(),
        _ => Vec::new(),
    }
}

/// Tags from the first kind the page has: JSON-LD `keywords` and `article:tag`,
/// else `rel="tag"` links, else links to tag pages. Not `<meta name="keywords">`:
/// stores fill it with search phrases.
fn tags(doc: &Html, meta: &Meta, main: Option<&Map<String, Value>>) -> Vec<String> {
    let keywords: Vec<String> = match main.and_then(|m| m.get("keywords")) {
        Some(Value::String(s)) => s.split(',').map(str::to_string).collect(),
        Some(Value::Array(items)) => items.iter().filter_map(|i| ld_text(Some(i))).collect(),
        _ => Vec::new(),
    };
    let link_text = |a: ElementRef| a.text().collect::<String>();
    let found = clean_tags(keywords.into_iter().chain(meta.all(&["article:tag"]).map(str::to_string)));
    if !found.is_empty() {
        return found;
    }
    let found = clean_tags(doc.select(&sel(r#"a[rel~="tag"]"#)).map(link_text));
    if !found.is_empty() {
        return found;
    }
    clean_tags(doc.select(&sel("a[href]")).filter(|a| a.value().attr("href").is_some_and(|h| TAG_LINK.is_match(h))).map(link_text))
}

/// Tags as stored: lowercase, without `#`, no numbers or sentences, each once.
fn clean_tags(found: impl Iterator<Item = String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for t in found {
        let t = collapse(&t).trim_start_matches('#').trim().to_lowercase();
        if !t.is_empty() && t.chars().count() <= MAX_TAG_CHARS && !t.chars().all(|c| c.is_ascii_digit()) && !out.contains(&t) && out.len() < MAX_TAGS {
            out.push(t);
        }
    }
    out
}

/// A model's name without what stores add: the site's name or "STL files" after
/// a separator, and wording such as "3D print model".
fn clean_title(title: &str, site: Option<&str>) -> String {
    let mut t = collapse(title);
    let noise = |part: &str| {
        let p = part.trim().to_lowercase();
        site.is_some_and(|s| p.contains(&s.to_lowercase()))
            || SITES.iter().any(|(_, n)| p.contains(&n.to_lowercase()))
            || TITLE_NOISE.replace_all(&p, "").trim().is_empty()
    };
    while let Some((start, end)) = TITLE_TAIL.find_iter(&t).last().map(|m| (m.start(), m.end())) {
        if !noise(&t[end..]) {
            break;
        }
        t.truncate(start);
    }
    let cleaned = collapse(&TITLE_NOISE.replace_all(&t, " "));
    let cleaned = cleaned.trim_matches(|c: char| c.is_whitespace() || "-|–—·:,".contains(c)).to_string();
    if cleaned.is_empty() { collapse(title) } else { cleaned }
}

/// Text on one line, runs of white space as one space.
fn collapse(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A description as plain text: HTML read as text (paragraphs and `<br>` as line
/// breaks), Windows line breaks as `\n`, at most one empty line in a row.
fn clean_text(s: &str) -> String {
    let text = if HTML_TAG.is_match(s) {
        let mut out = String::new();
        html_text(Html::parse_fragment(s).root_element(), &mut out);
        out
    } else {
        s.replace("\r\n", "\n").replace('\r', "\n")
    };
    let lines: Vec<String> = text.split('\n').map(|l| SPACES.replace_all(l, " ").trim().to_string()).collect();
    BLANK_LINES.replace_all(&lines.join("\n"), "\n\n").trim().to_string()
}

fn html_text(el: ElementRef, out: &mut String) {
    for child in el.children() {
        match child.value() {
            Node::Text(t) => out.push_str(&collapse_keep_edges(t)),
            Node::Element(e) => {
                let name = e.name();
                if name == "br" {
                    out.push('\n');
                    continue;
                }
                if matches!(name, "script" | "style") {
                    continue;
                }
                let block = matches!(name, "p" | "div" | "li" | "ul" | "ol" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "tr" | "blockquote" | "section" | "pre");
                if block {
                    out.push('\n');
                }
                if let Some(c) = ElementRef::wrap(child) {
                    html_text(c, out);
                }
                if block {
                    out.push('\n');
                }
            }
            _ => {}
        }
    }
}

/// HTML text: white space runs (line breaks too) as one space, kept at the edges.
fn collapse_keep_edges(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut space = false;
    for c in s.chars() {
        if c.is_whitespace() {
            space = true;
        } else {
            if space {
                out.push(' ');
                space = false;
            }
            out.push(c);
        }
    }
    if space {
        out.push(' ');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_store_page_with_json_ld() {
        // Gambody's layout: a Product in an @graph, an og:image thumbnail, search phrases as keywords.
        let html = r#"<html><head>
            <title>BattleTech Marauder MWO 3D Printing Model - STL files | Gambody</title>
            <meta name="keywords" content="buy battletech marauder 3d model, download marauder 3d model files">
            <meta property="og:site_name" content="Gambody">
            <meta property="og:url" content="https://www.gambody.com/premium/marauder">
            <meta property="og:image" content="https://www.gambody.com/storage/150/preview.webp">
            <meta property="og:description" content="Explore high-poly 3D printing models on Gambody.">
            <script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
              {"@type": "Product", "name": "MWO Marauder 3D Printing Model | Assembly + Action",
               "description": "Explore high-poly 3D printing models on Gambody. Download STL files of the Marauder.",
               "image": ["https://www.gambody.com/storage/150/a_980x500.png", "https://www.gambody.com/storage/150/b_980x500.webp"],
               "offers": {"@type": "Offer", "seller": {"@type": "Organization", "name": "Gambody"}}},
              {"@type": "BreadcrumbList", "itemListElement": []}]}</script>
            </head><body><h1>Something else</h1></body></html>"#;
        let p = read("https://www.gambody.com/premium/marauder?ref=x", html, &[]);
        assert_eq!(p.url, "https://www.gambody.com/premium/marauder");
        assert_eq!(p.title.as_deref(), Some("MWO Marauder | Assembly + Action"));
        assert_eq!((p.creator, p.source.as_deref()), (None, Some("Gambody")));
        assert_eq!(p.description.as_deref(), Some("Explore high-poly 3D printing models on Gambody. Download STL files of the Marauder."));
        assert!(p.tags.is_empty(), "keywords are search phrases: {:?}", p.tags);
        assert_eq!(
            p.images,
            ["https://www.gambody.com/storage/150/a_980x500.png", "https://www.gambody.com/storage/150/b_980x500.webp", "https://www.gambody.com/storage/150/preview.webp"]
        );
    }

    #[test]
    fn a_creator_as_brand_and_text_with_line_breaks() {
        // MyMiniFactory's layout: JSON-LD with escaped slashes and Windows line breaks.
        let html = "<html><head><script type=\"application/ld+json\">\n\t\t{\"@context\": \"http://schema.org/\", \"@type\": \"Product\",
            \"name\": \"Ettercap Hunter - Tabletop Miniature (Pre-Supported)\",
            \"description\": \"Join me on Patreon!\\r\\nEttercaps are humanoid spiders.\\r\\n\\r\\n\\r\\nPre-supported.\",
            \"brand\": \"YKSMiniatures\", \"keywords\": \"Ettercap, spider ,#Monster\",
            \"image\": [\"https:\\/\\/images2.myminifactory.com\\/plain\\/ettercap-a.jpg\", {\"@type\": \"ImageObject\", \"url\": \"https:\\/\\/images2.myminifactory.com\\/plain\\/ettercap-b.jpg\"}]}
            </script></head><body></body></html>";
        let p = read("https://www.myminifactory.com/object/3d-print-278136", html, &[]);
        assert_eq!(p.title.as_deref(), Some("Ettercap Hunter - Tabletop Miniature (Pre-Supported)"));
        assert_eq!((p.creator.as_deref(), p.source.as_deref()), (Some("YKSMiniatures"), Some("MyMiniFactory")));
        assert_eq!(p.description.as_deref(), Some("Join me on Patreon!\nEttercaps are humanoid spiders.\n\nPre-supported."));
        assert_eq!(p.tags, ["ettercap", "spider", "monster"]);
        assert_eq!(p.images, ["https://images2.myminifactory.com/plain/ettercap-a.jpg", "https://images2.myminifactory.com/plain/ettercap-b.jpg"]);
        assert_eq!(p.url, "https://www.myminifactory.com/object/3d-print-278136");
    }

    #[test]
    fn a_patreon_post_read_in_the_desktop_app() {
        let html = r#"<html><head><title>Ettercap release | YKS Miniatures on Patreon</title>
            <meta property="og:title" content="Ettercap release">
            <meta property="og:description" content="This month: the Ettercap.">
            <meta property="og:image" content="https://c10.patreonusercontent.com/4/a.png?token=1">
            </head><body><h1>Ettercap release</h1>
            <a href="/c/yks/posts?filters%5Btag%5D=ettercap">#Ettercap</a>
            <a href="/c/yks/posts?filters%5Btag%5D=Monsters">Monsters</a>
            <a href="/c/yks/about">About</a></body></html>"#;
        let seen = ["https://c10.patreonusercontent.com/4/b.png?token=2".to_string(), "https://c10.patreonusercontent.com/4/a.png?token=1".to_string()];
        let p = read("https://www.patreon.com/posts/ettercap-release-123", html, &seen);
        assert_eq!((p.title.as_deref(), p.creator.as_deref()), (Some("Ettercap release"), Some("YKS Miniatures")));
        assert_eq!(p.source.as_deref(), Some("Patreon"));
        assert_eq!(p.tags, ["ettercap", "monsters"]);
        assert_eq!(p.images, ["https://c10.patreonusercontent.com/4/a.png?token=1", "https://c10.patreonusercontent.com/4/b.png?token=2"]);
    }

    #[test]
    fn html_descriptions_relative_images_and_tag_links() {
        let html = r#"<html><head>
            <title>Wolverine Full version 3D print model - Cults</title>
            <link rel="canonical" href="https://elsewhere.example/wolverine">
            <meta name="author" content="brzuba">
            <meta property="og:image" content="/uploads/wolverine.jpg">
            <script type="application/ld+json">{"@type": "CreativeWork", "name": "\n  Wolverine Full version 3D print model\n ",
              "description": "<p>Good night &amp; welcome!</p><p>Model made in 1/6 scale.<br>Files in STL and OBJ.</p>"}</script>
            <script type="application/ld+json">not json</script>
            </head><body><a rel="tag" href="/en/tags/wolverine">wolverine</a> <a rel="tag" href="/en/tags/marvel">Marvel</a>
            <a rel="tag" href="/en/tags/marvel">marvel</a></body></html>"#;
        let p = read("https://cults3d.com/en/3d-model/art/wolverine", html, &[]);
        assert_eq!(p.title.as_deref(), Some("Wolverine Full version"));
        assert_eq!((p.creator.as_deref(), p.source.as_deref()), (Some("brzuba"), Some("Cults3D")));
        assert_eq!(p.description.as_deref(), Some("Good night & welcome!\n\nModel made in 1/6 scale.\nFiles in STL and OBJ."));
        assert_eq!(p.tags, ["wolverine", "marvel"]);
        assert_eq!(p.images, ["https://cults3d.com/uploads/wolverine.jpg"]);
        assert_eq!(p.url, "https://cults3d.com/en/3d-model/art/wolverine", "a canonical link on another site is not taken");
    }

    #[test]
    fn pages_without_markup() {
        let p = read("https://example.org/x", "<html><head><title>Dragon</title></head></html>", &["javascript:alert(1)".into(), "data:image/png;base64,AA".into()]);
        assert_eq!((p.title.as_deref(), p.source.as_deref()), (Some("Dragon"), None));
        assert!(p.images.is_empty() && p.tags.is_empty());
        assert_eq!(read("not a url", "", &[]), WebPage { url: "not a url".into(), ..Default::default() });
        assert_eq!(clean_title("3D print model", None), "3D print model", "nothing left: the title as it was");
        assert_eq!(site_name("platform.stlflix.com"), Some("STLFlix"));
        assert_eq!(site_name("notcults3d.com"), None);
    }
}
