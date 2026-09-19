"""Collect Google Search Console query × page data without risking prior rows."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from seo_storage import connect, create_gsc_run, fail_gsc_run, migrate, store_page_query_rows

logger = logging.getLogger(__name__)
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
DEFAULT_SITE = "sc-domain:findvexa.com"


def credentials_from_b64(value: str):
    try:
        info = json.loads(base64.b64decode(value.strip(), validate=True).decode("utf-8"))
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        credentials.refresh(Request())
        return credentials
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Search Console credentials are invalid") from error


def collect(site_url: str, start_date: str, end_date: str, db_path: Path, credentials_b64: str, session=requests) -> int:
    connection = connect(db_path); migrate(connection)
    run_id = create_gsc_run(connection, site_url, start_date, end_date)
    try:
        credentials = credentials_from_b64(credentials_b64)
        response = session.post(
            "https://www.googleapis.com/webmasters/v3/sites/" + quote(site_url, safe="") + "/searchAnalytics/query",
            headers={"Authorization": f"Bearer {credentials.token}"},
            json={"startDate": start_date, "endDate": end_date, "dimensions": ["query", "page"], "rowLimit": 25000}, timeout=30,
        )
        response.raise_for_status()
        rows = response.json().get("rows", [])
        return store_page_query_rows(connection, run_id, start_date, end_date, rows)
    except Exception as error:
        fail_gsc_run(connection, run_id, type(error).__name__)
        logger.warning("Search Console collection failed: %s", type(error).__name__)
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=28)
    args = parser.parse_args()
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=max(1, args.days) - 1)
    try:
        count = collect(os.getenv("SEARCH_CONSOLE_SITE_URL", DEFAULT_SITE), start.isoformat(), end.isoformat(), Path(os.getenv("SEARCH_CONSOLE_DB", "/app/data/search_console.sqlite3")), os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", ""))
    except Exception:
        return 1
    logger.info("Stored %s Search Console page-query rows", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
