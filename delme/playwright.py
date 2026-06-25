import re
import queue
import threading
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dataiku.llm.agent_tools import BaseAgentTool

PLATFORM_PATTERNS = [
    "cdn.shopify.com", "Shopify.shop", "shopify-features",
    "woocommerce", "wc-blocks", "is-woocommerce", "wp-content/plugins/woocommerce",
    "mage/", "Mage.Cookies", "data-mage-init", "pub/static/frontend",
    "BCData", "bigcommerce", "stencil-", "x-bc-storefront",
    "shopware", "sw-plugin", "sw-storefront", "bundles/storefront",
    "prestashop", "id_product", "add_to_cart",
    "opencart", "catalog/view/theme",
    "schema.org/Product", "schema.org/Offer", "schema.org/Store",
    "js.stripe.com", "paypalobjects.com", "paypal.com/sdk",
    "klarna.com", "adyen.com", "squareup.com",
    "braintreegateway.com", "mollie.com", "redsys.es",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_TEXT_LENGTH = 150
MAX_TEXT_LENGTH = 38000

# Hard caps to guarantee bounded work / return time.
MAX_HTML_LENGTH = 3_000_000      # bytes of HTML fed to BeautifulSoup (per parse)
MAX_FRAMES = 5                  # max iframes inspected in _collect_all_frames_html
FRAME_EVAL_TIMEOUT_MS = 10000    # per-frame evaluate timeout
GLOBAL_DEADLINE_SECONDS = 360     # absolute wall-clock cap for a single invoke()
CURL_TIMEOUT = (10, 15)          # (connect, read) seconds for curl_cffi


def extract_signals(html: str) -> str:
    soup = BeautifulSoup(html[:MAX_HTML_LENGTH], "html.parser")
    found = []

    head = soup.find("head")
    if head:
        for tag in head.find_all(["meta", "link"]):
            tag_str = str(tag)
            if any(p.lower() in tag_str.lower() for p in PLATFORM_PATTERNS):
                found.append(tag_str[:200])

    for tag in soup.find_all("script"):
        tag_str = str(tag)
        if any(p.lower() in tag_str.lower() for p in PLATFORM_PATTERNS):
            found.append(tag_str[:300])

    for tag in soup.find_all("script", type="application/ld+json"):
        found.append(str(tag)[:500])

    return "\n".join(found[:30])


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    parsed = url.split("://", 1)
    if len(parsed) == 2:
        scheme, rest = parsed
        # Strip any path/query to check if it's a bare TLD
        domain_part = rest.split("/")[0]
        if rest == domain_part:  # no path at all
            url = f"{scheme}://{domain_part}/"
    return url


def clean_text(text: str) -> str:
    return re.sub(r'[ \t]*[\r\n][\s]*', '\r\n', text).strip()


def extract_text(html: str, url: str) -> str:
    soup = BeautifulSoup(html[:MAX_HTML_LENGTH], "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    text = clean_text(text[:MAX_TEXT_LENGTH]) if text else ""
    return text


_JS_OR_BLOCK_MARKERS = (
    "enable javascript",
    "javascript is required",
    "javascript is disabled",
    "requires javascript",
    "please enable js",
    "turn on javascript",
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "captcha",
    "px-captcha",
    "datadome",
)


def _looks_like_js_or_block(html: str) -> bool:
    low = html.lower()
    return any(marker in low for marker in _JS_OR_BLOCK_MARKERS)


def build_links_section(html: str, url: str, max_links: int = 0) -> str:
    soup = BeautifulSoup(html[:MAX_HTML_LENGTH], "html.parser")
    base_tag = soup.find("base", href=True)
    base_url = urljoin(url, base_tag["href"]) if base_tag else url

    links = []
    for a_tag in soup.find_all("a", href=True):
        link_text = a_tag.get_text(strip=True)
        link_href = urljoin(base_url, a_tag["href"])
        if link_text:
            links.append(f"{link_text} -> {link_href}")

    if max_links and max_links > 0:
        links = links[:max_links]

    return "\n\n--- LINKS ---\n" + "\n".join(links) if links else ""


def _truncate_to_words(text: str, max_words: int) -> str:
    if max_words is None or max_words <= 0:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def fetch_curl(url: str):
    from curl_cffi import requests
    resp = requests.get(
        url,
        timeout=CURL_TIMEOUT,
        headers=HEADERS,
        impersonate="chrome",
        max_redirects=10,
    )
    return resp.text, resp.headers.get("Content-Type", ""), resp.status_code


def _collect_all_frames_html(page) -> str:
    main_html = page.content()
    frame_fragments = []
    inspected = 0
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        if inspected >= MAX_FRAMES:
            break
        inspected += 1
        try:
            body_html = frame.evaluate(
                "() => document.body ? document.body.innerHTML : ''",
                timeout=FRAME_EVAL_TIMEOUT_MS,
            )
        except Exception:
            continue
        if body_html:
            frame_fragments.append(
                f'<div data-frame-url="{frame.url}">{body_html}</div>'
            )

    if not frame_fragments:
        return main_html

    injected = "\n".join(frame_fragments)
    if "</body>" in main_html:
        return main_html.replace("</body>", injected + "</body>", 1)
    return main_html + injected


def fetch_playwright(url: str):
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            timeout=30000,
        )
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={
                "Accept-Language": HEADERS["Accept-Language"],
            },
        )
        context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type
                in ("image", "media", "font", "stylesheet")
                else route.continue_()
            ),
        )

        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=5000)
            except PlaywrightTimeoutError:
                pass

            content_type = (
                response.headers.get("content-type", "") if response else ""
            )
            if "application/json" in content_type or url.endswith(".json"):
                body = response.text() if response else page.content()
                return body, content_type
            return _collect_all_frames_html(page), content_type
        finally:
            context.close()
            browser.close()


class WebScrapeTool(BaseAgentTool):
    """Scrapes the text content of a webpage given its URL.
    Extracts the full visible text with BeautifulSoup (no main-content
    pruning, so prices/specs/sidebars are kept). For JSON endpoints
    returns raw JSON.
    """

    def __init__(self):
        self._pw_queue: queue.Queue | None = None
        self._pw_thread: threading.Thread | None = None
        self._pw_lock = threading.Lock()

    def _ensure_pw_thread(self):
        with self._pw_lock:
            if self._pw_thread is None or not self._pw_thread.is_alive():
                self._pw_queue = queue.Queue()
                self._pw_thread = threading.Thread(
                    target=self._pw_worker,
                    args=(self._pw_queue,),
                    daemon=True,
                )
                self._pw_thread.start()

    def _pw_worker(self, job_queue: queue.Queue):
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                timeout=30000,
            )
            while True:
                job = job_queue.get()
                if job is None:
                    break
                url, result_q = job
                try:
                    context = browser.new_context(
                        user_agent=HEADERS["User-Agent"],
                        extra_http_headers={
                            "Accept-Language": HEADERS["Accept-Language"],
                        },
                    )
                    context.route(
                        "**/*",
                        lambda route: (
                            route.abort()
                            if route.request.resource_type
                            in ("image", "media", "font", "stylesheet")
                            else route.continue_()
                        ),
                    )
                    page = context.new_page()
                    try:
                        response = page.goto(
                            url, timeout=60000, wait_until="domcontentloaded"
                        )
                        try:
                            page.wait_for_load_state("load", timeout=5000)
                        except PlaywrightTimeoutError:
                            pass
                        content_type = (
                            response.headers.get("content-type", "")
                            if response else ""
                        )
                        if "application/json" in content_type or url.endswith(".json"):
                            body = response.text() if response else page.content()
                            result_q.put(("ok", (body, content_type)))
                        else:
                            result_q.put(
                                ("ok", (_collect_all_frames_html(page), content_type))
                            )
                    finally:
                        context.close()
                except Exception as e:
                    result_q.put(("err", e))

    def _playwright_fetch(self, url: str):
        self._ensure_pw_thread()
        result_q: queue.Queue = queue.Queue()
        self._pw_queue.put((url, result_q))
        status, value = result_q.get(timeout=90)
        if status == "err":
            raise value
        return value

    def load_sample_query(self, tool):
        return {
            "url": "https://example.com",
            "retrieve_links": True,
            "fetch_method": "auto",
            "extract_platform_signals": False,
            "max_words": 0,
            "max_links": 0,
        }

    def get_descriptor(self, tool):
        return {
            "description": (
                "Scrapes and returns the visible text content of a webpage given its URL. "
                "For JSON endpoints (e.g. /products.json) returns the raw JSON. "
                "Also extracts all hyperlinks from the page."
            ),
            "inputSchema": {
                "$id": "scrape-webpage",
                "title": "ScrapeWebpage",
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL of the webpage to scrape (e.g. https://example.com)"
                    },
                    "retrieve_links": {
                        "type": "boolean",
                        "description": (
                            "Whether to append the extracted hyperlinks section to the output. Defaults to true."
                        ),
                        "default": True
                    },
                    "fetch_method": {
                        "type": "string",
                        "enum": ["auto", "curl", "playwright"],
                        "description": (
                            "'auto' (default): try curl first, fall back to Playwright if blocked or JS-rendered. "
                            "'curl': use curl only, never launch a browser. "
                            "'playwright': use the headless browser only, skip curl entirely."
                        ),
                        "default": "auto"
                    },
                    "max_words": {
                        "type": "integer",
                        "description": (
                            "Maximum number of words to include in the scraped text. "
                            "If the text exceeds this limit it is truncated and '...' is appended. "
                            "Links are always appended in full after the truncated text. "
                            "Omit or set to 0 for no limit."
                        )
                    },
                    "max_links": {
                        "type": "integer",
                        "description": (
                            "Maximum number of hyperlinks to include in the links section. "
                            "Links are taken in page order. "
                            "Omit or set to 0 for no limit."
                        )
                    },
                    "extract_platform_signals": {
                        "type": "boolean",
                        "description": (
                            "When true, scans the raw HTML for platform-specific fingerprints "
                            "(Shopify, WooCommerce, Magento, BigCommerce, etc.) and payment provider "
                            "scripts before stripping tags. Matched signals are prepended to the output "
                            "under a '--- PLATFORM SIGNALS ---' header. "
                            "Defaults to false."
                        ),
                        "default": False
                    },
                },
                "required": ["url"]
            }
        }

    def invoke(self, input, trace):
        import threading

        url_for_error = input.get("input", {}).get("url", "")
        result: list = [None]
        exc: list = [None]

        def _run():
            try:
                result[0] = self._scrape(input, trace)
            except Exception as e:
                exc[0] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=GLOBAL_DEADLINE_SECONDS)

        if t.is_alive():
            return {
                "output": (
                    f"SCRAPE_TIMEOUT: exceeded {GLOBAL_DEADLINE_SECONDS}s "
                    f"while scraping {url_for_error}"
                ),
                "sources": [],
            }
        if exc[0] is not None:
            return {
                "output": f"SCRAPE_ERROR: {type(exc[0]).__name__}: {str(exc[0])[:300]}",
                "sources": [],
            }
        return result[0]

    def _scrape(self, input, trace):
        url = normalize_url(input["input"].get("url"))
        retrieve_links = input["input"].get("retrieve_links", True)
        extract_platform_signals = input["input"].get("extract_platform_signals", False)
        max_words = input["input"].get("max_words") or 0
        max_links = input["input"].get("max_links") or 0

        # fetch_method supersedes the legacy force_playwright flag
        fetch_method = input["input"].get("fetch_method")
        if not fetch_method:
            fetch_method = "playwright" if input["input"].get("force_playwright", False) else "auto"

        try:
            html = None
            text = None
            need_playwright = fetch_method == "playwright"

            if fetch_method in ("auto", "curl"):
                try:
                    c_html, content_type, status = fetch_curl(url)
                    if "application/json" in content_type or url.endswith(".json"):
                        return {"output": _truncate_to_words(clean_text(c_html), max_words), "sources": [{"url": url}]}
                    if status and status < 400 and c_html:
                        if _looks_like_js_or_block(c_html):
                            need_playwright = True  # page requires JS rendering
                        else:
                            candidate = extract_text(c_html, url)
                            if len(candidate) >= MIN_TEXT_LENGTH:
                                html, text = c_html, candidate
                            # short but not blocked — accept curl result as-is
                    else:
                        need_playwright = True  # curl got an error status
                except Exception:
                    need_playwright = True  # network error or curl unavailable

            if need_playwright and fetch_method in ("auto", "playwright"):
                try:
                    p_body, content_type = self._playwright_fetch(url)
                except Exception as e:
                    return {
                        "output": f"SCRAPE_UNAVAILABLE: {type(e).__name__}: {str(e)[:300]}",
                        "sources": [],
                    }
                if "application/json" in content_type or url.endswith(".json"):
                    return {"output": _truncate_to_words(clean_text(p_body), max_words), "sources": [{"url": url}]}
                html = p_body
                text = extract_text(html, url)

            if html is None:
                return {
                    "output": f"SCRAPE_UNAVAILABLE: no content retrieved with fetch_method={fetch_method!r}",
                    "sources": [],
                }

            output = _truncate_to_words(text or "", max_words)

            # ← new block
            if extract_platform_signals and html:
                signals = extract_signals(html)
                if signals:
                    output = f"--- PLATFORM SIGNALS ---\n{signals}\n\n--- PAGE TEXT ---\n{output}"

            if retrieve_links:
                output += build_links_section(html, url, max_links)

            return {"output": output, "sources": [{"url": url}]}

        except Exception as e:
            return {
                "output": f"SCRAPE_ERROR: {type(e).__name__}: {str(e)[:300]}",
                "sources": [],
            }
