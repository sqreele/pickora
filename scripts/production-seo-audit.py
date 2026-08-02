#!/usr/bin/env python3
"""Check essential SEO signals on a deployed Pickora site."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.h1_count = 0
        self.json_ld_count = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta" and values.get("name", "").lower() == "description":
            self.description = values.get("content", "")
        elif tag == "link" and "canonical" in (values.get("rel") or "").split():
            self.canonical = values.get("href", "")
        elif tag == "script" and values.get("type") == "application/ld+json":
            self.json_ld_count += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def fetch(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "PickoraSEOAudit/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8"), response.headers.get_content_type()


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "https://pickora.hotelcarepro.com").rstrip("/")
    errors: list[str] = []
    try:
        robots, _ = fetch(f"{base}/robots.txt")
        if f"Sitemap: {base}/sitemap.xml" not in robots:
            errors.append("robots.txt does not advertise the canonical sitemap")
        sitemap, content_type = fetch(f"{base}/sitemap.xml")
        if content_type not in {"application/xml", "text/xml"}:
            errors.append(f"sitemap has unexpected content type: {content_type}")
        root = ET.fromstring(sitemap)
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [item.text or "" for item in root.findall("s:url/s:loc", namespace)]
        if not urls or len(urls) > 50_000:
            errors.append(f"sitemap URL count is invalid: {len(urls)}")
        if len(urls) != len(set(urls)):
            errors.append("sitemap contains duplicate URLs")
        if any(urlparse(url).scheme != "https" for url in urls):
            errors.append("sitemap contains a non-HTTPS URL")
        candidates = [f"{base}/"]
        for marker in ("/products/", "/categories/"):
            candidate = next((url for url in urls if marker in url), "")
            if candidate:
                candidates.append(candidate)
        for url in candidates:
            source, content_type = fetch(url)
            if content_type != "text/html":
                errors.append(f"{url}: expected text/html, got {content_type}")
                continue
            page = PageParser()
            page.feed(source)
            if not page.title.strip():
                errors.append(f"{url}: missing title")
            if not page.description.strip():
                errors.append(f"{url}: missing meta description")
            if page.canonical != url:
                errors.append(f"{url}: canonical mismatch ({page.canonical or 'missing'})")
            if page.h1_count != 1:
                errors.append(f"{url}: expected one h1, found {page.h1_count}")
            if not page.json_ld_count:
                errors.append(f"{url}: missing JSON-LD")
    except (urllib.error.URLError, ET.ParseError, UnicodeDecodeError) as error:
        errors.append(f"production request failed: {error}")
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors))
        return 1
    print(f"Production SEO audit passed: {len(urls)} sitemap URLs; {len(candidates)} pages sampled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
