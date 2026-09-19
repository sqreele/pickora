"""Durable local storage for product history and Search Console page/query rows."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS seo_gsc_runs (
      id INTEGER PRIMARY KEY, site_url TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
      requested_dimensions TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
      finished_at TEXT, rows_imported INTEGER NOT NULL DEFAULT 0, api_response_rows INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, error_message TEXT, import_version INTEGER NOT NULL DEFAULT 1,
      UNIQUE(site_url,start_date,end_date,requested_dimensions)
    );
    CREATE TABLE IF NOT EXISTS product_history (
      product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL, category TEXT NOT NULL,
      image_url TEXT, last_reference_price REAL, affiliate_url TEXT,
      first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_active_at TEXT NOT NULL,
      historical_payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS seo_gsc_page_query_rows (
      run_id INTEGER NOT NULL REFERENCES seo_gsc_runs(id) ON DELETE CASCADE,
      start_date TEXT NOT NULL, end_date TEXT NOT NULL, query TEXT NOT NULL, page TEXT NOT NULL,
      clicks REAL NOT NULL, impressions REAL NOT NULL, ctr REAL NOT NULL, position REAL NOT NULL,
      collected_at TEXT NOT NULL,
      PRIMARY KEY (run_id, query, page)
    );
    """)
    connection.commit()


def upsert_active_products(connection: sqlite3.Connection, products: Iterable[dict]) -> None:
    timestamp = now()
    for product in products:
        identifier = str(product["id"])
        name = str(product.get("title") or "").strip()
        category = str(product.get("category") or "").strip()
        if not name or not category:
            continue
        payload = json.dumps({
            "title": name, "category": category, "image": str(product.get("image") or ""),
            "price": float(product.get("price") or 0), "priceMax": float(product.get("priceMax") or 0),
        }, ensure_ascii=False, separators=(",", ":"))
        connection.execute("""
        INSERT INTO product_history(product_id,product_name,category,image_url,last_reference_price,affiliate_url,first_seen_at,last_seen_at,last_active_at,historical_payload_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_id) DO UPDATE SET
          product_name=CASE WHEN excluded.product_name<>'' THEN excluded.product_name ELSE product_history.product_name END,
          category=CASE WHEN excluded.category<>'' THEN excluded.category ELSE product_history.category END,
          image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE product_history.image_url END,
          last_reference_price=CASE WHEN excluded.last_reference_price>0 THEN excluded.last_reference_price ELSE product_history.last_reference_price END,
          affiliate_url=CASE WHEN excluded.affiliate_url<>'' THEN excluded.affiliate_url ELSE product_history.affiliate_url END,
          last_seen_at=excluded.last_seen_at,last_active_at=excluded.last_active_at,
          historical_payload_json=excluded.historical_payload_json,updated_at=excluded.updated_at
        """, (identifier,name,category,str(product.get("image") or ""),float(product.get("price") or 0),str(product.get("affiliateUrl") or ""),timestamp,timestamp,timestamp,payload,timestamp,timestamp))
    connection.commit()


def archived_products(connection: sqlite3.Connection, active_ids: set[str]) -> list[dict]:
    rows = connection.execute("SELECT * FROM product_history ORDER BY last_active_at DESC").fetchall()
    return [dict(row) for row in rows if row["product_id"] not in active_ids and useful_archive(dict(row))]


def useful_archive(record: dict) -> bool:
    return bool(record.get("product_id") and record.get("product_name") and record.get("category") and record.get("last_reference_price") and record.get("last_active_at"))


def create_gsc_run(connection: sqlite3.Connection, site_url: str, start_date: str, end_date: str) -> int:
    timestamp = now()
    connection.execute("""
      INSERT INTO seo_gsc_runs(site_url,start_date,end_date,requested_dimensions,status,started_at,created_at)
      VALUES(?,?,?,'query,page','running',?,?)
      ON CONFLICT(site_url,start_date,end_date,requested_dimensions) DO UPDATE SET status='running',started_at=excluded.started_at,finished_at=NULL,error_message=NULL
    """, (site_url,start_date,end_date,timestamp,timestamp))
    row = connection.execute("SELECT id FROM seo_gsc_runs WHERE site_url=? AND start_date=? AND end_date=? AND requested_dimensions='query,page'", (site_url,start_date,end_date)).fetchone()
    connection.commit()
    return int(row["id"])


def store_page_query_rows(connection: sqlite3.Connection, run_id: int, start_date: str, end_date: str, rows: Iterable[dict]) -> int:
    collected_at = now(); count = 0
    for row in rows:
        keys = row.get("keys") or []
        if len(keys) != 2: continue
        connection.execute("""
        INSERT INTO seo_gsc_page_query_rows VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id,query,page) DO UPDATE SET clicks=excluded.clicks,impressions=excluded.impressions,ctr=excluded.ctr,position=excluded.position,collected_at=excluded.collected_at
        """, (run_id,start_date,end_date,str(keys[0]),str(keys[1]),float(row.get("clicks") or 0),float(row.get("impressions") or 0),float(row.get("ctr") or 0),float(row.get("position") or 0),collected_at))
        count += 1
    connection.execute("UPDATE seo_gsc_runs SET status='success',finished_at=?,rows_imported=?,api_response_rows=? WHERE id=?", (now(),count,count,run_id))
    connection.commit(); return count


def fail_gsc_run(connection: sqlite3.Connection, run_id: int, error_kind: str) -> None:
    connection.execute("UPDATE seo_gsc_runs SET status='failed',finished_at=?,error_message=? WHERE id=?", (now(),error_kind,run_id))
    connection.commit()
