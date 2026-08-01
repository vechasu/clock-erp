import unittest
from pathlib import Path
from unittest import mock

from app import web


PRODUCT_ID = "mobile-product-1"


def warehouse_item():
    return {
        "id": PRODUCT_ID,
        "name": "Часы Mobile",
        "article": "MOBILE-001",
        "code": "MOB-1",
        "barcode": "460000000001",
        "brand": "Tictactoy",
        "category": "Наручные часы/Мужские",
        "cell": "A-01",
        "stock": 7,
        "stock_display": "7",
        "reserve": 0,
        "quantity": 7,
        "created_at": 1,
        "created_at_display": "01.01.2026",
        "has_images": False,
        "thumbnail_url": "",
        "gallery": [],
        "price_display": "12 900 ₽",
        "cell_source": "product",
        "cell_source_label": "у позиции",
        "cell_source_path": "",
        "moysklad_url": "#",
        "raw_category": "Наручные часы/Мужские",
    }


def sale_record():
    return {
        "id": "mobile-sale-1",
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "created_at": "2026-07-28",
        "source": "Tictactoy",
        "source_key": "tictactoy",
        "order_number": "ORDER-1",
        "product_id": PRODUCT_ID,
        "product_name": "Часы Mobile",
        "barcode": "460000000001",
        "brand": "Tictactoy",
        "category": "Наручные часы/Мужские",
        "quantity_value": 2,
        "quantity_display": "2",
        "unit_price": 12900.0,
        "unit_price_display": "12 900 ₽",
        "total_amount": 25800.0,
        "total_amount_display": "25 800 ₽",
        "track_number": "TRACK-1",
        "delivery_method": "СДЭК",
        "delivery_cost": 500,
        "delivery_cost_display": "500 ₽",
        "region": "Москва",
        "city": "Москва",
        "payment_method": "Карта",
        "recipient_name": "",
        "platform": "",
        "country": "",
        "delivery_address": "",
        "invoice_number": "",
        "note": "Тест",
        "order_status": "completed",
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "sticker_number": "",
        "commission_amount": 0,
        "commission_display": "0 ₽",
    }


def receipt_record():
    return {
        "id": "mobile-receipt-1",
        "number": "ПР-0001",
        "receipt_date": "2026-07-28",
        "created_at": "2026-07-28",
        "brand": "Tictactoy",
        "supplier": "Tictactoy",
        "category": "Наручные часы/Мужские",
        "invoice_number": "INV-1",
        "product_name": "Часы Mobile",
        "quantity": 5,
        "total_quantity": 5,
        "purchase_price": 7500.0,
        "total_amount": 37500.0,
        "note": "Тест",
        "status_label": "Проведён",
        "moysklad_document_url": "#",
        "positions": [{
            "product_name": "Часы Mobile",
            "purchase_price": 7500.0,
        }],
    }


class MobileErpLayoutTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.item = warehouse_item()
        self.patches = [
            mock.patch.object(
                web,
                "get_excel_warehouse_items",
                return_value=[self.item],
            ),
            mock.patch.object(
                web,
                "get_warehouse_items",
                return_value=[self.item],
            ),
            mock.patch.object(
                web,
                "build_sales_report_records",
                return_value=[sale_record()],
            ),
            mock.patch.object(
                web,
                "load_receipts",
                return_value=[receipt_record()],
            ),
            mock.patch.object(
                web.ExcelProductCatalog,
                "list_manual_stock_operations",
                return_value=[],
            ),
        ]

        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def test_primary_pages_share_mobile_navigation(self):
        pages = {
            "/sales?source=all": "Продажи",
            "/receipts": "Приход",
        }

        for url, active_label in pages.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    html.count('class="mobile-erp-navigation"'),
                    1,
                )
                self.assertIn('id="mobileErpMoreTrigger"', html)
                self.assertIn('id="mobileErpMoreSheet"', html)
                self.assertIn(f"<span>{active_label}</span>", html)

    def test_warehouse_uses_the_react_products_entrypoint(self):
        redirect = self.client.get("/warehouse?open_add=1&in_stock=1")
        self.assertEqual(redirect.status_code, 302)
        self.assertTrue(
            redirect.headers["Location"].endswith(
                "/app/products?open_add=1&in_stock=1"
            )
        )
        shell = self.client.get("/app/products")
        html = shell.get_data(as_text=True)
        self.assertEqual(shell.status_code, 200)
        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"',
            html,
        )
        self.assertIn('<div id="root"></div>', html)
        self.assertIn('/app/assets/', html)

    def test_mobile_views_reuse_rendered_rows_and_forms(self):
        sales_html = self.client.get(
            "/sales?source=all"
        ).get_data(as_text=True)
        receipts_html = self.client.get(
            "/receipts"
        ).get_data(as_text=True)

        self.assertIn('data-sale-id="mobile-sale-1"', sales_html)
        self.assertIn('id="salesMobileList"', sales_html)
        self.assertIn("25\u00a0800\u00a0₽", sales_html)
        self.assertIn("openMobileSaleEditor", sales_html)
        self.assertIn('id="salesMobileSort"', sales_html)

        self.assertIn('class="receipt-row"', receipts_html)
        self.assertIn('id="receiptDateFilter"', receipts_html)
        self.assertIn("warehouse-calendar-popup", receipts_html)
        self.assertIn('id="receiptMobileSort"', receipts_html)
        self.assertIn("28.07.2026", receipts_html)
        self.assertIn("css/erp-components.css", receipts_html)

        products_source = (
            Path(web.PROJECT_ROOT)
            / "frontend/src/features/products/ProductsPage.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn('className="mobile-product-card"', products_source)
        self.assertIn("renderMobileCard={(product)", products_source)

    def test_shared_css_keeps_mobile_breakpoint_below_desktop(self):
        css_path = (
            Path(web.app.root_path)
            / "static"
            / "css"
            / "erp-components.css"
        )
        css = css_path.read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 767px)", css)
        self.assertIn(".mobile-erp-list", css)
        self.assertIn(".warehouse-products-table tbody tr", css)
        self.assertIn(".sales-data-card .erp-table-card", css)
        self.assertIn(".receipts-table .receipt-row", css)
        self.assertNotIn("@media (min-width: 768px)", css)

        react_css = (
            Path(web.PROJECT_ROOT) / "frontend/src/styles/global.css"
        ).read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 720px)", react_css)
        self.assertIn(".mobile-product-card", react_css)
        self.assertIn(".product-create-card", react_css)


if __name__ == "__main__":
    unittest.main()
