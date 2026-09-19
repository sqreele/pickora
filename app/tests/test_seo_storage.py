from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from seo_storage import (
    archived_products, connect, create_gsc_run, migrate, store_page_query_rows,
    upsert_active_products,
)
from search_console import collect
from run_pipeline import create_archived_product_page


class SeoStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "seo.sqlite3"
        self.connection = connect(self.path); migrate(self.connection)

    def tearDown(self):
        self.connection.close(); self.directory.cleanup()

    def product(self, title="Historical product", price=199):
        return {"id": "a" * 16, "title": title, "category": "Beauty", "image": "https://img.test/a", "price": price, "affiliateUrl": "https://s.shopee.co.th/example"}

    def test_active_product_persists_and_disappeared_product_is_recoverable(self):
        upsert_active_products(self.connection, [self.product()])
        archived = archived_products(self.connection, set())
        self.assertEqual(archived[0]["product_name"], "Historical product")
        self.assertEqual(archived[0]["last_reference_price"], 199)

    def test_blank_new_data_does_not_erase_historical_fields(self):
        upsert_active_products(self.connection, [self.product()])
        upsert_active_products(self.connection, [self.product(title="", price=0)])
        row = self.connection.execute("SELECT product_name,last_reference_price FROM product_history").fetchone()
        self.assertEqual(tuple(row), ("Historical product", 199))

    def test_page_query_rows_upsert_without_duplicates(self):
        run_id = create_gsc_run(self.connection, "sc-domain:findvexa.com", "2026-09-01", "2026-09-28")
        rows = [{"keys": ["query", "https://findvexa.com/products/" + "a" * 16 + "/"], "clicks": 1, "impressions": 5, "ctr": .2, "position": 7}]
        store_page_query_rows(self.connection, run_id, "2026-09-01", "2026-09-28", rows)
        rows[0]["clicks"] = 2
        store_page_query_rows(self.connection, run_id, "2026-09-01", "2026-09-28", rows)
        saved = self.connection.execute("SELECT clicks,count(*) OVER() FROM seo_gsc_page_query_rows").fetchone()
        self.assertEqual(tuple(saved), (2.0, 1))

    def test_failed_collection_retains_prior_rows(self):
        run_id = create_gsc_run(self.connection, "sc-domain:findvexa.com", "2026-08-01", "2026-08-28")
        store_page_query_rows(self.connection, run_id, "2026-08-01", "2026-08-28", [{"keys": ["q", "https://findvexa.com/"], "clicks": 1, "impressions": 1, "ctr": 1, "position": 1}])
        self.connection.close()
        with self.assertRaises(RuntimeError):
            collect("sc-domain:findvexa.com", "2026-09-01", "2026-09-28", self.path, "not-base64")
        connection = sqlite3.connect(self.path)
        self.assertEqual(connection.execute("SELECT count(*) FROM seo_gsc_page_query_rows").fetchone()[0], 1)
        connection.close()

    def test_fact_complete_archive_is_truthful_and_uses_only_allowed_schema(self):
        page = create_archived_product_page({
            "product_id": "b" * 16, "product_name": "Known former product",
            "category": "Beauty", "last_reference_price": 250,
            "last_active_at": "2026-09-01T00:00:00+00:00",
        }, [{"id": "c" * 16, "detailUrl": "/products/" + "c" * 16 + "/", "title": "Related active product", "category": "Beauty", "price": 100, "pickoraScore": 80, "image": "https://img.test/c"}])
        self.assertIn("สินค้านี้ไม่อยู่ในรายการอัปเดตล่าสุดของ FindVexa", page)
        self.assertIn("สินค้าที่เกี่ยวข้องในหมวดเดียวกัน", page)
        self.assertIn('"@type": "WebPage"', page)
        self.assertIn('"@type": "BreadcrumbList"', page)
        self.assertNotIn('"@type": "Product"', page)
        self.assertNotIn("เช็กราคาล่าสุดใน Shopee", page)


if __name__ == "__main__":
    unittest.main()
