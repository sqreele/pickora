from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import run_pipeline  # noqa: E402
from run_pipeline import (  # noqa: E402
    build_product_sub_id,
    build_shopee_affiliate_link,
    create_product_page,
    display_category,
    normalise_product_text,
    product_images,
    repair_mojibake,
)


class ShopeeAffiliateLinkTest(unittest.TestCase):
    product_url = "https://shopee.co.th/product/10308716/18895969590"

    def test_builds_required_endpoint_and_parameters(self):
        link = build_shopee_affiliate_link(
            self.product_url, "affiliate-test", "pickora-product-18895969590"
        )
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), (
            "https", "s.shopee.co.th", "/an_redir",
        ))
        self.assertEqual(params["origin_link"], [self.product_url])
        self.assertEqual(params["affiliate_id"], ["affiliate-test"])
        self.assertEqual(params["sub_id"], ["pickora-product-18895969590"])

    def test_preserves_product_query_without_double_encoding(self):
        product_url = f"{self.product_url}?utm_source=feed&variation=42"
        link = build_shopee_affiliate_link(product_url, "123", "pickora-product-1")
        self.assertEqual(parse_qs(urlparse(link).query)["origin_link"], [product_url])
        self.assertNotIn("%252F", link)

    def test_empty_and_unapproved_urls_are_rejected(self):
        self.assertEqual(build_shopee_affiliate_link("  ", "123", "sub"), "")
        for url in (
            "http://shopee.co.th/product/1/2",
            "https://example.com/product/1/2",
            "https://shope.ee/an_redir?origin_link=x",
        ):
            with self.subTest(url=url):
                self.assertEqual(build_shopee_affiliate_link(url, "123", "sub"), "")

    def test_missing_affiliate_id_raises(self):
        with self.assertRaisesRegex(ValueError, "SHOPEE_AFFILIATE_ID"):
            build_shopee_affiliate_link(self.product_url, "", "sub")

    def test_existing_redirect_is_unwrapped_not_double_wrapped(self):
        first = build_shopee_affiliate_link(self.product_url, "old", "old-sub")
        rebuilt = build_shopee_affiliate_link(first, "new", "new-sub")
        params = parse_qs(urlparse(rebuilt).query)
        self.assertEqual(params["origin_link"], [self.product_url])
        self.assertNotIn("s.shopee.co.th/an_redir", params["origin_link"][0])
        self.assertEqual(params["affiliate_id"], ["new"])

    def test_sub_id_is_stable_and_ascii_safe(self):
        first = build_product_sub_id("Pickora Thailand", "18895969590", "fallback")
        second = build_product_sub_id("Pickora Thailand", "18895969590", "fallback")
        self.assertEqual(first, second)
        self.assertEqual(first, "Pickora-Thailand-product-18895969590")
        self.assertRegex(first, r"^[A-Za-z0-9_-]+$")

    def test_sub_id_uses_stable_fallback(self):
        self.assertEqual(
            build_product_sub_id("pickora", "", "abc123"),
            "pickora-product-abc123",
        )


class ProductImagesTest(unittest.TestCase):
    def test_accepts_json_and_delimited_galleries(self):
        first = "https://cdn.example.com/one.jpg"
        second = "https://cdn.example.com/two.jpg"
        self.assertEqual(product_images(f'["{first}", "{second}"]'), [first, second])
        self.assertEqual(product_images(f"{first}|{second}"), [first, second])

    def test_drops_invalid_and_duplicate_urls(self):
        image = "https://cdn.example.com/product.jpg"
        self.assertEqual(product_images(f"{image};javascript:alert(1);{image}"), [image])

    def test_generated_product_page_renders_gallery_for_multiple_images(self):
        product = {
            "id": "0123456789abcdef", "title": "สินค้าทดสอบ",
            "image": "https://cdn.example.com/one.jpg",
            "images": ["https://cdn.example.com/one.jpg", "https://cdn.example.com/two.jpg"],
            "link": "https://shopee.example.com/item", "detailUrl": "/products/0123456789abcdef/",
            "category": "ของใช้", "categoryUrl": "/categories/test/", "priceHistory": [],
        }
        page = create_product_page(product, [])
        self.assertIn('class="product-thumbnails"', page)
        self.assertEqual(page.count("data-gallery-image="), 2)
        self.assertIn('<script src="/product-gallery.js"></script>', page)

    def test_generated_product_page_labels_feed_price_as_reference(self):
        product = {
            "id": "0123456789abcdef", "title": "สินค้าทดสอบ",
            "image": "https://cdn.example.com/one.jpg",
            "link": "https://shopee.example.com/item",
            "detailUrl": "/products/0123456789abcdef/",
            "category": "ของใช้", "categoryUrl": "/categories/test/",
            "price": 400, "priceHistory": [],
        }

        page = create_product_page(product, [])

        self.assertIn("ราคาอ้างอิง ฿400", page)
        self.assertIn("โปรโมชันจริงอาจต่ำกว่านี้", page)


class RepairMojibakeTest(unittest.TestCase):
    def test_preserves_correct_thai(self):
        value = "VFOODS วีฟู้ดส์"
        self.assertEqual(repair_mojibake(value), value)

    def test_repairs_thai_mojibake(self):
        broken = "VFOODS à¸§à¸µà¸à¸¹à¹à¸à¸ªà¹"
        self.assertEqual(repair_mojibake(broken), "VFOODS วีฟู้ดส์")

    def test_preserves_english(self):
        self.assertEqual(repair_mojibake("VFOODS snack"), "VFOODS snack")

    def test_preserves_non_string_values(self):
        for value in (None, 42, 3.5, {"title": "value"}):
            with self.subTest(value=value):
                self.assertIs(repair_mojibake(value), value)

    def test_unrecoverable_marker_string_does_not_raise(self):
        value = "invalid à¸ text \N{SNOWMAN}"
        self.assertEqual(repair_mojibake(value), value)

    def test_normalises_all_product_text_fields(self):
        broken = "à¸§à¸µà¸à¸¹à¹à¸à¸ªà¹"
        product = {
            "title": f"VFOODS {broken}",
            "category": broken,
            "shop": broken,
            "description": f"Details {broken}",
            "price": 99,
        }

        normalise_product_text(product)

        self.assertEqual(product["title"], "VFOODS วีฟู้ดส์")
        self.assertEqual(product["category"], "วีฟู้ดส์")
        self.assertEqual(product["shop"], "วีฟู้ดส์")
        self.assertEqual(product["description"], "Details วีฟู้ดส์")
        self.assertEqual(product["price"], 99)


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


class ProductGenerationTest(unittest.TestCase):
    def test_generation_keeps_canonical_and_marks_commission_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            public_dir = root / "public"
            data_dir.mkdir()
            public_dir.mkdir()
            feed = data_dir / "shopee_feed.csv"
            feed.write_text(
                "title,image,product_link,itemid,shopid,price,sale_price,price_min,price_max,rating,sold\n"
                "Test product,https://cdn.example.com/a.jpg,"
                "https://shopee.co.th/product/10/20,20,10,100,79,,,5,25\n"
                "Regular price product,https://cdn.example.com/b.jpg,"
                "https://shopee.co.th/product/10/21,21,10,100,,,,5,25\n"
                "Variant product,https://cdn.example.com/c.jpg,"
                "https://shopee.co.th/product/10/22,22,10,400,,284,400,5,25\n",
                encoding="utf-8",
            )
            paths = {
                "DATA_DIR": data_dir, "PUBLIC_DIR": public_dir,
                "FEED_FILE": feed, "PRODUCTS_FILE": public_dir / "products.json",
                "STATUS_FILE": public_dir / "feed-status.json",
                "PRICE_HISTORY_FILE": public_dir / "price-history.json",
                "SEO_STATUS_FILE": public_dir / "seo-status.json",
                "SITEMAP_FILE": public_dir / "sitemap.xml",
                "PRODUCT_PAGES_DIR": public_dir / "products",
                "CATEGORY_PAGES_DIR": public_dir / "categories",
            }
            with patch.multiple(run_pipeline, **paths), patch.dict(
                "os.environ",
                {"SHOPEE_AFFILIATE_ID": "test-affiliate", "PIPELINE_ENV": "test"},
            ):
                run_pipeline.process_feed()

            products = json.loads(paths["PRODUCTS_FILE"].read_text(encoding="utf-8"))
            product = next(item for item in products if item["externalId"] == "20")
            regular_price_product = next(
                item for item in products if item["externalId"] == "21"
            )
            variant_product = next(
                item for item in products if item["externalId"] == "22"
            )
            self.assertEqual(product["productUrl"], "https://shopee.co.th/product/10/20")
            self.assertEqual(product["link"], product["affiliateUrl"])
            self.assertIsNone(product["commission"])
            self.assertEqual(product["commissionStatus"], "unknown")
            self.assertEqual(product["externalId"], "20")
            self.assertEqual(product["shopId"], "10")
            self.assertEqual(product["price"], 79)
            self.assertEqual(regular_price_product["price"], 100)
            self.assertEqual(variant_product["price"], 284)
            self.assertEqual(variant_product["priceMax"], 400)
            self.assertTrue(product["priceUpdatedAt"])
            self.assertIn('rel="nofollow sponsored noopener noreferrer"', (
                paths["PRODUCT_PAGES_DIR"] / product["id"] / "index.html"
            ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
