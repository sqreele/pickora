from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account

load_dotenv()

PUBLIC_DIR = Path("/app/public")
OUTPUT_FILE = PUBLIC_DIR / "analytics-summary.json"
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "").strip()
SEARCH_CONSOLE_SITE_URL = os.getenv("SEARCH_CONSOLE_SITE_URL", "").strip()
CREDENTIALS_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
TIMEOUT = float(os.getenv("GOOGLE_API_TIMEOUT", "30"))

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def load_credentials() -> service_account.Credentials | None:
    if not CREDENTIALS_B64:
        return None
    try:
        info = json.loads(base64.b64decode(CREDENTIALS_B64).decode("utf-8"))
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
        credentials.refresh(Request())
        return credentials
    except Exception as error:
        raise RuntimeError(
            f"Cannot load GOOGLE_SERVICE_ACCOUNT_JSON_B64: {type(error).__name__}"
        ) from error


def post_json(url: str, token: str, payload: dict[str, object]) -> dict[str, object]:
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def ga4_summary(token: str) -> dict[str, object]:
    if not GA4_PROPERTY_ID:
        return {"status": "not_configured"}
    url = (
        "https://analyticsdata.googleapis.com/v1beta/properties/"
        f"{quote(GA4_PROPERTY_ID, safe='')}:runReport"
    )
    totals = post_json(url, token, {
        "dateRanges": [{"startDate": "28daysAgo", "endDate": "yesterday"}],
        "metrics": [
            {"name": "activeUsers"}, {"name": "sessions"},
            {"name": "screenPageViews"}, {"name": "eventCount"},
        ],
    })
    events = post_json(url, token, {
        "dateRanges": [{"startDate": "28daysAgo", "endDate": "yesterday"}],
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {
            "filter": {
                "fieldName": "eventName",
                "inListFilter": {
                    "values": [
                        "view_item", "select_item", "begin_checkout",
                        "affiliate_click", "search", "share", "sign_up",
                    ]
                },
            }
        },
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
    })
    total_values = (totals.get("rows") or [{}])[0].get("metricValues") or []
    names = ["activeUsers", "sessions", "pageViews", "eventCount"]
    return {
        "status": "ready",
        "period": "28daysAgo..yesterday",
        "totals": {
            name: int(float(value.get("value") or 0))
            for name, value in zip(names, total_values)
        },
        "events": {
            row["dimensionValues"][0]["value"]: int(
                float(row["metricValues"][0]["value"])
            )
            for row in events.get("rows", [])
        },
    }


def search_console_summary(token: str) -> dict[str, object]:
    if not SEARCH_CONSOLE_SITE_URL:
        return {"status": "not_configured"}
    site = quote(SEARCH_CONSOLE_SITE_URL, safe="")
    end_date = datetime.now(timezone.utc).date() - timedelta(days=2)
    start_date = end_date - timedelta(days=27)
    response = post_json(
        f"https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query",
        token,
        {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
    )
    rows = response.get("rows", [])
    clicks = sum(float(row.get("clicks") or 0) for row in rows)
    impressions = sum(float(row.get("impressions") or 0) for row in rows)
    weighted_position = (
        sum(
            float(row.get("position") or 0) * float(row.get("impressions") or 0)
            for row in rows
        ) / impressions
        if impressions else 0
    )
    return {
        "status": "ready",
        "period": f"{start_date.isoformat()}..{end_date.isoformat()}",
        "clicks": round(clicks),
        "impressions": round(impressions),
        "ctr": round(clicks / impressions, 4) if impressions else 0,
        "averagePosition": round(weighted_position, 2),
    }


def main() -> int:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "not_configured",
        "ga4": {"status": "not_configured"},
        "searchConsole": {"status": "not_configured"},
    }
    try:
        credentials = load_credentials()
        if credentials:
            report["ga4"] = ga4_summary(credentials.token)
            report["searchConsole"] = search_console_summary(credentials.token)
            report["status"] = (
                "ready"
                if any(
                    section.get("status") == "ready"
                    for section in (report["ga4"], report["searchConsole"])
                )
                else "not_configured"
            )
    except Exception as error:
        logging.exception("Analytics summary failed")
        report["status"] = "error"
        report["error"] = type(error).__name__

    temporary = OUTPUT_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(OUTPUT_FILE)
    logging.info("Analytics summary status: %s", report["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
