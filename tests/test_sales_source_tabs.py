import html
import json
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

from openpyxl import load_workbook

from app import web


PRODUCT_ID = "11111111-1111-1111-1111-111111111111"

EXPECTED_COLUMNS = {
    "all": [
        "Дата",
        "Номер заказа",
        "Трекинг",
        "Баркод",
        "Источник",
        "Бренд",
        "Категория",
        "Товар",
        "Количество",
        "Цена",
        "Комиссия",
        "Статус",
        "Примечание",
    ],
    "tictactoy": [
        "Дата",
        "Баркод",
        "Бренд",
        "Категория",
        "Товар",
        "Количество",
        "Цена продажи",
        "Комиссия",
        "Статус",
        "Номер заказа",
        "Трекинг",
        "Стоимость доставки",
        "Страна",
        "Регион",
        "Город",
        "Способ оплаты",
        "Примечание",
    ],
    "wildberries": [
        "Дата",
        "Баркод",
        "Бренд",
        "Категория",
        "Товар",
        "Номер стикера",
        "Номер заказа",
        "Количество",
        "Цена продажи",
        "Комиссия",
        "Статус",
        "Примечание",
    ],
    "amazon": [
        "Дата",
        "Баркод",
        "Бренд",
        "Категория",
        "Товар",
        "Количество",
        "Цена",
        "Комиссия",
        "Статус",
        "ФИО получателя",
        "Номер заказа",
        "Площадка",
        "Страна",
        "Номер накладной",
        "Примечание",
    ],
}

RETURN_REPORT_COLUMNS = [
    "Возвращено",
    "Дата возврата",
    "Причина возврата",
]


def warehouse_item():
    return {
        "id": PRODUCT_ID,
        "name": "Часы Test",
        "article": "ARTICLE-1",
        "code": "BARCODE-1",
        "brand": "Brand",
        "category": "Коллекция",
        "stock": 5,
        "stock_display": "5",
    }


def sale_record(source="Tictactoy", **changes):
    source_key = web.normalize_sales_source_key(source)
    sale = {
        "id": "sale-1",
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "created_at": "2026-07-22",
        "source": source,
        "source_key": source_key,
        "barcode": "BARCODE-1",
        "brand": "Brand",
        "category": "Коллекция",
        "product_id": PRODUCT_ID,
        "product_name": "Часы Test",
        "quantity_value": 2,
        "quantity_display": "2",
        "unit_price": 1000.0,
        "unit_price_display": "1 000 ₽",
        "total_amount": 2000.0,
        "total_amount_display": "2 000 ₽",
        "order_number": "ORDER-100",
        "track_number": "TRACK-100",
        "delivery_method": "СДЭК",
        "delivery_cost": 350.0,
        "delivery_cost_display": "350 ₽",
        "region": "Москва",
        "city": "Москва",
        "payment_method": "Карта",
        "recipient_name": "Иван Иванов",
        "platform": "Amazon.de",
        "country": "Германия",
        "delivery_address": "Berlin, Test str. 1",
        "invoice_number": "INV-100",
        "note": "Тестовое примечание",
        "order_status": "completed",
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "sticker_number": "STICKER-100",
        "commission_amount": 0,
        "commission_display": "0 ₽",
        "commission": "Оплата по СБП (0)",
    }
    sale.update(changes)
    return sale


class SalesSourceTabsTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_directory.name)
        self.manual_sales_path = (
            self.temp_path / "manual_sales.json"
        )
        self.overrides_path = (
            self.temp_path / "automatic_sales_overrides.json"
        )
        self.patchers = [
            mock.patch.object(
                web,
                "get_manual_sales_path",
                return_value=self.manual_sales_path,
            ),
            mock.patch.object(
                web,
                "get_automatic_sales_overrides_path",
                return_value=self.overrides_path,
            ),
            mock.patch.object(
                web,
                "get_warehouse_items",
                return_value=[warehouse_item()],
            ),
            mock.patch.object(
                web,
                "get_excel_warehouse_items",
                return_value=[warehouse_item()],
            ),
            mock.patch.object(
                web,
                "load_stock_operations",
                return_value=[],
            ),
        ]

        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

        self.temp_directory.cleanup()

    def get_headers(self, source):
        response = self.client.get(
            f"/sales?source={source}"
        )
        page = response.get_data(as_text=True)
        header = re.search(
            r"<thead>\s*<tr>(.*?)</tr>",
            page,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(header)

        return [
            re.sub(
                r"\s*↕\s*$",
                "",
                re.sub(r"<[^>]+>", "", value).strip(),
            )
            for value in re.findall(
                r"<th\b[^>]*>(.*?)</th>",
                header.group(1),
                flags=re.DOTALL,
            )
        ]

    def get_combobox_values(self, page, component_id, next_marker):
        start = page.index(f'id="{component_id}"')
        end = page.index(next_marker, start)
        return [
            html.unescape(value)
            for value in re.findall(
                r'data-brand="([^"]*)"',
                page[start:end],
            )
        ]

    def test_page_has_exactly_four_source_tabs(self):
        response = self.client.get("/sales?source=all")
        page = response.get_data(as_text=True)
        tabs = re.findall(
            r'data-source-tab="([^"]+)"',
            page,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            tabs,
            ["all", "tictactoy", "wildberries", "amazon"],
        )
        self.assertNotIn("Ручные продажи", page)

    def test_sale_choice_fields_reuse_brand_combobox_and_keep_option_order(self):
        self.manual_sales_path.write_text(
            json.dumps([
                {
                    "id": "legacy-platform",
                    "source": "Amazon",
                    "platform": "Amazon.de",
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        page = self.client.get(
            "/sales?source=amazon"
        ).get_data(as_text=True)

        for component_id in (
            "saleStatus",
            "saleCommission",
            "salePlatform",
            "saleCountry",
            "tictactoyCountry",
            "tictactoyRegion",
            "tictactoyCity",
        ):
            with self.subTest(component_id=component_id):
                component_start = page.index(
                    f'id="{component_id}"'
                )
                component_excerpt = page[
                    component_start:component_start + 2400
                ]
                self.assertIn(
                    "brand-combobox filter-combobox",
                    component_excerpt,
                )
                self.assertIn(
                    "brand-combobox-trigger",
                    component_excerpt,
                )
                self.assertIn(
                    "brand-combobox-search-clear",
                    component_excerpt,
                )

        self.assertEqual(
            self.get_combobox_values(
                page,
                "saleStatus",
                'id="saleCommission"',
            ),
            ["shipped", "returned"],
        )
        self.assertEqual(
            self.get_combobox_values(
                page,
                "saleCommission",
                'data-source-fields="tictactoy"',
            ),
            web.SALE_COMMISSION_OPTIONS,
        )
        self.assertEqual(
            self.get_combobox_values(
                page,
                "salePlatform",
                'id="saleCountry"',
            ),
            [
                *web.SALE_PLATFORM_OPTIONS,
                "Amazon.de",
            ],
        )

        countries = self.get_combobox_values(
            page,
            "saleCountry",
            'id="invoice_number"',
        )
        self.assertEqual(
            countries[:4],
            ["Америка", "Япония", "Канада", "Мексика"],
        )
        self.assertGreaterEqual(len(countries), 195)
        self.assertEqual(len(countries), len(set(countries)))
        self.assertEqual(
            countries[4:],
            sorted(countries[4:], key=str.casefold),
        )
        for pinned_country in countries[:4]:
            self.assertNotIn(pinned_country, countries[4:])

        self.assertEqual(
            self.get_combobox_values(
                page,
                "tictactoyCountry",
                'id="tictactoyRegion"',
            ),
            web.TICTACTOY_SALE_COUNTRIES,
        )
        self.assertIn(
            'data-search-enabled="false"',
            page[
                page.index('id="tictactoyCountry"'):
                page.index('id="tictactoyRegion"')
            ],
        )

        tictactoy_fields = page[
            page.index('data-source-fields="tictactoy"'):
            page.index('data-source-fields="wildberries"')
        ]
        amazon_fields = page[
            page.index('data-source-fields="amazon"'):
            page.index('class="form-actions"')
        ]
        self.assertNotIn('name="payment_method"', tictactoy_fields)
        self.assertNotIn("Способ оплаты", tictactoy_fields)
        self.assertNotIn('name="delivery_method"', tictactoy_fields)
        self.assertNotIn("Способ доставки", tictactoy_fields)
        self.assertNotIn('name="delivery_address"', amazon_fields)
        self.assertNotIn("Адрес доставки", amazon_fields)

    def test_tictactoy_locations_use_committed_site_catalog(self):
        catalog = web.get_tictactoy_location_catalog()

        self.assertEqual(
            list(catalog),
            web.TICTACTOY_SALE_COUNTRIES,
        )
        self.assertEqual(catalog["Россия"]["Москва"], ["Москва"])
        self.assertIn(
            "Минск",
            catalog["Беларусь"]["Минская область"],
        )
        self.assertIn(
            "Алматы",
            catalog["Казахстан"]["Алматинская область"],
        )

        for country, regions in catalog.items():
            with self.subTest(country=country):
                self.assertEqual(
                    len(regions),
                    len(set(regions)),
                )
                self.assertEqual(
                    list(regions),
                    sorted(regions, key=str.casefold),
                )

                for cities in regions.values():
                    self.assertEqual(
                        len(cities),
                        len(set(cities)),
                    )
                    self.assertEqual(
                        cities,
                        sorted(cities, key=str.casefold),
                    )

    def test_source_filters_exclude_unknown_from_all_without_deleting(self):
        stored = [
            {
                "id": "tictactoy",
                "source": "Заказ Битрикс",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": 1,
                "unit_price": 1000,
            },
            {
                "id": "wildberries",
                "source": "Wildberries",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": 1,
                "unit_price": 1000,
            },
            {
                "id": "amazon",
                "source": "Amazon",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": 1,
                "unit_price": 1000,
            },
            {
                "id": "unknown",
                "source": "Avito",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": 1,
                "unit_price": 1000,
            },
        ]
        self.manual_sales_path.write_text(
            json.dumps(stored, ensure_ascii=False),
            encoding="utf-8",
        )

        records = web.build_sales_report_records()

        self.assertEqual(
            {
                sale["id"]
                for sale in web.filter_sales_by_source(
                    records,
                    "all",
                )
            },
            {"tictactoy", "wildberries", "amazon"},
        )
        for source in ("tictactoy", "wildberries", "amazon"):
            with self.subTest(source=source):
                self.assertEqual(
                    [
                        sale["id"]
                        for sale in web.filter_sales_by_source(
                            records,
                            source,
                        )
                    ],
                    [source],
                )
        self.assertEqual(web.load_manual_sales(), stored)

    def test_each_tab_uses_exact_column_names_and_order(self):
        self.assertEqual(
            set(web.SALES_TABLE_COLUMNS),
            set(EXPECTED_COLUMNS),
        )

        for source, expected in EXPECTED_COLUMNS.items():
            with self.subTest(source=source):
                self.assertEqual(
                    self.get_headers(source),
                    [*expected, "Действия"],
                )
                self.assertEqual(
                    [
                        label
                        for _, label in web.SALES_TABLE_COLUMNS[
                            source
                        ]
                    ],
                    expected,
                )

    def test_actions_are_locked_last_and_shared_by_every_source_tab(self):
        stored = [
            {
                "id": source,
                "source": source,
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": 1,
                "unit_price": 1000,
                "created_at": "2026-07-22",
            }
            for source in ("Tictactoy", "Wildberries", "Amazon")
        ]
        self.manual_sales_path.write_text(
            json.dumps(stored, ensure_ascii=False),
            encoding="utf-8",
        )

        for source in EXPECTED_COLUMNS:
            with self.subTest(source=source):
                page = self.client.get(
                    f"/sales?source={source}"
                ).get_data(as_text=True)

                self.assertEqual(
                    self.get_headers(source)[-1],
                    "Действия",
                )
                self.assertIn(
                    'data-system-column="actions"',
                    page,
                )
                self.assertIn(
                    'aria-label="Редактировать продажу"',
                    page,
                )
                self.assertIn(
                    'aria-label="Удалить продажу"',
                    page,
                )
                self.assertIn(
                    'onclick="openSaleEditor(this)"',
                    page,
                )
                self.assertIn(
                    'onclick="deleteSale(this)"',
                    page,
                )
                self.assertNotIn(
                    'data-column-visibility-key="actions"',
                    page,
                )
                self.assertNotIn(
                    'data-sort-field="actions"',
                    page,
                )

    def test_each_tab_has_independent_column_settings_storage(self):
        for source in EXPECTED_COLUMNS:
            with self.subTest(source=source):
                page = self.client.get(
                    f"/sales?source={source}"
                ).get_data(as_text=True)

                self.assertIn(
                    f'data-sales-settings-key="sales_{source}"',
                    page,
                )
                self.assertIn(
                    'id="salesColumnSettingsTrigger"',
                    page,
                )
                self.assertIn(
                    'id="salesColumnSettingsReset"',
                    page,
                )
                self.assertIn(
                    "localStorage.getItem(settingsKey)",
                    page,
                )
                self.assertIn(
                    "localStorage.setItem(",
                    page,
                )

    def test_column_settings_keep_one_visible_and_accept_new_columns(self):
        page = self.client.get(
            "/sales?source=all"
        ).get_data(as_text=True)

        self.assertIn(
            "defaultOrder.forEach((key) => {",
            page,
        )
        self.assertIn(
            "if (hidden.length >= order.length)",
            page,
        )
        self.assertIn(
            "Оставьте видимым хотя бы один столбец.",
            page,
        )
        self.assertIn(
            "view.order = sanitizeOrder(defaultOrder);",
            page,
        )
        self.assertIn(
            "salesColumnSettings = {",
            page,
        )

    def test_source_url_is_active_and_filters_survive_tab_links(self):
        response = self.client.get(
            "/sales?source=amazon&q=Berlin"
            "&date_from=2026-07-01&date_to=2026-07-31"
            "&sort=created_at&sort_dir=desc"
        )
        page = response.get_data(as_text=True)
        self.assertIn(
            'data-source-tab="amazon"\n'
            '                        aria-current="page"',
            page,
        )
        wildberries_link = re.search(
            r'class="sales-tab[^"]*"\s+'
            r'href="([^"]+)"\s+'
            r'data-source-tab="wildberries"',
            page,
        )
        self.assertIsNotNone(wildberries_link)
        parsed = urlparse(
            html.unescape(wildberries_link.group(1))
        )
        query = parse_qs(parsed.query)
        self.assertEqual(query["source"], ["wildberries"])
        self.assertEqual(query["q"], ["Berlin"])
        self.assertEqual(query["date_from"], ["2026-07-01"])
        self.assertEqual(query["date_to"], ["2026-07-31"])
        self.assertEqual(query["sort"], ["created_at"])
        self.assertEqual(query["sort_dir"], ["desc"])

    def test_add_modal_chooses_source_only_from_all_tab(self):
        all_page = self.client.get(
            "/sales?source=all"
        ).get_data(as_text=True)
        amazon_page = self.client.get(
            "/sales?source=amazon"
        ).get_data(as_text=True)

        self.assertIn('id="saleSourceChoice"', all_page)
        self.assertEqual(
            all_page.count("data-sale-source="),
            3,
        )
        self.assertIn(
            'const activeSalesSource = "all";',
            all_page,
        )
        self.assertIn(
            'const activeSalesSource = "amazon";',
            amazon_page,
        )
        self.assertIn(
            'showSaleForm(activeSalesSource);',
            amazon_page,
        )
        self.assertNotIn("Ручные продажи", amazon_page)

    def test_form_uses_brand_category_product_cascade(self):
        page = self.client.get(
            "/sales?source=wildberries"
        ).get_data(as_text=True)
        brand_position = page.index('id="saleBrand"')
        category_position = page.index('id="saleCategory"')
        product_position = page.index('id="saleProduct"')

        self.assertLess(brand_position, category_position)
        self.assertLess(category_position, product_position)
        self.assertLess(
            product_position,
            page.index('id="created_at"'),
        )
        self.assertIn(
            'data-shared-catalog-kind="brand"',
            page,
        )
        self.assertIn(
            'data-shared-catalog-kind="category"',
            page,
        )
        self.assertIn(
            'data-shared-catalog-kind="product"',
            page,
        )
        self.assertIn(
            'data-shared-catalog-scope',
            page,
        )
        self.assertNotIn("function refreshCategoryOptions", page)
        self.assertNotIn("function productMatchesSelection", page)
        self.assertIn(
            "catalog-combobox.css",
            page,
        )
        self.assertIn(
            "catalog-combobox.js",
            page,
        )
    def test_every_source_tab_renders_the_same_catalog_picker(self):
        for source in ("all", "tictactoy", "wildberries", "amazon"):
            with self.subTest(source=source):
                page = self.client.get(
                    f"/sales?source={source}"
                ).get_data(as_text=True)

                self.assertEqual(page.count('id="saleBrand"'), 1)
                self.assertEqual(page.count('id="saleCategory"'), 1)
                self.assertEqual(page.count('id="saleProduct"'), 1)
                self.assertIn(
                    'name="product_brand"',
                    page,
                )
                self.assertIn(
                    'name="product_category"',
                    page,
                )
                self.assertIn(
                    'name="product_id"',
                    page,
                )

    def test_picker_uses_sorted_deduplicated_products_catalog(self):
        first = warehouse_item()
        first.update(
            id="catalog-2",
            name="Модель 10",
            brand="Zulu",
            category="Часы/Мужские",
            stock=2,
            stock_display="2",
        )
        second = warehouse_item()
        second.update(
            id="catalog-1",
            name="Модель 2",
            brand="Alpha",
            category="Часы/Женские",
            stock=3,
            stock_display="3",
        )
        duplicate = dict(second)
        duplicate["name"] = "Не должен попасть"
        unavailable = warehouse_item()
        unavailable.update(
            id="catalog-3",
            brand="Hidden",
            category="Часы",
            stock=0,
            stock_display="0",
        )

        catalog = web.build_sales_catalog_items([
            first,
            second,
            duplicate,
            unavailable,
        ])

        self.assertEqual(
            [item["id"] for item in catalog],
            ["catalog-1", "catalog-2"],
        )
        self.assertEqual(catalog[0]["name"], "Модель 2")

        with mock.patch.object(
            web,
            "get_excel_warehouse_items",
            return_value=[first, second],
        ) as legacy_catalog:
            page = self.client.get("/sales").get_data(as_text=True)

        legacy_catalog.assert_not_called()
        self.assertNotIn('"id": "catalog-1"', page)
        self.assertNotIn('"id": "catalog-2"', page)
        self.assertIn("catalog-combobox.js", page)

    def test_add_rejects_unknown_or_incompatible_catalog_product(self):
        base_data = {
            "created_at": "2026-07-22",
            "source": "Amazon",
            "product_name": "Произвольный товар",
            "quantity": "1",
            "unit_price": "1000",
        }

        unknown = self.client.post(
            "/sales/manual/add",
            data={
                **base_data,
                "product_id": "missing-product",
            },
        )
        incompatible = self.client.post(
            "/sales/manual/add",
            data={
                **base_data,
                "product_id": PRODUCT_ID,
                "product_brand": "Другой бренд",
                "product_category": "Коллекция",
            },
        )

        self.assertEqual(unknown.status_code, 302)
        self.assertEqual(incompatible.status_code, 302)
        self.assertEqual(web.load_manual_sales(), [])
        unknown_message = parse_qs(
            urlparse(unknown.headers["Location"]).query
        )["message"][0]
        incompatible_message = parse_qs(
            urlparse(incompatible.headers["Location"]).query
        )["message"][0]
        self.assertEqual(
            unknown_message,
            "Выберите товар из каталога",
        )
        self.assertIn("не относится", incompatible_message)

    def test_add_requires_brand_and_category_from_catalog_chain(self):
        response = self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-22",
                "source": "Amazon",
                "product_id": PRODUCT_ID,
                "quantity": "1",
                "unit_price": "1000",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(web.load_manual_sales(), [])
        self.assertIn(
            "Выберите бренд",
            parse_qs(
                urlparse(response.headers["Location"]).query
            )["message"][0],
        )

    def test_add_rejects_quantity_above_catalog_stock_without_writes(self):
        with mock.patch.object(
            web,
            "save_stock_operations",
        ) as save_stock_operations:
            response = self.client.post(
                "/sales/manual/add",
                data={
                    "created_at": "2026-07-22",
                    "source": "Tictactoy",
                    "product_id": PRODUCT_ID,
                    "product_brand": "Brand",
                    "product_category": "Коллекция",
                    "quantity": "6",
                    "unit_price": "1000",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(web.load_manual_sales(), [])
        self.assertEqual(
            parse_qs(
                urlparse(response.headers["Location"]).query
            )["message"][0],
            "Недостаточно товара на складе. Доступно: 5",
        )
        save_stock_operations.assert_not_called()

    def test_valid_add_saves_once_and_does_not_write_stock(self):
        original_save = web.save_manual_sales

        with mock.patch.object(
            web,
            "save_manual_sales",
            wraps=original_save,
        ) as save_manual_sales, mock.patch.object(
            web,
            "save_stock_operations",
        ) as save_stock_operations:
            response = self.client.post(
                "/sales/manual/add",
                data={
                    "created_at": "2026-07-22",
                    "source": "Wildberries",
                    "product_id": PRODUCT_ID,
                    "product_name": "Подмена",
                    "product_brand": "Brand",
                    "product_category": "Коллекция",
                    "quantity": "1",
                    "unit_price": "1000",
                },
            )

        stored = web.load_manual_sales()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["product_name"], "Часы Test")
        self.assertEqual(stored[0]["brand"], "Brand")
        self.assertEqual(stored[0]["category"], "Коллекция")
        self.assertEqual(stored[0]["barcode"], "BARCODE-1")
        save_manual_sales.assert_called_once()
        save_stock_operations.assert_not_called()

    def test_sale_choices_save_reload_and_render_for_both_form_statuses(self):
        commission = web.SALE_COMMISSION_OPTIONS[0]

        for index, (status, status_label) in enumerate(
            web.SALE_FORM_STATUS_LABELS.items(),
            start=1,
        ):
            with self.subTest(status=status):
                response = self.client.post(
                    "/sales/manual/add",
                    data={
                        "created_at": f"2026-07-{20 + index:02d}",
                        "source": "Amazon",
                        "return_source": "amazon",
                        "product_id": PRODUCT_ID,
                        "product_name": "Часы Test",
                        "product_brand": "Brand",
                        "product_category": "Коллекция",
                        "quantity": "1",
                        "unit_price": "1000",
                        "commission": commission,
                        "order_status": status,
                        "platform": "Amazon (US)",
                        "country": "Япония",
                    },
                )

                self.assertEqual(response.status_code, 302)

                stored = web.load_manual_sales()[-1]
                self.assertEqual(stored["commission"], commission)
                self.assertEqual(stored["platform"], "Amazon (US)")
                self.assertEqual(stored["country"], "Япония")
                self.assertEqual(stored["order_status"], status)
                self.assertEqual(stored["commission_amount"], 0)

                record = web.build_sales_report_records()[0]
                self.assertEqual(record["commission"], commission)
                self.assertEqual(record["platform"], "Amazon (US)")
                self.assertEqual(record["country"], "Япония")
                self.assertEqual(record["order_status"], status)
                self.assertEqual(
                    record["order_status_label"],
                    status_label,
                )
                self.assertEqual(record["total_amount"], 1000)

        page = self.client.get(
            "/sales?source=amazon"
        ).get_data(as_text=True)
        self.assertIn(
            f'class="col-commission"',
            page,
        )
        self.assertIn(
            f'class="col-order_status_label"',
            page,
        )
        self.assertIn(commission, page)
        self.assertIn("Amazon (US)", page)
        self.assertIn("Япония", page)
        self.assertIn("Отправлен", page)
        self.assertIn("Возврат", page)

    def test_ajax_edit_updates_manual_sale_without_stock_write(self):
        self.manual_sales_path.write_text(
            json.dumps([
                {
                    "id": "manual-1",
                    "created_at": "2026-07-22",
                    "source": "Amazon",
                    "product_id": PRODUCT_ID,
                    "product_name": "Часы Test",
                    "barcode": "BARCODE-1",
                    "brand": "Brand",
                    "category": "Коллекция",
                    "quantity": 1,
                    "unit_price": 1000,
                    "order_number": "OLD-1",
                    "delivery_address": "Legacy Amazon address",
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        with mock.patch.object(
            web,
            "save_stock_operations",
        ) as save_stock_operations:
            response = self.client.post(
                "/sales/manual/update",
                data={
                    "sale_id": "manual-1",
                    "created_at": "2026-07-28",
                    "source": "Amazon",
                    "product_id": PRODUCT_ID,
                    "product_name": "Часы Test",
                    "quantity": "2",
                    "unit_price": "1250",
                    "order_status": "returned",
                    "commission": web.SALE_COMMISSION_OPTIONS[1],
                    "commission_amount": "75.50",
                    "order_number": "AMZ-NEW",
                    "recipient_name": "Иван Иванов",
                    "platform": "Amazon (CA)",
                    "country": "Канада",
                    "invoice_number": "INV-NEW",
                    "note": "Обновлено",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )

        stored = web.load_manual_sales()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"ok": True, "message": "Изменения сохранены"},
        )
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], "manual-1")
        self.assertEqual(stored[0]["created_at"], "2026-07-28")
        self.assertEqual(stored[0]["quantity"], 2)
        self.assertEqual(stored[0]["unit_price"], 1250.0)
        self.assertEqual(stored[0]["total_amount"], 2500.0)
        self.assertEqual(
            stored[0]["commission"],
            web.SALE_COMMISSION_OPTIONS[1],
        )
        self.assertEqual(stored[0]["commission_amount"], 75.5)
        self.assertEqual(stored[0]["order_status"], "returned")
        self.assertEqual(stored[0]["platform"], "Amazon (CA)")
        self.assertEqual(stored[0]["country"], "Канада")
        self.assertEqual(stored[0]["order_number"], "AMZ-NEW")
        self.assertEqual(
            stored[0]["delivery_address"],
            "Legacy Amazon address",
        )
        save_stock_operations.assert_not_called()

    def test_ajax_edit_validation_keeps_manual_sale_unchanged(self):
        stored = [{
            "id": "manual-1",
            "created_at": "2026-07-22",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "unit_price": 1000,
        }]
        self.manual_sales_path.write_text(
            json.dumps(stored, ensure_ascii=False),
            encoding="utf-8",
        )

        response = self.client.post(
            "/sales/manual/update",
            data={
                "sale_id": "manual-1",
                "created_at": "28.07.2026",
                "source": "Tictactoy",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "quantity": "2",
                "unit_price": "1250",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["ok"], False)
        self.assertEqual(
            response.get_json()["message"],
            "Укажите корректную дату продажи",
        )
        self.assertEqual(web.load_manual_sales(), stored)

    def test_ajax_edit_updates_automatic_override_not_stock_operation(self):
        operation = {
            "id": "automatic-1",
            "created_at": "2026-07-22",
            "source": "Заказ Битрикс",
            "type": "writeoff",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "order_number": "ORDER-1",
        }

        with mock.patch.object(
            web,
            "load_stock_operations",
            return_value=[operation],
        ), mock.patch.object(
            web,
            "save_stock_operations",
        ) as save_stock_operations:
            response = self.client.post(
                "/sales/automatic/update",
                data={
                    "operation_id": "automatic-1",
                    "created_at": "2026-07-28",
                    "source": "Tictactoy",
                    "product_name": "Часы Test",
                    "quantity": "2",
                    "unit_price": "1500",
                    "order_status": "processing",
                    "order_number": "ORDER-1",
                    "country": "Россия",
                    "region": "Москва",
                    "city": "Москва",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )

        overrides = web.load_automatic_sales_overrides()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(overrides["automatic-1"]["quantity"], 2)
        self.assertEqual(
            overrides["automatic-1"]["total_amount"],
            3000.0,
        )
        self.assertEqual(overrides["automatic-1"]["country"], "Россия")
        self.assertEqual(overrides["automatic-1"]["region"], "Москва")
        self.assertEqual(overrides["automatic-1"]["city"], "Москва")
        self.assertEqual(operation["quantity"], 1)
        save_stock_operations.assert_not_called()

    def test_delete_soft_hides_manual_and_automatic_sales_without_stock_write(self):
        self.manual_sales_path.write_text(
            json.dumps([
                {
                    "id": "manual-1",
                    "created_at": "2026-07-22",
                    "source": "Amazon",
                    "product_id": PRODUCT_ID,
                    "product_name": "Часы Test",
                    "quantity": 1,
                    "unit_price": 1000,
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        operation = {
            "id": "automatic-1",
            "created_at": "2026-07-22",
            "source": "Заказ Битрикс",
            "type": "writeoff",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
        }

        with mock.patch.object(
            web,
            "load_stock_operations",
            return_value=[operation],
        ), mock.patch.object(
            web,
            "save_stock_operations",
        ) as save_stock_operations:
            manual_response = self.client.post(
                "/sales/delete",
                data={
                    "sale_id": "manual-1",
                    "sale_type": "manual",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            automatic_response = self.client.post(
                "/sales/delete",
                data={
                    "sale_id": "automatic-1",
                    "sale_type": "automatic",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            records = web.build_sales_report_records()
            repeated_response = self.client.post(
                "/sales/delete",
                data={
                    "sale_id": "automatic-1",
                    "sale_type": "automatic",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )

        manual_sales = web.load_manual_sales()
        overrides = web.load_automatic_sales_overrides()
        self.assertEqual(manual_response.status_code, 200)
        self.assertEqual(automatic_response.status_code, 200)
        self.assertEqual(repeated_response.status_code, 410)
        self.assertTrue(manual_sales[0]["deleted_at"])
        self.assertTrue(overrides["automatic-1"]["deleted_at"])
        self.assertEqual(records, [])
        save_stock_operations.assert_not_called()

    def test_created_amazon_sale_uses_product_snapshot_and_is_not_duplicated(self):
        response = self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-22",
                "source": "Amazon",
                "return_source": "amazon",
                "product_id": PRODUCT_ID,
                "product_name": "Подменённое название",
                "product_brand": "Brand",
                "product_category": "Коллекция",
                "quantity": "2",
                "unit_price": "1000",
                "recipient_name": "Иван Иванов",
                "order_number": "AMZ-100",
                "platform": "Amazon.de",
                "country": "Германия",
                "delivery_address": "Berlin, Test str. 1",
                "invoice_number": "INV-100",
                "note": "Amazon note",
            },
        )
        stored = web.load_manual_sales()
        records = web.build_sales_report_records()

        self.assertEqual(response.status_code, 302)
        self.assertIn("source=amazon", response.headers["Location"])
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["source"], "Amazon")
        self.assertEqual(stored[0]["product_name"], "Часы Test")
        self.assertEqual(stored[0]["barcode"], "BARCODE-1")
        self.assertEqual(stored[0]["brand"], "Brand")
        self.assertEqual(stored[0]["category"], "Коллекция")
        self.assertEqual(
            stored[0]["delivery_address"],
            "Berlin, Test str. 1",
        )
        self.assertEqual(stored[0]["platform"], "Amazon.de")
        self.assertEqual(stored[0]["invoice_number"], "INV-100")
        self.assertEqual(records[0]["platform"], "Amazon.de")
        self.assertNotIn(
            "Адрес доставки",
            self.get_headers("amazon"),
        )
        self.assertEqual(
            len(web.filter_sales_by_source(records, "all")),
            1,
        )
        self.assertEqual(
            len(web.filter_sales_by_source(records, "amazon")),
            1,
        )

    def test_tictactoy_specific_fields_are_saved(self):
        self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-22",
                "source": "Tictactoy",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "product_brand": "Brand",
                "product_category": "Коллекция",
                "quantity": "1",
                "unit_price": "1200",
                "order_number": "ORDER-1",
                "track_number": "TRACK-1",
                "delivery_cost": "350,50",
                "country": "Россия",
                "region": "Москва",
                "city": "Москва",
                "note": "Tictactoy note",
            },
        )
        sale = web.load_manual_sales()[0]

        self.assertEqual(sale["delivery_cost"], 350.5)
        self.assertEqual(sale["track_number"], "TRACK-1")
        self.assertEqual(sale["country"], "Россия")
        self.assertEqual(sale["region"], "Москва")
        self.assertEqual(sale["city"], "Москва")
        self.assertEqual(sale["payment_method"], "")
        self.assertTrue(
            "delivery_method" not in sale
            or sale["delivery_method"] == ""
        )
        self.assertEqual(sale["note"], "Tictactoy note")

    def test_tictactoy_location_edit_restores_and_validates_hierarchy(self):
        stored = {
            "id": "manual-ttt",
            "created_at": "2026-07-22",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "brand": "Brand",
            "category": "Коллекция",
            "quantity": 1,
            "unit_price": 1000,
            "country": "Россия",
            "region": "Москва",
            "city": "Москва",
            "payment_method": "Историческая оплата",
        }
        self.manual_sales_path.write_text(
            json.dumps([stored], ensure_ascii=False),
            encoding="utf-8",
        )
        common_data = {
            "sale_id": "manual-ttt",
            "created_at": "2026-07-28",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": "1",
            "unit_price": "1000",
            "country": "Беларусь",
            "region": "Минская область",
            "city": "Минск",
        }

        response = self.client.post(
            "/sales/manual/update",
            data=common_data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        updated = web.load_manual_sales()[0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(updated["country"], "Беларусь")
        self.assertEqual(updated["region"], "Минская область")
        self.assertEqual(updated["city"], "Минск")
        self.assertEqual(
            updated["payment_method"],
            "Историческая оплата",
        )
        page = self.client.get(
            "/sales?source=tictactoy"
        ).get_data(as_text=True)
        self.assertIn("Беларусь", page)
        self.assertIn("Минская область", page)
        self.assertIn("Минск", page)

        invalid_data = {
            **common_data,
            "country": "Казахстан",
            "region": "Минская область",
        }
        invalid_response = self.client.post(
            "/sales/manual/update",
            data=invalid_data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(
            web.load_manual_sales()[0]["country"],
            "Беларусь",
        )

    def test_wildberries_sticker_and_order_are_saved(self):
        self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-22",
                "source": "Wildberries",
                "product_id": PRODUCT_ID,
                "product_name": "Часы Test",
                "product_brand": "Brand",
                "product_category": "Коллекция",
                "quantity": "1",
                "unit_price": "1200",
                "sticker_number": "STICKER-1",
                "order_number": "WB-ORDER-1",
                "note": "Wildberries note",
            },
        )
        sale = web.load_manual_sales()[0]

        self.assertEqual(sale["sticker_number"], "STICKER-1")
        self.assertEqual(sale["order_number"], "WB-ORDER-1")
        self.assertEqual(sale["note"], "Wildberries note")

    def test_old_amazon_address_is_preserved_but_not_used_as_platform(self):
        stored = [{
            "id": "amazon-old",
            "source": "Amazon",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "unit_price": 1000,
            "delivery_address": "Berlin, Real address 1",
        }]
        self.manual_sales_path.write_text(
            json.dumps(stored, ensure_ascii=False),
            encoding="utf-8",
        )

        records = web.build_sales_report_records()

        self.assertEqual(
            records[0]["delivery_address"],
            "Berlin, Real address 1",
        )
        self.assertEqual(records[0]["platform"], "")
        self.assertEqual(web.load_manual_sales(), stored)
        preserved_optional_fields = web.build_sale_optional_fields(
            {},
            existing={
                "delivery_address": "Berlin, Real address 1",
                "platform": "Amazon.de",
            },
        )
        self.assertEqual(
            preserved_optional_fields["delivery_address"],
            "Berlin, Real address 1",
        )
        self.assertEqual(
            preserved_optional_fields["platform"],
            "Amazon.de",
        )
        self.assertNotIn(
            "delivery_address",
            [
                key
                for key, _ in web.SALES_TABLE_COLUMNS["amazon"]
            ],
        )

    def test_search_uses_only_fields_of_active_source(self):
        amazon = sale_record(
            source="Amazon",
            delivery_address="Berlin address",
            platform="Marketplace-DE",
        )
        all_search = web.build_sales_search_text(
            amazon,
            "all",
        )
        amazon_search = web.build_sales_search_text(
            amazon,
            "amazon",
        )

        self.assertIn("Amazon", all_search)
        self.assertIn("ORDER-100", all_search)
        self.assertIn("TRACK-100", all_search)
        self.assertNotIn("Berlin address", all_search)
        self.assertIn("Berlin address", amazon_search)
        self.assertIn("Marketplace-DE", amazon_search)
        self.assertNotIn("Amazon", amazon_search)

        wildberries_search = web.build_sales_search_text(
            sale_record(source="Wildberries"),
            "wildberries",
        )
        self.assertIn("STICKER-100", wildberries_search)
        self.assertIn("ORDER-100", wildberries_search)

        filtered = web.filter_sales_report_records(
            [amazon],
            {
                "source": "amazon",
                "q": "berlin address",
            },
        )
        self.assertEqual(filtered, [amazon])

    def test_period_and_metrics_apply_after_source_filter(self):
        records = [
            sale_record(
                source="Wildberries",
                id="visible",
                created_at="2026-07-15",
            ),
            sale_record(
                source="Wildberries",
                id="outside",
                created_at="2026-06-30",
            ),
            sale_record(
                source="Amazon",
                id="amazon",
                created_at="2026-07-15",
            ),
        ]

        with mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=records,
        ), web.app.test_request_context(
            "/sales/report?source=wildberries"
            "&date_from=2026-07-01&date_to=2026-07-31"
        ):
            context = web.build_sales_report_context()

        self.assertEqual(
            [sale["id"] for sale in context["sales"]],
            ["visible"],
        )
        self.assertEqual(context["total_sales"], 1)
        self.assertEqual(context["total_quantity"], "2")
        self.assertEqual(context["total_revenue"], 2000.0)

    def test_report_html_and_xlsx_follow_each_active_tab(self):
        records = [
            sale_record(
                source="Tictactoy",
                id="tictactoy",
            ),
            sale_record(
                source="Wildberries",
                id="wildberries",
                order_number="WB-100",
            ),
            sale_record(
                source="Amazon",
                id="amazon",
                order_number="AMZ-100",
            ),
        ]

        with mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=records,
        ):
            for source, expected in EXPECTED_COLUMNS.items():
                with self.subTest(source=source):
                    html_response = self.client.get(
                        f"/sales/report?source={source}"
                    )
                    xlsx_response = self.client.get(
                        f"/sales/report.xlsx?source={source}"
                    )
                    report_page = html_response.get_data(
                        as_text=True
                    )
                    header = re.search(
                        r"<thead>\s*<tr>(.*?)</tr>",
                        report_page,
                        flags=re.DOTALL,
                    )
                    html_headers = [
                        re.sub(r"<[^>]+>", "", value).strip()
                        for value in re.findall(
                            r"<th>(.*?)</th>",
                            header.group(1),
                            flags=re.DOTALL,
                        )
                    ]
                    workbook = load_workbook(
                        BytesIO(xlsx_response.data),
                        read_only=True,
                    )
                    sheet = workbook.active
                    xlsx_headers = [
                        cell.value
                        for cell in sheet[4]
                        if cell.value is not None
                    ]

                    self.assertEqual(
                        html_response.status_code,
                        200,
                    )
                    self.assertEqual(
                        xlsx_response.status_code,
                        200,
                    )
                    self.assertEqual(
                        html_headers,
                        expected + RETURN_REPORT_COLUMNS,
                    )
                    self.assertEqual(
                        xlsx_headers,
                        expected + RETURN_REPORT_COLUMNS,
                    )

                    if source == "amazon":
                        self.assertIn("AMZ-100", report_page)
                        self.assertIn("Amazon.de", report_page)
                        self.assertNotIn(
                            "<th>Адрес доставки</th>",
                            report_page,
                        )
                        self.assertEqual(
                            sheet["L5"].value,
                            "Amazon.de",
                        )
                        self.assertNotIn(
                            "TRACK-100",
                            report_page,
                        )

    def test_bitrix_import_records_remain_tictactoy(self):
        operation = {
            "id": "automatic-1",
            "created_at": "2026-07-22",
            "source": "Заказ Битрикс",
            "type": "writeoff",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "order_number": "ORDER-1",
        }

        with mock.patch.object(
            web,
            "load_stock_operations",
            return_value=[operation],
        ):
            records = web.build_sales_report_records()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "Tictactoy")
        self.assertEqual(records[0]["source_key"], "tictactoy")

    def test_mobile_layout_keeps_page_and_table_scroll_separate(self):
        template = (
            Path(web.app.root_path)
            / web.app.template_folder
            / "sales.html"
        ).read_text(encoding="utf-8")

        self.assertIn("overflow-x: hidden", template)
        self.assertIn(".table-wrap", template)
        self.assertIn("overflow-x: auto", template)
        self.assertIn("scrollbar-color: #94a3b8 #e8edf3", template)
        self.assertIn(".sales-tabs-scroll", template)
        self.assertIn("@media (max-width: 560px)", template)
        self.assertIn("max-width: 100%", template)

    def test_sales_table_pins_identity_columns_and_formats_date_visually(self):
        records = [
            sale_record(
                source="Amazon",
                created_at="2026-07-27",
                order_number="AMZ-27",
            ),
        ]

        with mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=records,
        ):
            page = self.client.get(
                "/sales?source=amazon"
            ).get_data(as_text=True)

        self.assertIn("27.07.2026", page)
        self.assertIn(
            'data-sort-date="2026-07-27"',
            page,
        )
        self.assertIn(
            'title="2026-07-27"',
            page,
        )
        self.assertIn(
            'const pinnedColumns = [',
            page,
        )
        self.assertIn(
            '"created_at",\n            "order_number",',
            page,
        )
        self.assertIn(
            '"--sales-sticky-date-width"',
            page,
        )


if __name__ == "__main__":
    unittest.main()
