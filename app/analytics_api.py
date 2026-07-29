from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import secrets
import threading
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, make_response, redirect, request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ALLOWED_DAYS = {7, 28, 90}
SESSION_COOKIE = "pickora_analytics_session"
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


class AnalyticsConfigurationError(RuntimeError):
    pass


class AnalyticsUpstreamError(RuntimeError):
    pass


class SessionCodec:
    def __init__(self, secret: str, lifetime_seconds: int):
        self.lifetime_seconds = lifetime_seconds
        self.serializer = (
            URLSafeTimedSerializer(secret, salt="pickora-analytics-session-v1")
            if secret else None
        )

    def create(self) -> str:
        if not self.serializer:
            raise AnalyticsConfigurationError("Dashboard token is missing")
        return self.serializer.dumps({"nonce": secrets.token_urlsafe(16)})

    def valid(self, session_id: str | None) -> bool:
        if not session_id or not self.serializer:
            return False
        try:
            payload = self.serializer.loads(
                session_id, max_age=self.lifetime_seconds
            )
            return isinstance(payload, dict) and bool(payload.get("nonce"))
        except (BadSignature, SignatureExpired):
            return False


class GA4Client:
    def __init__(self, credentials_b64: str, property_id: str, timeout: float = 30):
        self.credentials_b64 = credentials_b64.strip()
        self.property_id = property_id.strip()
        self.timeout = timeout
        self._credentials: service_account.Credentials | None = None
        self._lock = threading.Lock()

    def configured(self) -> bool:
        return bool(self.credentials_b64 and self.property_id)

    def _get_credentials(self) -> service_account.Credentials:
        if not self.configured():
            raise AnalyticsConfigurationError(
                "GA4_PROPERTY_ID or GOOGLE_SERVICE_ACCOUNT_JSON_B64 is missing"
            )
        with self._lock:
            if self._credentials is None:
                try:
                    decoded = base64.b64decode(self.credentials_b64, validate=True)
                    info = json.loads(decoded.decode("utf-8"))
                    self._credentials = (
                        service_account.Credentials.from_service_account_info(
                            info, scopes=[ANALYTICS_SCOPE]
                        )
                    )
                except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    raise AnalyticsConfigurationError(
                        "Service-account configuration is invalid"
                    ) from error
            if not self._credentials.valid or self._credentials.expired:
                self._credentials.refresh(GoogleAuthRequest())
            return self._credentials

    def report(self, days: int) -> dict[str, Any]:
        credentials = self._get_credentials()
        url = (
            "https://analyticsdata.googleapis.com/v1beta/properties/"
            f"{quote(self.property_id, safe='')}:batchRunReports"
        )
        payload = {"requests": self._report_requests(days)}
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as error:
            logger.warning("GA4 Data API request failed: %s", type(error).__name__)
            raise AnalyticsUpstreamError("GA4 Data API request failed") from error
        reports = body.get("reports", [])
        if len(reports) != 5:
            raise AnalyticsUpstreamError("GA4 Data API returned an incomplete response")
        return self._format_report(days, reports)

    @staticmethod
    def _report_requests(days: int) -> list[dict[str, Any]]:
        date_range = [{"startDate": f"{days}daysAgo", "endDate": "yesterday"}]
        return [
            {
                "dateRanges": date_range,
                "metrics": [
                    {"name": "activeUsers"}, {"name": "sessions"},
                    {"name": "screenPageViews"}, {"name": "engagementRate"},
                ],
            },
            {
                "dateRanges": date_range,
                "dimensions": [{"name": "landingPagePlusQueryString"}],
                "metrics": [
                    {"name": "sessions"}, {"name": "activeUsers"},
                    {"name": "screenPageViews"}, {"name": "engagementRate"},
                ],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                "limit": "10",
            },
            {
                "dateRanges": date_range,
                "dimensions": [{"name": "sessionSource"}, {"name": "sessionMedium"}],
                "metrics": [
                    {"name": "sessions"}, {"name": "activeUsers"},
                    {"name": "engagementRate"},
                ],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                "limit": "10",
            },
            {
                "dateRanges": date_range,
                "dimensions": [{"name": "eventName"}],
                "metrics": [{"name": "eventCount"}],
                "dimensionFilter": {
                    "filter": {
                        "fieldName": "eventName",
                        "inListFilter": {
                            "values": ["affiliate_click", "outbound_click", "click"]
                        },
                    }
                },
            },
            {
                "dateRanges": date_range,
                "dimensions": [{"name": "date"}],
                "metrics": [
                    {"name": "activeUsers"}, {"name": "sessions"},
                    {"name": "screenPageViews"},
                ],
                "orderBys": [{"dimension": {"dimensionName": "date"}}],
            },
        ]

    @classmethod
    def _format_report(
        cls, days: int, reports: list[dict[str, Any]]
    ) -> dict[str, Any]:
        total_values = cls._metric_values((reports[0].get("rows") or [{}])[0])
        totals = {
            "activeUsers": cls._integer(total_values, 0),
            "sessions": cls._integer(total_values, 1),
            "pageViews": cls._integer(total_values, 2),
            "engagementRate": cls._decimal(total_values, 3),
        }
        landing_pages = [
            {
                "page": cls._dimension(row, 0),
                "sessions": cls._integer(cls._metric_values(row), 0),
                "activeUsers": cls._integer(cls._metric_values(row), 1),
                "pageViews": cls._integer(cls._metric_values(row), 2),
                "engagementRate": cls._decimal(cls._metric_values(row), 3),
            }
            for row in reports[1].get("rows", [])
        ]
        traffic = [
            {
                "source": cls._dimension(row, 0),
                "medium": cls._dimension(row, 1),
                "sessions": cls._integer(cls._metric_values(row), 0),
                "activeUsers": cls._integer(cls._metric_values(row), 1),
                "engagementRate": cls._decimal(cls._metric_values(row), 2),
            }
            for row in reports[2].get("rows", [])
        ]
        click_events = {
            cls._dimension(row, 0): cls._integer(cls._metric_values(row), 0)
            for row in reports[3].get("rows", [])
        }
        daily = [
            {
                "date": cls._dimension(row, 0),
                "activeUsers": cls._integer(cls._metric_values(row), 0),
                "sessions": cls._integer(cls._metric_values(row), 1),
                "pageViews": cls._integer(cls._metric_values(row), 2),
            }
            for row in reports[4].get("rows", [])
        ]
        return {
            "rangeDays": days,
            "totals": totals,
            "landingPages": landing_pages,
            "trafficSources": traffic,
            "clickEvents": click_events,
            "affiliateClicks": sum(click_events.values()),
            "dailyTrend": daily,
        }

    @staticmethod
    def _metric_values(row: dict[str, Any]) -> list[dict[str, str]]:
        return row.get("metricValues") or []

    @staticmethod
    def _dimension(row: dict[str, Any], index: int) -> str:
        values = row.get("dimensionValues") or []
        return values[index].get("value", "") if len(values) > index else ""

    @staticmethod
    def _integer(values: list[dict[str, str]], index: int) -> int:
        return int(float(values[index].get("value") or 0)) if len(values) > index else 0

    @staticmethod
    def _decimal(values: list[dict[str, str]], index: int) -> float:
        return float(values[index].get("value") or 0) if len(values) > index else 0.0


def create_app(
    config: dict[str, Any] | None = None,
    ga_client: GA4Client | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update({
        "DASHBOARD_TOKEN": os.getenv("ANALYTICS_DASHBOARD_TOKEN", ""),
        "SESSION_SECONDS": int(os.getenv("ANALYTICS_SESSION_SECONDS", "28800")),
        "COOKIE_SECURE": os.getenv("ANALYTICS_COOKIE_SECURE", "true").lower()
        not in {"0", "false", "no"},
        "COOKIE_PATH": "/api/analytics/",
    })
    if config:
        app.config.update(config)
    client = ga_client or GA4Client(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", ""),
        os.getenv("GA4_PROPERTY_ID", ""),
        float(os.getenv("GOOGLE_API_TIMEOUT", "30")),
    )
    sessions = SessionCodec(
        str(app.config["DASHBOARD_TOKEN"]), app.config["SESSION_SECONDS"]
    )

    def authenticated() -> bool:
        return sessions.valid(request.cookies.get(SESSION_COOKIE))

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "not_found"}), 404

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "configured": client.configured()})

    @app.post("/login")
    def login():
        expected = str(app.config["DASHBOARD_TOKEN"])
        supplied = request.form.get("token", "")
        if not expected:
            return redirect("/analytics/?auth=unconfigured", code=303)
        if not hmac.compare_digest(supplied, expected):
            return redirect("/analytics/?auth=failed", code=303)
        response = make_response(redirect("/analytics/", code=303))
        response.set_cookie(
            SESSION_COOKIE,
            sessions.create(),
            max_age=app.config["SESSION_SECONDS"],
            httponly=True,
            secure=app.config["COOKIE_SECURE"],
            samesite="Strict",
            path=app.config["COOKIE_PATH"],
        )
        return response

    @app.post("/logout")
    def logout():
        response = make_response("", 204)
        response.delete_cookie(SESSION_COOKIE, path=app.config["COOKIE_PATH"])
        return response

    @app.get("/session")
    def session_status():
        if not authenticated():
            return jsonify({"authenticated": False}), 401
        return jsonify({"authenticated": True, "configured": client.configured()})

    @app.get("/report")
    def report():
        if not authenticated():
            return jsonify({"error": "unauthorized"}), 401
        try:
            days = int(request.args.get("days", "28"))
        except ValueError:
            return jsonify({"error": "invalid_date_range"}), 400
        if days not in ALLOWED_DAYS:
            return jsonify({"error": "invalid_date_range"}), 400
        try:
            return jsonify(client.report(days))
        except AnalyticsConfigurationError:
            return jsonify({"error": "analytics_not_configured"}), 503
        except AnalyticsUpstreamError:
            return jsonify({"error": "ga4_api_error"}), 502
        except Exception:
            logger.exception("Unexpected analytics API failure")
            return jsonify({"error": "internal_error"}), 500

    return app


app = create_app()
