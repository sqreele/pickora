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
            "frontend/analytics/dashboard-state.css",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_quantified_location_regexes_are_quoted_for_nginx(self):
        nginx = (ROOT / "nginx/default.conf").read_text(encoding="utf-8")
        self.assertIn('location ~ "^/products/([a-f0-9]{16})/?$" {', nginx)
        self.assertIn('location ~ "^/categories/([a-f0-9]{12})/$" {', nginx)

    def test_product_routes_do_not_fall_back_to_home_page(self):
        nginx = (ROOT / "nginx/default.conf").read_text(encoding="utf-8")
        product_block = nginx.split(
            'location ~ "^/products/([a-f0-9]{16})/?$" {', 1
        )[1].split("}", 1)[0]
        self.assertIn("try_files /data/products/$1/index.html =404;", product_block)
        self.assertIn("location /products/ {", nginx)
        self.assertNotIn("try_files $uri $uri/ /index.html", product_block)

    def test_dashboard_hidden_states_cannot_be_overridden(self):
        css = (
            ROOT / "frontend/analytics/dashboard-state.css"
        ).read_text(encoding="utf-8")
        self.assertIn("display: none !important", css)


if __name__ == "__main__":
    unittest.main()
