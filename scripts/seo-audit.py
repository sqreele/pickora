#!/usr/bin/env python3
"""Fail-fast SEO checks for Pickora's source-controlled HTML."""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.links: list[str] = []
        self.h1_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.meta[key] = values.get("content") or ""
        elif tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href") or ""
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "h1":
            self.h1_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def local_target_exists(href: str) -> bool:
    path = urlparse(href).path
    if not path or path == "/" or path.startswith(("/products/", "/categories/")):
        return True
    target = FRONTEND / path.lstrip("/")
    return (
        target.is_file()
        or (target / "index.html").is_file()
        or (not target.suffix and target.with_suffix(".html").is_file())
    )


def main() -> int:
    errors: list[str] = []
    seen_titles: dict[str, Path] = {}
    seen_canonicals: dict[str, Path] = {}
    pages = sorted(FRONTEND.rglob("*.html"))
    for page in pages:
        relative = page.relative_to(ROOT)
        source = page.read_text(encoding="utf-8")
        parser = PageParser()
        parser.feed(source)
        noindex = "noindex" in parser.meta.get("robots", "").lower()
        if not parser.title.strip():
            errors.append(f"{relative}: missing title")
        elif parser.title.strip() in seen_titles:
            errors.append(
                f"{relative}: duplicate title also used by {seen_titles[parser.title.strip()]}"
            )
        else:
            seen_titles[parser.title.strip()] = relative
        if not parser.meta.get("description", "").strip():
            errors.append(f"{relative}: missing meta description")
        if not noindex and not parser.canonical:
            errors.append(f"{relative}: missing canonical")
        if parser.canonical and not parser.canonical.startswith("https://"):
            errors.append(f"{relative}: canonical must use HTTPS")
        if parser.canonical in seen_canonicals:
            errors.append(
                f"{relative}: duplicate canonical also used by {seen_canonicals[parser.canonical]}"
            )
        elif parser.canonical:
            seen_canonicals[parser.canonical] = relative
        if not noindex and parser.h1_count != 1:
            errors.append(f"{relative}: expected one h1, found {parser.h1_count}")
        for href in parser.links:
            if href.startswith("/") and not local_target_exists(href):
                errors.append(f"{relative}: broken internal link {href}")
        for block in re.findall(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            source,
            re.DOTALL,
        ):
            try:
                json.loads(block)
            except json.JSONDecodeError as error:
                errors.append(f"{relative}: invalid JSON-LD ({error})")

    if errors:
        print("\n".join(f"ERROR {error}" for error in errors))
        return 1
    print(f"SEO audit passed: {len(pages)} HTML pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
