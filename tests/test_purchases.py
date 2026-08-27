import sqlite3
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from app.customer_registry_migrations import migrate_database as migrate_customers
from app.purchases_migrations import (
    LEGACY_SCHEMA_CHECKSUM,
    LEGACY_SCHEMA_VERSION,
    migrate_database,
    verify_database,
)
from app.services.customer_registry import CustomerRegistry
from app.services.purchases import PurchaseStore, PurchaseValidationError


class PurchasesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "purchases.db"
        migrate_database(self.path)
        self.store = PurchaseStore(self.path)
        self.customers = {1, 2, 3}
        self.products = {
            10: {"product_name": "Seiko 5", "brand": "Seiko", "model": "5",
                 "article": "SNK-1", "image_url": "/image.jpg"},
            11: {"product_name": "Seiko 5 Blue", "brand": "Seiko", "model": "5",
                 "article": "SNK-2", "image_url": ""},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **values):
        payload = {"customer_id": 1, "product_id": 10, "quantity": 1,
                   "channel": "telegram", "target_price": 25000,
                   "requested_at": "2026-08-27T12:00:00+03:00"}
        payload.update(values)
        return self.store.create_request(
            payload, 7, lambda value: int(value) in self.customers,
            lambda value: self.products.get(int(value)),
        )

    def test_migration_is_idempotent_and_has_foreign_keys(self):
        migrate_database(self.path)
        self.assertTrue(verify_database(self.path))
        with sqlite3.connect(str(self.path)) as connection:
            self.assertTrue(connection.execute("PRAGMA foreign_key_list(purchase_request_history)").fetchall())
            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())

    def test_existing_and_unknown_product_validation(self):
        existing = self.create()
        self.assertEqual((existing["brand"], existing["article"]), ("Seiko", "SNK-1"))
        unknown = self.create(product_id=None, product_name="Редкие часы", brand="Новый бренд")
        self.assertIsNone(unknown["product_id"])
        with self.assertRaises(PurchaseValidationError) as missing:
            self.create(product_id=None, product_name="", brand="", model="", description="")
        self.assertEqual(missing.exception.field, "product_or_comment")
        self.assertIn("товар или заполните комментарий", str(missing.exception).lower())
        with self.assertRaises(PurchaseValidationError):
            self.create(customer_id=404)
        with self.assertRaises(PurchaseValidationError):
            self.create(quantity=0)
        with self.assertRaises(PurchaseValidationError):
            self.create(product_id=None, product_name="Часы", image_url="javascript:alert(1)")

    def test_edit_status_history_archive_and_reopen(self):
        item = self.create()
        edited = self.store.update_request(
            item["id"], {"quantity": 3, "status": "review", "status_comment": "Проверяем"}, 8,
            lambda value: int(value) in self.customers, lambda value: self.products.get(int(value)),
        )
        self.assertEqual((edited["quantity"], edited["status"]), (3, "review"))
        self.assertEqual(edited["history"][0]["old_status"], "new")
        closed = self.store.archive_request(item["id"], True, 8, "Неактуально")
        self.assertEqual((closed["archived"], closed["status"]), (1, "closed"))
        reopened = self.store.archive_request(item["id"], False, 8)
        self.assertEqual((reopened["archived"], reopened["status"]), (0, "review"))
        self.assertEqual(reopened["history"][0]["action"], "reopened")

    def test_search_filters_sort_and_pagination(self):
        self.create(customer_id=1, customer_comment="Нужен подарок", channel="call")
        self.create(customer_id=2, product_id=None, product_name="Omega Moon", brand="Omega",
                    model="Moon", article="OM-1", channel="email")
        self.assertEqual(self.store.list_requests({"q": "подарок"})["total"], 1)
        self.assertEqual(self.store.list_requests({"brand": "omega", "channel": "email"})["total"], 1)
        self.assertEqual(self.store.list_requests({"q": "Иван"}, customer_ids=[2])["total"], 1)
        self.assertEqual(self.store.list_requests({"customer_id": 1})["total"], 1)
        page = self.store.list_requests({"per_page": 20, "page": 99, "sort": "oldest"})
        self.assertEqual((page["page"], page["pages"]), (1, 1))

    def test_anonymous_request_with_existing_product(self):
        item = self.create(customer_id=None)
        self.assertIsNone(item["customer_id"])
        self.assertEqual(item["product_id"], 10)

    def test_anonymous_comment_only_request_uses_null_product_fields(self):
        item = self.create(
            customer_id=None, product_id=None, product_name="", brand="", model="",
            article="", product_url="", image_url="", description="",
            customer_comment="Ищет небольшие квадратные часы в синем цвете до 15 000 рублей",
        )
        self.assertIsNone(item["customer_id"])
        for field in ("product_id", "product_name", "brand", "model", "article",
                      "product_url", "image_url", "description"):
            self.assertIsNone(item[field])
        edited = self.store.update_request(
            item["id"], {"internal_note": "Уточнить бюджет"}, 8,
            lambda value: int(value) in self.customers,
            lambda value: self.products.get(int(value)),
        )
        self.assertEqual(edited["internal_note"], "Уточнить бюджет")
        self.assertEqual(self.store.list_requests({"q": "квадратные часы"})["total"], 1)

    def test_request_key_makes_repeated_submit_idempotent(self):
        first = self.create(request_key="same-browser-submit")
        second = self.create(request_key="same-browser-submit")
        self.assertEqual(first["id"], second["id"])
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM purchase_requests").fetchone()[0], 1)

    def test_v1_migration_preserves_existing_request(self):
        legacy = Path(self.temporary.name) / "legacy.db"
        migrate_database(legacy)
        with sqlite3.connect(str(legacy)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("PRAGMA legacy_alter_table=ON")
            for name in (
                "idx_purchase_requests_status_date", "idx_purchase_requests_customer",
                "idx_purchase_requests_product", "idx_purchase_requests_brand",
                "idx_purchase_requests_channel", "idx_purchase_requests_valid",
                "idx_purchase_requests_request_key",
            ):
                connection.execute("DROP INDEX " + name)
            connection.execute("ALTER TABLE purchase_requests RENAME TO purchase_requests_v2")
            connection.execute("""CREATE TABLE purchase_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
                product_id INTEGER, product_name TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '', article TEXT NOT NULL DEFAULT '', product_url TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1,
                target_price REAL, channel TEXT NOT NULL, requested_at TEXT NOT NULL, valid_until TEXT,
                customer_comment TEXT NOT NULL DEFAULT '', internal_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
                archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                created_by INTEGER NOT NULL, updated_by INTEGER NOT NULL,
                CHECK(product_id IS NOT NULL OR length(trim(product_name || brand || model || description)) > 0)
            )""")
            connection.execute(
                "INSERT INTO purchase_requests(id,customer_id,product_id,product_name,brand,model,article,quantity,channel,requested_at,customer_comment,internal_note,status,archived,created_at,updated_at,created_by,updated_by) "
                "VALUES(1,1,10,'Seiko 5','Seiko','5','SNK-1',1,'telegram','2026-08-27T12:00:00+03:00','Старый запрос','','new',0,'2026-08-27T09:00:00+00:00','2026-08-27T09:00:00+00:00',7,7)"
            )
            connection.execute(
                "INSERT INTO purchase_request_history(request_id,action,actor_id,created_at) "
                "VALUES(1,'created',7,'2026-08-27T09:00:00+00:00')"
            )
            connection.execute("DROP TABLE purchase_requests_v2")
            connection.execute("CREATE INDEX idx_purchase_requests_status_date ON purchase_requests(status,requested_at,id)")
            connection.execute("CREATE INDEX idx_purchase_requests_customer ON purchase_requests(customer_id,requested_at,id)")
            connection.execute("CREATE INDEX idx_purchase_requests_product ON purchase_requests(product_id,status)")
            connection.execute("CREATE INDEX idx_purchase_requests_brand ON purchase_requests(brand,status)")
            connection.execute("CREATE INDEX idx_purchase_requests_channel ON purchase_requests(channel,status)")
            connection.execute("CREATE INDEX idx_purchase_requests_valid ON purchase_requests(valid_until,status)")
            connection.execute("UPDATE purchase_meta SET value=? WHERE key='schema_version'", (LEGACY_SCHEMA_VERSION,))
            connection.execute("UPDATE purchase_meta SET value=? WHERE key='schema_checksum'", (LEGACY_SCHEMA_CHECKSUM,))
        migrate_database(legacy)
        self.assertTrue(verify_database(legacy))
        migrated = PurchaseStore(legacy).get_request(1)
        self.assertEqual((migrated["customer_id"], migrated["product_id"], migrated["customer_comment"]),
                         (1, 10, "Старый запрос"))
        self.assertIsNone(migrated["request_key"])
        self.assertEqual(len(migrated["history"]), 1)
        with sqlite3.connect(str(legacy)) as connection:
            targets = {row[2] for row in connection.execute(
                "PRAGMA foreign_key_list(purchase_request_history)"
            )}
        self.assertEqual(targets, {"purchase_requests"})

    def test_safe_grouping_demand_stock_and_plan_idempotency(self):
        first = self.create(customer_id=1, quantity=2)
        second = self.create(customer_id=2, quantity=3)
        different = self.create(customer_id=3, product_id=11, quantity=4)
        self.assertEqual(self.store.add_to_plan(first["id"], 7), self.store.add_to_plan(first["id"], 7))
        self.store.add_to_plan(second["id"], 7)
        self.store.add_to_plan(different["id"], 7)
        plan = self.store.list_plan(lambda product_id: 1 if product_id == 10 else 0)
        self.assertEqual(len(plan), 2)
        grouped = next(item for item in plan if item["product_id"] == 10)
        self.assertEqual((grouped["request_count"], grouped["customer_count"], grouped["demand_quantity"], grouped["recommended_quantity"]), (2, 2, 5, 4))

    def test_unknown_products_only_group_on_complete_exact_identity(self):
        one = self.create(product_id=None, product_name="A", brand="Brand", model="Model", article="REF")
        two = self.create(customer_id=2, product_id=None, product_name="A alt", brand=" brand ", model="MODEL", article="ref")
        ambiguous = self.create(customer_id=3, product_id=None, product_name="A", brand="Brand", model="Model", article="")
        for item in (one, two, ambiguous): self.store.add_to_plan(item["id"], 7)
        plan = self.store.list_plan(lambda _value: 0)
        self.assertEqual(sorted(item["request_count"] for item in plan), [1, 2])

    def test_supplier_order_links_status_partial_receipt_and_no_stock_side_effect(self):
        first = self.create(customer_id=1, quantity=1)
        second = self.create(customer_id=2, quantity=2)
        plan_id = self.store.add_to_plan(first["id"], 7)
        self.store.add_to_plan(second["id"], 7)
        stock = {10: 9}
        order = self.store.create_supplier_order(
            {"supplier_name": "Поставщик", "internal_number": "PO-TEST",
             "plan_item_ids": [plan_id], "prices": {str(plan_id): 100}}, 7,
        )
        self.assertEqual(order["total"], 300)
        self.store.set_order_status(order["id"], "ordered", 7)
        self.assertEqual(self.store.get_request(first["id"])["status"], "ordered")
        partial = self.store.receive_item(order["items"][0]["id"], 1, 7)
        self.assertEqual(partial["status"], "partially_received")
        self.assertEqual(self.store.get_request(first["id"])["status"], "arrived")
        self.assertEqual(self.store.get_request(second["id"])["status"], "ordered")
        self.assertEqual(stock[10], 9)
        waiting = self.store.summary()
        self.assertEqual(waiting["arrived_unnotified"], 1)


class PurchaseCustomerCreationTest(unittest.TestCase):
    def test_quick_create_reuses_normalized_phone_and_email(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "customers.db"
            migrate_customers(path)
            registry = CustomerRegistry(path)
            first, created = registry.create_minimal("Иван", "8 (921) 123-45-67", "ivan@example.ru")
            same_phone, created_phone = registry.create_minimal("Иван другой", "+7 921 1234567", "")
            same_email, created_email = registry.create_minimal("Иван email", "", "IVAN@EXAMPLE.RU")
            self.assertTrue(created)
            self.assertFalse(created_phone)
            self.assertFalse(created_email)
            self.assertEqual({first["id"], same_phone["id"], same_email["id"]}, {first["id"]})
            with self.assertRaises(ValueError): registry.create_minimal("Без контакта")


@unittest.skipUnless(os.getenv("ERP_TEST_ROOT"), "requires isolated application test harness")
class PurchasesWebContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import web
        cls.web = web
        web.app.config.update(TESTING=True, AUTH_TESTING=False)

    def payload(self, **values):
        result = {
            "customer_id": None, "product_id": None, "quantity": 1,
            "channel": "whatsapp", "requested_at": "2026-08-27T12:00",
            "customer_comment": "Ищет квадратные часы", "request_key": values.pop("request_key", None),
        }
        result.update(values)
        return result

    def test_page_navigation_permissions_and_csrf_contract(self):
        web = self.web
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        with mock.patch.object(web, "auth_is_enabled", return_value=False):
            response = web.app.test_client().get("/app/purchases")
        self.assertEqual(response.status_code, 200)
        source = response.get_data(as_text=True)
        self.assertIn("Запросы клиентов", source)
        self.assertIn("План закупки", source)
        self.assertIn("Заказы поставщикам", source)
        with web.app.test_request_context("/app/purchases"):
            with mock.patch.object(web, "auth_is_enabled", return_value=True), \
                    mock.patch.object(web, "current_auth_user", return_value={"id": 2, "role": "employee"}), \
                    mock.patch.object(web, "require_csrf_when_authenticated") as csrf:
                self.assertTrue(web._purchase_can_view())
                web._purchase_require_edit()
                csrf.assert_called_once_with()
            with mock.patch.object(web, "auth_is_enabled", return_value=True), \
                    mock.patch.object(web, "current_auth_user", return_value={"id": 3, "role": "viewer"}):
                self.assertFalse(web._purchase_can_view())

    def test_new_customer_is_created_and_linked_to_request(self):
        client = self.web.app.test_client()
        customer_response = client.post(
            "/api/v1/purchases/customers",
            json={"name": "Новый клиент закупки", "phone": "+79990000002", "email": ""},
        )
        self.assertEqual(customer_response.status_code, 201)
        customer = customer_response.get_json()["customer"]
        request_response = client.post(
            "/api/v1/purchases/requests",
            json=self.payload(customer_id=customer["id"], request_key="web-new-customer"),
        )
        self.assertEqual(request_response.status_code, 201)
        self.assertEqual(request_response.get_json()["request"]["customer"]["id"], customer["id"])

    def test_anonymous_request_does_not_create_customer_and_renders_detail(self):
        customer_path = Path(os.environ["CUSTOMERS_DATABASE_PATH"])
        with sqlite3.connect(str(customer_path)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        client = self.web.app.test_client()
        response = client.post(
            "/api/v1/purchases/requests",
            json=self.payload(
                customer_comment="Только комментарий для анонимного запроса",
                request_key="web-anonymous-detail",
            ),
        )
        self.assertEqual(response.status_code, 201)
        request_id = response.get_json()["request"]["id"]
        with sqlite3.connect(str(customer_path)) as connection:
            after = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        self.assertEqual(after, before)
        detail = client.get("/app/purchases?tab=requests&request_id={}".format(request_id))
        self.assertEqual(detail.status_code, 200)
        source = detail.get_data(as_text=True)
        self.assertIn("Клиент не указан", source)
        self.assertIn("Товар не указан — см. комментарий", source)
        edited = client.patch(
            "/api/v1/purchases/requests/{}".format(request_id),
            json={"internal_note": "Отредактировано"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["request"]["internal_note"], "Отредактировано")

    def test_missing_product_and_comment_returns_structured_validation_error(self):
        response = self.web.app.test_client().post(
            "/api/v1/purchases/requests",
            json=self.payload(customer_comment="", request_key="web-invalid-empty"),
        )
        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertEqual(error["field"], "product_or_comment")
        self.assertIn("товар или заполните комментарий", error["message"].lower())

    def test_form_contract_keeps_data_on_error_and_guards_repeat_submit(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/js/purchases.js").read_text(encoding="utf-8")
        template = (Path(__file__).resolve().parents[1] / "app/templates/purchases.html").read_text(encoding="utf-8")
        self.assertIn('requestForm.dataset.submitting === "1"', source)
        self.assertIn("data.request_key = requestForm.dataset.requestKey", source)
        self.assertIn("catch (error)", source)
        self.assertNotIn("Клиент *", template)
        self.assertNotIn("<legend>Товар *</legend>", template)


if __name__ == "__main__":
    unittest.main()
