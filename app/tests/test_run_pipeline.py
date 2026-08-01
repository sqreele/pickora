from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import run_pipeline  # noqa: E402
from run_pipeline import display_category  # noqa: E402


class DisplayCategoryTest(unittest.TestCase):
    def test_replaces_foreign_product_labels_with_thai_fallback(self):
        for category in ("Product", "products", "Foreign", "ต่างด้าว"):
            with self.subTest(category=category):
                self.assertEqual(display_category(category), "สินค้าแนะนำ")

    def test_replaces_empty_feed_values(self):
        for category in (None, "", "nan", "null"):
            with self.subTest(category=category):
                self.assertEqual(display_category(category), "สินค้าแนะนำ")

    def test_preserves_real_category(self):
        self.assertEqual(display_category("เครื่องใช้ไฟฟ้า"), "เครื่องใช้ไฟฟ้า")


class GeneratedPagePermissionsTest(unittest.TestCase):
    def test_generated_document_roots_are_traversable_by_nginx(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            products_dir = public_dir / "products"
            categories_dir = public_dir / "categories"
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=products_dir,
                CATEGORY_PAGES_DIR=categories_dir,
                SITEMAP_FILE=public_dir / "sitemap.xml",
            ):
                run_pipeline.write_product_pages_and_sitemap([])

            self.assertEqual(products_dir.stat().st_mode & 0o777, 0o755)
            self.assertEqual(categories_dir.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
