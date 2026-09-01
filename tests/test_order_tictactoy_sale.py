import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.order_status import OrderStatusService
from app.services.sales_inventory import SalesInventory
from app.services.shared_catalog import SharedCatalog


class OrderTictactoySaleTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp_directory.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('batch-order', 'sha-order', 'order.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:00+00:00')"
            )
        catalog = ExcelProductCatalog(self.database)
        self.watch = catalog.create_product(
            name="Bradley Steel", article="BRADLEY-STEEL", brand="Bradley",
            category="Часы", stock=5,
        )
        self.strap = catalog.create_product(
            name="Ремешок Bradley", article="STRAP-1", brand="Bradley",
            category="Ремешки", stock=3,
        )
        self.shared = SharedCatalog(self.database)
        self.inventory = SalesInventory(self.database)
        self.order = {
            "id": "18593", "number": "18593", "status": "A",
            "customer": "Иван Иванов", "order_total": 17400,
            "created_at": "2026-08-18 10:00",
            "products": [
                {"id": "line-1", "product_id": "bx-watch", "name": "Часы", "quantity": 2, "price": 7500},
                {"id": "line-2", "product_id": "bx-strap", "name": "Ремешок", "quantity": 1, "price": 2400},
            ],
        }
        self.mappings = {
            "line:line-1": {"product_id": str(self.watch["id"])},
            "line:line-2": {"product_id": str(self.strap["id"])},
        }

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def patches(self, order=None, mappings=None):
        selected = self.order if order is None else order
        return (
            mock.patch.object(web, "get_orders", return_value=[selected]),
            mock.patch.object(web, "get_order", return_value=selected),
            mock.patch.object(web, "load_order_product_mappings", return_value=self.mappings if mappings is None else mappings),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
        )

    def render_order(self, query=""):
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return self.client.get("/order/18593" + query)

    def conduct(self, order=None, mappings=None, performed_at=None, **data):
        patches = self.patches(order, mappings)
        time_patch = mock.patch.object(
            web,
            "sale_now_iso",
            return_value=performed_at or "2026-08-18T15:05:00+03:00",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], time_patch:
            return self.client.post(
                "/order/18593/stock-writeoff",
                data={"csrf_token": "test-token", **data},
            )

    def test_shared_catalog_finds_bradley_and_new_products_without_restart(self):
        found = self.shared.list_products(query="Bradley Steel")
        self.assertEqual([item["id"] for item in found], [str(self.watch["id"])])
        created = ExcelProductCatalog(self.database).create_product(
            name="Bradley Steel New", article="BRADLEY-NEW", brand="Bradley",
            category="Часы", stock=0,
        )
        self.assertIn(str(created["id"]), [item["id"] for item in self.shared.list_products(query="BRADLEY-NEW")])

    def test_order_route_conducts_strap_replacement_without_selling_base_sku(self):
        catalog = ExcelProductCatalog(self.database)
        base = catalog.create_product(
            name="Bradley Blue", article="BRADLEY-BLUE", brand="Bradley",
            category="Часы", stock=2,
        )
        removed = catalog.create_product(
            name="Синий ремешок Bradley", article="STRAP-BLUE", brand="Bradley",
            category="Ремешки", stock=0,
        )
        installed = catalog.create_product(
            name="Чёрный ремешок Bradley", article="STRAP-BLACK", brand="Bradley",
            category="Ремешки", stock=2,
        )
        response = self.conduct(
            operation_mode="strap_replacement",
            strap_operation_id="route-strap-operation",
            strap_line_index="0",
            strap_base_product_id=str(base["id"]),
            removed_strap_mode="existing",
            removed_strap_product_id=str(removed["id"]),
            installed_strap_product_id=str(installed["id"]),
            original_price_0="7500",
            original_price_1="2400",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.startswith("/sales?"))
        sales = self.inventory.list_sales()
        self.assertEqual(len({sale["id"] for sale in sales}), 1)
        sold_product_ids = {sale["product_id"] for sale in sales}
        self.assertIn(str(self.watch["id"]), sold_product_ids)
        self.assertNotIn(str(base["id"]), sold_product_ids)
        with self.database.connect() as connection:
            stock = {
                row["id"]: row["stock"] for row in connection.execute(
                    "SELECT id,stock FROM catalog_excel_products WHERE id IN (?,?,?)",
                    (base["id"], removed["id"], installed["id"]),
                )
            }
        self.assertEqual(stock, {
            base["id"]: 0, removed["id"]: 2, installed["id"]: 0,
        })

    def test_unique_bitrix_product_id_maps_automatically_without_persisting(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_external_product_id = ? "
                "WHERE id = ?",
                ("bx-watch", self.watch["id"]),
            )
        context = web.build_order_product_mapping_context(
            [self.order["products"][0]], mappings={}, catalog=self.shared,
        )
        mapping = context["line:line-1"]
        self.assertEqual(mapping["state"], "mapped")
        self.assertEqual(mapping["mapping_method"], "bitrix_product_id")
        self.assertEqual(mapping["product"]["id"], str(self.watch["id"]))
        self.assertEqual(web.load_order_product_mappings("18593", self.database), {})

    def test_ambiguous_bitrix_product_id_keeps_manual_fallback(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_external_product_id = ? "
                "WHERE id IN (?, ?)",
                ("bx-watch", self.watch["id"], self.strap["id"]),
            )
        context = web.build_order_product_mapping_context(
            [self.order["products"][0]], mappings={}, catalog=self.shared,
        )
        self.assertEqual(context["line:line-1"]["state"], "unmapped")
        self.assertIsNone(context["line:line-1"]["product"])

    def test_order_counts_use_distinct_existing_non_cancelled_orders(self):
        orders = [
            self.order,
            {
                **self.order,
                "id": "18594",
                "number": "18594",
                "products": [
                    {"id": "line-1", "product_id": "bx-watch", "name": "Часы"},
                    {"id": "line-1", "product_id": "bx-watch", "name": "Часы повторно"},
                ],
            },
            {
                **self.order,
                "id": "18595",
                "number": "18595",
                "status": "C",
                "products": [
                    {"product_id": "bx-watch", "name": "Отменённые часы"},
                ],
            },
        ]

        counts = web.build_catalog_product_order_counts(
            orders, mappings=self.mappings, catalog=self.shared
        )

        self.assertEqual(counts[str(self.watch["id"])], 2)
        self.assertEqual(counts[str(self.strap["id"])], 1)
        product_without_orders = ExcelProductCatalog(self.database).create_product(
            name="Без заказов", article="NO-ORDERS", brand="Bradley",
            category="Часы", stock=1,
        )
        self.assertEqual(counts.get(str(product_without_orders["id"]), 0), 0)

    def test_order_counts_follow_mapping_created_after_order_and_remapping(self):
        external_id = "line:line-1"
        mappings = {}
        before = web.build_catalog_product_order_counts(
            [self.order], mappings=mappings, catalog=self.shared
        )
        self.assertEqual(before.get(str(self.watch["id"]), 0), 0)

        mappings[external_id] = {"product_id": str(self.watch["id"])}
        first = web.build_catalog_product_order_counts(
            [self.order], mappings=mappings, catalog=self.shared
        )
        mappings[external_id] = {"product_id": str(self.watch["id"])}
        repeated = web.build_catalog_product_order_counts(
            [self.order], mappings=mappings, catalog=self.shared
        )
        self.assertEqual(first[str(self.watch["id"])], 1)
        self.assertEqual(repeated[str(self.watch["id"])], 1)

        mappings[external_id] = {"product_id": str(self.strap["id"])}
        moved = web.build_catalog_product_order_counts(
            [self.order], mappings=mappings, catalog=self.shared
        )
        self.assertEqual(moved.get(str(self.watch["id"]), 0), 0)
        self.assertEqual(moved[str(self.strap["id"])], 1)

    def test_mapping_context_exposes_current_order_count(self):
        counts = web.build_catalog_product_order_counts(
            [self.order], mappings=self.mappings, catalog=self.shared
        )
        context = web.build_order_product_mapping_context(
            self.order["products"], mappings=self.mappings,
            catalog=self.shared, order_counts=counts,
        )
        self.assertEqual(context["line:line-1"]["product"]["orders_count"], 1)
        self.assertEqual(context["line:line-2"]["product"]["orders_count"], 1)

    def test_catalog_options_can_include_order_counts_in_one_response(self):
        product = self.shared.get_product(self.watch["id"])
        with (
            mock.patch.object(
                web._catalog_application,
                "catalog_options",
                return_value=([product], 1),
            ),
            mock.patch.object(web, "get_orders", return_value=[self.order]),
            mock.patch.object(
                web, "load_all_order_product_mappings", return_value=self.mappings
            ),
        ):
            response = self.client.get(
                "/api/v1/catalog/options?type=product"
                "&brand_id={}&category_id={}&include_order_counts=1".format(
                    product["brand_id"], product["category_id"]
                )
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"][0]["orders_count"], 1)

    def test_order_21113_assembled_mapping_accepts_product_without_stock(self):
        unavailable = ExcelProductCatalog(self.database).create_product(
            name="RIVAL GALAXY", article="RIVAL-GALAXY",
            brand="Zinvo", category="Наручные часы", stock=0,
        )
        order = {
            **self.order,
            "id": "21113",
            "number": "21113",
            "status": "D",
        }
        with (
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            response = self.client.post(
                "/api/orders/21113/items/line-1/mapping",
                json={"product_id": unavailable["id"]},
            )
            saved = web.load_order_product_mappings(
                "21113", database=self.database
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(
            saved["line:line-1"]["product_id"], str(unavailable["id"])
        )

    def test_manual_mapping_persists_on_reopen_and_is_idempotent(self):
        rival = ExcelProductCatalog(self.database).create_product(
            name="RIVAL GALAXY", article="RIVAL-GALAXY",
            brand="Zinvo", category="Наручные часы", stock=2,
        )
        order = {
            **self.order,
            "id": "21113",
            "number": "21113",
            "status": "D",
            "products": [{
                "id": "basket-21113-1",
                "product_id": "234193",
                "name": "RIVAL GALAXY",
                "quantity": 1,
                "price": 24900,
            }],
        }
        patches = (
            mock.patch.object(web, "get_orders", return_value=[order]),
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
            mock.patch.object(web, "AuditJournal"),
        )
        payload = {"product_id": rival["id"]}

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5], patches[6]:
            before = self.client.get("/order/21113")
            first = self.client.post(
                "/api/orders/21113/items/basket-21113-1/mapping", json=payload
            )
            reopened = self.client.get("/order/21113")
            repeated = self.client.post(
                "/api/orders/21113/items/basket-21113-1/mapping", json=payload
            )
            reopened_again = self.client.get("/order/21113")
            mappings = web.load_order_product_mappings(
                "21113", database=self.database
            )
            context = web.build_order_product_mapping_context(
                order["products"], mappings=mappings, catalog=self.shared
            )

        self.assertIn("Не удалось определить товар ERP", before.get_data(as_text=True))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        for response in (reopened, reopened_again):
            html = response.get_data(as_text=True)
            self.assertIn("RIVAL GALAXY", html)
            self.assertIn("Изменить товар ERP", html)
            self.assertNotIn("Есть несопоставленные позиции", html)
        self.assertEqual(
            mappings["line:basket-21113-1"]["product_id"], str(rival["id"])
        )
        self.assertTrue(web.build_order_sale_readiness(order, context)["ready"])
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM erp_order_product_mappings "
                "WHERE order_id = ? AND order_item_id = ?",
                ("21113", "basket-21113-1"),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_dialog_and_shared_cascade_are_rendered(self):
        html = self.render_order("?open_sale=1").get_data(as_text=True)
        self.assertIn('id="orderSaleModal"', html)
        self.assertIn('data-auto-open="1"', html)
        self.assertIn('data-shared-catalog-kind="product"', html)
        self.assertIn('data-catalog-product-global="true"', html)
        self.assertIn('data-catalog-in-stock="false"', html)
        self.assertIn("Bradley Steel", html)
        self.assertIn("Нет в наличии", Path("app/static/js/catalog-combobox.js").read_text())
        self.assertIn('name="csrf_token"', html)
        self.assertIn('type="button" data-close-sale-dialog', html)

    def test_sale_dialog_renders_every_source_order_line_and_totals(self):
        html = self.render_order().get_data(as_text=True)
        self.assertIn("Позиции заказа", html)
        self.assertIn("Часы", html)
        self.assertIn("Ремешок", html)
        self.assertIn("Артикул: BRADLEY-STEEL", html)
        self.assertIn("Артикул: STRAP-1", html)
        self.assertIn('data-amount="7500.00"', html)
        self.assertIn('data-amount="15000.00"', html)
        self.assertIn('data-amount="2400.00"', html)
        self.assertIn("К списанию: <strong>3 шт.</strong>", html)
        self.assertIn('data-amount="17400.00"', html)

    def test_sale_dialog_keeps_read_only_lines_and_compact_copy(self):
        html = self.render_order().get_data(as_text=True)
        self.assertNotIn("Город / населённый пункт", html)
        self.assertIn('for="orderSaleCityTrigger">Город</label>', html)
        self.assertIn('for="orderSaleTracking">Трекинг</label>', html)
        self.assertNotIn("Продажа создаётся атомарно", html)
        self.assertIn(
            "После проведения продажа появится в разделе «Продажи», "
            "а остатки товаров спишутся.", html,
        )
        self.assertNotIn('name="quantity_', html)
        self.assertNotIn('name="price_', html)

    def test_sale_dialog_has_responsive_layout_and_keyboard_guards(self):
        template = Path("app/templates/orders.html").read_text()
        styles = Path("app/static/css/orders.css").read_text()
        self.assertIn("width:min(940px,100%)", styles)
        self.assertIn("max-height:calc(100dvh - 40px)", styles)
        self.assertIn("overflow-y:auto", styles)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", styles)
        self.assertIn("@media (max-width:600px)", styles)
        self.assertIn("grid-template-columns:1fr", styles)
        self.assertIn("event.key==='Tab'", template)
        self.assertIn("saleDialogReturnFocus?.focus()", template)
        self.assertIn("if(!setPending(event.currentTarget,'Проводим…'))", template)

    def test_unconfirmed_order_keeps_sale_action_and_explains_confirmation(self):
        html = self.render_order_for({**self.order, "status": "N"})
        self.assertIn(">Провести продажу</button>", html)
        self.assertIn(
            "Чтобы провести продажу, сначала подтвердите заказ.", html
        )
        self.assertIn('name="status"', html)
        self.assertNotIn(">Сохранить статус</button>", html)
        self.assertIn("data-status-autosave", html)
        self.assertNotIn('id="orderSaleModal"', html)

    def test_confirmed_order_keeps_active_sale_action(self):
        html = self.render_order_for({**self.order, "status": "A"})
        self.assertIn("data-open-sale-dialog>Провести продажу</button>", html)
        self.assertIn('id="orderSaleModal"', html)

    def test_assembled_order_with_sale_links_to_that_sale(self):
        self.conduct()
        sale_id = self.inventory.list_sales()[0]["id"]
        html = self.render_order_for({**self.order, "status": "D"})
        self.assertIn("Продажа проведена", html)
        self.assertIn("Открыть продажу", html)
        self.assertIn("sale_id={}".format(sale_id), html)
        self.assertNotIn(">Провести продажу</button>", html)

    def test_assembled_order_without_sale_exposes_recovery_action(self):
        html = self.render_order_for({**self.order, "status": "D"})
        self.assertIn("Продажа не найдена", html)
        self.assertIn("data-open-sale-dialog>Провести продажу</button>", html)
        self.assertIn('id="orderSaleModal"', html)

    def test_order_21110_sale_dialog_opens_while_another_writer_holds_database(self):
        order = {**self.order, "id": "21110", "number": "21110"}
        statuses = OrderStatusService(self.database)
        statuses.ingest("21110", "A")
        self.shared.database.cache_initialization = True
        self.shared.database.initialize()
        writer = self.database.connect()
        writer.execute("BEGIN IMMEDIATE")
        try:
            with (
                mock.patch.object(web, "get_orders", return_value=[order]),
                mock.patch.object(
                    web,
                    "get_order",
                    side_effect=lambda _order_id: statuses.overlay(order),
                ),
                mock.patch.object(
                    web, "load_order_product_mappings", return_value=self.mappings
                ),
                mock.patch.object(web, "SharedCatalog", return_value=self.shared),
                mock.patch.object(
                    web, "SalesInventory", return_value=self.inventory
                ),
                mock.patch.object(web, "load_stock_operations", return_value=[]),
            ):
                response = self.client.get("/order/21110?open_sale=1")
                regular_response = self.client.get("/order/21110")
        finally:
            writer.rollback()
            writer.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(regular_response.status_code, 200)
        self.assertIn('id="orderSaleModal"', response.get_data(as_text=True))
        self.assertIn('data-auto-open="1"', response.get_data(as_text=True))

    def test_dialog_shows_geography_and_prefills_existing_tracking(self):
        order = {
            **self.order,
            "properties": [
                {"CODE": "COUNTRY", "VALUE": "Россия"},
                {"CODE": "REGION", "VALUE": "Московская область"},
                {"CODE": "CITY", "VALUE": "Химки"},
                {"code": "TRACKING_NUMBER", "value": "  001-AB  "},
            ],
        }
        html = self.render_order_for(order)
        self.assertIn('name="country"', html)
        self.assertIn('value="Россия"', html)
        self.assertIn('name="region"', html)
        self.assertIn('value="Московская область"', html)
        self.assertIn('name="city"', html)
        self.assertIn('value="Химки"', html)
        self.assertIn('id="orderSaleCountry"', html)
        self.assertIn('id="orderSaleRegion"', html)
        self.assertIn('id="orderSaleCity"', html)
        self.assertGreaterEqual(html.count("brand-combobox-search"), 3)
        self.assertIn("catalog-combobox:change", html)
        self.assertIn('name="tracking"', html)
        self.assertIn('value="001-AB"', html)
        self.assertIn('placeholder="Введите номер отправления"', html)
        self.assertIn('maxlength="255"', html)
        self.assertIn("window.sessionStorage.setItem", html)
        self.assertIn("window.sessionStorage.getItem", html)
        self.assertNotIn("order-sale-geography:", html)

    def render_order_for(self, order, query=""):
        patches = self.patches(order=order)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return self.client.get("/order/18593" + query).get_data(as_text=True)

    def test_dialog_keeps_unknown_geography_empty(self):
        html = self.render_order_for(self.order)
        self.assertIn('id="orderSaleCountry"', html)
        self.assertIn('id="orderSaleRegion"', html)
        self.assertIn('id="orderSaleCity"', html)
        self.assertGreaterEqual(html.count('value=""'), 3)

    def test_order_21110_location_id_resolves_to_moscow(self):
        order = web.normalize_order({
            "id": "21110",
            "status": "A",
            "properties": [
                {"code": "LOCATION", "name": "Местоположение", "value": "107"},
                {"code": "CITY", "name": "Город", "value": None},
                {"code": "ADDRESS", "name": "Адрес доставки", "value": "ПВЗ СДЭК"},
            ],
        })

        self.assertEqual(
            web.get_order_geography(order),
            {"country": "Россия", "region": "Москва", "city": "Москва"},
        )

    def test_other_bitrix_location_id_resolves_without_hardcoding_107(self):
        self.assertEqual(
            web.get_order_geography({"location_id": "468"}),
            {
                "country": "Россия",
                "region": "Московская область",
                "city": "Красногорск",
            },
        )

    def test_geography_prefers_structured_fields_and_parses_missing_address_parts(self):
        self.assertEqual(
            web.get_order_geography({
                "country": "Казахстан",
                "region": "Алматинская область",
                "city": "Конаев",
                "address": "Россия, Московская область, г. Химки",
            }),
            {
                "country": "Казахстан",
                "region": "Алматинская область",
                "city": "Конаев",
            },
        )
        self.assertEqual(
            web.get_order_geography({
                "address": "Россия, Московская область, г. Химки, ул. Ленина, 1",
            }),
            {
                "country": "Россия",
                "region": "Московская область",
                "city": "Химки",
            },
        )

    def test_international_address_is_parsed_only_when_components_are_explicit(self):
        self.assertEqual(
            web.get_order_geography({
                "address": "Germany, state Berlin, city Berlin, Friedrichstrasse 1",
            }),
            {"country": "Германия", "region": "state Berlin", "city": "Berlin"},
        )
        self.assertEqual(
            web.get_order_geography({"address": "Неизвестная улица, дом 1"}),
            {"country": "", "region": "", "city": ""},
        )

    def test_successful_manual_status_change_saves_only_after_bitrix(self):
        status_service = OrderStatusService(self.database)
        status_service.ingest("18593", "N")
        with (
            mock.patch.object(web, "update_order_status", return_value={"status": "ok"}),
            mock.patch.object(web, "order_status_service", return_value=status_service),
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
        ):
            response = self.client.post("/order/18593/status", data={"status": "A"})
        self.assertEqual(
            parse_qs(urlsplit(response.location).query)["notice"], ["success"]
        )
        self.assertEqual(status_service.get("18593")["erp_status"], "confirmed")
        self.assertEqual(self.inventory.list_sales(), [])

    def test_bitrix_status_error_preserves_local_status_without_pending_state(self):
        status_service = OrderStatusService(self.database)
        status_service.ingest("18593", "D")
        with (
            mock.patch.object(web, "update_order_status", return_value={
                "status": "error", "message": "Bitrix недоступен",
            }),
            mock.patch.object(web, "order_status_service", return_value=status_service),
        ):
            response = self.client.post("/order/18593/status", data={"status": "N"})
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["notice"], ["error"])
        self.assertIn("Bitrix недоступен", query["message"][0])
        state = status_service.get("18593")
        self.assertEqual(state["erp_status"], "assembled")
        self.assertEqual(state["sync_status"], "synced")

    def test_manual_reverse_transitions_send_exact_bitrix_codes(self):
        cases = (
            ("19001", "D", "A", "confirmed"),
            ("19002", "D", "N", "unconfirmed"),
            ("19003", "A", "N", "unconfirmed"),
            ("19004", "N", "D", "assembled"),
        )
        status_service = OrderStatusService(self.database)
        for order_id, initial, target, expected in cases:
            with self.subTest(initial=initial, target=target):
                status_service.ingest(order_id, initial)
                with (
                    mock.patch.object(
                        web, "update_order_status", return_value={"status": "ok"}
                    ) as update,
                    mock.patch.object(
                        web, "order_status_service", return_value=status_service
                    ),
                ):
                    response = self.client.post(
                        "/order/{}/status".format(order_id),
                        data={"status": target},
                    )
                self.assertEqual(response.status_code, 302)
                update.assert_called_once_with(int(order_id), target)
                self.assertEqual(
                    status_service.get(order_id)["erp_status"], expected
                )

    def test_repeating_synced_status_is_idempotent(self):
        status_service = OrderStatusService(self.database)
        status_service.ingest("18593", "A")
        with (
            mock.patch.object(web, "update_order_status") as update,
            mock.patch.object(
                web, "order_status_service", return_value=status_service
            ),
        ):
            response = self.client.post(
                "/order/18593/status", data={"status": "A"}
            )
        self.assertEqual(response.status_code, 302)
        update.assert_not_called()
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_order_status_sync_queue"
            ).fetchone()[0], 0)

    def test_status_change_preserves_completed_sale_and_stock(self):
        self.conduct()
        sale = self.inventory.list_sales()[0]
        stock_before = ExcelProductCatalog(self.database).get_product(
            self.watch["id"]
        )["stock"]
        status_service = OrderStatusService(self.database)
        with (
            mock.patch.object(web, "update_order_status", return_value={"status": "ok"}),
            mock.patch.object(web, "order_status_service", return_value=status_service),
        ):
            response = self.client.post(
                "/order/18593/status", data={"status": "N"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"],
            stock_before,
        )
        sales = self.inventory.list_sales()
        self.assertEqual({row["id"] for row in sales}, {sale["id"]})
        self.assertEqual({row["order_status"] for row in sales}, {"completed"})

    def test_success_is_one_local_sale_with_two_lines_and_exact_redirect(self):
        response = self.conduct()
        self.assertEqual(urlsplit(response.location).path, "/sales")
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["source"], ["tictactoy"])
        self.assertEqual(query["order_number"], ["18593"])
        rows = self.inventory.list_sales()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 1)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 3)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.strap["id"])["stock"], 2)
        report = web.build_sales_report_records(
            warehouse_items=[], operations=[], stored_manual_sales=rows,
            automatic_overrides={},
        )
        self.assertEqual(web.calculate_sales_kpis(report)["sales_count"], 1)

    def test_order_sale_uses_performed_time_and_preserves_order_time(self):
        order = {
            **self.order,
            "created_at": "2026-08-18 00:11:36",
        }
        performed_at = "2026-08-18T15:05:00+03:00"

        response = self.conduct(order=order, performed_at=performed_at)

        self.assertEqual(urlsplit(response.location).path, "/sales")
        rows = self.inventory.list_sales()
        self.assertEqual({row["created_at"] for row in rows}, {performed_at})
        self.assertEqual(
            {row["order_created_at"] for row in rows},
            {"2026-08-18 00:11:36"},
        )
        self.assertEqual(order["created_at"], "2026-08-18 00:11:36")

        report = web.build_sales_report_records(
            warehouse_items=[],
            operations=[],
            stored_manual_sales=rows,
            automatic_overrides={},
        )
        self.assertEqual({row["created_at"] for row in report}, {performed_at})
        serialized = web.serialize_api_sale(report[0])
        self.assertEqual(serialized["created_at"], performed_at)

        with mock.patch.object(
            web, "build_sales_report_records", return_value=report
        ), mock.patch.object(web, "get_warehouse_items", return_value=[]):
            page = self.client.get("/sales?source=tictactoy")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("18.08.2026", html)
        self.assertIn("15:05", html)

    def test_tracking_and_region_are_snapshotted_in_sale(self):
        order = {
            **self.order,
            "region_name": "Санкт-Петербург",
            "tracking_number": "SOURCE-TRACK",
        }
        response = self.conduct(order=order, tracking="  001-AB  ")
        self.assertEqual(urlsplit(response.location).path, "/sales")
        rows = self.inventory.list_sales()
        self.assertEqual({row["region"] for row in rows}, {"Санкт-Петербург"})
        self.assertEqual({row["track_number"] for row in rows}, {"001-AB"})

    def test_existing_tracking_is_used_when_submitted_from_prefilled_field(self):
        order = {**self.order, "delivery": {"waybill": "ZX-009"}}
        self.conduct(order=order)
        self.assertEqual(
            {row["track_number"] for row in self.inventory.list_sales()},
            {"ZX-009"},
        )

    def test_empty_tracking_and_manual_geography_corrections_are_saved(self):
        order = {**self.order, "region": "Москва"}
        response = self.conduct(
            order=order,
            tracking="   ",
            country="Беларусь",
            region="Минская область",
            city="Минск",
        )
        self.assertEqual(urlsplit(response.location).path, "/sales")
        rows = self.inventory.list_sales()
        self.assertEqual({row["track_number"] for row in rows}, {""})
        self.assertEqual({row["country"] for row in rows}, {"Беларусь"})
        self.assertEqual({row["region"] for row in rows}, {"Минская область"})
        self.assertEqual({row["city"] for row in rows}, {"Минск"})

    def test_internal_location_id_is_normalized_before_sale_is_saved(self):
        response = self.conduct(
            country="Россия",
            region="107",
            city="",
        )

        self.assertEqual(urlsplit(response.location).path, "/sales")
        rows = self.inventory.list_sales()
        self.assertEqual({row["country"] for row in rows}, {"Россия"})
        self.assertEqual({row["region"] for row in rows}, {"Москва"})
        self.assertEqual({row["city"] for row in rows}, {"Москва"})

    def test_address_geography_is_snapshotted_and_returned_by_sale_api(self):
        order = {
            **self.order,
            "address": "Россия, Ленинградская область, пос. Мурино, Центральная, 1",
        }
        self.conduct(order=order)
        sale = self.inventory.list_sales()[0]
        self.assertEqual(sale["country"], "Россия")
        self.assertEqual(sale["region"], "Ленинградская область")
        self.assertEqual(sale["city"], "Мурино")
        serialized = web.serialize_api_sale({
            **sale,
            "sale_type": "automatic",
            "sale_type_label": "Автоматическая",
            "is_manual": False,
            "inventory_managed": True,
            "quantity_value": sale["quantity"],
            "quantity_display": "2",
            "net_quantity_value": sale["quantity"],
            "total_amount": 0,
            "gross_total_amount": 0,
            "returned_amount": 0,
            "is_cancelled": False,
        })
        self.assertEqual(
            (serialized["country"], serialized["region"], serialized["city"]),
            ("Россия", "Ленинградская область", "Мурино"),
        )

    def test_tracking_over_limit_is_rejected_without_stock_change(self):
        response = self.conduct(tracking="X" * 256)
        self.assertIn(
            "255 символов",
            parse_qs(urlsplit(response.location).query)["message"][0],
        )
        self.assertEqual(self.inventory.list_sales(), [])
        self.assertEqual(
            ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"],
            5,
        )

    def test_repeated_post_is_idempotent(self):
        first = self.conduct()
        second = self.conduct()
        self.assertEqual(urlsplit(first.location).path, "/sales")
        self.assertIn("уже проведена", parse_qs(urlsplit(second.location).query)["message"][0])
        self.assertEqual(len({row["id"] for row in self.inventory.list_sales()}), 1)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 3)

    def test_assembled_order_without_sale_can_be_recovered_atomically(self):
        order = {**self.order, "status": "D"}
        response = self.conduct(order=order)
        self.assertEqual(urlsplit(response.location).path, "/sales")
        rows = self.inventory.list_sales()
        self.assertEqual(len({row["id"] for row in rows}), 1)
        state = OrderStatusService(self.database).get("18593")
        self.assertEqual(state["erp_status"], "assembled")
        self.assertEqual(state["sale_id"], rows[0]["id"])
        self.assertEqual(
            ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"],
            3,
        )

    def test_full_unconfirmed_to_sale_scenario(self):
        unconfirmed = {**self.order, "status": "N"}
        first_html = self.render_order_for(unconfirmed)
        self.assertIn(">Провести продажу</button>", first_html)
        self.assertIn("сначала подтвердите заказ", first_html)

        statuses = OrderStatusService(self.database)
        with (
            mock.patch.object(web, "order_status_service", return_value=statuses),
            mock.patch.object(web, "get_orders", return_value=[unconfirmed]),
            mock.patch.object(
                web, "update_order_status", return_value={"status": "ok"}
            ),
        ):
            confirmation = self.client.post(
                "/order/18593/status",
                data={"csrf_token": "test-token", "status": "A"},
            )
        self.assertEqual(
            parse_qs(urlsplit(confirmation.location).query)["notice"], ["success"]
        )

        confirmed = {**self.order, "status": "A"}
        sale_response = self.conduct(order=confirmed)
        self.assertEqual(urlsplit(sale_response.location).path, "/sales")
        sale = self.inventory.list_sales()[0]
        assembled = {**self.order, "status": "D"}
        final_html = self.render_order_for(assembled)
        self.assertIn("Продажа проведена", final_html)
        self.assertIn("sale_id={}".format(sale["id"]), final_html)
        self.assertEqual(
            ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"],
            3,
        )

    def test_all_validation_errors_are_returned_before_stock_change(self):
        order = dict(self.order)
        order["status"] = "N"
        order["products"] = [
            {"id": "missing-line", "product_id": "missing", "name": "Нет связи", "quantity": 1},
            {"id": "line-1", "product_id": "bx-watch", "name": "Bradley Steel", "quantity": 6},
        ]
        response = self.conduct(order=order)
        message = parse_qs(urlsplit(response.location).query)["message"][0]
        self.assertIn("сначала подтвердите заказ", message)
        self.assertIn("Нет связи", message)
        self.assertIn("требуется 6, доступно 5", message)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 5)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_duplicate_product_lines_use_aggregate_stock(self):
        order = dict(self.order)
        order["products"] = [
            {"id": "duplicate-1", "product_id": "bx-watch", "name": "A", "quantity": 3},
            {"id": "duplicate-2", "product_id": "bx-watch", "name": "B", "quantity": 3},
        ]
        response = self.conduct(order=order, mappings={
            "line:duplicate-1": {"product_id": str(self.watch["id"])},
            "line:duplicate-2": {"product_id": str(self.watch["id"])},
        })
        self.assertIn("требуется 6, доступно 5", parse_qs(urlsplit(response.location).query)["message"][0])
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 5)

    def test_zero_stock_product_is_visible_but_cannot_be_conducted(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 0 WHERE id = ?",
                (self.watch["id"],),
            )
        self.assertEqual(self.shared.list_products(query="Bradley Steel")[0]["stock"], 0)
        response = self.conduct()
        self.assertIn("доступно 0", parse_qs(urlsplit(response.location).query)["message"][0])
        self.assertEqual(self.inventory.list_sales(), [])

    def test_mapping_is_stable_by_product_id_after_rename_and_archive_blocks_sale(self):
        catalog = ExcelProductCatalog(self.database)
        catalog.update_product(self.watch["id"], name="Переименованные часы")
        context = web.build_order_product_mapping_context(
            self.order["products"], mappings=self.mappings, catalog=self.shared,
        )
        self.assertEqual(
            context["line:line-1"]["product"]["name"],
            "Переименованные часы",
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 0 WHERE id = ?",
                (self.watch["id"],),
            )
        catalog.archive_product(self.watch["id"])
        context = web.build_order_product_mapping_context(
            self.order["products"], mappings=self.mappings, catalog=self.shared,
        )
        self.assertEqual(context["line:line-1"]["state"], "archived")
        response = self.conduct()
        self.assertIn("архивирован", parse_qs(urlsplit(response.location).query)["message"][0])

    def test_mapping_endpoint_does_not_depend_on_brand_or_category(self):
        product = self.shared.get_product(self.watch["id"])
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            response = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={
                    "product_id": product["id"],
                    "brand_id": "wrong",
                    "category_id": "wrong",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["mapping"]["product"]["id"], str(product["id"]))

    def test_mapping_endpoint_rejects_unknown_product_and_item(self):
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
        ):
            unknown_product = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": 999999},
            )
            unknown_item = self.client.post(
                "/api/orders/18593/items/missing/mapping",
                json={"product_id": self.watch["id"]},
            )
        self.assertEqual(unknown_product.status_code, 404)
        self.assertEqual(unknown_product.get_json()["error"]["code"], "PRODUCT_NOT_FOUND")
        self.assertEqual(unknown_item.status_code, 404)
        self.assertEqual(unknown_item.get_json()["error"]["code"], "ORDER_ITEM_NOT_FOUND")

    def test_mapping_saves_only_order_item_and_erp_product_ids(self):
        product = self.shared.get_product(self.watch["id"])
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            response = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": product["id"]},
            )
            saved = web.load_order_product_mappings(
                "18593", database=self.database
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(saved["line:line-1"]["product_id"], str(self.watch["id"]))
        self.assertEqual(saved["line:line-1"]["order_item_id"], "line-1")
        self.assertNotIn("brand_id", saved["line:line-1"])

    def test_uncategorized_product_requires_assignment_before_mapping(self):
        uncategorized = ExcelProductCatalog(self.database).create_product(
            name="Bradley без категории", article="BRADLEY-NO-CATEGORY",
            brand="Bradley", category="", stock=4,
        )
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            response = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": uncategorized["id"]},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "PRODUCT_CATEGORY_REQUIRED",
        )
        self.assertEqual(
            web.load_order_product_mappings("18593", self.database), {}
        )
        self.assertEqual(
            self.shared.get_product(uncategorized["id"])["stock"], 4
        )

    def test_category_assignment_is_atomic_idempotent_and_mapping_continues(self):
        uncategorized = ExcelProductCatalog(self.database).create_product(
            name="Bradley новый", article="BRADLEY-NEW-CATEGORY",
            brand="Bradley", category="", stock=4,
        )
        with self.database.connect() as connection:
            movements_before = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_manual_stock_operations "
                "WHERE product_id = ?",
                (uncategorized["id"],),
            ).fetchone()[0]
        with mock.patch.object(web, "SharedCatalog", return_value=self.shared):
            first = self.client.post(
                "/api/v1/products/{}/category-assignment".format(
                    uncategorized["id"]
                ),
                json={"category_name": "  Новая категория  "},
            )
            repeated = self.client.post(
                "/api/v1/products/{}/category-assignment".format(
                    uncategorized["id"]
                ),
                json={"category_name": "новая категория"},
            )
        self.assertEqual((first.status_code, repeated.status_code), (200, 200))
        assigned = first.get_json()["data"]
        self.assertEqual(assigned["category"], "Новая категория")
        self.assertEqual(repeated.get_json()["data"]["category_id"],
                         assigned["category_id"])
        with self.database.connect() as connection:
            category_count = connection.execute(
                "SELECT COUNT(*) FROM erp_categories "
                "WHERE normalized_name = 'новая категория'"
            ).fetchone()[0]
            movement_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_manual_stock_operations "
                "WHERE product_id = ?",
                (uncategorized["id"],),
            ).fetchone()[0]
        self.assertEqual(category_count, 1)
        self.assertEqual(movement_count, movements_before)
        self.assertEqual(
            self.shared.get_product(uncategorized["id"])["stock"], 4
        )

        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            mapped = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": uncategorized["id"]},
            )
        self.assertEqual(mapped.status_code, 200)
        self.assertEqual(
            mapped.get_json()["data"]["mapping"]["product"]["category_id"],
            assigned["category_id"],
        )

    def test_category_assignment_rejects_unknown_category_without_changes(self):
        uncategorized = ExcelProductCatalog(self.database).create_product(
            name="Bradley invalid category", article="BRADLEY-INVALID-CATEGORY",
            brand="Bradley", category="", stock=2,
        )
        with mock.patch.object(web, "SharedCatalog", return_value=self.shared):
            response = self.client.post(
                "/api/v1/products/{}/category-assignment".format(
                    uncategorized["id"]
                ),
                json={"category_id": 999999},
            )
        self.assertEqual(response.status_code, 422)
        product = self.shared.get_product(uncategorized["id"])
        self.assertIsNone(product["category_id"])
        self.assertEqual(product["stock"], 2)

    def test_mapping_can_change_between_duplicate_named_erp_products(self):
        duplicate = ExcelProductCatalog(self.database).create_product(
            name="Bradley Steel", article="BRADLEY-STEEL-DUPLICATE",
            brand="Another brand", category="Other category", stock=7,
        )
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            first = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": self.watch["id"]},
            )
            changed = self.client.post(
                "/api/orders/18593/items/line-1/mapping",
                json={"product_id": duplicate["id"]},
            )
        reopened_database = CatalogDatabase(self.database.path)
        saved = web.load_order_product_mappings(
            "18593", database=reopened_database
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(
            changed.get_json()["data"]["mapping"]["product"]["id"],
            str(duplicate["id"]),
        )
        self.assertEqual(saved["line:line-1"]["product_id"], str(duplicate["id"]))
        with reopened_database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM erp_order_product_mappings "
                "WHERE order_id = '18593' AND order_item_id = 'line-1'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_duplicate_named_order_items_map_independently_and_drive_readiness(self):
        duplicate = ExcelProductCatalog(self.database).create_product(
            name="Bradley Steel", article="BRADLEY-STEEL-SECOND",
            brand="Bradley", category="Часы", stock=4,
        )
        order = {
            **self.order,
            "products": [
                {"basket_id": "basket-a", "name": "Nato 84", "quantity": 1},
                {"basket_id": "basket-b", "name": "Nato 84", "quantity": 1},
            ],
        }
        with (
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "AuditJournal"),
        ):
            first = self.client.post(
                "/api/orders/18593/items/basket-a/mapping",
                json={"product_id": self.watch["id"]},
            )
            second = self.client.post(
                "/api/orders/18593/items/basket-b/mapping",
                json={"product_id": duplicate["id"]},
            )
        self.assertFalse(first.get_json()["data"]["order"]["all_items_mapped"])
        self.assertFalse(first.get_json()["data"]["order"]["sale_ready"])
        self.assertTrue(second.get_json()["data"]["order"]["all_items_mapped"])
        self.assertTrue(second.get_json()["data"]["order"]["sale_ready"])
        saved = web.load_order_product_mappings("18593", database=self.database)
        self.assertEqual(saved["line:basket-a"]["product_id"], str(self.watch["id"]))
        self.assertEqual(saved["line:basket-b"]["product_id"], str(duplicate["id"]))

    def test_mapping_delete_unmaps_item_and_disables_sale_readiness(self):
        web.save_order_product_mapping(
            "18593", "line-1", self.watch["id"], self.database
        )
        web.save_order_product_mapping(
            "18593", "line-2", self.strap["id"], self.database
        )
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "CatalogDatabase", return_value=self.database),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
        ):
            response = self.client.delete(
                "/api/orders/18593/items/line-1/mapping"
            )
        data = response.get_json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(data["mapping"])
        self.assertFalse(data["order"]["all_items_mapped"])
        self.assertFalse(data["order"]["sale_ready"])
        self.assertNotIn(
            "line:line-1",
            web.load_order_product_mappings("18593", database=self.database),
        )

    def test_mapping_endpoint_rejects_unknown_order(self):
        with mock.patch.object(web, "get_order", return_value=None):
            response = self.client.post(
                "/api/orders/99999/items/line-1/mapping",
                json={"product_id": self.watch["id"]},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "ORDER_NOT_FOUND")

    def test_mapping_schema_contains_only_explicit_relation_and_timestamps(self):
        with self.database.connect() as connection:
            columns = [
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(erp_order_product_mappings)"
                ).fetchall()
            ]
        self.assertEqual(columns, [
            "order_id", "order_item_id", "product_id", "created_at", "updated_at"
        ])

    def test_refusal_requires_cancelling_linked_sale_first(self):
        self.conduct()
        status_service = OrderStatusService(self.database)
        with (
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "order_status_service", return_value=status_service),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
            mock.patch.object(web, "update_order_status") as update,
        ):
            response = self.client.post("/order/18593/status", data={"status": "C"})
        update.assert_not_called()
        self.assertIn(
            "Сначала отмените связанную продажу",
            parse_qs(urlsplit(response.location).query)["message"][0],
        )

    def test_refusal_can_be_set_manually_without_sale(self):
        status_service = OrderStatusService(self.database)
        status_service.ingest("18593", "A")
        with (
            mock.patch.object(
                web, "update_order_status", return_value={"status": "ok"}
            ) as update,
            mock.patch.object(
                web, "order_status_service", return_value=status_service
            ),
        ):
            response = self.client.post(
                "/order/18593/status", data={"status": "C"}
            )
        self.assertEqual(response.status_code, 302)
        update.assert_called_once_with(18593, "C")
        self.assertEqual(
            status_service.get("18593")["erp_status"], "refused"
        )


if __name__ == "__main__":
    unittest.main()
