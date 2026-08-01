from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DATA_DIR = Path("/app/data")
PUBLIC_DIR = Path("/app/public")
FEED_FILE = DATA_DIR / "shopee_feed.csv"
PRODUCTS_FILE = PUBLIC_DIR / "products.json"
STATUS_FILE = PUBLIC_DIR / "feed-status.json"
PRICE_HISTORY_FILE = PUBLIC_DIR / "price-history.json"
SEO_STATUS_FILE = PUBLIC_DIR / "seo-status.json"
SITEMAP_FILE = PUBLIC_DIR / "sitemap.xml"
PRODUCT_PAGES_DIR = PUBLIC_DIR / "products"
CATEGORY_PAGES_DIR = PUBLIC_DIR / "categories"

FEED_URL = os.getenv("SHOPEE_FEED_URL", "").strip()
SITE_URL = os.getenv("SITE_URL", "https://pickora.hotelcarepro.com").strip().rstrip("/")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "50000"))
MAX_PRODUCTS = int(os.getenv("MAX_PRODUCTS", "600"))
TOP_PER_CHUNK = int(os.getenv("TOP_PER_CHUNK", "300"))

MIN_PRICE = float(os.getenv("MIN_PRICE", "50"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "2500"))
MIN_RATING = float(os.getenv("MIN_RATING", "4.5"))
MIN_SOLD = float(os.getenv("MIN_SOLD", "0"))

INCLUDE_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv("INCLUDE_KEYWORDS", "").split(",")
    if x.strip()
]
EXCLUDE_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv("EXCLUDE_KEYWORDS", "").split(",")
    if x.strip()
]

WEIGHT_SOLD = float(os.getenv("WEIGHT_SOLD", "0.50"))
WEIGHT_RATING = float(os.getenv("WEIGHT_RATING", "1000"))
WEIGHT_COMMISSION = float(os.getenv("WEIGHT_COMMISSION", "500"))
WEIGHT_DISCOUNT = float(os.getenv("WEIGHT_DISCOUNT", "20"))

STATIC_SITEMAP_PATHS = (
    ("/", "daily", "1.0"), ("/about/", "monthly", "0.6"),
    ("/guides/", "weekly", "0.8"),
    ("/compare-products/", "monthly", "0.5"),
    ("/affiliate-disclosure/", "yearly", "0.4"),
    ("/reviews/portable-fan.html", "monthly", "0.7"),
    ("/reviews/power-bank.html", "monthly", "0.7"),
    ("/reviews/robot-vacuum.html", "monthly", "0.7"),
    ("/reviews/air-fryer.html", "monthly", "0.7"),
    ("/reviews/car-camera.html", "monthly", "0.7"),
    ("/reviews/mechanical-keyboard.html", "monthly", "0.7"),
    ("/reviews/wireless-earbuds.html", "monthly", "0.7"),
    ("/reviews/office-chair.html", "monthly", "0.7"),
    ("/reviews/smart-watch.html", "monthly", "0.7"),
    ("/reviews/water-bottle.html", "monthly", "0.7"),
    ("/compare/portable-fan-types.html", "monthly", "0.7"),
    ("/compare/power-bank-capacity.html", "monthly", "0.7"),
    ("/compare/air-fryer-vs-oven.html", "monthly", "0.7"),
)


def normalise(value: str) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )


def find_column(columns: Iterable[str], candidates: list[str]) -> str | None:
    lookup = {normalise(column): column for column in columns}
    for candidate in candidates:
        key = normalise(candidate)
        if key in lookup:
            return lookup[key]
    return None


def write_status(status: str, **extra: object) -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    temp = STATUS_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATUS_FILE)


def download_feed() -> None:
    if not FEED_URL:
        raise RuntimeError("SHOPEE_FEED_URL is missing in .env")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_status("downloading")

    logging.info("Downloading Shopee data feed")
    with requests.get(
        FEED_URL,
        stream=True,
        timeout=(30, 3600),
        allow_redirects=True,
        headers={"User-Agent": "PickoraFeed/1.0"},
    ) as response:
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(
            dir=DATA_DIR,
            delete=False,
            suffix=".download",
        ) as temporary:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    temporary.write(block)
            temporary_path = Path(temporary.name)

    if temporary_path.stat().st_size < 100:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded feed is unexpectedly small")

    temporary_path.replace(FEED_FILE)
    logging.info("Feed saved: %s (%s bytes)", FEED_FILE, FEED_FILE.stat().st_size)


def detect_encoding() -> str:
    with FEED_FILE.open("rb") as file:
        raw = file.read(200000)

    for encoding in ("utf-8-sig", "utf-8", "cp874", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "utf-8"

def detect_separator(encoding: str) -> str:
    with FEED_FILE.open(
        "r",
        encoding=encoding,
        errors="ignore",
    ) as file:
        sample = file.read(50000)

    try:
        return csv.Sniffer().sniff(
            sample,
            delimiters=",;\t|",
        ).delimiter
    except csv.Error:
        return ","
def numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("฿", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def keyword_filter(frame: pd.DataFrame, title_column: str) -> pd.DataFrame:
    title = frame[title_column].astype(str).str.lower()

    if INCLUDE_KEYWORDS:
        include_mask = title.apply(
            lambda value: any(keyword in value for keyword in INCLUDE_KEYWORDS)
        )
        frame = frame[include_mask]
        title = frame[title_column].astype(str).str.lower()

    if EXCLUDE_KEYWORDS:
        exclude_mask = title.apply(
            lambda value: any(keyword in value for keyword in EXCLUDE_KEYWORDS)
        )
        frame = frame[~exclude_mask]

    return frame


def product_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def category_id(category: str) -> str:
    return hashlib.sha256(category.encode("utf-8")).hexdigest()[:12]


def display_category(value: object) -> str:
    """Return a useful Thai category label for values supplied by the feed."""
    category = str(value).strip()
    if category.casefold() in {
        "", "nan", "none", "null", "product", "products", "foreign",
    } or category == "ต่างด้าว":
        return "สินค้าแนะนำ"
    return category


def safe_external_url(value: object) -> str:
    url = str(value).strip()
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def update_price_history(products: list[dict[str, object]]) -> None:
    try:
        history = json.loads(PRICE_HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}
    today = datetime.now(timezone.utc).date().isoformat()
    active_ids = {str(product["id"]) for product in products}
    for product in products:
        identifier = str(product["id"])
        entries = history.get(identifier, [])
        price = float(product.get("price") or 0)
        if price > 0 and (
            not entries or entries[-1].get("date") != today
            or float(entries[-1].get("price") or 0) != price
        ):
            entries.append({"date": today, "price": price})
        history[identifier] = entries[-90:]
        product["priceHistory"] = history[identifier]
    history = {key: value for key, value in history.items() if key in active_ids}
    temporary = PRICE_HISTORY_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(history, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(PRICE_HISTORY_FILE)


def product_card(product: dict[str, object]) -> str:
    title = str(product["title"])
    price = float(product.get("price") or 0)
    price_text = f"฿{price:,.0f}" if price > 0 else "ดูราคาล่าสุด"
    pickora_score = int(product.get("pickoraScore") or 0)
    return f"""<article class="card related-card">
<a class="card-image-link" href="{html.escape(str(product['detailUrl']), quote=True)}">
<img src="{html.escape(safe_external_url(product.get('image', '')), quote=True)}" alt="{html.escape(title, quote=True)}" loading="lazy" decoding="async" width="600" height="600"></a>
<div class="card-body"><div class="category">{html.escape(str(product.get('category') or 'สินค้าแนะนำ'))}</div>
<h3 class="title"><a href="{html.escape(str(product['detailUrl']), quote=True)}">{html.escape(title)}</a></h3>
<div class="score-badge" title="คำนวณจากคะแนน ยอดขาย ส่วนลด และข้อมูล Affiliate">Pickora Score {pickora_score}</div>
<div class="price">{html.escape(price_text)}</div>
<a class="primary buy" href="{html.escape(str(product['detailUrl']), quote=True)}">ดูรายละเอียด →</a></div></article>"""


def breadcrumb_schema(items: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem", "position": position,
                "name": name, "item": f"{SITE_URL}{path}",
            }
            for position, (name, path) in enumerate(items, start=1)
        ],
    }


def create_product_page(
    product: dict[str, object], related: list[dict[str, object]]
) -> str:
    title = str(product["title"]).strip()
    canonical = f"{SITE_URL}{product['detailUrl']}"
    description = (
        f"ดูราคา คะแนน ยอดขาย และรายละเอียด {title[:100]} "
        "พร้อมลิงก์ตรวจสอบราคาล่าสุดบน Shopee"
    )
    image = safe_external_url(product.get("image", ""))
    affiliate_link = safe_external_url(product.get("link", ""))
    category = str(product.get("category") or "สินค้าแนะนำ")
    shop = str(product.get("shop") or "")
    price = float(product.get("price") or 0)
    rating = float(product.get("rating") or 0)
    sold = int(float(product.get("sold") or 0))
    pickora_score = int(product.get("pickoraScore") or 0)
    price_text = f"฿{price:,.0f}" if price > 0 else "ดูราคาล่าสุด"
    meta = []
    if rating > 0:
        meta.append(f"★ {rating:g}")
    if sold > 0:
        meta.append(f"ขายแล้ว {sold:,}")
    schema = {
        "@type": "Product", "name": title, "sku": str(product["id"]),
        "image": [image], "category": category, "url": canonical,
        "description": description,
    }
    if price > 0:
        schema["offers"] = {
            "@type": "Offer", "url": canonical, "priceCurrency": "THB",
            "price": f"{price:.2f}",
        }
        if shop:
            schema["offers"]["seller"] = {
                "@type": "Organization", "name": shop,
            }
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            schema,
            breadcrumb_schema([
                ("หน้าแรก", "/"), (category, str(product["categoryUrl"])),
                (title, str(product["detailUrl"])),
            ]),
        ],
    }
    schema_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    related_html = "".join(product_card(item) for item in related)
    history = list(product.get("priceHistory") or [])
    history_rows = "".join(
        f"<li><time datetime=\"{html.escape(str(entry['date']), quote=True)}\">{html.escape(str(entry['date']))}</time><strong>฿{float(entry['price']):,.0f}</strong></li>"
        for entry in reversed(history[-12:])
    )
    product_context = json.dumps({
        "id": str(product["id"]), "title": title,
        "url": str(product["detailUrl"]), "image": image,
        "price": price, "score": pickora_score, "category": category,
    }, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title[:55])} | Pickora</title>
<meta name="description" content="{html.escape(description[:155], quote=True)}">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<meta property="og:type" content="product"><meta property="og:site_name" content="Pickora">
<meta property="og:title" content="{html.escape(title, quote=True)}"><meta property="og:description" content="{html.escape(description[:155], quote=True)}">
<meta property="og:url" content="{html.escape(canonical, quote=True)}"><meta property="og:image" content="{html.escape(image, quote=True)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{html.escape(title, quote=True)}"><meta name="twitter:image" content="{html.escape(image, quote=True)}">
<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/content.css"><link rel="stylesheet" href="/catalog.css"><link rel="stylesheet" href="/product.css">
<script type="application/ld+json">{schema_json}</script></head><body>
<div class="notice">หน้านี้มีลิงก์ Affiliate และ Pickora อาจได้รับค่าคอมมิชชัน โดยไม่มีค่าใช้จ่ายเพิ่มสำหรับผู้ซื้อ</div>
<header class="header"><div class="container nav"><a class="brand" href="/"><span class="logo">P</span><span>Pickora</span></a><nav><a href="/affiliate-disclosure/">Affiliate Disclosure</a></nav></div></header>
<main class="content-main"><article class="container product-detail">
<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">หน้าแรก</a> / <a href="{html.escape(str(product['categoryUrl']), quote=True)}">{html.escape(category)}</a> / <span aria-current="page">{html.escape(title)}</span></nav>
<div class="product-detail-grid"><img class="product-detail-image" src="{html.escape(image, quote=True)}" alt="{html.escape(title, quote=True)}" decoding="async" fetchpriority="high" width="800" height="800">
<div><span class="pill">{html.escape(category)}</span><h1>{html.escape(title)}</h1>
<p class="product-shop">{html.escape(shop)}</p><p>{html.escape(" · ".join(meta))}</p>
<div class="product-score"><strong>{pickora_score}</strong><span>Pickora Score<small>คำนวณจากคะแนน ยอดขาย ส่วนลด และข้อมูล Affiliate</small></span></div>
<div class="product-detail-price">{html.escape(price_text)}</div>
<p class="affiliate-inline">ลิงก์ด้านล่างเป็น Affiliate link ราคา สต็อก และโปรโมชันอาจเปลี่ยนแปลง โปรดตรวจสอบบนหน้าร้านก่อนสั่งซื้อ</p>
<a class="primary product-buy" href="{html.escape(affiliate_link, quote=True)}" target="_blank" rel="nofollow sponsored noopener" data-affiliate-link data-product-id="{html.escape(str(product['id']), quote=True)}" data-product-name="{html.escape(title, quote=True)}">เช็กราคาล่าสุดใน Shopee →</a>
<button class="secondary-action" type="button" data-compare-product="{html.escape(str(product['id']), quote=True)}">เพิ่มเพื่อเปรียบเทียบ</button>
<div class="share-actions" aria-label="แชร์สินค้า">
<button type="button" data-native-share data-share-title="{html.escape(title, quote=True)}" data-share-url="{html.escape(canonical, quote=True)}">แชร์</button>
<a href="https://social-plugins.line.me/lineit/share?url={html.escape(canonical, quote=True)}" target="_blank" rel="noopener">LINE</a>
<a href="https://www.facebook.com/sharer/sharer.php?u={html.escape(canonical, quote=True)}" target="_blank" rel="noopener">Facebook</a>
<a href="https://twitter.com/intent/tweet?url={html.escape(canonical, quote=True)}&amp;text={html.escape(title, quote=True)}" target="_blank" rel="noopener">X</a>
</div>
</div></div>
<section class="price-history"><h2>ประวัติราคา</h2><p>บันทึกจากราคาที่ปรากฏใน feed แต่ละวัน ไม่ใช่ราคาหน้าชำระเงิน</p><ul>{history_rows or '<li>เริ่มเก็บข้อมูลราคาแล้ว โปรดกลับมาตรวจสอบหลังการอัปเดตครั้งถัดไป</li>'}</ul></section>
<section class="related-products"><h2>สินค้าที่เกี่ยวข้อง</h2><div class="grid">{related_html}</div></section>
<p class="more-guides"><a href="/guides/">อ่านคู่มือเลือกซื้อและบทความเปรียบเทียบเพิ่มเติม →</a></p>
</article></main>
<footer><div class="container">Pickora · <a href="/affiliate-disclosure/">Affiliate Disclosure</a></div></footer>
<script type="application/json" id="product-context">{product_context}</script>
</body></html>"""


def create_category_page(
    category: str, category_url: str, products: list[dict[str, object]]
) -> str:
    canonical = f"{SITE_URL}{category_url}"
    cards = "".join(product_card(product) for product in products)
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            breadcrumb_schema([("หน้าแรก", "/"), (category, category_url)]),
            {
                "@type": "ItemList", "name": f"สินค้า {category}",
                "numberOfItems": len(products),
                "itemListElement": [
                    {
                        "@type": "ListItem", "position": position,
                        "url": f"{SITE_URL}{product['detailUrl']}",
                        "name": str(product["title"]),
                    }
                    for position, product in enumerate(products, start=1)
                ],
            },
        ],
    }
    schema_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(category[:55])} สินค้าแนะนำ | Pickora</title>
<meta name="description" content="รวมสินค้า {html.escape(category, quote=True)} ที่คัดจากคะแนน ยอดขาย ราคา และส่วนลด อัปเดตจากข้อมูลสินค้าเป็นประจำ">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Pickora"><meta property="og:title" content="{html.escape(category, quote=True)} สินค้าแนะนำ | Pickora"><meta property="og:url" content="{html.escape(canonical, quote=True)}">
<meta name="twitter:card" content="summary">
<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/content.css"><link rel="stylesheet" href="/catalog.css"><link rel="stylesheet" href="/product.css">
<script type="application/ld+json">{schema_json}</script></head><body>
<header class="header"><div class="container nav"><a class="brand" href="/"><span class="logo">P</span><span>Pickora</span></a></div></header>
<main><section class="section"><div class="container">
<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">หน้าแรก</a> / <span aria-current="page">{html.escape(category)}</span></nav>
<div class="category-header"><h1>{html.escape(category)}</h1><p>พบ {len(products):,} สินค้าที่ระบบคัดไว้</p></div>
<div class="grid">{cards}</div></div></section></main>
<footer><div class="container">Pickora · <a href="/guides/">คู่มือเลือกซื้อ</a> · <a href="/affiliate-disclosure/">Affiliate Disclosure</a></div></footer>
</body></html>"""


def write_product_pages_and_sitemap(products: list[dict[str, object]]) -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    temporary_pages = Path(tempfile.mkdtemp(prefix="products-", dir=PUBLIC_DIR))
    temporary_categories = Path(tempfile.mkdtemp(prefix="categories-", dir=PUBLIC_DIR))
    # mkdtemp intentionally creates directories with mode 0700. These directories
    # become the public document roots below, so the unprivileged nginx worker
    # must be able to traverse them after the atomic rename.
    temporary_pages.chmod(0o755)
    temporary_categories.chmod(0o755)
    products_by_category: dict[str, list[dict[str, object]]] = {}
    for product in products:
        category = str(product.get("category") or "สินค้าแนะนำ")
        products_by_category.setdefault(category, []).append(product)
    try:
        for product in products:
            page_dir = temporary_pages / str(product["id"])
            page_dir.mkdir()
            related = [
                item for item in products_by_category[str(product["category"])]
                if item["id"] != product["id"]
            ][:4]
            (page_dir / "index.html").write_text(
                create_product_page(product, related), encoding="utf-8"
            )
        for category, category_products in products_by_category.items():
            page_dir = temporary_categories / category_id(category)
            page_dir.mkdir()
            (page_dir / "index.html").write_text(
                create_category_page(
                    category, str(category_products[0]["categoryUrl"]),
                    category_products,
                ),
                encoding="utf-8",
            )
        old_pages = PUBLIC_DIR / "generated.previous"
        if old_pages.exists():
            shutil.rmtree(old_pages)
        old_pages.mkdir()
        if PRODUCT_PAGES_DIR.exists():
            PRODUCT_PAGES_DIR.replace(old_pages / "products")
        if CATEGORY_PAGES_DIR.exists():
            CATEGORY_PAGES_DIR.replace(old_pages / "categories")
        temporary_pages.replace(PRODUCT_PAGES_DIR)
        temporary_categories.replace(CATEGORY_PAGES_DIR)
        shutil.rmtree(old_pages)
    except Exception:
        if temporary_pages.exists():
            shutil.rmtree(temporary_pages)
        if temporary_categories.exists():
            shutil.rmtree(temporary_categories)
        raise

    today = datetime.now(timezone.utc).date().isoformat()
    entries = [
        f"<url><loc>{html.escape(SITE_URL + path)}</loc><changefreq>{frequency}</changefreq><priority>{priority}</priority></url>"
        for path, frequency, priority in STATIC_SITEMAP_PATHS
    ]
    entries.extend(
        f"<url><loc>{html.escape(SITE_URL + str(product['detailUrl']))}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>"
        for product in products
    )
    entries.extend(
        f"<url><loc>{html.escape(SITE_URL + str(category_products[0]['categoryUrl']))}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.7</priority></url>"
        for category_products in products_by_category.values()
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n</urlset>\n"
    )
    temporary_sitemap = SITEMAP_FILE.with_suffix(".tmp")
    temporary_sitemap.write_text(sitemap, encoding="utf-8")
    temporary_sitemap.replace(SITEMAP_FILE)


def process_feed() -> None:
    if not FEED_FILE.exists():
        raise FileNotFoundError(FEED_FILE)

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    write_status("processing")

    encoding = detect_encoding()
    separator = detect_separator(encoding)
    logging.info("Detected encoding=%s separator=%r", encoding, separator)

    winners: list[pd.DataFrame] = []
    total_rows = 0

    reader = pd.read_csv(
        FEED_FILE,
        chunksize=CHUNK_SIZE,
        low_memory=False,
        on_bad_lines="skip",
        encoding=encoding,
        sep=separator,
    )

    for chunk_number, chunk in enumerate(reader, start=1):
        total_rows += len(chunk)
        columns = list(chunk.columns)

        title_col = find_column(columns, [
            "product_name", "item_name", "title", "product_title", "productname"
        ])
        image_col = find_column(columns, [
            "image_url", "image_link", "product_image", "image", "item_image",
            "imageurl"
        ])
        link_col = find_column(columns, [
            "affiliate_link", "product_link", "offer_link", "item_url",
            "product_url", "tracking_link", "affiliate_url",
            "product_short_link"
        ])
        price_col = find_column(columns, [
            "price", "sale_price", "current_price", "product_price"
        ])
        rating_col = find_column(columns, [
            "rating", "item_rating", "product_rating"
        ])
        sold_col = find_column(columns, [
            "sold", "sales", "historical_sold", "item_sold", "sales_volume"
        ])
        commission_col = find_column(columns, [
            "commission_rate", "commission", "commission_percentage",
            "estimated_commission_rate"
        ])
        discount_col = find_column(columns, [
            "discount", "discount_percentage", "discount_rate"
        ])
        category_col = find_column(columns, [
            "category", "category_name", "product_category", "category_l1",
            "global_category1"
        ])
        shop_col = find_column(columns, [
            "shop_name", "seller_name", "merchant_name"
        ])
        product_id_col = find_column(columns, [
            "product_id", "item_id", "offer_id", "sku_id", "product_sku"
        ])

        if not title_col or not image_col or not link_col:
            missing = [
                name
                for name, column in (
                    ("title", title_col),
                    ("image", image_col),
                    ("link", link_col),
                )
                if not column
            ]
            raise RuntimeError(
                f"Required columns were not found: {', '.join(missing)}. "
                f"Available columns: {columns}"
            )

        filtered = chunk[
            chunk[title_col].notna()
            & chunk[image_col].notna()
            & chunk[link_col].notna()
        ].copy()

        filtered = keyword_filter(filtered, title_col)

        filtered["_price"] = numeric(filtered[price_col]) if price_col else 0
        filtered["_rating"] = numeric(filtered[rating_col]) if rating_col else 0
        filtered["_sold"] = numeric(filtered[sold_col]) if sold_col else 0
        filtered["_commission"] = (
            numeric(filtered[commission_col]) if commission_col else 0
        )
        filtered["_discount"] = (
            numeric(filtered[discount_col]) if discount_col else 0
        )

        if price_col:
            filtered = filtered[filtered["_price"].between(MIN_PRICE, MAX_PRICE)]
        if rating_col:
            filtered = filtered[filtered["_rating"] >= MIN_RATING]
        if sold_col:
            filtered = filtered[filtered["_sold"] >= MIN_SOLD]

        filtered["_score"] = (
            filtered["_sold"].clip(upper=100000) * WEIGHT_SOLD
            + filtered["_rating"] * WEIGHT_RATING
            + filtered["_commission"] * WEIGHT_COMMISSION
            + filtered["_discount"] * WEIGHT_DISCOUNT
        )

        output = pd.DataFrame({
            "title": filtered[title_col].astype(str),
            "image": filtered[image_col].astype(str),
            "link": filtered[link_col].astype(str),
            "price": filtered["_price"].round(2),
            "rating": filtered["_rating"].round(2),
            "sold": filtered["_sold"].round(0),
            "commission": filtered["_commission"].round(2),
            "discount": filtered["_discount"].round(2),
            "score": filtered["_score"].round(2),
            "externalId": (
                filtered[product_id_col].astype(str)
                if product_id_col else ""
            ),
            "category": (
                filtered[category_col].astype(str)
                if category_col else "สินค้าแนะนำ"
            ),
            "shop": (
                filtered[shop_col].astype(str)
                if shop_col else ""
            ),
        })

        winners.append(output.nlargest(TOP_PER_CHUNK, "score"))
        logging.info(
            "Chunk %s: read=%s candidate=%s",
            chunk_number,
            f"{len(chunk):,}",
            f"{len(output):,}",
        )

    if not winners:
        raise RuntimeError("No products passed the filters")

    result = pd.concat(winners, ignore_index=True)
    external_ids = result["externalId"].astype(str).str.strip()
    result["_identity"] = external_ids.where(
        ~external_ids.str.lower().isin({"", "nan", "none", "null"}),
        result["link"].astype(str),
    )
    result = (
        result
        .drop_duplicates(subset=["_identity"])
        .nlargest(MAX_PRODUCTS, "score")
        .drop(columns=["_identity"])
        .reset_index(drop=True)
    )
    all_records = result.to_dict(orient="records")
    records = [
        product for product in all_records
        if safe_external_url(product.get("link", ""))
        and safe_external_url(product.get("image", ""))
    ]
    invalid_urls = len(all_records) - len(records)
    record_count = len(records)
    for rank, product in enumerate(records):
        external_id = str(product.get("externalId") or "").strip()
        identity = (
            f"feed:{external_id}"
            if external_id.lower() not in {"", "nan", "none", "null"}
            else f"link:{product['link']}"
        )
        identifier = product_id(identity)
        category = display_category(product.get("category"))
        product["category"] = category
        product["id"] = identifier
        product["detailUrl"] = f"/products/{identifier}/"
        product["categoryUrl"] = f"/categories/{category_id(category)}/"
        product["pickoraScore"] = (
            100 if record_count == 1
            else round(100 - (rank / (record_count - 1)) * 50)
        )

    update_price_history(records)
    write_product_pages_and_sitemap(records)
    SEO_STATUS_FILE.write_text(
        json.dumps({
            "status": "ready",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "products": len(records),
            "categories": len({product["category"] for product in records}),
            "invalidProductsDropped": invalid_urls,
            "missingPrice": sum(float(product.get("price") or 0) <= 0 for product in records),
            "missingRating": sum(float(product.get("rating") or 0) <= 0 for product in records),
            "missingShop": sum(not str(product.get("shop") or "").strip() for product in records),
            "productPages": len(records),
            "canonicalBase": SITE_URL,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temp = PRODUCTS_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            records,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temp.replace(PRODUCTS_FILE)

    write_status(
        "ready",
        products=len(records),
        rowsProcessed=total_rows,
        sourceFileBytes=FEED_FILE.stat().st_size,
    )
    logging.info("Created %s with %s products", PRODUCTS_FILE, len(result))


def main() -> int:
    try:
        download_feed()
        process_feed()
        return 0
    except Exception as error:
        write_status("error", message=str(error))
        logging.exception("Pipeline failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
