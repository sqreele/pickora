from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
from dotenv import load_dotenv

load_dotenv()

PUBLIC_DIR = Path("/app/public")
PRODUCTS_FILE = PUBLIC_DIR / "products.json"
SITEMAP_FILE = PUBLIC_DIR / "sitemap.xml"
REPORT_FILE = PUBLIC_DIR / "link-health.json"
SAMPLE_SIZE = int(os.getenv("LINK_CHECK_SAMPLE", "20"))
TIMEOUT = float(os.getenv("LINK_CHECK_TIMEOUT", "12"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def check_url(url: str) -> tuple[int | None, str | None]:
    headers = {"User-Agent": "PickoraLinkMonitor/1.0"}
    try:
        response = requests.head(
            url, allow_redirects=True, timeout=TIMEOUT, headers=headers
        )
        if response.status_code in {403, 405}:
            response = requests.get(
                url, allow_redirects=True, timeout=TIMEOUT, headers=headers,
                stream=True,
            )
        return response.status_code, None
    except requests.RequestException as error:
        return None, type(error).__name__


def sitemap_urls() -> list[str]:
    root = ElementTree.fromstring(SITEMAP_FILE.read_text(encoding="utf-8"))
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [
        element.text for element in root.findall("s:url/s:loc", namespace)
        if element.text
    ]


def main() -> int:
    if not PRODUCTS_FILE.exists() or not SITEMAP_FILE.exists():
        logging.warning("Products or sitemap are not ready; skipping link monitor")
        return 0

    products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    internal_urls = sitemap_urls()[:SAMPLE_SIZE]
    affiliate_products = products[:SAMPLE_SIZE]
    internal_results = []
    affiliate_results = []

    for url in internal_urls:
        status, error = check_url(url)
        internal_results.append({"url": url, "status": status, "error": error})

    for product in affiliate_products:
        url = str(product.get("link") or "")
        status, error = check_url(url)
        affiliate_results.append({
            "productId": product.get("id"),
            "domain": urlparse(url).hostname,
            "status": status,
            "error": error,
        })

    def healthy(item: dict[str, object]) -> bool:
        status = item.get("status")
        return isinstance(status, int) and 200 <= status < 400

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "healthy"
            if all(healthy(item) for item in internal_results + affiliate_results)
            else "degraded"
        ),
        "internal": {
            "checked": len(internal_results),
            "healthy": sum(healthy(item) for item in internal_results),
            "results": internal_results,
        },
        "affiliate": {
            "checked": len(affiliate_results),
            "healthy": sum(healthy(item) for item in affiliate_results),
            "results": affiliate_results,
        },
    }
    temporary = REPORT_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(REPORT_FILE)
    logging.info("Link monitor completed: %s", report["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
