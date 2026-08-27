from __future__ import annotations

import csv
import fcntl
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlparse

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
FEED_TEMP_PREFIX = "shopee_feed_"
FEED_TEMP_SUFFIX = ".download"
STALE_FEED_TEMP_AGE_SECONDS = 6 * 60 * 60
MIN_FEED_DOWNLOAD_FREE_BYTES = 8 * 1024**3
PRODUCTS_FILE = PUBLIC_DIR / "products.json"
STATUS_FILE = PUBLIC_DIR / "feed-status.json"
PRICE_HISTORY_FILE = PUBLIC_DIR / "price-history.json"
SEO_STATUS_FILE = PUBLIC_DIR / "seo-status.json"
SITEMAP_FILE = PUBLIC_DIR / "sitemap.xml"
HOMEPAGE_CATALOG_FILE = PUBLIC_DIR / "homepage-catalog.html"
PRODUCT_PAGES_DIR = PUBLIC_DIR / "products"
CATEGORY_PAGES_DIR = PUBLIC_DIR / "categories"
HOMEPAGE_PRODUCT_LIMIT = 12
CATEGORY_MIN_PRODUCTS = 4
CATEGORY_PAGE_PRODUCT_LIMIT = 24
THIN_CATEGORY_DISCOVERY_LIMIT = 12
CATEGORY_GUIDE_MAP = {
    "Audio": [("/reviews/wireless-earbuds.html", "คู่มือเลือกหูฟังไร้สาย")],
    "Computers & Accessories": [("/reviews/mechanical-keyboard.html", "คู่มือเลือก Mechanical Keyboard")],
    "Home & Living": [("/reviews/office-chair.html", "คู่มือเลือกเก้าอี้ทำงาน"), ("/reviews/water-bottle.html", "คู่มือเลือกขวดเก็บอุณหภูมิ")],
    "Home Appliances": [("/reviews/air-fryer.html", "คู่มือเลือกหม้อทอดไร้น้ำมัน"), ("/reviews/robot-vacuum.html", "คู่มือเลือกหุ่นยนต์ดูดฝุ่น"), ("/reviews/portable-fan.html", "คู่มือเลือกพัดลมพกพา")],
    "Mobile & Gadgets": [("/reviews/power-bank.html", "คู่มือเลือก Power Bank"), ("/reviews/smart-watch.html", "คู่มือเลือก Smart Watch")],
}
# These are deliberately broad buyer checks.  They describe what to verify
# before buying a type of product, rather than claiming that any individual
# listing has a particular feature.
CATEGORY_BUYING_CONSIDERATIONS = {
    "Audio": ["ตรวจสอบรูปแบบการเชื่อมต่อ", "พิจารณาเวลาใช้งานแบตเตอรี่และความพอดีกับการใช้งาน"],
    "Computers & Accessories": ["ตรวจสอบการรองรับกับอุปกรณ์ที่ใช้งาน", "พิจารณาขนาด การเชื่อมต่อ และรูปแบบการใช้งาน"],
    "Home & Living": ["ตรวจสอบขนาดให้เหมาะกับพื้นที่ใช้งาน", "พิจารณาวัสดุและวิธีดูแลรักษา"],
    "Home Appliances": ["ตรวจสอบความจุและขนาดก่อนจัดวาง", "พิจารณากำลังไฟและการดูแลรักษา"],
    "Mobile & Gadgets": ["ตรวจสอบการรองรับกับอุปกรณ์ที่ใช้งาน", "พิจารณาพอร์ต มาตรฐานการเชื่อมต่อ และขนาดสำหรับการพกพา"],
    "Mom & Baby": ["ตรวจสอบช่วงวัยและขนาดที่เหมาะสม", "อ่านรายละเอียดวัสดุและวิธีใช้งานจากหน้าร้านก่อนสั่งซื้อ"],
    "Women Clothes": ["ตรวจสอบตารางขนาดและทรงของสินค้า", "พิจารณาวัสดุและวิธีดูแลรักษา"],
    "Men Clothes": ["ตรวจสอบตารางขนาดและทรงของสินค้า", "พิจารณาวัสดุและวิธีดูแลรักษา"],
    "Women Shoes": ["ตรวจสอบตารางขนาดและความพอดี", "พิจารณาวัสดุและการดูแลรักษา"],
    "Men Shoes": ["ตรวจสอบตารางขนาดและความพอดี", "พิจารณาวัสดุและการดูแลรักษา"],
}

PRODUCT_AUDIENCE_RULES = (
    (("power bank", "พาวเวอร์แบง"), "ผู้ที่ต้องการพกพาพลังงานสำรองสำหรับอุปกรณ์เคลื่อนที่"),
    (("office chair", "เก้าอี้ทำงาน"), "ผู้ที่กำลังเลือกเก้าอี้สำหรับพื้นที่ทำงาน"),
    (("earbud", "หูฟัง"), "ผู้ที่กำลังเลือกอุปกรณ์เสียงสำหรับการใช้งานประจำวัน"),
)

CATEGORY_AUDIENCE = {
    "Audio": "ผู้ที่กำลังเลือกอุปกรณ์เสียงตามรูปแบบการใช้งานของตน",
    "Home Appliances": "ผู้ที่กำลังเลือกเครื่องใช้สำหรับบ้านตามพื้นที่และการใช้งาน",
    "Mobile & Gadgets": "ผู้ที่กำลังเลือกอุปกรณ์พกพาหรืออุปกรณ์เสริม",
    "Mom & Baby": "ผู้ปกครองที่กำลังเลือกสินค้าในหมวดแม่และเด็ก",
}

# Static editorial guidance for category landing pages.  It is intentionally
# limited to broad category-level checks and never describes a listing feature.
CATEGORY_TOPICAL_CONTENT = {
    "Beauty": {
        "intro": (
            "หมวด Beauty รวมผลิตภัณฑ์ดูแลผิวและความงามจากรายการสินค้าที่ Pickora คัดไว้. "
            "ก่อนเลือกซื้อ ควรเปรียบเทียบประเภทสินค้า ส่วนผสม และวิธีใช้ให้เหมาะกับการใช้งานของตน. "
            "Pickora แสดงราคาอ้างอิง คะแนน ยอดขาย และรายละเอียดรายการเพื่อช่วยให้เปรียบเทียบก่อนเปิดหน้าร้าน."
        ),
        "considerations": ["เลือกประเภทสินค้าและอ่านส่วนผสม", "ตรวจสอบความเข้ากันได้กับผิวและวิธีใช้", "ตรวจสอบผู้ขายและรายละเอียดสินค้าจากหน้าร้านก่อนสั่งซื้อ"],
        "faq": [("เลือกผลิตภัณฑ์ Beauty ควรดูอะไรบ้าง?", "เริ่มจากประเภทสินค้า ส่วนผสม วิธีใช้ และรายละเอียดจากหน้าร้าน เพื่อเปรียบเทียบกับความต้องการของตน."), ("ข้อมูลราคาใน Pickora ใช้ตัดสินใจได้อย่างไร?", "ใช้เป็นราคาอ้างอิงเพื่อเปรียบเทียบรายการ แล้วตรวจสอบราคาและโปรโมชันล่าสุดบนหน้าร้านก่อนสั่งซื้อ.")],
    },
    "Mobile & Gadgets": {
        "intro": (
            "หมวด Mobile & Gadgets รวมอุปกรณ์พกพาและอุปกรณ์เสริมสำหรับการใช้งานประจำวัน. "
            "ควรเปรียบเทียบความเข้ากันได้ การเชื่อมต่อ พอร์ต และความจุหรือกำลังไฟตามประเภทสินค้า. "
            "Pickora ช่วยเรียงรายการตามข้อมูลที่มี เพื่อให้เปิดดูรายละเอียดและราคาอ้างอิงได้สะดวกขึ้น."
        ),
        "considerations": ["ตรวจสอบความเข้ากันได้กับอุปกรณ์ที่ใช้งาน", "เปรียบเทียบพอร์ตและมาตรฐานการเชื่อมต่อ", "ตรวจสอบความจุหรือกำลังไฟตามประเภทสินค้า", "อ่านเงื่อนไขผู้ขายและรายละเอียดสินค้าก่อนสั่งซื้อ"],
        "faq": [("เลือกอุปกรณ์เสริมมือถือควรดูอะไรบ้าง?", "ตรวจสอบรุ่นอุปกรณ์ที่รองรับ พอร์ต และมาตรฐานการเชื่อมต่อก่อนเปรียบเทียบราคา."), ("เลือก Power Bank ควรตรวจสอบอะไร?", "ตรวจสอบความจุ มาตรฐานการชาร์จ พอร์ต และขนาดสำหรับการพกพาจากรายละเอียดหน้าร้าน.")],
    },
    "Home & Living": {
        "intro": (
            "หมวด Home & Living รวมของใช้และอุปกรณ์สำหรับพื้นที่ภายในบ้าน. "
            "การเปรียบเทียบควรเริ่มจากขนาด วัสดุ พื้นที่ติดตั้ง และวิธีดูแลรักษาตามการใช้งานจริง. "
            "Pickora รวบรวมข้อมูลรายการและราคาอ้างอิงเพื่อช่วยให้เลือกดูรายละเอียดที่เกี่ยวข้องได้ง่ายขึ้น."
        ),
        "considerations": ["วัดขนาดพื้นที่ก่อนเลือกสินค้า", "ตรวจสอบวัสดุและการดูแลรักษา", "พิจารณาการติดตั้งหรือการประกอบเมื่อเกี่ยวข้อง"],
        "faq": [("เลือกของใช้ในบ้านควรเริ่มจากอะไร?", "เริ่มจากขนาดพื้นที่และรูปแบบการใช้งาน แล้วเปรียบเทียบวัสดุและวิธีดูแลรักษา."), ("ควรตรวจสอบขนาดจากตรงไหน?", "ตรวจสอบขนาดสินค้าจากรายละเอียดหน้าร้านและเทียบกับพื้นที่ที่จะใช้งานก่อนสั่งซื้อ.")],
    },
    "Audio": {
        "intro": (
            "หมวด Audio รวมอุปกรณ์เสียงสำหรับการฟัง การสื่อสาร และการใช้งานแบบพกพา. "
            "ควรเปรียบเทียบรูปแบบการเชื่อมต่อ เวลาใช้งานแบตเตอรี่ ไมโครโฟน และรูปทรงที่เหมาะกับการใช้งาน. "
            "Pickora แสดงรายการที่มีข้อมูลราคาอ้างอิง คะแนน และยอดขายเพื่อช่วยให้เปรียบเทียบก่อนดูหน้าร้าน."
        ),
        "considerations": ["เลือกรูปแบบการเชื่อมต่อให้เหมาะกับอุปกรณ์", "พิจารณาเวลาใช้งานแบตเตอรี่เมื่อเป็นอุปกรณ์ไร้สาย", "ตรวจสอบความต้องการใช้ไมโครโฟนและรูปทรงสินค้า"],
        "faq": [("ควรเลือกหูฟังแบบ Bluetooth หรือมีสาย?", "เลือกตามอุปกรณ์ที่ใช้ ความสะดวกในการพกพา และรูปแบบการเชื่อมต่อที่ต้องการ."), ("เลือกอุปกรณ์เสียงควรดูไมโครโฟนหรือไม่?", "หากต้องใช้โทรหรือประชุม ควรอ่านรายละเอียดไมโครโฟนและการเชื่อมต่อจากหน้าร้าน.")],
    },
    "Health": {
        "intro": (
            "หมวด Health รวมสินค้าที่เกี่ยวกับการดูแลตนเองและการใช้งานตามรายละเอียดของผู้ขาย. "
            "ควรเปรียบเทียบวัตถุประสงค์การใช้ ขนาดหรือข้อมูลจำเพาะ และคำแนะนำบนฉลากหรือหน้าร้านอย่างรอบคอบ. "
            "Pickora ช่วยให้ดูรายการและราคาอ้างอิงได้ แต่ไม่ทดแทนคำแนะนำทางการแพทย์หรือข้อมูลจากผู้ผลิต."
        ),
        "considerations": ["ตรวจสอบวัตถุประสงค์การใช้งานและรายละเอียดสินค้า", "พิจารณาขนาดหรือข้อมูลจำเพาะที่เกี่ยวข้อง", "อ่านคำแนะนำและข้อมูลการขึ้นทะเบียนจากผู้ผลิตหรือหน้าร้านเมื่อมี"],
        "faq": [("เลือกสินค้าในหมวด Health ควรดูอะไร?", "อ่านวัตถุประสงค์การใช้ ข้อมูลจำเพาะ และคำแนะนำจากผู้ผลิตหรือหน้าร้านก่อนตัดสินใจ."), ("Pickora ให้คำแนะนำทางการแพทย์หรือไม่?", "ไม่ให้คำแนะนำทางการแพทย์; หน้านี้ใช้เพื่อเปรียบเทียบข้อมูลรายการและราคาอ้างอิง.")],
    },
    "Mom & Baby": {
        "intro": (
            "หมวด Mom & Baby รวมสินค้าสำหรับผู้ปกครองและเด็กตามรายละเอียดของแต่ละรายการ. "
            "ควรเปรียบเทียบช่วงวัย ขนาด วัสดุ คำแนะนำด้านความปลอดภัย และวิธีทำความสะอาดก่อนเลือกซื้อ. "
            "Pickora ช่วยรวบรวมรายการและราคาอ้างอิงเพื่อให้ตรวจสอบรายละเอียดจากหน้าร้านได้สะดวกขึ้น."
        ),
        "considerations": ["ตรวจสอบช่วงวัยและขนาดที่เหมาะสม", "อ่านข้อมูลวัสดุและคำแนะนำด้านความปลอดภัย", "พิจารณาวิธีทำความสะอาดและดูแลรักษา"],
        "faq": [("เลือกสินค้าแม่และเด็กควรดูอะไรบ้าง?", "ตรวจสอบช่วงวัย ขนาด วัสดุ และคำแนะนำจากผู้ผลิตหรือหน้าร้านก่อนสั่งซื้อ."), ("ควรตรวจสอบวิธีดูแลรักษาหรือไม่?", "ควรอ่านวิธีทำความสะอาดและการดูแลรักษา เพื่อให้เหมาะกับการใช้งานของครอบครัว.")],
    },
}

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
WEIGHT_DISCOUNT = float(os.getenv("WEIGHT_DISCOUNT", "20"))

STATIC_SITEMAP_PATHS = (
    ("/", "daily", "1.0"), ("/about/", "monthly", "0.6"),
    ("/categories/", "weekly", "0.7"),
    ("/guides/", "weekly", "0.8"),
    ("/affiliate-disclosure/", "yearly", "0.4"),
    ("/methodology/", "yearly", "0.5"),
    ("/privacy/", "yearly", "0.3"),
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

PRODUCT_TEXT_FIELDS = ("title", "category", "shop", "description")
SHOPEE_AFFILIATE_ENDPOINT = "https://s.shopee.co.th/an_redir"
SHOPEE_PRODUCT_HOSTS = frozenset({"shopee.co.th", "www.shopee.co.th"})


def build_shopee_affiliate_link(
    product_url: str, affiliate_id: str, sub_id: str,
) -> str:
    """Build one validated Shopee Thailand affiliate redirect URL."""
    product_url = str(product_url or "").strip()
    if not product_url:
        return ""

    parsed = urlparse(product_url)
    if (
        parsed.scheme == "https"
        and parsed.hostname == "s.shopee.co.th"
        and parsed.path.rstrip("/") == "/an_redir"
    ):
        origins = parse_qs(parsed.query).get("origin_link", [])
        if len(origins) != 1:
            return ""
        product_url = origins[0].strip()
        parsed = urlparse(product_url)

    if parsed.scheme != "https" or parsed.hostname not in SHOPEE_PRODUCT_HOSTS:
        return ""
    if not str(affiliate_id or "").strip():
        raise ValueError("SHOPEE_AFFILIATE_ID is required to build affiliate links")

    return f"{SHOPEE_AFFILIATE_ENDPOINT}?{urlencode({
        'origin_link': product_url,
        'affiliate_id': str(affiliate_id).strip(),
        'sub_id': sanitise_sub_id(sub_id),
    })}"


def sanitise_sub_id(value: object, *, fallback: str = "product") -> str:
    """Return a short ASCII Sub ID accepted by Shopee reporting."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-_") or fallback
    return cleaned[:100].rstrip("-_")


def build_product_sub_id(prefix: str, item_id: object, fallback_id: str) -> str:
    safe_prefix = sanitise_sub_id(prefix, fallback="pickora")[:32]
    safe_identifier = sanitise_sub_id(item_id, fallback=fallback_id)[:48]
    return sanitise_sub_id(f"{safe_prefix}-product-{safe_identifier}")


def repair_mojibake(value: object) -> object:
    """Repair common UTF-8-as-Western-encoding mojibake, when clearly marked.

    Correct Unicode and values of other types are intentionally returned without
    conversion.  Trying Latin-1 first handles byte-preserving misdecoding, while
    CP1252 covers feeds that used Windows' closely related Western code page.
    """
    if not isinstance(value, str):
        return value

    mojibake_markers = ("à¸", "à¹", "Ã", "Â")
    if not any(marker in value for marker in mojibake_markers):
        return value

    for source_encoding in ("latin1", "cp1252"):
        try:
            repaired = value.encode(source_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired != value:
            return repaired

    return value


def normalise_product_text(product: dict[str, object]) -> None:
    """Repair feed-provided textual product fields in place."""
    for field in PRODUCT_TEXT_FIELDS:
        if field in product:
            product[field] = repair_mojibake(product[field])

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


def cleanup_stale_feed_downloads(now: float | None = None) -> None:
    """Remove only old, unlocked temporary files owned by this downloader."""
    current_time = time.time() if now is None else now
    pattern = f"{FEED_TEMP_PREFIX}*{FEED_TEMP_SUFFIX}"

    for temporary_path in DATA_DIR.glob(pattern):
        try:
            file_stat = temporary_path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            logging.exception(
                "Failed to inspect temporary feed download: %s", temporary_path
            )
            continue

        age_seconds = current_time - file_stat.st_mtime
        if not temporary_path.is_file() or age_seconds <= STALE_FEED_TEMP_AGE_SECONDS:
            continue

        try:
            with temporary_path.open("rb") as temporary:
                try:
                    fcntl.flock(
                        temporary.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                except BlockingIOError:
                    logging.info(
                        "Preserving active temporary feed download: %s",
                        temporary_path,
                    )
                    continue
                fcntl.flock(temporary.fileno(), fcntl.LOCK_UN)

            temporary_path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            logging.exception(
                "Failed to remove stale temporary feed download: %s",
                temporary_path,
            )
        else:
            logging.warning(
                "Removed stale temporary feed download: %s "
                "(age=%.1f hours, size=%s bytes)",
                temporary_path,
                age_seconds / 3600,
                file_stat.st_size,
            )


def validate_feed_download(temporary_path: Path) -> None:
    """Apply inexpensive checks before replacing the multi-gigabyte feed."""
    try:
        size = temporary_path.stat().st_size
    except FileNotFoundError as error:
        raise RuntimeError("Downloaded feed is missing") from error

    if size < 100:
        raise RuntimeError("Downloaded feed is unexpectedly small")


def required_feed_download_free_bytes(feed_size: int) -> int:
    """Return space needed for a full replacement download plus headroom."""
    return max(MIN_FEED_DOWNLOAD_FREE_BYTES, feed_size * 2)


def ensure_feed_download_disk_space() -> None:
    """Reject a feed refresh before network I/O when free disk is unsafe."""
    try:
        feed_size = FEED_FILE.stat().st_size
    except FileNotFoundError:
        feed_size = 0

    required_bytes = required_feed_download_free_bytes(feed_size)
    try:
        free_bytes = shutil.disk_usage(DATA_DIR).free
    except OSError:
        logging.exception(
            "Failed to determine free disk space before feed download: data_dir=%s",
            DATA_DIR,
        )
        raise

    if free_bytes < required_bytes:
        message = (
            "Insufficient disk space for feed download: "
            f"free={free_bytes} bytes ({free_bytes / 1024**3:.2f} GiB), "
            f"required={required_bytes} bytes "
            f"({required_bytes / 1024**3:.2f} GiB), "
            f"current_feed={feed_size} bytes ({feed_size / 1024**3:.2f} GiB), "
            f"data_dir={DATA_DIR}"
        )
        logging.error(message)
        raise RuntimeError(message)


def download_feed() -> None:
    if not FEED_URL:
        raise RuntimeError("SHOPEE_FEED_URL is missing in .env")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_stale_feed_downloads()
    ensure_feed_download_disk_space()
    write_status("downloading")

    logging.info("Downloading Shopee data feed")
    temporary_path: Path | None = None
    try:
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
                prefix=FEED_TEMP_PREFIX,
                suffix=FEED_TEMP_SUFFIX,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                fcntl.flock(temporary.fileno(), fcntl.LOCK_EX)
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        temporary.write(block)
                temporary.flush()
                validate_feed_download(temporary_path)
                os.replace(temporary_path, FEED_FILE)
                temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logging.exception(
                    "Failed to remove temporary feed download: %s",
                    temporary_path,
                )

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


def product_images(value: object, *, limit: int = 8) -> list[str]:
    """Return the unique, valid image URLs supplied by an affiliate feed.

    Feeds use either a JSON array or a separator-delimited string for galleries.
    A plain URL remains fully backwards compatible with the original single-image
    feed format.
    """
    candidates: list[object]
    if isinstance(value, (list, tuple)):
        candidates = list(value)
    elif not isinstance(value, str):
        candidates = []
    else:
        raw = value.strip()
        if raw.startswith("["):
            try:
                decoded = json.loads(raw)
                candidates = decoded if isinstance(decoded, list) else [raw]
            except json.JSONDecodeError:
                candidates = re.split(r"[|;\n]+", raw)
        else:
            candidates = re.split(r"[|;\n]+", raw)

    images: list[str] = []
    for candidate in candidates:
        image = safe_external_url(candidate)
        if image and image not in images:
            images.append(image)
        if len(images) == limit:
            break
    return images


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
    price_max = max(price, float(product.get("priceMax") or 0))
    price_text = (
        f"ราคาอ้างอิง ฿{price:,.0f}–฿{price_max:,.0f}"
        if price > 0 and price_max > price
        else f"ราคาอ้างอิง ฿{price:,.0f}"
        if price > 0 else "ดูราคาล่าสุด"
    )
    pickora_score = int(product.get("pickoraScore") or 0)
    return f"""<article class="card related-card">
<a class="card-image-link" href="{html.escape(str(product['detailUrl']), quote=True)}">
<img src="{html.escape(safe_external_url(product.get('image', '')), quote=True)}" alt="{html.escape(title, quote=True)}" loading="lazy" decoding="async" width="600" height="600"></a>
<div class="card-body"><div class="category">{html.escape(str(product.get('category') or 'สินค้าแนะนำ'))}</div>
<h3 class="title"><a href="{html.escape(str(product['detailUrl']), quote=True)}">{html.escape(title)}</a></h3>
<div class="score-badge" title="คำนวณจากคะแนน ยอดขาย ส่วนลด และข้อมูล Affiliate">Pickora Score {pickora_score}</div>
<div class="price">{html.escape(price_text)}</div>
{f'<small class="price-note">โปรโมชันจริงอาจต่ำกว่านี้</small>' if price > 0 else ''}
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


def ranked_products(products: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep score ordering deterministic when feed scores are tied."""
    return sorted(
        products,
        key=lambda product: (-float(product.get("score") or 0), str(product["id"])),
    )


TITLE_TOKEN_STOPWORDS = {
    "สินค้า", "พร้อมส่ง", "ของแท้", "โปรโมชั่น", "โปรโมชัน", "ลดราคา",
    "sale", "shop", "ฟรี", "ใหม่", "แท้", "the", "and", "with",
}


def title_tokens(title: object) -> set[str]:
    """Extract conservative, deterministic comparison signals from a title."""
    normalized = re.sub(r"[^a-zA-Zก-๙]+", " ", str(title).lower())
    return {
        token for token in normalized.split()
        if len(token) >= 3 and token not in TITLE_TOKEN_STOPWORDS
    }


def select_related_products(
    product: dict[str, object], category_products: list[dict[str, object]], limit: int = 6,
) -> list[dict[str, object]]:
    """Keep related cards in-category; title overlap only refines their order."""
    source_tokens = title_tokens(product.get("title"))
    candidates = [item for item in category_products if item["id"] != product["id"]]
    return sorted(
        candidates,
        key=lambda item: (
            -len(source_tokens & title_tokens(item.get("title"))),
            -float(item.get("score") or 0),
            str(item["id"]),
        ),
    )[:limit]


def paginate_products(
    products: list[dict[str, object]], page_size: int = 24
) -> list[list[dict[str, object]]]:
    """Split an already ranked product list into deterministic fixed-size pages."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return [products[index:index + page_size] for index in range(0, len(products), page_size)]


def category_page_url(category_url: str, page_number: int) -> str:
    """Return a category page URL from its immutable page-one URL."""
    return category_url if page_number <= 1 else f"{category_url}page/{page_number}/"


def select_homepage_products(products: list[dict[str, object]]) -> list[dict[str, object]]:
    """Select score-ranked products with one top candidate per category first."""
    by_category: dict[str, list[dict[str, object]]] = {}
    for product in ranked_products(products):
        by_category.setdefault(str(product.get("category") or "สินค้าแนะนำ"), []).append(product)

    selected: list[dict[str, object]] = []
    round_index = 0
    while len(selected) < HOMEPAGE_PRODUCT_LIMIT:
        candidates = [
            category_products[round_index]
            for category_products in by_category.values()
            if len(category_products) > round_index
        ]
        if not candidates:
            break
        for product in ranked_products(candidates):
            if len(selected) == HOMEPAGE_PRODUCT_LIMIT:
                break
            selected.append(product)
        round_index += 1
    return selected


def category_intro(category: str) -> str:
    return (
        f"รวมสินค้าน่าสนใจในหมวด {category} ที่ Pickora คัดจากข้อมูลราคา "
        "คะแนน ยอดขาย และสัญญาณจาก Affiliate feed เพื่อช่วยเปรียบเทียบก่อนดูรายละเอียดสินค้า"
    )


def category_landing_intro(category: str) -> str:
    content = CATEGORY_TOPICAL_CONTENT.get(category)
    if content:
        return str(content["intro"])
    return (
        f"หน้านี้รวบรวมสินค้าในหมวด {category} จากรายการที่ Pickora คัดไว้. "
        "เปรียบเทียบชื่อสินค้า ราคาอ้างอิง คะแนน และยอดขายที่แสดงในแต่ละรายการก่อนเปิดดูรายละเอียดจากหน้าร้าน."
    )


def category_considerations(category: str) -> list[str]:
    content = CATEGORY_TOPICAL_CONTENT.get(category, {})
    return list(content.get("considerations", []))


def category_faq(category: str) -> list[tuple[str, str]]:
    content = CATEGORY_TOPICAL_CONTENT.get(category, {})
    return list(content.get("faq", []))


def category_metadata_title(category: str, page_number: int) -> str:
    if page_number > 1:
        return f"{category} · หน้า {page_number} | Pickora"
    return f"{category} สินค้าแนะนำและวิธีเลือก | Pickora"


def category_metadata_description(category: str, page_number: int) -> str:
    if page_number > 1:
        return (
            f"ดูสินค้า {category} หน้า {page_number} พร้อมราคาอ้างอิง คะแนน และยอดขาย "
            "เพื่อเปรียบเทียบรายละเอียดก่อนตัดสินใจซื้อ"
        )
    return (
        f"เลือกดูสินค้า {category} พร้อมเปรียบเทียบราคาอ้างอิง คะแนน และยอดขาย "
        "จากรายการที่ Pickora คัดไว้ก่อนดูรายละเอียดสินค้า"
    )


def category_guide_links(category: str, heading: str = "คู่มือเลือกซื้อที่เกี่ยวข้อง") -> str:
    guides = CATEGORY_GUIDE_MAP.get(category, [])[:3]
    if not guides:
        return ""
    return f'<section class="related-products"><h2>{html.escape(heading)}</h2><ul>' + "".join(
        f'<li><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a></li>'
        for url, title in guides
    ) + "</ul></section>"


def product_summary(product: dict[str, object], category: str, price_text: str) -> str:
    """Return concise, feed-grounded copy for a generated product page."""
    facts = [f"{product['title']} อยู่ในหมวด {category}"]
    if float(product.get("price") or 0) > 0:
        facts.append(f"ข้อมูลราคาอ้างอิงที่ Pickora แสดงคือ {price_text}")
    score = int(product.get("pickoraScore") or 0)
    if score > 0:
        facts.append(f"Pickora Score {score}")
    rating = float(product.get("rating") or 0)
    sold = int(float(product.get("sold") or 0))
    if rating > 0:
        facts.append(f"คะแนนบนแพลตฟอร์ม {rating:g}")
    if sold > 0:
        facts.append(f"ยอดขายที่ข้อมูลรายการระบุ {sold:,} ชิ้น")
    return " · ".join(facts) + "."


def clean_product_metadata_title(title: object, max_length: int = 60) -> str:
    """Compact marketplace titles for metadata without changing the visible H1."""
    value = re.sub(r"\s+", " ", str(title)).strip()
    value = re.sub(
        r"^(?:\[(?=[^\]]*(?:ลด|code|sale|special|free))[^\]]{1,80}\]\s*)+",
        "", value, flags=re.IGNORECASE,
    )
    if len(value) <= max_length:
        return value
    boundary = value.rfind(" ", 0, max_length + 1)
    if boundary >= max_length // 2:
        return value[:boundary].rstrip(" -–—|,;:") + "…"
    return value[:max_length].rstrip() + "…"


def product_metadata_titles(products: list[dict[str, object]]) -> dict[str, str]:
    """Disambiguate only duplicate metadata titles with factual feed context."""
    grouped: dict[str, list[dict[str, object]]] = {}
    for product in products:
        grouped.setdefault(clean_product_metadata_title(product.get("title")), []).append(product)
    titles: dict[str, str] = {}
    for base, group in grouped.items():
        if len(group) == 1:
            titles[str(group[0]["id"])] = base
            continue
        prices = {float(product.get("price") or 0) for product in group}
        categories = {str(product.get("category") or "") for product in group}
        for product in group:
            if len(prices) > 1 and float(product.get("price") or 0) > 0:
                suffix = f" · ฿{float(product['price']):,.0f}"
            elif len(categories) > 1:
                suffix = f" · {str(product.get('category') or 'สินค้า')}"
            else:
                suffix = f" · รหัส {str(product.get('externalId') or product['id'])[-6:]}"
            titles[str(product["id"])] = clean_product_metadata_title(
                base, max_length=max(20, 60 - len(suffix))
            ) + suffix
    return titles


def product_meta_description(
    product: dict[str, object], category: str, price_text: str,
) -> str:
    title = clean_product_metadata_title(product.get("title"), max_length=78)
    description = f"ดูข้อมูล {title} ในหมวด {category}"
    if float(product.get("price") or 0) > 0:
        description += f" พร้อม{price_text}"
    score = int(product.get("pickoraScore") or 0)
    if score > 0:
        description += f" และ Pickora Score {score}"
    description += " เพื่อช่วยเปรียบเทียบก่อนตัดสินใจซื้อ"
    if len(description) <= 155:
        return description
    boundary = description.rfind(" ", 0, 155)
    return (description[:boundary] if boundary >= 80 else description[:155]).rstrip() + "…"


def product_audience(product: dict[str, object], category: str) -> str:
    """Use only conservative title/category signals for audience guidance."""
    title = str(product.get("title") or "").lower()
    for keywords, audience in PRODUCT_AUDIENCE_RULES:
        if any(keyword in title for keyword in keywords):
            return audience
    return CATEGORY_AUDIENCE.get(category, "")


def buying_considerations(category: str) -> list[str]:
    return CATEGORY_BUYING_CONSIDERATIONS.get(category, [])


def create_homepage_catalog(products: list[dict[str, object]]) -> str:
    """Build a compact crawlable catalog snapshot for the homepage SSI include."""
    categories: dict[str, tuple[str, int]] = {}
    for product in products:
        category = str(product.get("category") or "สินค้าแนะนำ")
        category_url = str(product.get("categoryUrl") or "")
        if not category_url:
            continue
        _, count = categories.get(category, (category_url, 0))
        categories[category] = (category_url, count + 1)

    category_links = "".join(
        f'<a class="filter" href="{html.escape(url, quote=True)}">'
        f'{html.escape(category)} <span>{count:,}</span></a>'
        for category, (url, count) in sorted(
            categories.items(), key=lambda item: (-item[1][1], item[0])
        )
    )
    cards = "".join(product_card(product) for product in select_homepage_products(products))
    return f"""<div class="homepage-catalog">
<nav aria-labelledby="homepage-categories-heading">
<h3 id="homepage-categories-heading">เลือกสินค้าตามหมวดหมู่</h3>
<div class="filters">{category_links}</div>
<p class="more-guides"><a href="/categories/">ดูหมวดหมู่สินค้าทั้งหมด →</a></p>
</nav>
<div class="grid">{cards}</div>
</div>"""


def create_product_page(
    product: dict[str, object], related: list[dict[str, object]]
) -> str:
    title = str(product["title"]).strip()
    canonical = f"{SITE_URL}{product['detailUrl']}"
    images = product_images(product.get("images") or product.get("image", ""))
    image = images[0] if images else ""
    affiliate_link = safe_external_url(product.get("link", ""))
    category = str(product.get("category") or "สินค้าแนะนำ")
    category_url = str(product.get("categoryUrl") or "")
    guide_links = category_guide_links(category)
    shop = str(product.get("shop") or "")
    price = float(product.get("price") or 0)
    price_max = max(price, float(product.get("priceMax") or 0))
    rating = float(product.get("rating") or 0)
    sold = int(float(product.get("sold") or 0))
    pickora_score = int(product.get("pickoraScore") or 0)
    price_text = (
        f"ราคาอ้างอิง ฿{price:,.0f}–฿{price_max:,.0f}"
        if price > 0 and price_max > price
        else f"ราคาอ้างอิง ฿{price:,.0f}"
        if price > 0 else "ดูราคาล่าสุด"
    )
    metadata_title = str(product.get("_metadataTitle") or clean_product_metadata_title(title))
    description = product_meta_description(product, category, price_text)
    price_updated_at = str(product.get("priceUpdatedAt") or "")
    price_note = "โปรโมชันจริงอาจต่ำกว่านี้ ราคานี้เป็นข้อมูลอ้างอิงจาก Affiliate Feed"
    if price_updated_at:
        price_note += f" · อัปเดต {price_updated_at}"
    meta = []
    if rating > 0:
        meta.append(f"★ {rating:g}")
    if sold > 0:
        meta.append(f"ขายแล้ว {sold:,}")
    summary_html = (
        '<section class="product-content"><h2>สรุปสินค้า</h2><p>'
        + html.escape(product_summary(product, category, price_text))
        + "</p></section>"
    )
    audience = product_audience(product, category)
    audience_html = (
        '<section class="product-content"><h2>เหมาะกับใคร</h2><p>'
        + html.escape(audience)
        + "</p></section>"
        if audience else ""
    )
    considerations = buying_considerations(category)
    considerations_html = (
        '<section class="product-content"><h2>จุดที่ควรพิจารณาก่อนเลือกซื้อ</h2><ul>'
        + "".join(f"<li>{html.escape(item)}</li>" for item in considerations)
        + "</ul></section>"
        if considerations else ""
    )
    schema = {
        "@type": "Product", "name": title,
        "image": images, "category": category, "url": canonical,
        "description": description,
    }
    # Feed prices are explicitly presented as reference prices and may differ
    # from checkout pricing, so emitting Offer markup would overstate them.
    breadcrumbs = [("หน้าแรก", "/")]
    if category_url:
        breadcrumbs.append((category, category_url))
    breadcrumbs.append((title, str(product["detailUrl"])))
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            schema,
            breadcrumb_schema(breadcrumbs),
        ],
    }
    schema_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    related_html = "".join(product_card(item) for item in related)
    related_section = (
        '<section class="related-products"><h2>สินค้าที่เกี่ยวข้อง</h2><div class="grid">'
        + related_html + "</div></section>"
        if related_html else ""
    )
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
    thumbnails = "".join(
        f'<button class="product-thumbnail{" active" if index == 0 else ""}" type="button" data-gallery-image="{html.escape(item, quote=True)}" aria-label="ดูรูปที่ {index + 1}" aria-pressed="{"true" if index == 0 else "false"}"><img src="{html.escape(item, quote=True)}" alt="" loading="lazy" width="112" height="112"></button>'
        for index, item in enumerate(images)
    )
    gallery = f'''<div class="product-gallery">
<img class="product-detail-image" data-gallery-main src="{html.escape(image, quote=True)}" alt="{html.escape(title, quote=True)}" decoding="async" fetchpriority="high" width="800" height="800">
{f'<div class="product-thumbnails" aria-label="รูปสินค้า {len(images)} รูป">{thumbnails}</div>' if len(images) > 1 else ''}</div>'''
    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(metadata_title)} | Pickora</title>
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
<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">หน้าแรก</a> / {f'<a href="{html.escape(category_url, quote=True)}">{html.escape(category)}</a>' if category_url else f'<span>{html.escape(category)}</span>'} / <span aria-current="page">{html.escape(title)}</span></nav>
<div class="product-detail-grid">{gallery}
<div><span class="pill">{html.escape(category)}</span><h1>{html.escape(title)}</h1>
<p class="product-shop">{html.escape(shop)}</p><p>{html.escape(" · ".join(meta))}</p>
<div class="product-score"><strong>{pickora_score}</strong><span>Pickora Score<small>คำนวณจากคะแนน ยอดขาย ส่วนลด และข้อมูล Affiliate</small></span></div>
<div class="product-detail-price">{html.escape(price_text)}</div>
{f'<p class="product-price-note">{html.escape(price_note)}</p>' if price > 0 else ''}
<p class="affiliate-inline">ลิงก์ด้านล่างเป็น Affiliate link ราคา สต็อก และโปรโมชันอาจเปลี่ยนแปลง โปรดตรวจสอบบนหน้าร้านก่อนสั่งซื้อ</p>
<a class="primary product-buy" href="{html.escape(affiliate_link, quote=True)}" target="_blank" rel="nofollow sponsored noopener noreferrer" data-affiliate-link data-product-id="{html.escape(str(product.get('externalId') or product['id']), quote=True)}" data-product-name="{html.escape(title, quote=True)}" data-shop-id="{html.escape(str(product.get('shopId') or ''), quote=True)}" data-placement="product-detail">เช็กราคาล่าสุดใน Shopee →</a>
<button class="secondary-action" type="button" data-compare-product="{html.escape(str(product['id']), quote=True)}">เพิ่มเพื่อเปรียบเทียบ</button>
<div class="share-actions" aria-label="แชร์สินค้า">
<button type="button" data-native-share data-share-title="{html.escape(title, quote=True)}" data-share-url="{html.escape(canonical, quote=True)}">แชร์</button>
<a href="https://social-plugins.line.me/lineit/share?url={html.escape(canonical, quote=True)}" target="_blank" rel="noopener">LINE</a>
<a href="https://www.facebook.com/sharer/sharer.php?u={html.escape(canonical, quote=True)}" target="_blank" rel="noopener">Facebook</a>
<a href="https://twitter.com/intent/tweet?url={html.escape(canonical, quote=True)}&amp;text={html.escape(title, quote=True)}" target="_blank" rel="noopener">X</a>
</div>
</div></div>
{summary_html}
{audience_html}
{considerations_html}
<section class="price-history"><h2>ประวัติราคา</h2><p>บันทึกจากราคาที่ปรากฏใน feed แต่ละวัน ไม่ใช่ราคาหน้าชำระเงิน</p><ul>{history_rows or '<li>เริ่มเก็บข้อมูลราคาแล้ว โปรดกลับมาตรวจสอบหลังการอัปเดตครั้งถัดไป</li>'}</ul></section>
{guide_links}
{related_section}
<p class="more-guides"><a href="/guides/">อ่านคู่มือเลือกซื้อและบทความเปรียบเทียบเพิ่มเติม →</a></p>
</article></main>
<footer><div class="container"><a href="/">หน้าแรก</a> · <a href="/guides/">คู่มือ</a> · <a href="/about/">เกี่ยวกับเรา</a> · <a href="/methodology/">วิธีคัดเลือกสินค้า</a> · <a href="/privacy/">ความเป็นส่วนตัว</a> · <a href="/affiliate-disclosure/">Affiliate Disclosure</a></div></footer>
<script type="application/json" id="product-context">{product_context}</script>
<script src="/product-gallery.js"></script>
</body></html>"""


def create_category_page(
    category: str, category_url: str, products: list[dict[str, object]],
    page_number: int = 1, page_count: int = 1, category_total: int | None = None,
) -> str:
    page_url = category_page_url(category_url, page_number)
    canonical = f"{SITE_URL}{page_url}"
    metadata_title = category_metadata_title(category, page_number)
    metadata_description = category_metadata_description(category, page_number)
    total_products = category_total if category_total is not None else len(products)
    visible_products = products
    cards = "".join(product_card(product) for product in visible_products)
    page_one = page_number == 1
    intro = category_landing_intro(category) if page_one else ""
    considerations = category_considerations(category) if page_one else []
    faq = category_faq(category) if page_one else []
    considerations_html = (
        '<section class="category-content"><h2>สิ่งที่ควรพิจารณาก่อนเลือกซื้อ</h2><ul>'
        + "".join(f"<li>{html.escape(item)}</li>" for item in considerations)
        + "</ul></section>"
        if considerations else ""
    )
    faq_html = (
        '<section class="category-content"><h2>คำถามที่พบบ่อย</h2>'
        + "".join(
            f"<details><summary>{html.escape(question)}</summary><p>{html.escape(answer)}</p></details>"
            for question, answer in faq
        ) + "</section>"
        if faq else ""
    )
    guide_links = category_guide_links(category, heading="คู่มือเลือกซื้อ")
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            breadcrumb_schema([("หน้าแรก", "/"), (category, page_url)]),
            {
                "@type": "ItemList", "name": f"สินค้า {category}",
                "numberOfItems": len(visible_products),
                "itemListElement": [
                    {
                        "@type": "ListItem", "position": position,
                        "url": f"{SITE_URL}{product['detailUrl']}",
                        "name": str(product["title"]),
                    }
                    for position, product in enumerate(visible_products, start=1)
                ],
            },
        ],
    }
    schema_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    pagination_parts = []
    if page_number > 1:
        previous_url = category_page_url(category_url, page_number - 1)
        pagination_parts.append(f'<a href="{html.escape(previous_url, quote=True)}">ก่อนหน้า</a>')
    if page_number < page_count:
        next_url = category_page_url(category_url, page_number + 1)
        pagination_parts.append(f'<a href="{html.escape(next_url, quote=True)}">ถัดไป</a>')
    pagination_html = (
        '<nav class="pagination" aria-label="หน้าสินค้า">' + "".join(pagination_parts) + "</nav>"
        if pagination_parts else ""
    )
    return f"""<!doctype html><html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(metadata_title)}</title>
<meta name="description" content="{html.escape(metadata_description, quote=True)}">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Pickora"><meta property="og:title" content="{html.escape(category, quote=True)} สินค้าแนะนำ | Pickora"><meta property="og:url" content="{html.escape(canonical, quote=True)}">
<meta name="twitter:card" content="summary">
<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/content.css"><link rel="stylesheet" href="/catalog.css"><link rel="stylesheet" href="/product.css">
<script type="application/ld+json">{schema_json}</script></head><body>
<header class="header"><div class="container nav"><a class="brand" href="/"><span class="logo">P</span><span>Pickora</span></a></div></header>
<main><section class="section"><div class="container">
<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">หน้าแรก</a> / <a href="/categories/">หมวดหมู่</a> / <span aria-current="page">{html.escape(category)}</span>{f' / <span aria-current="page">หน้า {page_number}</span>' if page_number > 1 else ''}</nav>
<div class="category-header"><h1>{html.escape(category)}{f' · หน้า {page_number}' if page_number > 1 else ''}</h1>{f'<p>{html.escape(intro)}</p>' if intro else ''}<p>พบ {total_products:,} สินค้าที่ระบบคัดไว้ แสดงรายการ {len(visible_products):,} รายการ{f' · หน้า {page_number}' if page_count > 1 else ''}</p></div>
{considerations_html}
{faq_html}
<section class="category-products"><h2>สินค้าในหมวดนี้</h2><p>สินค้าเรียงตาม Pickora Score โดยใช้ข้อมูลรายการที่มีในระบบ</p><div class="grid">{cards}</div></section></div></section></main>
{guide_links}
{pagination_html}
<p class="more-guides"><a href="/guides/">อ่านคู่มือเลือกซื้อเพิ่มเติม</a></p>
<footer><div class="container"><a href="/">หน้าแรก</a> · <a href="/guides/">คู่มือ</a> · <a href="/about/">เกี่ยวกับเรา</a> · <a href="/methodology/">วิธีคัดเลือกสินค้า</a> · <a href="/privacy/">ความเป็นส่วนตัว</a> · <a href="/affiliate-disclosure/">Affiliate Disclosure</a></div></footer>
</body></html>"""


def create_category_index(
    categories: list[tuple[str, str, int]], thin_products: list[dict[str, object]]
) -> str:
    canonical = f"{SITE_URL}/categories/"
    links = "".join(
        f'<a class="content-card" href="{html.escape(url, quote=True)}"><strong>{html.escape(category)}</strong><span>{count:,} สินค้าที่ระบบคัดไว้</span><span>{html.escape(category_intro(category))}</span></a>'
        for category, url, count in categories
    )
    graph = {
        "@context": "https://schema.org",
        "@graph": [breadcrumb_schema([("หน้าแรก", "/"), ("หมวดหมู่", "/categories/")])],
    }
    schema_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    fallback = ""
    if thin_products:
        fallback = (
            '<section class="section"><h2>สินค้าเพิ่มเติม</h2>'
            '<p>สินค้าจากหมวดหมู่ที่ยังมีรายการไม่มาก</p><div class="grid">'
            + "".join(product_card(product) for product in thin_products)
            + "</div></section>"
        )
    return f"""<!doctype html><html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>หมวดหมู่สินค้า | Pickora</title>
<meta name="description" content="เลือกดูสินค้าที่ Pickora คัดไว้ตามหมวดหมู่ พร้อมข้อมูลราคา คะแนน และยอดขายเพื่อเปรียบเทียบก่อนดูรายละเอียดสินค้า">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/content.css"><link rel="stylesheet" href="/catalog.css">
<script type="application/ld+json">{schema_json}</script></head><body>
<header class="header"><div class="container nav"><a class="brand" href="/"><span class="logo">P</span><span>Pickora</span></a></div></header>
<main><section class="section"><div class="container"><nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">หน้าแรก</a> / <span aria-current="page">หมวดหมู่</span></nav><div class="category-header"><h1>หมวดหมู่สินค้า</h1><p>เลือกดูสินค้าที่ Pickora คัดไว้ตามหมวดหมู่</p></div><div class="content-cards">{links}</div>{fallback}</div></section></main>
<footer><div class="container"><a href="/">หน้าแรก</a> · <a href="/guides/">คู่มือ</a> · <a href="/about/">เกี่ยวกับเรา</a> · <a href="/methodology/">วิธีคัดเลือกสินค้า</a> · <a href="/privacy/">ความเป็นส่วนตัว</a> · <a href="/affiliate-disclosure/">Affiliate Disclosure</a></div></footer>
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
    indexable_categories = {
        category: category_products
        for category, category_products in products_by_category.items()
        if len(category_products) >= CATEGORY_MIN_PRODUCTS
    }
    metadata_titles = product_metadata_titles(products)
    try:
        for product in products:
            page_dir = temporary_pages / str(product["id"])
            page_dir.mkdir()
            category = str(product.get("category") or "สินค้าแนะนำ")
            page_product = dict(product)
            page_product["_metadataTitle"] = metadata_titles[str(product["id"])]
            # Thin categories deliberately have no landing page, so their
            # product pages retain category text without emitting a dead link.
            if category not in indexable_categories:
                page_product["categoryUrl"] = ""
            related = select_related_products(
                page_product, products_by_category[category], limit=6,
            )
            (page_dir / "index.html").write_text(
                create_product_page(page_product, related), encoding="utf-8"
            )
        if indexable_categories:
            category_index = [
                (category, str(category_products[0]["categoryUrl"]), len(category_products))
                for category, category_products in sorted(indexable_categories.items())
            ]
            thin_products = ranked_products([
                product
                for category_products in products_by_category.values()
                if len(category_products) < CATEGORY_MIN_PRODUCTS
                for product in category_products
            ])[:THIN_CATEGORY_DISCOVERY_LIMIT]
            (temporary_categories / "index.html").write_text(
                create_category_index(category_index, thin_products), encoding="utf-8"
            )
        for category, category_products in indexable_categories.items():
            page_dir = temporary_categories / category_id(category)
            page_dir.mkdir()
            category_url = str(category_products[0]["categoryUrl"])
            pages = paginate_products(ranked_products(category_products), CATEGORY_PAGE_PRODUCT_LIMIT)
            for page_number, page_products in enumerate(pages, start=1):
                output_dir = page_dir if page_number == 1 else page_dir / "page" / str(page_number)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "index.html").write_text(
                    create_category_page(
                        category, category_url, page_products, page_number,
                        len(pages), len(category_products),
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

    entries = [
        f"<url><loc>{html.escape(SITE_URL + path)}</loc><changefreq>{frequency}</changefreq><priority>{priority}</priority></url>"
        for path, frequency, priority in STATIC_SITEMAP_PATHS
        if path != "/categories/" or indexable_categories
    ]
    entries.extend(
        f"<url><loc>{html.escape(SITE_URL + str(product['detailUrl']))}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>"
        for product in products
    )
    entries.extend(
        f"<url><loc>{html.escape(SITE_URL + category_page_url(str(category_products[0]['categoryUrl']), page_number))}</loc><changefreq>daily</changefreq><priority>0.7</priority></url>"
        for category_products in indexable_categories.values()
        for page_number, _ in enumerate(
            paginate_products(ranked_products(category_products), CATEGORY_PAGE_PRODUCT_LIMIT),
            start=1,
        )
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n</urlset>\n"
    )
    temporary_sitemap = SITEMAP_FILE.with_suffix(".tmp")
    temporary_sitemap.write_text(sitemap, encoding="utf-8")
    temporary_sitemap.replace(SITEMAP_FILE)
    temporary_homepage_catalog = HOMEPAGE_CATALOG_FILE.with_suffix(".tmp")
    temporary_homepage_catalog.write_text(
        create_homepage_catalog(products), encoding="utf-8"
    )
    temporary_homepage_catalog.replace(HOMEPAGE_CATALOG_FILE)


def process_feed() -> None:
    if not FEED_FILE.exists():
        raise FileNotFoundError(FEED_FILE)

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    write_status("processing")

    affiliate_id = os.getenv("SHOPEE_AFFILIATE_ID", "").strip()
    sub_id_prefix = os.getenv("SHOPEE_SUB_ID_PREFIX", "pickora").strip()
    pipeline_environment = os.getenv("PIPELINE_ENV", "production").strip().lower()
    allow_untracked_links = pipeline_environment in {"development", "dev", "test"}
    if not affiliate_id and not allow_untracked_links:
        raise RuntimeError(
            "SHOPEE_AFFILIATE_ID is required in production pipeline mode"
        )
    if not affiliate_id:
        logging.warning(
            "SHOPEE_AFFILIATE_ID is missing; development output will use "
            "canonical, untracked Shopee product links"
        )

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
            "image_urls", "images", "product_images", "item_images",
            "image_url", "image_link", "product_image", "image", "item_image",
            "imageurl",
        ])
        link_col = find_column(columns, [
            "product_link", "product_url", "item_url", "offer_link",
        ])
        price_cols = [
            column
            for candidate in (
                "sale_price", "current_price", "price_min", "min_price",
                "lowest_price", "discounted_price", "price", "product_price",
            )
            if (column := find_column(columns, [candidate])) is not None
        ]
        price_max_col = find_column(columns, [
            "price_max", "max_price", "highest_price",
        ])
        rating_col = find_column(columns, [
            "rating", "item_rating", "product_rating"
        ])
        sold_col = find_column(columns, [
            "sold", "sales", "historical_sold", "item_sold", "sales_volume"
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
        shop_id_col = find_column(columns, ["shopid", "shop_id", "seller_id"])
        product_id_col = find_column(columns, [
            "itemid", "item_id", "product_id", "offer_id", "sku_id",
            "product_sku",
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

        filtered["_price"] = 0.0
        for price_col in price_cols:
            candidate_price = numeric(filtered[price_col])
            filtered["_price"] = filtered["_price"].where(
                filtered["_price"] > 0,
                candidate_price.where(candidate_price > 0, 0),
            )
        filtered["_price_max"] = (
            numeric(filtered[price_max_col]) if price_max_col
            else filtered["_price"]
        )
        filtered["_price_max"] = filtered["_price_max"].where(
            filtered["_price_max"] >= filtered["_price"],
            filtered["_price"],
        )
        filtered["_rating"] = numeric(filtered[rating_col]) if rating_col else 0
        filtered["_sold"] = numeric(filtered[sold_col]) if sold_col else 0
        filtered["_discount"] = (
            numeric(filtered[discount_col]) if discount_col else 0
        )

        if price_cols:
            filtered = filtered[filtered["_price"].between(MIN_PRICE, MAX_PRICE)]
        if rating_col:
            filtered = filtered[filtered["_rating"] >= MIN_RATING]
        if sold_col:
            filtered = filtered[filtered["_sold"] >= MIN_SOLD]

        filtered["_score"] = (
            filtered["_sold"].clip(upper=100000) * WEIGHT_SOLD
            + filtered["_rating"] * WEIGHT_RATING
            + filtered["_discount"] * WEIGHT_DISCOUNT
        )

        output = pd.DataFrame({
            "title": filtered[title_col].astype(str),
            "image": filtered[image_col].astype(str),
            "productUrl": filtered[link_col].astype(str).str.strip(),
            "price": filtered["_price"].round(2),
            "priceMax": filtered["_price_max"].round(2),
            "rating": filtered["_rating"].round(2),
            "sold": filtered["_sold"].round(0),
            "commission": None,
            "commissionStatus": "unknown",
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
            "shopId": (
                filtered[shop_id_col].astype(str)
                if shop_id_col else ""
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
        result["productUrl"].astype(str),
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
        if urlparse(str(product.get("productUrl", "")).strip()).scheme == "https"
        and urlparse(str(product.get("productUrl", "")).strip()).hostname in SHOPEE_PRODUCT_HOSTS
        and product_images(product.get("image", ""))
    ]
    invalid_urls = len(all_records) - len(records)
    record_count = len(records)
    price_updated_at = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    for rank, product in enumerate(records):
        normalise_product_text(product)
        images = product_images(product.get("image", ""))
        product["image"] = images[0]
        product["images"] = images
        external_id = str(product.get("externalId") or "").strip()
        identity = (
            f"feed:{external_id}"
            if external_id.lower() not in {"", "nan", "none", "null"}
            else f"link:{product['productUrl']}"
        )
        identifier = product_id(identity)
        sub_id = build_product_sub_id(sub_id_prefix, external_id, identifier)
        affiliate_url = (
            build_shopee_affiliate_link(
                str(product["productUrl"]), affiliate_id, sub_id,
            )
            if affiliate_id else str(product["productUrl"])
        )
        product["affiliateUrl"] = affiliate_url
        product["link"] = affiliate_url
        category = display_category(product.get("category"))
        product["category"] = category
        product["id"] = identifier
        product["detailUrl"] = f"/products/{identifier}/"
        product["pickoraScore"] = (
            100 if record_count == 1
            else round(100 - (rank / (record_count - 1)) * 50)
        )
        product["priceUpdatedAt"] = price_updated_at

    category_counts: dict[str, int] = {}
    for product in records:
        category = str(product["category"])
        category_counts[category] = category_counts.get(category, 0) + 1
    for product in records:
        category = str(product["category"])
        product["categoryUrl"] = (
            f"/categories/{category_id(category)}/"
            if category_counts[category] >= CATEGORY_MIN_PRODUCTS else ""
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
