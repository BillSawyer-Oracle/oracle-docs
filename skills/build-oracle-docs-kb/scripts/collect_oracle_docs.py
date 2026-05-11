#!/usr/bin/env python3
"""Collect raw Oracle Database documentation artifacts from supported bookshelf URLs."""

from __future__ import annotations

import argparse
import collections
import hashlib
import html.parser
import json
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


SKILL_NAME = "build-oracle-docs-kb"
SCHEMA_VERSION = 1
DEFAULT_USER_AGENT = "build-oracle-docs-kb/0.1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_DELAY = 0.5
API_TITLE_PATTERN = re.compile(r"(?i)\bapis?\b|javadoc")
IMAGE_TITLE_PATTERN = re.compile(r"(?i)technical architecture|reference architectures?|diagrams?")
SVG_URL_PATTERN = re.compile(r"""(?P<url>[A-Za-z0-9_./:%-]+\.svg)""", re.IGNORECASE)
HTML_URL_PATTERN = re.compile(r"""(?P<url>[A-Za-z0-9_./:%=-]+\.html)""", re.IGNORECASE)
REQUIRE_DEPS_PATTERN = re.compile(
    r"""(?:define|require)\s*\(\s*(?:['"][^'"]+['"]\s*,\s*)?\[(?P<deps>.*?)\]""",
    re.DOTALL,
)
REQUIRE_CONFIG_DEPS_PATTERN = re.compile(r"""['"]?deps['"]?\s*:\s*\[(?P<deps>.*?)\]""", re.DOTALL)
REQUIRE_PATHS_BLOCK_PATTERN = re.compile(r"""paths\s*:\s*\{(?P<paths>.*?)\}""", re.DOTALL)
REQUIRE_PATH_PATTERN = re.compile(
    r"""['"]?(?P<name>[A-Za-z0-9_-]+)['"]?\s*:\s*['"]?(?P<path>[A-Za-z0-9_./-]+)['"]?""",
    re.DOTALL,
)
META_TAG_PATTERN = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR_PATTERN = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['"])(.*?)\2""", re.DOTALL)
QUOTED_VALUE_PATTERN = re.compile(r"""['"](?P<value>[^'"]+)['"]""")

SUPPORTED_URLS = {
    "https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html": "26ai",
    "https://docs.oracle.com/en/database/oracle/oracle-database/21/books.html": "21c",
    "https://docs.oracle.com/en/database/oracle/oracle-database/19/books.html": "19c",
}

DEFERRED_DECISIONS = [
    "Revisit zip acceleration path after baseline per-book collector is validated.",
    "Revisit support for other Oracle documentation library URLs and test against non-database products.",
]

ASSET_EXTENSIONS = {
    ".css",
    ".js",
    ".mjs",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".map",
    ".json",
}

STATIC_ASSET_PATH_MARKERS = (
    "/en/dcommon/",
    "/en/asset/",
    "/assets/",
    "/static/",
    "/webfolder/",
    "/js/",
    "/css/",
    "/images/",
    "/img/",
)

IGNORED_SVG_BASENAMES = {
    "favicon.svg",
    "oracle-logo.svg",
    "oracle-o.svg",
}

IMAGE_SCRIPT_BASENAMES = {
    "app-config.js",
    "app-main.js",
    "article.js",
    "configure-toolbar.js",
    "lookup.js",
    "manifest.js",
}

JAVADOC_SUPPORT_FILES = {
    "element-list",
    "package-list",
    "member-search-index.js",
    "module-search-index.js",
    "package-search-index.js",
    "search.js",
    "search-page.js",
    "tag-search-index.js",
    "type-search-index.js",
}

JAVADOC_PROFILE = "javadoc_api"
OHC_PROFILE = "ohc_book"
VERSIONED_LATEST_PROFILE = "versioned_latest_book"
GENERIC_PROFILE = "generic_html"
RELEASE_SEGMENT_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")


class CollectorError(Exception):
    """Raised for expected collector failures."""


@dataclass(frozen=True)
class Book:
    title: str
    html_url: str | None
    pdf_url: str | None
    method: str
    slug: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def decode_body(body: bytes, charset: str | None = None) -> str:
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return body.decode("utf-16", errors="replace")
    if body.startswith(b"\xef\xbb\xbf"):
        return body.decode("utf-8-sig", errors="replace")
    return body.decode("utf-8", errors="replace")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "untitled"


def unique_slugs(records: list[dict[str, str | None]]) -> list[Book]:
    seen: set[str] = set()
    books: list[Book] = []
    for record in records:
        title = str(record["title"])
        base = slugify(title)
        slug = base
        if slug in seen:
            digest = hashlib.sha1((title + "|" + str(record.get("html_url"))).encode("utf-8")).hexdigest()[:8]
            slug = f"{base}-{digest}"
        seen.add(slug)
        pdf_url = record.get("pdf_url")
        html_url = record.get("html_url")
        method = "pdf" if pdf_url else "html_crawl"
        books.append(Book(title=title, html_url=html_url, pdf_url=pdf_url, method=method, slug=slug))
    return books


class TokenizingHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict[str, str]] = []
        self._link_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {name.lower(): value for name, value in attrs}
        href = attrs_dict.get("href")
        self._link_stack.append({"href": href or "", "parts": []})

    def handle_data(self, data: str) -> None:
        text = normalize_ws(data)
        if not text:
            return
        if self._link_stack:
            self._link_stack[-1]["parts"].append(text)
        else:
            self.tokens.append({"kind": "text", "text": text})

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._link_stack:
            return
        link = self._link_stack.pop()
        href = normalize_ws(str(link.get("href", "")))
        text = normalize_ws(" ".join(link.get("parts", [])))
        if href:
            self.tokens.append({"kind": "link", "text": text, "href": href})


def parse_bookshelf(html_text: str, bookshelf_url: str) -> tuple[list[Book], str | None]:
    parser = TokenizingHTMLParser()
    parser.feed(html_text)
    tokens = parser.tokens

    zip_url: str | None = None
    for token in tokens:
        if token["kind"] == "link" and "zip file" in token.get("text", "").lower():
            zip_url = urljoin(bookshelf_url, token["href"])
            break

    start_index = 0
    for index, token in enumerate(tokens):
        if token["kind"] == "text":
            text = token.get("text", "").lower()
            if "browse" in text and "bookshelf" in text:
                start_index = index + 1
                break

    records: list[dict[str, str | None]] = []
    seen_html_urls: set[str] = set()
    for index in range(start_index, len(tokens)):
        token = tokens[index]
        if token["kind"] != "link" or token.get("text", "").strip().upper() != "HTML":
            continue

        title = find_title_before(tokens, index, start_index)
        if not title:
            continue

        html_url = urljoin(bookshelf_url, token["href"])
        if html_url in seen_html_urls:
            continue
        seen_html_urls.add(html_url)

        pdf_url: str | None = None
        lookahead = index + 1
        while lookahead < min(index + 4, len(tokens)):
            next_token = tokens[lookahead]
            if next_token["kind"] == "text":
                break
            if next_token["kind"] == "link" and next_token.get("text", "").strip().upper() == "PDF":
                pdf_url = urljoin(bookshelf_url, next_token["href"])
                break
            lookahead += 1

        records.append({"title": title, "html_url": html_url, "pdf_url": pdf_url})

    if not records:
        records = parse_bookshelf_with_bs4(html_text, bookshelf_url)

    if not records:
        raise CollectorError("No books were discovered on the bookshelf page.")

    return unique_slugs(records), zip_url


def find_title_before(tokens: list[dict[str, str]], index: int, start_index: int) -> str | None:
    for cursor in range(index - 1, start_index - 1, -1):
        token = tokens[cursor]
        if token["kind"] != "text":
            continue
        title = normalize_ws(token.get("text", ""))
        if not title:
            continue
        if title.lower().startswith("browse "):
            continue
        return title
    return None


def parse_bookshelf_with_bs4(html_text: str, bookshelf_url: str) -> list[dict[str, str | None]]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    records: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for html_link in soup.find_all("a"):
        if normalize_ws(html_link.get_text()).upper() != "HTML":
            continue
        parent = html_link.parent
        title = None
        for previous in html_link.find_all_previous(string=True, limit=12):
            text = normalize_ws(str(previous))
            if text and text.upper() not in {"HTML", "PDF"} and not text.lower().startswith("browse "):
                title = text
                break
        if not title:
            continue
        html_url = urljoin(bookshelf_url, str(html_link.get("href")))
        if html_url in seen:
            continue
        seen.add(html_url)
        pdf_url = None
        if parent is not None:
            for sibling_link in parent.find_all("a"):
                if normalize_ws(sibling_link.get_text()).upper() == "PDF":
                    pdf_url = urljoin(bookshelf_url, str(sibling_link.get("href")))
                    break
        records.append({"title": title, "html_url": html_url, "pdf_url": pdf_url})
    return records


class PageReferenceParser(html.parser.HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.page_links: set[str] = set()
        self.asset_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {name.lower(): value for name, value in attrs if value}
        if tag == "a" and attrs_dict.get("href"):
            self.page_links.add(urljoin(self.base_url, attrs_dict["href"]))
            return
        if tag in {"frame", "iframe"} and attrs_dict.get("src"):
            self.page_links.add(urljoin(self.base_url, attrs_dict["src"]))
            return
        if tag in {"img", "script", "source", "video", "audio", "track"} and attrs_dict.get("src"):
            self.asset_links.add(urljoin(self.base_url, attrs_dict["src"]))
            if tag == "script" and attrs_dict.get("data-main"):
                data_main = attrs_dict["data-main"]
                if not Path(data_main).suffix:
                    data_main = f"{data_main}.js"
                self.asset_links.add(urljoin(self.base_url, data_main))
            return
        if tag == "link" and attrs_dict.get("href"):
            rel = attrs_dict.get("rel", "").lower()
            if "contents" in rel:
                self.page_links.add(urljoin(self.base_url, attrs_dict["href"]))
                return
            if any(part in rel for part in ("stylesheet", "icon", "preload", "modulepreload")):
                self.asset_links.add(urljoin(self.base_url, attrs_dict["href"]))
            return
        if tag == "object" and attrs_dict.get("data"):
            self.asset_links.add(urljoin(self.base_url, attrs_dict["data"]))


class RobotsCache:
    def __init__(self, user_agent: str, timeout: float, retries: int) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self._cache: dict[str, RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._cache.get(root)
        if parser is None:
            parser = RobotFileParser()
            robots_url = f"{root}/robots.txt"
            try:
                body, _, _ = fetch_url(
                    robots_url,
                    user_agent=self.user_agent,
                    timeout=self.timeout,
                    retries=self.retries,
                    check_status_only=False,
                )
                parser.parse(decode_body(body).splitlines())
            except CollectorError:
                parser.parse([])
            self._cache[root] = parser
        return parser.can_fetch(self.user_agent, url)


def fetch_url(
    url: str,
    *,
    user_agent: str,
    timeout: float,
    retries: int,
    check_status_only: bool = True,
) -> tuple[bytes, str | None, str | None]:
    body, content_type, charset, _ = fetch_url_details(
        url,
        user_agent=user_agent,
        timeout=timeout,
        retries=retries,
        check_status_only=check_status_only,
    )
    return body, content_type, charset


def fetch_url_details(
    url: str,
    *,
    user_agent: str,
    timeout: float,
    retries: int,
    check_status_only: bool = True,
) -> tuple[bytes, str | None, str | None, str]:
    transient_statuses = {408, 429, 500, 502, 503, 504}
    last_error: str | None = None

    for attempt in range(retries + 1):
        request = Request(url, headers={"User-Agent": user_agent})
        try:
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset()
                return response.read(), content_type, charset, response.geturl()
        except HTTPError as exc:
            last_error = f"HTTP {exc.code} for {url}"
            if check_status_only and exc.code not in transient_statuses:
                raise CollectorError(last_error) from exc
        except URLError as exc:
            last_error = f"{exc.reason} for {url}"

        if attempt < retries:
            time.sleep(min(2**attempt, 8))

    raise CollectorError(last_error or f"Failed to fetch {url}")


def validate_bookshelf_url(url: str) -> str:
    if url not in SUPPORTED_URLS:
        supported = "\n".join(f"  - {item}" for item in SUPPORTED_URLS)
        raise CollectorError(f"Unsupported bookshelf URL: {url}\nSupported URLs:\n{supported}")
    return SUPPORTED_URLS[url]


def read_bookshelf_html(args: argparse.Namespace) -> str:
    if args.fixture:
        return Path(args.fixture).read_text(encoding="utf-8")
    body, content_type, charset = fetch_url(
        args.bookshelf_url,
        user_agent=args.user_agent,
        timeout=args.timeout,
        retries=args.retries,
    )
    if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
        raise CollectorError(f"Expected HTML bookshelf, got {content_type}")
    return decode_body(body, charset)


def load_prior_manifest(output_dir: Path) -> dict[str, Any] | None:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def find_prior_item(prior_manifest: dict[str, Any] | None, slug: str) -> dict[str, Any] | None:
    if not prior_manifest:
        return None
    for item in prior_manifest.get("items", []):
        if item.get("slug") == slug:
            return item
    return None


def is_api_book(book: Book) -> bool:
    return book.method == "html_crawl" and bool(API_TITLE_PATTERN.search(f"{book.title} {book.slug}"))


def is_image_book(book: Book) -> bool:
    return book.method == "html_crawl" and bool(IMAGE_TITLE_PATTERN.search(f"{book.title} {book.slug}"))


def collection_for_book(book: Book) -> str:
    if is_image_book(book):
        return "images"
    return "api" if is_api_book(book) else "docs"


def collection_for_profile(book: Book, profile: str | None) -> str:
    if profile == JAVADOC_PROFILE:
        return "api"
    return collection_for_book(book)


def collection_base_dir(output_dir: Path, collection: str) -> Path:
    if collection == "api":
        return output_dir.parent / "api" / output_dir.name
    if collection == "images":
        return output_dir.parent / "images" / output_dir.name
    return output_dir


def collection_base_label(output_dir: Path, collection: str) -> str:
    if collection == "api":
        return f"api/{output_dir.name}"
    if collection == "images":
        return f"images/{output_dir.name}"
    return output_dir.name


def apply_output_collection(
    output: dict[str, Any],
    book: Book,
    output_dir: Path,
    collection_override: str | None = None,
) -> None:
    collection = collection_override or collection_for_book(book)
    output["collection"] = collection
    output["base_dir"] = collection_base_label(output_dir, collection)


def output_base_dir_for_item(output_dir: Path, item: dict[str, Any]) -> Path:
    output = item.get("output") or {}
    collection = output.get("collection")
    if collection == "api":
        return output_dir.parent / "api" / output_dir.name
    if collection == "images":
        return output_dir.parent / "images" / output_dir.name
    return output_dir


def prior_output_is_valid(
    output_dir: Path,
    prior_item: dict[str, Any] | None,
    expected_collection: str | None = None,
) -> bool:
    if not prior_item:
        return False
    if prior_item.get("status") not in {"success", "reused"}:
        return False
    output = prior_item.get("output") or {}
    if expected_collection and output.get("collection", "docs") != expected_collection:
        return False
    base_dir = output_base_dir_for_item(output_dir, prior_item)
    files = output.get("files")
    if isinstance(files, list) and files:
        for file_record in files:
            rel_path = file_record.get("path")
            expected_hash = file_record.get("sha256")
            if not rel_path or not expected_hash:
                return False
            path = base_dir / rel_path
            if not path.exists() or sha256_file(path) != expected_hash:
                return False
        return True

    paths = output.get("paths") or []
    expected_hash = output.get("sha256")
    if len(paths) != 1 or not expected_hash:
        return False
    path = base_dir / paths[0]
    return path.exists() and sha256_file(path) == expected_hash


def mark_prior_item_reused(prior_item: dict[str, Any]) -> dict[str, Any]:
    reused = dict(prior_item)
    reused["status"] = "reused"
    reused["errors"] = []
    return reused


def existing_pdf_item(book: Book, output_dir: Path) -> dict[str, Any] | None:
    rel_path = f"{book.slug}.pdf"
    target = output_dir / rel_path
    if not target.is_file() or target.stat().st_size == 0:
        return None
    item = build_planned_item(book, output_dir)
    item["output"] = {
        "paths": [rel_path],
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }
    item["status"] = "reused"
    item["errors"] = []
    item["reuse"] = {"source": "existing_pdf_without_completed_manifest"}
    return item


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate_file_hash(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(files, key=lambda item: item["path"]):
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_bytes(path: Path, body: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def make_soup(text: str) -> Any | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    return BeautifulSoup(text, "html.parser")


def html_meta_value(text: str, name: str) -> str | None:
    soup = make_soup(text)
    if soup is not None:
        node = soup.find("meta", attrs={"name": name})
        if node is not None and node.get("content"):
            return normalize_ws(str(node.get("content")))

    for meta_match in META_TAG_PATTERN.finditer(text):
        attrs = {attr_name.lower(): value for attr_name, _, value in ATTR_PATTERN.findall(meta_match.group(0))}
        if attrs.get("name", "").lower() == name.lower() and attrs.get("content"):
            return normalize_ws(attrs["content"])
    return None


def html_generator_value(text: str) -> str:
    return html_meta_value(text, "generator") or ""


def detect_html_profile(book: Book, text: str, fetched_url: str | None = None) -> str:
    marker = f"{book.title} {book.slug} {html_generator_value(text)} {text[:4000]}".lower()
    if (
        "generated by javadoc" in marker
        or "javadoc" in marker
        or "<frameset" in marker
        or "package-summary.html" in marker
        or "allclasses-frame.html" in marker
        or book.slug.endswith("javadoc")
    ):
        return JAVADOC_PROFILE
    if fetched_url and len(extract_versioned_release_links(text, fetched_url)) >= 2:
        return VERSIONED_LATEST_PROFILE
    if 'rel="contents"' in marker or "oracle markdown generation" in marker or "oracle help center" in marker:
        return OHC_PROFILE
    return GENERIC_PROFILE


def release_version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def release_segment_from_url(candidate_url: str, product_root_path: str) -> str | None:
    path = urlparse(candidate_url).path
    if not path.startswith(product_root_path):
        return None
    rel = path[len(product_root_path) :].lstrip("/")
    if not rel:
        return None
    segment = rel.split("/", 1)[0]
    return segment if RELEASE_SEGMENT_PATTERN.fullmatch(segment) else None


def extract_versioned_release_links(text: str, base_url: str) -> dict[str, dict[str, str]]:
    parsed_base = urlparse(base_url)
    product_root_path = book_root_path(base_url)
    product_root_url = f"{parsed_base.scheme}://{parsed_base.netloc}{product_root_path}"
    releases: dict[str, dict[str, str]] = {}
    parser = PageReferenceParser(base_url)
    parser.feed(text)

    for link in parser.page_links:
        link = strip_fragment(link)
        if not same_origin(base_url, link):
            continue
        release = release_segment_from_url(link, product_root_path)
        if not release:
            continue
        release_root = urljoin(product_root_url, f"{release}/")
        releases.setdefault(
            release,
            {
                "release": release,
                "release_url": urljoin(release_root, "index.html"),
                "books_url": urljoin(release_root, "books.html"),
            },
        )
    return releases


def latest_release_from_links(links: dict[str, dict[str, str]]) -> dict[str, str] | None:
    if not links:
        return None
    return links[max(links, key=release_version_key)]


def extract_pdf_links(text: str, base_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    soup = make_soup(text)
    if soup is not None:
        for node in soup.find_all("a", href=True):
            href = str(node.get("href"))
            if ".pdf" not in href.lower():
                continue
            url = strip_fragment(urljoin(base_url, href))
            if url not in seen:
                seen.add(url)
                links.append(url)

    for match in re.finditer(r"""["']pdf["']\s*:\s*["']([^"']+\.pdf)["']""", text, flags=re.IGNORECASE):
        url = strip_fragment(urljoin(base_url, html.unescape(match.group(1))))
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def javadoc_effective_start_url(book: Book, fetched_url: str, text: str) -> str:
    fetched_url = strip_fragment(fetched_url)
    parsed = urlparse(fetched_url)
    if "/pls/topic/lookup" not in parsed.path:
        return fetched_url

    product = html_meta_value(text, "dcterms.product")
    identifier = html_meta_value(text, "dcterms.isVersionOf")
    if product and identifier:
        product_path = product.strip("/")
        if product_path.startswith("en/"):
            return f"{parsed.scheme}://{parsed.netloc}/{product_path}/{identifier.lower()}/index.html"
    return fetched_url


def is_toc_url(url: str) -> bool:
    return Path(urlparse(url).path).name.lower() in {"toc.htm", "toc.html"}


def is_html_file_record(record: dict[str, Any]) -> bool:
    rel_path = str(record.get("path") or "").lower()
    return rel_path.endswith((".html", ".htm"))


def is_content_html_record(record: dict[str, Any]) -> bool:
    rel_path = str(record.get("path") or "").lower().replace("\\", "/")
    basename = Path(rel_path).name
    return is_html_file_record(record) and basename not in {"index.html", "toc.htm", "toc.html"}


def html_page_count(item: dict[str, Any]) -> int:
    files = (item.get("output") or {}).get("files") or []
    return sum(1 for record in files if is_html_file_record(record))


def profile_validation_for_item(item: dict[str, Any]) -> str | None:
    profile = item.get("html_profile") or {}
    return profile.get("validation_status")


def is_suspicious_html_item(item: dict[str, Any] | None) -> bool:
    if not item or item.get("method") != "html_crawl":
        return False
    output = item.get("output") or {}
    if output.get("collection") == "images":
        return False
    if item.get("status") in {"failed", "skipped"}:
        return True
    if html_page_count(item) < 2:
        return True
    validation_status = profile_validation_for_item(item)
    return validation_status not in {None, "success"}


def javadoc_support_asset_url(start_url: str, candidate_url: str, root_path: str) -> bool:
    parsed = urlparse(candidate_url)
    if parsed.scheme not in {"http", "https"} or not same_origin(start_url, candidate_url):
        return False
    if not parsed.path.startswith(root_path):
        return False
    basename = Path(parsed.path).name
    return basename in JAVADOC_SUPPORT_FILES


def build_planned_item(book: Book, output_dir: Path | None = None) -> dict[str, Any]:
    if collection_for_book(book) == "images":
        paths: list[str] = []
    else:
        paths = [f"{book.slug}.pdf" if book.method == "pdf" else f"{book.slug}/"]
    output: dict[str, Any] = {"paths": paths, "bytes": 0, "sha256": None}
    if output_dir is not None:
        apply_output_collection(output, book, output_dir)
    return {
        "title": book.title,
        "slug": book.slug,
        "method": book.method,
        "html_url": book.html_url,
        "pdf_url": book.pdf_url,
        "output": output,
        "status": "planned",
        "errors": [],
    }


def collect_pdf(
    book: Book,
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
    prior_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    item = build_planned_item(book, output_dir)
    assert book.pdf_url is not None

    prior_item = find_prior_item(prior_manifest, book.slug)
    if not args.force and prior_output_is_valid(output_dir, prior_item, collection_for_book(book)):
        return mark_prior_item_reused(prior_item)

    rel_path = f"{book.slug}.pdf"
    target = output_dir / rel_path
    if not args.force:
        existing_item = existing_pdf_item(book, output_dir)
        if existing_item is not None:
            return existing_item

    if not robots.can_fetch(book.pdf_url):
        item["status"] = "skipped"
        item["errors"] = [f"robots.txt disallowed {book.pdf_url}"]
        return item

    try:
        body, _, _ = fetch_url(book.pdf_url, user_agent=args.user_agent, timeout=args.timeout, retries=args.retries)
        file_info = write_bytes(target, body)
        output = {"paths": [rel_path], "bytes": file_info["bytes"], "sha256": file_info["sha256"]}
        apply_output_collection(output, book, output_dir)
        item["output"] = output
        item["status"] = "success"
    except CollectorError as exc:
        item["status"] = "failed"
        item["errors"] = [str(exc)]
    return item


def collect_image_book(
    book: Book,
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
    prior_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    item = build_planned_item(book, output_dir)
    assert book.html_url is not None

    prior_item = find_prior_item(prior_manifest, book.slug)
    if not args.force and prior_output_is_valid(output_dir, prior_item, "images"):
        cleanup = delete_old_shallow_folder(book, output_dir)
        reused = mark_prior_item_reused(prior_item)
        if cleanup:
            reused["cleanup"] = cleanup
        return reused

    root_path = book_root_path(book.html_url)
    root_url = book_root_url(book.html_url)
    page_queue: collections.deque[str] = collections.deque([strip_fragment(book.html_url)])
    script_queue: collections.deque[str] = collections.deque()
    queued_pages: set[str] = {strip_fragment(book.html_url)}
    queued_scripts: set[str] = set()
    seen_pages: set[str] = set()
    seen_scripts: set[str] = set()
    svg_urls: set[str] = set()
    robots_skipped: list[str] = []
    page_errors: list[str] = []
    script_errors: list[str] = []

    def enqueue_page(candidate_url: str) -> None:
        candidate_url = strip_fragment(candidate_url)
        if (
            is_allowed_page_url(book.html_url, candidate_url, root_path)
            and candidate_url not in seen_pages
            and candidate_url not in queued_pages
        ):
            page_queue.append(candidate_url)
            queued_pages.add(candidate_url)

    def enqueue_script(candidate_url: str) -> None:
        candidate_url = strip_fragment(candidate_url)
        if (
            is_allowed_text_asset_url(book.html_url, candidate_url, root_path)
            and is_image_relevant_script_url(candidate_url)
            and candidate_url not in seen_scripts
            and candidate_url not in queued_scripts
        ):
            script_queue.append(candidate_url)
            queued_scripts.add(candidate_url)

    while page_queue or script_queue:
        while page_queue:
            page_url = strip_fragment(page_queue.popleft())
            queued_pages.discard(page_url)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            if not is_allowed_page_url(book.html_url, page_url, root_path):
                continue
            if not robots.can_fetch(page_url):
                robots_skipped.append(page_url)
                continue

            if len(seen_pages) > 1 and args.delay > 0:
                time.sleep(args.delay)

            try:
                body, content_type, charset, final_url = fetch_url_details(
                    page_url,
                    user_agent=args.user_agent,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                final_url = strip_fragment(final_url)
                text = decode_body(body, charset)
                derived_svg = derived_svg_url_for_page(final_url, book.html_url, root_path)
                if derived_svg:
                    svg_urls.add(derived_svg)
                svg_urls.update(extract_svg_urls(text, final_url, book.html_url, root_path))
                svg_urls.update(extract_svg_urls(text, root_url, book.html_url, root_path))

                for refresh_url in extract_meta_refresh_urls(text, final_url):
                    enqueue_page(refresh_url)
                for embedded_page_url in extract_html_urls(text, final_url, book.html_url, root_path):
                    enqueue_page(embedded_page_url)

                if content_type and "html" not in content_type:
                    continue
                parser = PageReferenceParser(final_url)
                parser.feed(text)
                for link in sorted(parser.page_links):
                    enqueue_page(link)
                for asset_url in sorted(parser.asset_links):
                    enqueue_script(asset_url)
            except CollectorError as exc:
                page_errors.append(str(exc))

        while script_queue:
            script_url = strip_fragment(script_queue.popleft())
            queued_scripts.discard(script_url)
            if script_url in seen_scripts:
                continue
            seen_scripts.add(script_url)

            if not robots.can_fetch(script_url):
                robots_skipped.append(script_url)
                continue
            try:
                body, _, charset, final_url = fetch_url_details(
                    script_url,
                    user_agent=args.user_agent,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                final_url = strip_fragment(final_url)
                text = decode_body(body, charset)
                svg_urls.update(extract_svg_urls(text, final_url, book.html_url, root_path))
                svg_urls.update(extract_svg_urls(text, root_url, book.html_url, root_path))
                for embedded_page_url in extract_html_urls(text, root_url, book.html_url, root_path):
                    derived_svg = derived_svg_url_for_page(embedded_page_url, book.html_url, root_path)
                    if derived_svg:
                        svg_urls.add(derived_svg)
                    enqueue_page(embedded_page_url)
                for module_url in extract_require_module_urls(text, final_url, book.html_url, root_path):
                    enqueue_script(module_url)
            except CollectorError as exc:
                script_errors.append(str(exc))

    download_result = download_image_svgs(book, sorted(svg_urls), output_dir, args, robots)
    output = {
        "paths": [record["path"] for record in download_result["files"]],
        "bytes": sum(int(record["bytes"]) for record in download_result["files"]),
        "sha256": aggregate_file_hash(download_result["files"]) if download_result["files"] else None,
        "files": download_result["files"],
    }
    apply_output_collection(output, book, output_dir)
    item["output"] = output
    item["image_collection"] = {
        "scanned_pages": sorted(seen_pages),
        "scanned_scripts": sorted(seen_scripts),
        "discovered_svg_urls": sorted(svg_urls),
        "duplicates": download_result["duplicates"],
        "robots_skipped": robots_skipped,
        "script_errors": script_errors,
    }

    errors = page_errors + [f"robots.txt disallowed {url}" for url in robots_skipped]
    if not svg_urls:
        errors.append("zero SVGs discovered")
    errors.extend(download_result["errors"])
    item["errors"] = errors

    if svg_urls and not page_errors and not download_result["errors"]:
        item["status"] = "success"
        cleanup = delete_old_shallow_folder(book, output_dir)
        if cleanup:
            item["cleanup"] = cleanup
    else:
        item["status"] = "failed"
    return item


def collect_versioned_latest_book(
    book: Book,
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
    *,
    start_url: str,
    start_final_url: str,
    start_text: str,
) -> dict[str, Any]:
    item = build_planned_item(book, output_dir)
    release_links = extract_versioned_release_links(start_text, start_final_url)
    selected = latest_release_from_links(release_links)
    validation_errors: list[str] = []
    if not selected:
        validation_errors.append("No version-like release links were discovered.")
        item["status"] = "failed"
        item["errors"] = validation_errors
        item["html_profile"] = {
            "profile": VERSIONED_LATEST_PROFILE,
            "start_url": start_url,
            "effective_start_url": start_final_url,
            "effective_root": book_root_url(start_final_url),
            "available_releases": sorted(release_links),
            "validation_status": "failed",
            "validation_errors": validation_errors,
        }
        return item

    selected_release = selected["release"]
    selected_release_url = selected["release_url"]
    selected_books_url = selected["books_url"]
    effective_root = urljoin(selected_release_url, "./")

    books_text = ""
    pdf_links: list[str] = []
    try:
        if not robots.can_fetch(selected_books_url):
            validation_errors.append(f"robots.txt disallowed selected release books page {selected_books_url}")
        else:
            books_body, _, books_charset, books_final_url = fetch_url_details(
                selected_books_url,
                user_agent=args.user_agent,
                timeout=args.timeout,
                retries=args.retries,
            )
            selected_books_url = strip_fragment(books_final_url)
            books_text = decode_body(books_body, books_charset)
            pdf_links = extract_pdf_links(books_text, selected_books_url)
    except CollectorError as exc:
        validation_errors.append(str(exc))

    pdf_url = pdf_links[0] if pdf_links else None
    if pdf_url:
        if not robots.can_fetch(pdf_url):
            validation_errors.append(f"robots.txt disallowed selected release PDF {pdf_url}")
        else:
            try:
                body, _, _, final_pdf_url = fetch_url_details(
                    pdf_url,
                    user_agent=args.user_agent,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                rel_path = f"{book.slug}.pdf"
                target = collection_base_dir(output_dir, "docs") / rel_path
                file_info = write_bytes(target, body)
                output = {
                    "paths": [rel_path],
                    "bytes": file_info["bytes"],
                    "sha256": file_info["sha256"],
                    "files": [{"path": rel_path, **file_info, "url": final_pdf_url}],
                    "acquisition_method": "versioned_latest_pdf",
                    "source_pdf_url": final_pdf_url,
                    "source_pdf_filename": Path(urlparse(final_pdf_url).path).name,
                    "selected_release": selected_release,
                    "selected_release_url": selected_release_url,
                    "selected_release_books_url": selected_books_url,
                }
                apply_output_collection(output, book, output_dir, "docs")
                item["output"] = output
                item["status"] = "success"
            except CollectorError as exc:
                validation_errors.append(str(exc))

    if not pdf_url:
        fallback_book = Book(
            title=book.title,
            html_url=selected_release_url,
            pdf_url=None,
            method=book.method,
            slug=book.slug,
        )
        fallback_item = collect_html_book(fallback_book, output_dir, args, robots, None, force_collect=True)
        fallback_profile = fallback_item.get("html_profile") or {}
        output = fallback_item.get("output") or {}
        output["acquisition_method"] = "versioned_latest_html"
        output["selected_release"] = selected_release
        output["selected_release_url"] = selected_release_url
        output["selected_release_books_url"] = selected_books_url
        apply_output_collection(output, book, output_dir, "docs")
        fallback_item["output"] = output
        fallback_item["title"] = book.title
        fallback_item["html_url"] = book.html_url
        fallback_item["html_profile"] = {
            "profile": VERSIONED_LATEST_PROFILE,
            "start_url": start_url,
            "effective_start_url": selected_release_url,
            "effective_root": effective_root,
            "available_releases": sorted(release_links, key=release_version_key),
            "selected_release": selected_release,
            "selected_release_url": selected_release_url,
            "selected_release_books_url": selected_books_url,
            "fallback_profile": fallback_profile,
            "validation_status": "success" if fallback_item.get("status") == "success" else "failed",
            "validation_errors": fallback_item.get("errors") or ["No latest-release PDF was discovered."],
        }
        return fallback_item

    if validation_errors:
        item["status"] = "failed"
        item["errors"] = validation_errors

    item["html_profile"] = {
        "profile": VERSIONED_LATEST_PROFILE,
        "start_url": start_url,
        "effective_start_url": selected_release_url,
        "effective_root": effective_root,
        "available_releases": sorted(release_links, key=release_version_key),
        "selected_release": selected_release,
        "selected_release_url": selected_release_url,
        "selected_release_books_url": selected_books_url,
        "source_pdf_url": (item.get("output") or {}).get("source_pdf_url") or pdf_url,
        "validation_status": "success" if item.get("status") == "success" else "failed",
        "validation_errors": validation_errors,
    }
    item.setdefault("warnings", [])
    return item


def collect_html_book(
    book: Book,
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
    prior_manifest: dict[str, Any] | None,
    *,
    force_collect: bool = False,
) -> dict[str, Any]:
    item = build_planned_item(book, output_dir)
    assert book.html_url is not None

    prior_item = find_prior_item(prior_manifest, book.slug)
    if not force_collect and not args.force and prior_output_is_valid(output_dir, prior_item, collection_for_book(book)):
        return mark_prior_item_reused(prior_item)

    try:
        start_body, start_content_type, start_charset, start_final_url = fetch_url_details(
            strip_fragment(book.html_url),
            user_agent=args.user_agent,
            timeout=args.timeout,
            retries=args.retries,
        )
    except CollectorError as exc:
        item["status"] = "failed"
        item["errors"] = [str(exc)]
        return item

    start_text = decode_body(start_body, start_charset)
    profile = detect_html_profile(book, start_text, start_final_url)
    if profile == VERSIONED_LATEST_PROFILE:
        return collect_versioned_latest_book(
            book,
            output_dir,
            args,
            robots,
            start_url=strip_fragment(book.html_url),
            start_final_url=strip_fragment(start_final_url),
            start_text=start_text,
        )
    effective_start_url = (
        javadoc_effective_start_url(book, start_final_url, start_text)
        if profile == JAVADOC_PROFILE
        else strip_fragment(start_final_url)
    )
    collection = collection_for_profile(book, profile)
    item["output"]["collection"] = collection
    item["output"]["base_dir"] = collection_base_label(output_dir, collection)

    page_queue: collections.deque[str] = collections.deque([effective_start_url])
    queued_pages: set[str] = {effective_start_url}
    seen_pages: set[str] = set()
    seen_assets: set[str] = set()
    saved_pages: set[str] = set()
    required_pages: set[str] = set()
    files: list[dict[str, Any]] = []
    robots_skipped: list[str] = []
    page_errors: list[str] = []
    required_page_errors: list[str] = []
    optional_page_errors: list[str] = []
    asset_errors: list[str] = []
    book_dir = collection_base_dir(output_dir, collection) / book.slug
    book_root = book_root_path(effective_start_url)
    effective_root = book_root_url(effective_start_url)
    prefetched_pages: dict[str, tuple[bytes, str | None, str | None]] = {
        effective_start_url: (start_body, start_content_type, start_charset)
    }
    javadoc_support_seeded = False

    if profile in {OHC_PROFILE, JAVADOC_PROFILE}:
        required_pages.add(effective_start_url)

    def enqueue_page(candidate_url: str, *, required: bool = False) -> None:
        candidate_url = strip_fragment(candidate_url)
        if not is_allowed_page_url(effective_start_url, candidate_url, book_root):
            return
        if required:
            required_pages.add(candidate_url)
        if candidate_url not in seen_pages and candidate_url not in queued_pages:
            page_queue.append(candidate_url)
            queued_pages.add(candidate_url)

    def enqueue_asset(candidate_url: str) -> None:
        candidate_url = strip_fragment(candidate_url)
        allowed = is_allowed_asset_url(effective_start_url, candidate_url, book_root) or (
            profile == JAVADOC_PROFILE and javadoc_support_asset_url(effective_start_url, candidate_url, book_root)
        )
        if allowed and candidate_url not in seen_assets:
            seen_assets.add(candidate_url)

    def download_asset(asset_url: str) -> None:
        if not robots.can_fetch(asset_url):
            robots_skipped.append(asset_url)
            return
        try:
            asset_body, _, _ = fetch_url(
                asset_url,
                user_agent=args.user_agent,
                timeout=args.timeout,
                retries=args.retries,
            )
            asset_rel_path = local_rel_path_for_url(asset_url, book_root, is_page=False)
            asset_info = write_bytes(book_dir / asset_rel_path, asset_body)
            files.append({"path": f"{book.slug}/{asset_rel_path}", **asset_info, "url": asset_url})
        except CollectorError as exc:
            asset_errors.append(str(exc))

    while page_queue:
        page_url = page_queue.popleft()
        page_url = strip_fragment(page_url)
        queued_pages.discard(page_url)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)

        if not is_allowed_page_url(effective_start_url, page_url, book_root):
            continue
        if not robots.can_fetch(page_url):
            robots_skipped.append(page_url)
            if page_url in required_pages:
                required_page_errors.append(f"robots.txt disallowed required page {page_url}")
            continue

        if len(seen_pages) > 1 and args.delay > 0:
            time.sleep(args.delay)

        try:
            if page_url in prefetched_pages:
                body, content_type, charset = prefetched_pages[page_url]
            else:
                body, content_type, charset = fetch_url(
                    page_url,
                    user_agent=args.user_agent,
                    timeout=args.timeout,
                    retries=args.retries,
                )
            rel_path = local_rel_path_for_url(page_url, book_root, is_page=True)
            file_info = write_bytes(book_dir / rel_path, body)
            files.append({"path": f"{book.slug}/{rel_path}", **file_info, "url": page_url})
            saved_pages.add(page_url)

            if content_type and "html" not in content_type:
                continue
            text = decode_body(body, charset)
            parser = PageReferenceParser(page_url)
            parser.feed(text)

            if profile == JAVADOC_PROFILE and not javadoc_support_seeded:
                for support_file in sorted(JAVADOC_SUPPORT_FILES):
                    enqueue_asset(urljoin(effective_root, support_file))
                javadoc_support_seeded = True

            for link in sorted(parser.page_links):
                link = strip_fragment(link)
                required = profile == JAVADOC_PROFILE or (
                    profile == OHC_PROFILE and (is_toc_url(link) or is_toc_url(page_url))
                )
                enqueue_page(link, required=required)

            for asset_url in sorted(parser.asset_links):
                enqueue_asset(asset_url)
        except CollectorError as exc:
            error = str(exc)
            page_errors.append(error)
            if page_url in required_pages or profile in {JAVADOC_PROFILE, GENERIC_PROFILE}:
                required_page_errors.append(error)
            else:
                optional_page_errors.append(error)

    for asset_url in sorted(seen_assets):
        download_asset(asset_url)

    total_bytes = sum(int(record["bytes"]) for record in files)
    output = {
        "paths": [record["path"] for record in files],
        "bytes": total_bytes,
        "sha256": aggregate_file_hash(files) if files else None,
        "files": files,
    }
    apply_output_collection(output, book, output_dir, collection)
    item["output"] = output
    html_records = [record for record in files if is_html_file_record(record)]
    content_records = [record for record in files if is_content_html_record(record)]
    required_pages_saved = sorted(required_pages & saved_pages)
    required_pages_missing = sorted(required_pages - saved_pages)
    validation_errors: list[str] = []
    if profile == OHC_PROFILE:
        if not any(is_toc_url(record["url"]) for record in html_records):
            validation_errors.append("OHC table of contents was not saved.")
        if not content_records:
            validation_errors.append("OHC crawl saved no content pages beyond index/toc.")
        if required_pages_missing:
            validation_errors.append(f"{len(required_pages_missing)} required OHC page(s) were not saved.")
        validation_errors.extend(required_page_errors)
    elif profile == JAVADOC_PROFILE:
        javadoc_content = [
            record
            for record in html_records
            if Path(str(record.get("path") or "")).name.lower() not in {"index.html"}
        ]
        if not javadoc_content:
            validation_errors.append("Javadoc crawl saved no API pages beyond the landing page.")
        if required_pages_missing:
            validation_errors.append(f"{len(required_pages_missing)} required Javadoc page(s) were not saved.")
        validation_errors.extend(required_page_errors)
    else:
        if not files:
            validation_errors.append("No HTML files were saved.")
        validation_errors.extend(required_page_errors)

    item["html_profile"] = {
        "profile": profile,
        "start_url": strip_fragment(book.html_url),
        "effective_start_url": effective_start_url,
        "effective_root": effective_root,
        "required_pages": sorted(required_pages),
        "required_pages_saved": required_pages_saved,
        "required_pages_missing": required_pages_missing[:50],
        "html_pages_saved": len(html_records),
        "content_pages_saved": len(content_records),
        "asset_warnings": asset_errors,
        "validation_status": "success" if not validation_errors else "failed",
        "validation_errors": validation_errors,
    }
    item["crawl"] = {
        "pages_seen": len(seen_pages),
        "files_saved": len(files),
        "robots_skipped": robots_skipped,
        "asset_errors": asset_errors,
        "optional_page_errors": optional_page_errors,
    }
    item["errors"] = validation_errors + [
        f"robots.txt disallowed {url}" for url in robots_skipped if url in required_pages
    ]
    item["warnings"] = asset_errors + optional_page_errors + [
        f"robots.txt disallowed {url}" for url in robots_skipped if url not in required_pages
    ]
    if files and not item["errors"]:
        item["status"] = "success"
    elif files:
        item["status"] = "failed"
    elif robots_skipped:
        item["status"] = "skipped"
    else:
        item["status"] = "failed"
        if not item["errors"]:
            item["errors"] = ["No HTML files were saved."]
    return item


def selected_repair_slugs(args: argparse.Namespace) -> set[str]:
    values = args.repair_slug or []
    slugs: set[str] = set()
    for value in values:
        for part in value.split(","):
            slug = part.strip()
            if slug:
                slugs.add(slug)
    return slugs


def repair_mode_enabled(args: argparse.Namespace) -> bool:
    return bool(selected_repair_slugs(args) or args.repair_suspicious_html)


def safe_remove_dir(path: Path, required_parent: Path) -> None:
    if not path.exists():
        return
    resolved_path = path.resolve()
    resolved_parent = required_parent.resolve()
    if resolved_path == resolved_parent or resolved_parent not in resolved_path.parents:
        raise CollectorError(f"Refusing to delete path outside expected parent: {resolved_path}")
    shutil.rmtree(resolved_path)


def safe_remove_file(path: Path, required_parent: Path) -> None:
    if not path.exists():
        return
    resolved_path = path.resolve()
    resolved_parent = required_parent.resolve()
    if resolved_parent not in resolved_path.parents:
        raise CollectorError(f"Refusing to delete file outside expected parent: {resolved_path}")
    if resolved_path.is_dir():
        raise CollectorError(f"Refusing to remove directory with file remover: {resolved_path}")
    resolved_path.unlink()


def repair_timestamp() -> str:
    return utc_now().replace(":", "").replace("-", "").replace("Z", "Z")


def prior_file_count(prior_item: dict[str, Any] | None) -> int:
    if not prior_item:
        return 0
    files = ((prior_item.get("output") or {}).get("files") or [])
    if files:
        return len(files)
    return len((prior_item.get("output") or {}).get("paths") or [])


def add_repair_history(
    item: dict[str, Any],
    prior_item: dict[str, Any] | None,
    *,
    timestamp: str,
    mode: str,
    profile: str | None,
    replaced: bool,
    diagnostic_path: str | None = None,
) -> dict[str, Any]:
    history = list((prior_item or {}).get("repair_history") or [])
    record: dict[str, Any] = {
        "timestamp": timestamp,
        "mode": mode,
        "profile": profile,
        "prior_status": (prior_item or {}).get("status"),
        "prior_file_count": prior_file_count(prior_item),
        "replaced_old_folder": replaced,
    }
    if diagnostic_path:
        record["diagnostic_path"] = diagnostic_path
    history.append(record)
    item["repair_history"] = history
    return item


def replace_repaired_item_folder(
    *,
    book: Book,
    staged_item: dict[str, Any],
    prior_item: dict[str, Any] | None,
    stage_version_dir: Path,
    output_dir: Path,
) -> None:
    collection = (staged_item.get("output") or {}).get("collection", collection_for_book(book))
    output = staged_item.get("output") or {}
    active_base = collection_base_dir(output_dir, collection)

    if output.get("acquisition_method") == "versioned_latest_pdf":
        paths = output.get("paths") or []
        if len(paths) != 1:
            raise CollectorError(f"Expected one staged PDF path for {book.slug}, found {len(paths)}")
        staged_file = collection_base_dir(stage_version_dir, collection) / paths[0]
        active_file = active_base / paths[0]
        if not staged_file.is_file():
            raise CollectorError(f"Staged repair PDF is missing: {staged_file}")
        safe_remove_file(active_file, active_base)
        if prior_item:
            prior_folder = output_base_dir_for_item(output_dir, prior_item) / book.slug
            safe_remove_dir(prior_folder, output_dir.parent)
        active_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_file), str(active_file))
        return

    staged_folder = collection_base_dir(stage_version_dir, collection) / book.slug
    active_folder = collection_base_dir(output_dir, collection) / book.slug
    if not staged_folder.exists():
        raise CollectorError(f"Staged repair output is missing: {staged_folder}")

    safe_remove_dir(active_folder, active_base)

    if prior_item:
        prior_folder = output_base_dir_for_item(output_dir, prior_item) / book.slug
        if prior_folder.resolve() != active_folder.resolve():
            safe_remove_dir(prior_folder, output_dir.parent)

    active_folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged_folder), str(active_folder))


def move_failed_repair_stage(stage_root: Path, output_dir: Path, timestamp: str, slug: str) -> Path:
    failed_root = output_dir.parent / "_repair_failed" / output_dir.name
    failed_root.mkdir(parents=True, exist_ok=True)
    target = failed_root / f"{slug}-{timestamp}"
    if target.exists():
        digest = hashlib.sha1(str(stage_root).encode("utf-8")).hexdigest()[:8]
        target = failed_root / f"{slug}-{timestamp}-{digest}"
    shutil.move(str(stage_root), str(target))
    return target


def repair_html_book(
    book: Book,
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
    prior_manifest: dict[str, Any] | None,
    *,
    mode: str,
) -> dict[str, Any]:
    prior_item = find_prior_item(prior_manifest, book.slug)
    timestamp = repair_timestamp()
    stage_root = output_dir.parent / "_repair_stage" / output_dir.name / f"{book.slug}-{timestamp}"
    stage_version_dir = stage_root / output_dir.name
    safe_remove_dir(stage_root, output_dir.parent / "_repair_stage")

    staged_item = collect_html_book(book, stage_version_dir, args, robots, None, force_collect=True)
    profile = (staged_item.get("html_profile") or {}).get("profile")

    if staged_item.get("status") == "success":
        replace_repaired_item_folder(
            book=book,
            staged_item=staged_item,
            prior_item=prior_item,
            stage_version_dir=stage_version_dir,
            output_dir=output_dir,
        )
        safe_remove_dir(stage_root, output_dir.parent / "_repair_stage")
        return add_repair_history(
            staged_item,
            prior_item,
            timestamp=timestamp,
            mode=mode,
            profile=profile,
            replaced=True,
        )

    diagnostic_path = move_failed_repair_stage(stage_root, output_dir, timestamp, book.slug)
    if prior_item:
        kept_item = dict(prior_item)
        kept_item["repair_attempt_failed"] = {
            "timestamp": timestamp,
            "diagnostic_path": str(diagnostic_path),
            "errors": staged_item.get("errors", []),
        }
        return add_repair_history(
            kept_item,
            prior_item,
            timestamp=timestamp,
            mode=mode,
            profile=profile,
            replaced=False,
            diagnostic_path=str(diagnostic_path),
        )

    staged_item["repair_attempt_failed"] = {"timestamp": timestamp, "diagnostic_path": str(diagnostic_path)}
    return add_repair_history(
        staged_item,
        prior_item,
        timestamp=timestamp,
        mode=mode,
        profile=profile,
        replaced=False,
        diagnostic_path=str(diagnostic_path),
    )


def strip_fragment(url: str) -> str:
    return urldefrag(url)[0]


def same_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left)
    right_parsed = urlparse(right)
    return (left_parsed.scheme, left_parsed.netloc) == (right_parsed.scheme, right_parsed.netloc)


def book_root_path(start_url: str) -> str:
    path = urlparse(start_url).path
    if path.endswith("/"):
        return path
    return path.rsplit("/", 1)[0] + "/"


def book_root_url(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}{book_root_path(start_url)}"


def looks_like_html_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return path.endswith("/") or suffix in {"", ".html", ".htm", ".xhtml"}


def is_allowed_page_url(start_url: str, candidate_url: str, root_path: str) -> bool:
    parsed = urlparse(candidate_url)
    return (
        parsed.scheme in {"http", "https"}
        and same_origin(start_url, candidate_url)
        and parsed.path.startswith(root_path)
        and looks_like_html_path(parsed.path)
    )


def is_allowed_asset_url(start_url: str, candidate_url: str, root_path: str) -> bool:
    parsed = urlparse(candidate_url)
    if parsed.scheme not in {"http", "https"} or not same_origin(start_url, candidate_url):
        return False
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in ASSET_EXTENSIONS:
        return False
    if parsed.path.startswith(root_path):
        return True
    return any(marker in parsed.path for marker in STATIC_ASSET_PATH_MARKERS)


def is_allowed_text_asset_url(start_url: str, candidate_url: str, root_path: str) -> bool:
    parsed = urlparse(candidate_url)
    if parsed.scheme not in {"http", "https"} or not same_origin(start_url, candidate_url):
        return False
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".js", ".mjs", ".json"}:
        return False
    if parsed.path.startswith(root_path):
        return True
    return any(marker in parsed.path for marker in STATIC_ASSET_PATH_MARKERS)


def is_image_relevant_script_url(candidate_url: str) -> bool:
    basename = Path(urlparse(candidate_url).path).name.lower()
    return basename in IMAGE_SCRIPT_BASENAMES or "config" in basename or "manifest" in basename


def is_allowed_svg_url(start_url: str, candidate_url: str, root_path: str) -> bool:
    parsed = urlparse(candidate_url)
    basename = Path(parsed.path).name.lower()
    return (
        parsed.scheme in {"http", "https"}
        and same_origin(start_url, candidate_url)
        and parsed.path.startswith(f"{root_path}images/")
        and Path(parsed.path).suffix.lower() == ".svg"
        and basename not in IGNORED_SVG_BASENAMES
    )


def safe_path_segment(value: str) -> str:
    segment = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    return segment or "_"


def local_rel_path_for_url(url: str, root_path: str, *, is_page: bool) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if path.startswith(root_path):
        rel = path[len(root_path) :]
    else:
        rel = "_assets/" + path.lstrip("/")
    if not rel or rel.endswith("/"):
        rel += "index.html"
    elif is_page and not Path(rel).suffix:
        rel = rel.rstrip("/") + "/index.html"

    parts = [safe_path_segment(part) for part in rel.split("/") if part]
    if parsed.query:
        digest = hashlib.sha1(parsed.query.encode("utf-8")).hexdigest()[:8]
        last = parts[-1]
        suffix = Path(last).suffix
        stem = last[: -len(suffix)] if suffix else last
        parts[-1] = f"{stem}-{digest}{suffix}"
    return "/".join(parts)


def extract_meta_refresh_urls(text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in META_TAG_PATTERN.finditer(text):
        attrs = {
            name.lower(): value
            for name, _, value in ATTR_PATTERN.findall(match.group(0))
        }
        if attrs.get("http-equiv", "").lower() != "refresh":
            continue
        content = attrs.get("content", "")
        url_match = re.search(r"url\s*=\s*([^;]+)", content, flags=re.IGNORECASE)
        if not url_match:
            continue
        target = url_match.group(1).strip().strip("'\"")
        if target:
            urls.append(urljoin(base_url, target))
    return urls


def extract_svg_urls(text: str, base_url: str, start_url: str, root_path: str) -> set[str]:
    urls: set[str] = set()
    unescaped = html.unescape(text)
    for match in SVG_URL_PATTERN.finditer(unescaped):
        raw_url = match.group("url").strip().strip("'\";,.")
        if not raw_url or raw_url.lower().startswith(("data:", "javascript:")):
            continue
        candidate = strip_fragment(urljoin(base_url, raw_url))
        if is_allowed_svg_url(start_url, candidate, root_path):
            urls.add(candidate)
    return urls


def extract_html_urls(text: str, base_url: str, start_url: str, root_path: str) -> set[str]:
    urls: set[str] = set()
    unescaped = html.unescape(text)
    for match in HTML_URL_PATTERN.finditer(unescaped):
        raw_url = match.group("url").strip().strip("'\";,.")
        raw_url = re.sub(r"(?i)^url\s*=\s*", "", raw_url)
        if not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html", raw_url):
            continue
        if not raw_url or raw_url.lower().startswith(("data:", "javascript:")):
            continue
        candidate = strip_fragment(urljoin(base_url, raw_url))
        if is_allowed_page_url(start_url, candidate, root_path):
            urls.add(candidate)
    return urls


def normalize_require_module(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "http:", "https:", "//")):
        return None
    if "!" in value:
        plugin, resource = value.split("!", 1)
        if not resource:
            return None
        value = resource if Path(resource).suffix else plugin
    suffix = Path(value).suffix.lower()
    if suffix and suffix not in {".js", ".mjs"}:
        return None
    if not suffix:
        value = f"{value}.js"
    return value


def require_module_url(value: str, base_url: str, start_url: str, root_path: str) -> str | None:
    module = normalize_require_module(value)
    if not module:
        return None
    candidate = strip_fragment(urljoin(base_url, module))
    return candidate if is_allowed_text_asset_url(start_url, candidate, root_path) else None


def extract_require_module_urls(text: str, base_url: str, start_url: str, root_path: str) -> set[str]:
    urls: set[str] = set()
    unescaped = html.unescape(text)
    dep_blocks = [match.group("deps") for match in REQUIRE_DEPS_PATTERN.finditer(unescaped)]
    dep_blocks.extend(match.group("deps") for match in REQUIRE_CONFIG_DEPS_PATTERN.finditer(unescaped))
    for dep_block in dep_blocks:
        for match in QUOTED_VALUE_PATTERN.finditer(dep_block):
            module_url = require_module_url(match.group("value"), base_url, start_url, root_path)
            if module_url:
                urls.add(module_url)
    for paths_block in REQUIRE_PATHS_BLOCK_PATTERN.finditer(unescaped):
        for match in REQUIRE_PATH_PATTERN.finditer(paths_block.group("paths")):
            module_url = require_module_url(match.group("path"), base_url, start_url, root_path)
            if module_url:
                urls.add(module_url)
    return urls


def derived_svg_url_for_page(page_url: str, start_url: str, root_path: str) -> str | None:
    parsed = urlparse(page_url)
    stem = Path(parsed.path).stem
    if not stem or stem.lower() in {"index", "all_diagrams", "all-diagrams"}:
        return None
    candidate = strip_fragment(urljoin(page_url, f"images/{stem}.svg"))
    return candidate if is_allowed_svg_url(start_url, candidate, root_path) else None


def svg_basename_for_url(url: str) -> str:
    name = safe_path_segment(unquote(Path(urlparse(url).path).name))
    if not name.lower().endswith(".svg"):
        name = f"{name}.svg"
    return name or "diagram.svg"


def svg_conflict_name(basename: str, url: str) -> str:
    suffix = Path(basename).suffix or ".svg"
    stem = basename[: -len(suffix)] if basename.endswith(suffix) else basename
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}{suffix}"


def download_image_svgs(
    book: Book,
    svg_urls: list[str],
    output_dir: Path,
    args: argparse.Namespace,
    robots: RobotsCache,
) -> dict[str, Any]:
    image_dir = collection_base_dir(output_dir, "images")
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    duplicates: list[dict[str, Any]] = []
    hash_to_path: dict[str, str] = {}
    basename_to_hash: dict[str, str] = {}
    existing_paths = {path.name for path in image_dir.glob("*.svg")} if image_dir.exists() else set()
    used_paths: set[str] = set(existing_paths)

    for svg_url in svg_urls:
        if not robots.can_fetch(svg_url):
            errors.append(f"robots.txt disallowed {svg_url}")
            continue
        try:
            body, _, _, final_url = fetch_url_details(
                svg_url,
                user_agent=args.user_agent,
                timeout=args.timeout,
                retries=args.retries,
            )
        except CollectorError as exc:
            errors.append(str(exc))
            continue

        digest = hashlib.sha256(body).hexdigest()
        if digest in hash_to_path:
            duplicates.append({"url": svg_url, "duplicate_of": hash_to_path[digest], "sha256": digest})
            continue

        basename = svg_basename_for_url(final_url)
        if basename in basename_to_hash and basename_to_hash[basename] != digest:
            local_name = svg_conflict_name(basename, final_url)
        else:
            local_name = basename
        target = image_dir / local_name
        if target.exists():
            existing_hash = sha256_file(target)
            if existing_hash == digest:
                files.append(
                    {
                        "path": local_name,
                        "bytes": target.stat().st_size,
                        "sha256": existing_hash,
                        "url": final_url,
                        "reuse": "existing_svg_same_hash",
                    }
                )
                hash_to_path[digest] = local_name
                basename_to_hash[basename] = digest
                continue
            local_name = svg_conflict_name(basename, final_url)
        while local_name in used_paths:
            local_name = svg_conflict_name(local_name, f"{final_url}:{len(used_paths)}")

        target = image_dir / local_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        file_info = {"path": local_name, "bytes": target.stat().st_size, "sha256": sha256_file(target), "url": final_url}
        files.append(file_info)
        used_paths.add(local_name)
        hash_to_path[digest] = local_name
        basename_to_hash[basename] = digest

    return {"files": files, "errors": errors, "duplicates": duplicates}


def delete_old_shallow_folder(book: Book, output_dir: Path) -> dict[str, Any] | None:
    old_dir = output_dir / book.slug
    if not old_dir.exists():
        return None
    resolved_output = output_dir.resolve()
    resolved_old = old_dir.resolve()
    if resolved_old == resolved_output or resolved_output not in resolved_old.parents:
        raise CollectorError(f"Refusing to delete path outside output version directory: {resolved_old}")
    shutil.rmtree(resolved_old)
    return {"deleted_old_folder": book.slug}


def build_manifest(
    *,
    books: list[Book],
    items: list[dict[str, Any]],
    bookshelf_url: str,
    version: str,
    zip_url: str | None,
    started_at: str,
    finished_at: str,
    dry_run: bool,
    repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = determine_run_status(items, dry_run)
    run: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": dry_run,
        "status": status,
        "item_count": len(books),
    }
    if repair:
        run["repair"] = repair
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "source": {
            "bookshelf_url": bookshelf_url,
            "version": version,
            "zip_url": zip_url,
            "zip_deferred": True,
        },
        "run": run,
        "items": items,
        "deferred": DEFERRED_DECISIONS,
    }


def determine_run_status(items: list[dict[str, Any]], dry_run: bool) -> str:
    if not items:
        return "failed"
    statuses = {item.get("status") for item in items}
    if dry_run and statuses <= {"planned"}:
        return "success"
    if not dry_run and "planned" in statuses:
        return "partial_failure"
    if statuses & {"failed", "skipped"}:
        return "partial_failure"
    return "success"


def atomic_write_text(path: Path, text: str) -> None:
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def write_manifest_and_summary(manifest: dict[str, Any], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    summary_path = output_dir / "summary.txt"
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    summary = build_summary(manifest)
    atomic_write_text(summary_path, summary)
    return summary


def build_summary(manifest: dict[str, Any]) -> str:
    items = manifest["items"]
    status_counts = collections.Counter(item["status"] for item in items)
    method_counts = collections.Counter(item["method"] for item in items)
    failures = [item for item in items if item["status"] in {"failed", "skipped"}]
    robots_skips = []
    for item in items:
        robots_skips.extend((item.get("crawl") or {}).get("robots_skipped", []))

    lines = [
        f"Skill: {manifest['skill']}",
        f"Bookshelf URL: {manifest['source']['bookshelf_url']}",
        f"Version: {manifest['source']['version']}",
        f"Dry run: {manifest['run']['dry_run']}",
        f"Run status: {manifest['run']['status']}",
        f"Item count: {len(items)}",
        "",
        "Methods:",
    ]
    for method, count in sorted(method_counts.items()):
        lines.append(f"  {method}: {count}")
    lines.append("")
    lines.append("Collections:")
    collection_counts = collections.Counter((item.get("output") or {}).get("collection", "docs") for item in items)
    for collection, count in sorted(collection_counts.items()):
        lines.append(f"  {collection}: {count}")

    lines.append("")
    lines.append("Statuses:")
    for status, count in sorted(status_counts.items()):
        lines.append(f"  {status}: {count}")

    repair = manifest.get("run", {}).get("repair")
    if repair:
        lines.append("")
        lines.append("Repair:")
        lines.append(f"  mode: {repair.get('mode')}")
        lines.append(f"  targeted: {repair.get('targeted', 0)}")
        lines.append(f"  succeeded: {repair.get('succeeded', 0)}")
        lines.append(f"  failed: {repair.get('failed', 0)}")
        targets = repair.get("targets") or []
        if targets:
            lines.append("  targets:")
            for target in targets:
                lines.append(f"    - {target}")

    lines.append("")
    lines.append(f"Zip URL: {manifest['source'].get('zip_url') or 'not found'}")
    lines.append(f"Zip deferred: {manifest['source'].get('zip_deferred')}")

    if failures:
        lines.append("")
        lines.append("Failures and skips:")
        for item in failures:
            first_error = item.get("errors", ["No error detail"])[0]
            lines.append(f"  - {item['title']} [{item['status']}]: {first_error}")

    if robots_skips:
        lines.append("")
        lines.append("Robots skips:")
        for url in robots_skips:
            lines.append(f"  - {url}")

    lines.append("")
    lines.append("Deferred:")
    for decision in manifest.get("deferred", []):
        lines.append(f"  - {decision}")
    lines.append("")
    return "\n".join(lines)


def collect(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    started_at = utc_now()
    version = validate_bookshelf_url(args.bookshelf_url)
    output_dir = Path(args.output_root).resolve() / version
    html_text = read_bookshelf_html(args)
    books, zip_url = parse_bookshelf(html_text, args.bookshelf_url)

    if args.dry_run:
        items = [build_planned_item(book, output_dir) for book in books]
    elif repair_mode_enabled(args):
        prior_manifest = load_prior_manifest(output_dir)
        robots = RobotsCache(args.user_agent, args.timeout, args.retries)
        explicit_slugs = selected_repair_slugs(args)
        updated_by_slug: dict[str, dict[str, Any]] = {
            item.get("slug"): item
            for item in (prior_manifest or {}).get("items", [])
            if item.get("slug")
        }
        targets: list[Book] = []
        for book in books:
            prior_item = find_prior_item(prior_manifest, book.slug)
            explicit = book.slug in explicit_slugs
            suspicious = args.repair_suspicious_html and is_suspicious_html_item(prior_item)
            if book.method == "html_crawl" and collection_for_book(book) != "images" and (explicit or suspicious):
                targets.append(book)

        if explicit_slugs:
            known_slugs = {book.slug for book in books}
            unknown = sorted(explicit_slugs - known_slugs)
            if unknown:
                raise CollectorError(f"Repair slug(s) not found on bookshelf: {', '.join(unknown)}")
        if not targets:
            raise CollectorError("Repair mode selected no HTML items to repair.")

        succeeded = 0
        failed = 0
        mode = "slug" if explicit_slugs and not args.repair_suspicious_html else "suspicious_html"
        for book in targets:
            repaired_item = repair_html_book(book, output_dir, args, robots, prior_manifest, mode=mode)
            updated_by_slug[book.slug] = repaired_item
            if repaired_item.get("status") == "success":
                succeeded += 1
            else:
                failed += 1

            checkpoint_items = [
                updated_by_slug.get(book.slug) or build_planned_item(book, output_dir)
                for book in books
            ]
            checkpoint_repair = {
                "mode": mode,
                "targeted": len(targets),
                "succeeded": succeeded,
                "failed": failed,
                "targets": [target.slug for target in targets],
            }
            checkpoint_manifest = build_manifest(
                books=books,
                items=checkpoint_items,
                bookshelf_url=args.bookshelf_url,
                version=version,
                zip_url=zip_url,
                started_at=started_at,
                finished_at=utc_now(),
                dry_run=False,
                repair=checkpoint_repair,
            )
            write_manifest_and_summary(checkpoint_manifest, output_dir)

        items = [
            updated_by_slug.get(book.slug) or build_planned_item(book, output_dir)
            for book in books
        ]
        repair = {
            "mode": mode,
            "targeted": len(targets),
            "succeeded": succeeded,
            "failed": failed,
            "targets": [target.slug for target in targets],
        }
    else:
        prior_manifest = load_prior_manifest(output_dir)
        robots = RobotsCache(args.user_agent, args.timeout, args.retries)
        items = []
        for index, book in enumerate(books):
            if book.method == "pdf":
                items.append(collect_pdf(book, output_dir, args, robots, prior_manifest))
            elif collection_for_book(book) == "images":
                items.append(collect_image_book(book, output_dir, args, robots, prior_manifest))
            else:
                items.append(collect_html_book(book, output_dir, args, robots, prior_manifest))
            checkpoint_items = items + [build_planned_item(item, output_dir) for item in books[index + 1 :]]
            checkpoint_manifest = build_manifest(
                books=books,
                items=checkpoint_items,
                bookshelf_url=args.bookshelf_url,
                version=version,
                zip_url=zip_url,
                started_at=started_at,
                finished_at=utc_now(),
                dry_run=False,
            )
            write_manifest_and_summary(checkpoint_manifest, output_dir)
        repair = None

    finished_at = utc_now()
    manifest = build_manifest(
        books=books,
        items=items,
        bookshelf_url=args.bookshelf_url,
        version=version,
        zip_url=zip_url,
        started_at=started_at,
        finished_at=finished_at,
        dry_run=args.dry_run,
        repair=repair if not args.dry_run else None,
    )
    return manifest, output_dir


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bookshelf_url", help="One exact supported Oracle Database bookshelf URL.")
    parser.add_argument("--dry-run", action="store_true", help="Plan the run without downloading book contents.")
    parser.add_argument("--fixture", help="Read bookshelf HTML from a local fixture instead of the network.")
    parser.add_argument("--output-root", default="raw", help="Output root directory. Defaults to ./raw.")
    parser.add_argument("--force", action="store_true", help="Redownload even when prior manifest entries match local files.")
    parser.add_argument(
        "--repair-slug",
        action="append",
        help="Repair one or more comma-separated HTML item slugs using staged profile-aware crawling.",
    )
    parser.add_argument(
        "--repair-suspicious-html",
        action="store_true",
        help="Repair prior non-image HTML crawls that look incomplete by profile evidence or file counts.",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between HTML page requests.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry count for transient HTTP failures.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP user agent.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        manifest, output_dir = collect(args)
        summary = write_manifest_and_summary(manifest, output_dir)
        print(summary)
        repair = manifest.get("run", {}).get("repair")
        if repair:
            return 0 if int(repair.get("failed") or 0) == 0 else 1
        if not args.dry_run and manifest["run"]["status"] != "success":
            return 1
        return 0 if manifest["run"]["status"] == "success" else 1
    except CollectorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
