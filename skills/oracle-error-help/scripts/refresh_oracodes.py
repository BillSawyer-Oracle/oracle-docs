#!/usr/bin/env python3
"""Refresh references/oracodes.csv from Oracle's official Error Help TOC.

Oracle publishes the master list of error-code prefix families in the
sidebar Table of Contents on every error-help index page (e.g.,
https://docs.oracle.com/en/error-help/db/acfs-index.html). Running this
script extracts that list and writes a clean, BOM-free, LF-terminated
oracodes.csv. Use this whenever you suspect the prefix list is stale —
e.g., a known-valid Oracle prefix is being rejected, or every few months
as a maintenance step.

Usage:
    python scripts/refresh_oracodes.py                         # uses default URL
    python scripts/refresh_oracodes.py --url <other-index-url>
    python scripts/refresh_oracodes.py --output some/path.csv  # custom output
    python scripts/refresh_oracodes.py --dry-run               # show diff, don't write

The script depends only on the Python standard library (no pip installs
required).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://docs.oracle.com/en/error-help/db/acfs-index.html"
USER_AGENT = "oracle-error-help-refresh/1.0 (+skill maintenance script)"


def fetch(url: str, timeout: int = 30) -> str:
    """Fetch a URL and return its body decoded as UTF-8."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_prefixes(html: str) -> list[str]:
    """Extract prefix family labels from the TOC navigation.

    The TOC lives inside `<div role="navigation" aria-label="Table of
    Contents">` and consists of `<a href="<prefix>-index.html">LABEL</a>`
    entries. We use the link's anchor text as the canonical prefix because
    it preserves casing and punctuation (e.g., `SQL*LOADER` keeps its
    asterisk, while the URL slug uses `sqlloader`).

    If the navigation block can't be located (page structure changed),
    we fall back to scanning the whole document for `*-index.html` links.
    The fallback is best-effort and may include false positives; the
    primary path should normally succeed.
    """
    nav_pattern = re.compile(
        r'<div\b[^>]*role="navigation"[^>]*aria-label="Table of Contents"[^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )
    nav_match = nav_pattern.search(html)
    scope = nav_match.group(1) if nav_match else html

    link_pattern = re.compile(
        r'<a\b[^>]+href="([^"]+-index\.html)"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )

    prefixes: list[str] = []
    seen: set[str] = set()
    for match in link_pattern.finditer(scope):
        label = match.group(2).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        prefixes.append(label)

    return prefixes


def read_existing(path: Path) -> list[str]:
    """Read an existing oracodes.csv, tolerating BOM and CRLF."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    # utf-8-sig strips a leading BOM if present
    text = raw.decode("utf-8-sig", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def write_csv(prefixes: list[str], path: Path) -> None:
    """Write a clean CSV: no BOM, LF line endings, one prefix per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open in binary mode to control line endings precisely
    with open(path, "wb") as f:
        for prefix in prefixes:
            f.write(prefix.encode("utf-8") + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh oracodes.csv from Oracle's error-help TOC.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Index page URL to scrape (default: {DEFAULT_URL})",
    )
    default_output = Path(__file__).resolve().parent.parent / "references" / "oracodes.csv"
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Output CSV path (default: {default_output})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the diff but do not write the output file.",
    )
    args = parser.parse_args()

    print(f"Fetching {args.url} ...", file=sys.stderr)
    try:
        html = fetch(args.url)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
        return 1

    prefixes = extract_prefixes(html)
    if not prefixes:
        print(
            "ERROR: extracted zero prefixes. Oracle may have changed the page "
            "structure; inspect the HTML and update this script.",
            file=sys.stderr,
        )
        return 2

    existing = read_existing(args.output)
    new_set = set(prefixes)
    old_set = set(existing)
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)

    print(f"Extracted {len(prefixes)} prefixes from TOC.", file=sys.stderr)
    print(f"Existing CSV had {len(existing)} prefixes.", file=sys.stderr)
    if added:
        print(f"  + Added ({len(added)}): {', '.join(added)}", file=sys.stderr)
    if removed:
        print(f"  - Removed ({len(removed)}): {', '.join(removed)}", file=sys.stderr)
    if not added and not removed:
        print("  (no changes)", file=sys.stderr)

    if args.dry_run:
        print("Dry run — not writing output.", file=sys.stderr)
        return 0

    write_csv(prefixes, args.output)
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
