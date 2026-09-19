from __future__ import annotations

import sys
import tempfile
import unittest
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
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
    paginate_products,
    product_images,
    repair_mojibake,
    select_homepage_products,
)


class FakeFeedResponse:
    def __init__(self, blocks):
        self.blocks = blocks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self.blocks


class FeedDownloadLifecycleTest(unittest.TestCase):
    def run_download(
        self,
        data_dir: Path,
        response: FakeFeedResponse,
        free_bytes: int = 16 * 1024**3,
    ):
        canonical_feed = data_dir / "shopee_feed.csv"
        public_dir = data_dir.parent / "public"
        public_dir.mkdir()
        with patch.multiple(
            run_pipeline,
            DATA_DIR=data_dir,
            FEED_FILE=canonical_feed,
            PUBLIC_DIR=public_dir,
            STATUS_FILE=public_dir / "feed-status.json",
            FEED_URL="https://feed.example.test/download",
        ), patch.object(run_pipeline.requests, "get", return_value=response):
            with patch.object(
                run_pipeline.shutil,
                "disk_usage",
                return_value=SimpleNamespace(free=free_bytes),
            ):
                run_pipeline.download_feed()
        return canonical_feed

    def test_success_replaces_canonical_feed_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")
            downloaded_feed = b"title,price\n" + (b"product,100\n" * 20)

            self.run_download(data_dir, FakeFeedResponse([downloaded_feed]))

            self.assertEqual(canonical_feed.read_bytes(), downloaded_feed)
            self.assertEqual(list(data_dir.glob("shopee_feed_*.download")), [])

    def test_download_exception_removes_temp_and_preserves_canonical(self):
        def failing_blocks():
            yield b"partial download"
            raise ConnectionError("download interrupted")

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")

            with self.assertRaisesRegex(ConnectionError, "download interrupted"):
                self.run_download(data_dir, FakeFeedResponse(failing_blocks()))

            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")
            self.assertEqual(list(data_dir.glob("shopee_feed_*.download")), [])

    def test_validation_failure_removes_temp_and_preserves_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")

            with self.assertRaisesRegex(RuntimeError, "unexpectedly small"):
                self.run_download(data_dir, FakeFeedResponse([b"too small"]))

            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")
            self.assertEqual(list(data_dir.glob("shopee_feed_*.download")), [])

    def test_replace_failure_removes_temp_and_preserves_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")
            downloaded_feed = b"title,price\n" + (b"product,100\n" * 20)

            with patch.object(run_pipeline, "write_status"), patch.object(
                run_pipeline.os, "replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    self.run_download(data_dir, FakeFeedResponse([downloaded_feed]))

            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")
            self.assertEqual(list(data_dir.glob("shopee_feed_*.download")), [])

    def test_cleanup_failure_does_not_hide_download_exception(self):
        def failing_blocks():
            yield b"partial download"
            raise ConnectionError("original download error")

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")

            with patch.object(Path, "unlink", side_effect=OSError("cleanup failed")):
                with self.assertRaisesRegex(ConnectionError, "original download error"):
                    self.run_download(data_dir, FakeFeedResponse(failing_blocks()))

            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")


class FeedDownloadDiskGuardrailTest(unittest.TestCase):
    def test_no_canonical_feed_requires_eight_gib(self):
        self.assertEqual(
            run_pipeline.required_feed_download_free_bytes(0),
            run_pipeline.MIN_FEED_DOWNLOAD_FREE_BYTES,
        )

    def test_small_canonical_feed_still_requires_eight_gib(self):
        self.assertEqual(
            run_pipeline.required_feed_download_free_bytes(1024**3),
            run_pipeline.MIN_FEED_DOWNLOAD_FREE_BYTES,
        )

    def test_large_canonical_feed_requires_twice_its_size(self):
        feed_size = 5 * 1024**3
        self.assertEqual(
            run_pipeline.required_feed_download_free_bytes(feed_size),
            feed_size * 2,
        )

    def test_free_disk_exactly_at_threshold_allows_download(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            downloaded_feed = b"title,price\n" + (b"product,100\n" * 20)
            lifecycle = FeedDownloadLifecycleTest()

            canonical_feed = lifecycle.run_download(
                data_dir,
                FakeFeedResponse([downloaded_feed]),
                free_bytes=run_pipeline.MIN_FEED_DOWNLOAD_FREE_BYTES,
            )

            self.assertEqual(canonical_feed.read_bytes(), downloaded_feed)

    def test_one_byte_below_threshold_rejects_before_network_or_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            public_dir = Path(directory) / "public"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")

            with patch.multiple(
                run_pipeline,
                DATA_DIR=data_dir,
                FEED_FILE=canonical_feed,
                PUBLIC_DIR=public_dir,
                STATUS_FILE=public_dir / "feed-status.json",
                FEED_URL="https://feed.example.test/download",
            ), patch.object(
                run_pipeline.shutil,
                "disk_usage",
                return_value=SimpleNamespace(
                    free=run_pipeline.MIN_FEED_DOWNLOAD_FREE_BYTES - 1
                ),
            ), patch.object(run_pipeline.requests, "get") as request_get, patch.object(
                run_pipeline.tempfile, "NamedTemporaryFile"
            ) as named_temporary_file:
                with self.assertRaisesRegex(RuntimeError, "Insufficient disk space"):
                    run_pipeline.download_feed()

            request_get.assert_not_called()
            named_temporary_file.assert_not_called()
            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")
            self.assertEqual(list(data_dir.glob("shopee_feed_*.download")), [])
            self.assertFalse(public_dir.exists())

    def test_disk_usage_failure_preserves_canonical_and_skips_network(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir()
            canonical_feed = data_dir / "shopee_feed.csv"
            canonical_feed.write_bytes(b"previous canonical feed")

            with patch.multiple(
                run_pipeline,
                DATA_DIR=data_dir,
                FEED_FILE=canonical_feed,
                FEED_URL="https://feed.example.test/download",
            ), patch.object(
                run_pipeline.shutil,
                "disk_usage",
                side_effect=OSError("disk usage unavailable"),
            ), patch.object(run_pipeline.requests, "get") as request_get, patch.object(
                run_pipeline.tempfile, "NamedTemporaryFile"
            ) as named_temporary_file:
                with self.assertRaisesRegex(OSError, "disk usage unavailable"):
                    run_pipeline.download_feed()

            request_get.assert_not_called()
            named_temporary_file.assert_not_called()
            self.assertEqual(canonical_feed.read_bytes(), b"previous canonical feed")


class StaleFeedDownloadCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary_directory.name)
        self.now = time.time()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def cleanup(self):
        with patch.object(run_pipeline, "DATA_DIR", self.data_dir):
            run_pipeline.cleanup_stale_feed_downloads(now=self.now)

    def make_file(self, name: str, age_seconds: int) -> Path:
        path = self.data_dir / name
        path.write_bytes(b"fixture")
        modified_at = self.now - age_seconds
        os.utime(path, (modified_at, modified_at))
        return path

    def test_old_project_owned_temp_is_deleted(self):
        path = self.make_file("shopee_feed_old.download", 7 * 60 * 60)
        self.cleanup()
        self.assertFalse(path.exists())

    def test_recent_project_owned_temp_is_preserved(self):
        path = self.make_file("shopee_feed_recent.download", 5 * 60 * 60)
        self.cleanup()
        self.assertTrue(path.exists())

    def test_old_but_active_project_owned_temp_is_preserved(self):
        path = self.make_file("shopee_feed_active.download", 7 * 60 * 60)
        with path.open("rb") as active_download:
            run_pipeline.fcntl.flock(active_download.fileno(), run_pipeline.fcntl.LOCK_EX)
            self.cleanup()
        self.assertTrue(path.exists())

    def test_unrelated_download_is_preserved(self):
        path = self.make_file("unrelated.download", 24 * 60 * 60)
        self.cleanup()
        self.assertTrue(path.exists())

    def test_canonical_feed_is_never_deleted(self):
        path = self.make_file("shopee_feed.csv", 24 * 60 * 60)
        self.cleanup()
        self.assertEqual(path.read_bytes(), b"fixture")


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


class ProductContentDepthTest(unittest.TestCase):
    @staticmethod
    def product(identifier, category="Mobile & Gadgets", score=100):
        return {
            "id": identifier, "externalId": identifier,
            "title": "Power Bank ทดสอบ" if category == "Mobile & Gadgets" else f"สินค้า {identifier}",
            "image": "https://cdn.example.com/product.jpg",
            "link": "https://shopee.co.th/product/1/2",
            "detailUrl": f"/products/{identifier}/", "category": category,
            "categoryUrl": f"/categories/{run_pipeline.category_id(category)}/",
            "price": 500, "priceMax": 500, "pickoraScore": score,
            "rating": 4.8, "sold": 12, "priceHistory": [],
        }

    def test_product_page_adds_grounded_summary_audience_and_considerations(self):
        page = create_product_page(self.product("a" * 16), [])
        self.assertIn("<h2>สรุปสินค้า</h2>", page)
        self.assertIn("อยู่ในหมวด Mobile &amp; Gadgets", page)
        self.assertIn("ราคาอ้างอิง ฿500", page)
        self.assertNotIn("เหมาะสำหรับทุกคน", page)
        self.assertIn("<h2>เหมาะกับใคร</h2>", page)
        self.assertIn("พกพาพลังงานสำรอง", page)
        self.assertIn("<h2>จุดที่ควรพิจารณาก่อนเลือกซื้อ</h2>", page)
        self.assertIn("มาตรฐานการเชื่อมต่อ", page)

    def test_unsupported_category_omits_inferred_guidance_safely(self):
        product = self.product("b" * 16, category="Unmapped")
        product["title"] = "สินค้าทดสอบ"
        page = create_product_page(product, [])
        self.assertIn("<h2>สรุปสินค้า</h2>", page)
        self.assertNotIn("<h2>เหมาะกับใคร</h2>", page)
        self.assertNotIn("<h2>จุดที่ควรพิจารณาก่อนเลือกซื้อ</h2>", page)

    def test_generation_keeps_related_products_ranked_and_uses_non_product_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            category = "Audio"
            category_url = f"/categories/{run_pipeline.category_id(category)}/"
            products = [
                self.product(f"{index:016x}", category, 100 - index)
                for index in range(8)
            ]
            for product in products:
                product["categoryUrl"] = category_url
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=public_dir / "products",
                CATEGORY_PAGES_DIR=public_dir / "categories",
                SITEMAP_FILE=public_dir / "sitemap.xml",
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
            ):
                run_pipeline.write_product_pages_and_sitemap(products)

            page = (public_dir / "products" / products[0]["id"] / "index.html").read_text(encoding="utf-8")
            related_section = page.split("<h2>สินค้าที่เกี่ยวข้อง</h2>", 1)[1].split("</section>", 1)[0]
            identifiers = re.findall(
                r'class="card-image-link" href="/products/([a-f0-9]{16})/"',
                related_section,
            )
            self.assertEqual(identifiers, [product["id"] for product in products[1:7]])
            self.assertEqual(len(identifiers), len(set(identifiers)))
            self.assertNotIn(products[0]["id"], identifiers)
            self.assertIn('/reviews/wireless-earbuds.html', page)
            self.assertIn('"@type": "WebPage"', page)
            self.assertIn('"@type": "BreadcrumbList"', page)
            self.assertNotIn('"@type": "Product"', page)
            self.assertNotIn('"Offer"', page)
            self.assertNotIn("AggregateOffer", page)
            self.assertNotIn("AggregateRating", page)
            self.assertIn(
                f'<link rel="canonical" href="{run_pipeline.SITE_URL}{products[0]["detailUrl"]}">',
                page,
            )

    def test_related_selection_prefers_meaningful_title_overlap_deterministically(self):
        current = self.product("a" * 16, category="Audio", score=10)
        current["title"] = "Wireless Earbuds Pro"
        matching = self.product("b" * 16, category="Audio", score=1)
        matching["title"] = "Wireless Earbuds Case"
        higher_score = self.product("c" * 16, category="Audio", score=99)
        higher_score["title"] = "Portable Speaker"
        selected = run_pipeline.select_related_products(
            current, [current, higher_score, matching]
        )
        self.assertEqual([item["id"] for item in selected], [matching["id"], higher_score["id"]])
        self.assertEqual(
            [item["id"] for item in selected],
            [item["id"] for item in run_pipeline.select_related_products(current, [matching, current, higher_score])],
        )

    def test_thin_category_product_does_not_emit_nonexistent_category_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            thin = self.product("t" * 16, category="Thin")
            thin["categoryUrl"] = "/categories/thin/"
            eligible = [self.product(f"{index:016x}", category="Audio") for index in range(4)]
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=public_dir / "products",
                CATEGORY_PAGES_DIR=public_dir / "categories",
                SITEMAP_FILE=public_dir / "sitemap.xml",
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
            ):
                run_pipeline.write_product_pages_and_sitemap(eligible + [thin])
            page = (public_dir / "products" / thin["id"] / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('href="/categories/thin/"', page)
            self.assertIn("<span>Thin</span>", page)

    def test_product_metadata_cleanup_and_description_are_factual_and_deterministic(self):
        product = self.product("c" * 16)
        product["title"] = "[โค้ดลด 10%] Power Bank สำหรับทดสอบชื่อสินค้าที่ยาวมาก เพื่อดูการตัดคำอย่างปลอดภัย"
        title = run_pipeline.clean_product_metadata_title(product["title"])
        self.assertNotIn("โค้ดลด", title)
        self.assertLessEqual(len(title), 61)
        description = run_pipeline.product_meta_description(
            product, product["category"], "ราคาอ้างอิง ฿500"
        )
        self.assertIn("Mobile & Gadgets", description)
        self.assertIn("ราคาอ้างอิง ฿500", description)
        self.assertIn("FindVexa Score 100", description)
        self.assertLessEqual(len(description), 156)
        first = create_product_page(product, [])
        second = create_product_page(dict(product), [])
        self.assertEqual(first, second)
        self.assertIn(
            f"<title>{run_pipeline.html.escape(title)} ราคา รีวิว และข้อมูลก่อนซื้อ | FindVexa</title>",
            first,
        )

    def test_duplicate_metadata_titles_use_factual_disambiguation(self):
        first = self.product("d" * 16)
        second = self.product("e" * 16)
        first["title"] = second["title"] = "Power Bank รุ่นทดสอบ"
        first["price"] = 500
        second["price"] = 700
        titles = run_pipeline.product_metadata_titles([first, second])
        self.assertEqual(len(set(titles.values())), 2)
        self.assertIn("฿500", titles[first["id"]])
        self.assertIn("฿700", titles[second["id"]])


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


class HomepageSelectionTest(unittest.TestCase):
    @staticmethod
    def product(identifier, category, score):
        return {
            "id": identifier, "category": category, "score": score,
            "detailUrl": f"/products/{identifier}/", "title": identifier,
        }

    def test_prefers_the_highest_ranked_product_in_each_category(self):
        products = [
            self.product("a2", "A", 90), self.product("a1", "A", 100),
            self.product("b1", "B", 80), self.product("c1", "C", 70),
        ]
        selected = select_homepage_products(products)
        self.assertEqual([item["id"] for item in selected[:3]], ["a1", "b1", "c1"])

    def test_selection_is_deterministic_and_has_no_duplicate_ids(self):
        products = [
            self.product(f"{category}{rank}", category, 100 - rank)
            for category in ("A", "B", "C", "D") for rank in range(5)
        ]
        first = select_homepage_products(products)
        second = select_homepage_products(list(reversed(products)))
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(len({item["id"] for item in first}), len(first))
        self.assertEqual(len(first), 12)
        self.assertEqual(len({item["category"] for item in first[:4]}), 4)

    def test_fallback_fills_twelve_products_when_categories_are_few(self):
        products = [
            self.product(f"a{rank:02}", "A", 100 - rank) for rank in range(10)
        ] + [
            self.product(f"b{rank:02}", "B", 80 - rank) for rank in range(10)
        ]
        selected = select_homepage_products(products)
        self.assertEqual(len(selected), 12)
        self.assertEqual({item["category"] for item in selected[:2]}, {"A", "B"})

    def test_fewer_than_twelve_products_are_returned_safely(self):
        products = [self.product("a", "A", 10), self.product("b", "B", 9)]
        self.assertEqual(select_homepage_products(products), products)


class ProductPaginationTest(unittest.TestCase):
    @staticmethod
    def products(count):
        return [{"id": f"{index:016x}"} for index in range(count)]

    def test_page_counts_at_boundaries(self):
        for count, expected_pages in ((0, 0), (24, 1), (25, 2), (48, 2), (49, 3)):
            with self.subTest(count=count):
                self.assertEqual(len(paginate_products(self.products(count))), expected_pages)

    def test_preserves_order_and_does_not_repeat_ids(self):
        products = self.products(49)
        pages = paginate_products(products)
        flattened = [product for page in pages for product in page]
        self.assertEqual(flattened, products)
        self.assertEqual(len({product["id"] for product in flattened}), len(products))


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
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
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
                "SEO_STORAGE_FILE": data_dir / "search_console.sqlite3",
                "SEO_STATUS_FILE": public_dir / "seo-status.json",
                "SITEMAP_FILE": public_dir / "sitemap.xml",
                "HOMEPAGE_CATALOG_FILE": public_dir / "homepage-catalog.html",
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
            product_page = (
                paths["PRODUCT_PAGES_DIR"] / product["id"] / "index.html"
            ).read_text(encoding="utf-8")
            self.assertNotIn('"sku":', product_page)
            schema_text = product_page.split(
                '<script type="application/ld+json">', 1
            )[1].split("</script>", 1)[0]
            schema = json.loads(schema_text)
            self.assertNotIn(None, schema["@graph"])
            self.assertEqual(schema["@graph"][0]["@type"], "WebPage")
            self.assertNotIn('"@type": "Product"', product_page)
            self.assertNotIn("undefined", schema_text)
            self.assertNotIn("offers", schema["@graph"][0])
            self.assertNotIn("aggregateRating", schema["@graph"][0])
            homepage_catalog = paths["HOMEPAGE_CATALOG_FILE"].read_text(
                encoding="utf-8"
            )
            self.assertIn(product["detailUrl"], homepage_catalog)
            self.assertEqual(product["categoryUrl"], "")


class CategoryGenerationTest(unittest.TestCase):
    @staticmethod
    def product(identifier, category, score, category_url):
        return {
            "id": identifier, "title": f"Product {identifier}",
            "detailUrl": f"/products/{identifier}/", "category": category,
            "categoryUrl": category_url, "score": score,
            "image": "https://cdn.example.com/product.jpg", "price": 100,
            "priceHistory": [], "link": "https://shopee.co.th/product/1/2",
        }

    def test_generates_indexable_categories_and_excludes_thin_ones_from_sitemap(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            eligible_url = "/categories/eligible/"
            products = [
                self.product(f"a{index:015d}", "Eligible", 100 - index, eligible_url)
                for index in range(4)
            ] + [
                self.product(f"b{index:015d}", "Thin", 50 - index, "")
                for index in range(3)
            ]
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=public_dir / "products",
                CATEGORY_PAGES_DIR=public_dir / "categories",
                SITEMAP_FILE=public_dir / "sitemap.xml",
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
            ):
                run_pipeline.write_product_pages_and_sitemap(products)

            index = (public_dir / "categories" / "index.html").read_text(encoding="utf-8")
            sitemap = (public_dir / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn(eligible_url, index)
            for product in products[4:]:
                self.assertIn(product["detailUrl"], index)
            self.assertNotIn(products[0]["detailUrl"], index)
            self.assertIn(eligible_url, sitemap)
            self.assertNotIn("/categories/thin/", sitemap)
            self.assertEqual(sitemap.count("<loc>"), len(set(
                item.split("</loc>", 1)[0]
                for item in sitemap.split("<loc>")[1:]
            )))

    def test_category_pagination_generates_ordered_nonduplicated_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            category = "Audio"
            category_url = f"/categories/{run_pipeline.category_id(category)}/"
            products = [
                self.product(f"{index:016x}", category, 1000 - index, category_url)
                for index in range(49)
            ]
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=public_dir / "products",
                CATEGORY_PAGES_DIR=public_dir / "categories",
                SITEMAP_FILE=public_dir / "sitemap.xml",
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
            ):
                run_pipeline.write_product_pages_and_sitemap(products)

            category_dir = public_dir / "categories" / run_pipeline.category_id(category)
            pages = [category_dir / "index.html", category_dir / "page/2/index.html", category_dir / "page/3/index.html"]
            self.assertTrue(all(page.is_file() for page in pages))
            self.assertFalse((category_dir / "page/1").exists())
            second = pages[1].read_text(encoding="utf-8")
            third = pages[2].read_text(encoding="utf-8")
            self.assertIn(f"{run_pipeline.SITE_URL}{category_url}page/2/", second)
            self.assertIn(f'href="{category_url}"', second)
            self.assertIn(f'href="{category_url}page/3/"', second)
            self.assertNotIn("/page/2/page/3/", second)
            self.assertIn(f'href="{category_url}page/2/"', third)
            self.assertNotIn("/page/3/page/4/", third)
            self.assertNotIn("ถัดไป", third)
            self.assertIn('/reviews/wireless-earbuds.html', second)
            identifiers = []
            for page in pages:
                identifiers.extend(re.findall(
                    r'class="card-image-link" href="/products/([a-f0-9]{16})/"',
                    page.read_text(encoding="utf-8"),
                ))
            self.assertEqual(identifiers, [product["id"] for product in products])
            self.assertEqual(len(set(identifiers)), 49)
            self.assertEqual(len(re.findall(r'<article class="card', third)), 1)
            sitemap = (public_dir / "sitemap.xml").read_text(encoding="utf-8")
            expected_urls = [
                f"{run_pipeline.SITE_URL}{category_url}",
                f"{run_pipeline.SITE_URL}{category_url}page/2/",
                f"{run_pipeline.SITE_URL}{category_url}page/3/",
            ]
            for url in expected_urls:
                self.assertIn(f"<loc>{url}</loc>", sitemap)
            self.assertNotIn("/page/1/", sitemap)
            self.assertNotIn("/page/2/page/3/", sitemap)
            self.assertNotIn("<lastmod>", sitemap)
            locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
            self.assertEqual(len(locs), len(set(locs)))

    def test_category_sitemap_omits_or_includes_page_two_at_boundaries(self):
        for count, has_page_two in ((24, False), (25, True)):
            with self.subTest(count=count):
                with tempfile.TemporaryDirectory() as directory:
                    public_dir = Path(directory)
                    category = "Audio"
                    category_url = f"/categories/{run_pipeline.category_id(category)}/"
                    products = [self.product(f"{index:016x}", category, 1000 - index, category_url) for index in range(count)]
                    with patch.multiple(run_pipeline, PUBLIC_DIR=public_dir, PRODUCT_PAGES_DIR=public_dir / "products", CATEGORY_PAGES_DIR=public_dir / "categories", SITEMAP_FILE=public_dir / "sitemap.xml", HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html"):
                        run_pipeline.write_product_pages_and_sitemap(products)
                    sitemap = (public_dir / "sitemap.xml").read_text(encoding="utf-8")
                    self.assertEqual(f"page/2/" in sitemap, has_page_two)

    def test_page_one_has_topical_content_but_page_two_stays_concise(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            category = "Audio"
            category_url = f"/categories/{run_pipeline.category_id(category)}/"
            products = [
                self.product(f"{index:016x}", category, 1000 - index, category_url)
                for index in range(25)
            ]
            with patch.multiple(
                run_pipeline,
                PUBLIC_DIR=public_dir,
                PRODUCT_PAGES_DIR=public_dir / "products",
                CATEGORY_PAGES_DIR=public_dir / "categories",
                SITEMAP_FILE=public_dir / "sitemap.xml",
                HOMEPAGE_CATALOG_FILE=public_dir / "homepage-catalog.html",
            ):
                run_pipeline.write_product_pages_and_sitemap(products)

            category_dir = public_dir / "categories" / run_pipeline.category_id(category)
            first = (category_dir / "index.html").read_text(encoding="utf-8")
            second = (category_dir / "page/2/index.html").read_text(encoding="utf-8")
            self.assertIn("สิ่งที่ควรพิจารณาก่อนเลือกซื้อ", first)
            self.assertIn("คำถามที่พบบ่อย", first)
            self.assertIn("คู่มือเลือกซื้อ", first)
            self.assertIn('/reviews/wireless-earbuds.html', first)
            self.assertIn("สินค้าเรียงตาม FindVexa Score", first)
            self.assertNotIn("สิ่งที่ควรพิจารณาก่อนเลือกซื้อ", second)
            self.assertNotIn("คำถามที่พบบ่อย", second)
            self.assertNotIn("หมวด Audio รวมอุปกรณ์เสียง", second)
            self.assertIn("<h1>Audio · หน้า 2</h1>", second)
            self.assertIn(
                f'<link rel="canonical" href="{run_pipeline.SITE_URL}{category_url}page/2/">',
                second,
            )
            self.assertIn(f'href="{category_url}"', second)
            self.assertNotIn("/page/1/", second)

    def test_unmapped_category_omits_category_guidance_and_faq(self):
        page = run_pipeline.create_category_page(
            "Unmapped", "/categories/unmapped/", [
                self.product("a" * 16, "Unmapped", 1, "/categories/unmapped/")
            ],
        )
        self.assertIn("หน้านี้รวบรวมสินค้าในหมวด Unmapped", page)
        self.assertNotIn("สิ่งที่ควรพิจารณาก่อนเลือกซื้อ", page)
        self.assertNotIn("คำถามที่พบบ่อย", page)

    def test_category_metadata_is_page_aware_and_self_canonical(self):
        category = "Audio"
        category_url = f"/categories/{run_pipeline.category_id(category)}/"
        product = self.product("a" * 16, category, 1, category_url)
        first = run_pipeline.create_category_page(category, category_url, [product])
        second = run_pipeline.create_category_page(
            category, category_url, [product], page_number=2, page_count=2,
        )
        self.assertIn("<title>Audio สินค้าแนะนำและวิธีเลือก | FindVexa</title>", first)
        self.assertIn("<title>Audio · หน้า 2 | FindVexa</title>", second)
        self.assertIn(
            f'<link rel="canonical" href="{run_pipeline.SITE_URL}{category_url}">', first,
        )
        self.assertIn(
            f'<link rel="canonical" href="{run_pipeline.SITE_URL}{category_url}page/2/">', second,
        )
        self.assertNotEqual(
            run_pipeline.category_metadata_description(category, 1),
            run_pipeline.category_metadata_description(category, 2),
        )

if __name__ == "__main__":
    unittest.main()
