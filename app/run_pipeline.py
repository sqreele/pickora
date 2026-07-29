from __future__ import annotations

import csv
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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

FEED_URL = os.getenv("SHOPEE_FEED_URL", "").strip()
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
    result = (
        result
        .drop_duplicates(subset=["link"])
        .nlargest(MAX_PRODUCTS, "score")
        .reset_index(drop=True)
    )

    temp = PRODUCTS_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            result.to_dict(orient="records"),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temp.replace(PRODUCTS_FILE)

    write_status(
        "ready",
        products=len(result),
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
