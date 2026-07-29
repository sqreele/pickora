from __future__ import annotations

import unittest

from analytics_api import GA4Client, create_app


SAMPLE_REPORT = {
    "rangeDays": 28,
    "totals": {
        "activeUsers": 42,
        "sessions": 60,
        "pageViews": 120,
        "engagementRate": 0.65,
    },
    "landingPages": [],
    "trafficSources": [],
    "clickEvents": {"affiliate_click": 8},
    "affiliateClicks": 8,
    "dailyTrend": [],
}


class FakeGA4Client:
    def __init__(self, configured: bool = True):
        self.is_configured = configured
        self.requested_days: list[int] = []

    def configured(self) -> bool:
        return self.is_configured

    def report(self, days: int):
        self.requested_days.append(days)
        return {**SAMPLE_REPORT, "rangeDays": days}


class AnalyticsApiTest(unittest.TestCase):
    def setUp(self):
        self.ga = FakeGA4Client()
        self.app = create_app({
            "TESTING": True,
            "DASHBOARD_TOKEN": "test-dashboard-token",
            "COOKIE_SECURE": False,
            "COOKIE_PATH": "/",
            "SESSION_SECONDS": 3600,
        }, ga_client=self.ga)
        self.client = self.app.test_client()

    def login(self, token: str = "test-dashboard-token"):
        return self.client.post("/login", data={"token": token})

    def test_report_requires_authentication(self):
        response = self.client.get("/report?days=28")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "unauthorized"})

    def test_missing_api_route_returns_json_404(self):
        response = self.client.get("/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json, {"error": "not_found"})

    def test_login_uses_http_only_cookie_and_does_not_echo_token(self):
        secure_app = create_app({
            "TESTING": True,
            "DASHBOARD_TOKEN": "test-dashboard-token",
            "COOKIE_SECURE": True,
            "COOKIE_PATH": "/",
            "SESSION_SECONDS": 3600,
        }, ga_client=self.ga)
        response = secure_app.test_client().post(
            "/login", data={"token": "test-dashboard-token"}
        )
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("test-dashboard-token", response.get_data(as_text=True))
        self.assertNotIn("test-dashboard-token", cookie)

    def test_invalid_login_does_not_create_session(self):
        response = self.login("wrong")
        self.assertEqual(response.status_code, 303)
        self.assertIn("auth=failed", response.headers["Location"])
        self.assertEqual(self.client.get("/session").status_code, 401)

    def test_authenticated_report_accepts_presets(self):
        self.login()
        for days in (7, 28, 90):
            response = self.client.get(f"/report?days={days}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["rangeDays"], days)
        self.assertEqual(self.ga.requested_days, [7, 28, 90])

    def test_report_rejects_arbitrary_ranges(self):
        self.login()
        for value in ("8", "abc", "365"):
            response = self.client.get(f"/report?days={value}")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["error"], "invalid_date_range")

    def test_logout_invalidates_session(self):
        self.login()
        self.assertEqual(self.client.get("/session").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 204)
        self.assertEqual(self.client.get("/session").status_code, 401)


class Ga4ResponseFormattingTest(unittest.TestCase):
    def test_formats_all_required_sections(self):
        reports = [
            {"rows": [{"metricValues": [
                {"value": "12"}, {"value": "18"}, {"value": "36"}, {"value": "0.75"}
            ]}]},
            {"rows": [{"dimensionValues": [{"value": "/landing"}], "metricValues": [
                {"value": "9"}, {"value": "8"}, {"value": "15"}, {"value": "0.7"}
            ]}]},
            {"rows": [{"dimensionValues": [{"value": "google"}, {"value": "organic"}],
                       "metricValues": [{"value": "7"}, {"value": "6"}, {"value": "0.8"}]}]},
            {"rows": [{"dimensionValues": [{"value": "affiliate_click"}],
                       "metricValues": [{"value": "5"}]}]},
            {"rows": [{"dimensionValues": [{"value": "20260728"}],
                       "metricValues": [{"value": "3"}, {"value": "4"}, {"value": "8"}]}]},
        ]
        report = GA4Client._format_report(28, reports)
        self.assertEqual(report["totals"]["activeUsers"], 12)
        self.assertEqual(report["totals"]["engagementRate"], 0.75)
        self.assertEqual(report["landingPages"][0]["page"], "/landing")
        self.assertEqual(report["trafficSources"][0]["medium"], "organic")
        self.assertEqual(report["affiliateClicks"], 5)
        self.assertEqual(report["dailyTrend"][0]["date"], "20260728")


if __name__ == "__main__":
    unittest.main()
