from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ProductionRouteTest(unittest.TestCase):
    def test_analytics_routes_do_not_use_spa_fallback(self):
        nginx = (ROOT / "nginx/default.conf").read_text(encoding="utf-8")
        self.assertIn("location = /analytics {", nginx)
        self.assertIn("location = /analytics/ {", nginx)
        self.assertIn("location ^~ /analytics/ {", nginx)
        self.assertIn("location ^~ /api/analytics/ {", nginx)
        self.assertIn("proxy_pass http://analytics-api:8000/;", nginx)
        analytics_block = nginx.split("location ^~ /analytics/ {", 1)[1].split("}", 1)[0]
        api_block = nginx.split("location ^~ /api/analytics/ {", 1)[1].split("}", 1)[0]
        self.assertNotIn("/index.html", analytics_block)
        self.assertNotIn("/index.html", api_block)

    def test_dashboard_assets_are_in_production_frontend(self):
        for path in (
            "frontend/analytics/index.html",
            "frontend/analytics/dashboard.js",
            "frontend/analytics/dashboard.css",
        ):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
