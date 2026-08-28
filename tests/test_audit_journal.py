import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal, whitelisted_changes
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.receipt_inventory import ReceiptInventory
from app.services.sales_inventory import SalesInventory
from app.services.shared_catalog import SharedCatalog


class Python36Datetime:
    """Datetime surface available on production Python 3.6."""

    strptime = staticmethod(datetime.strptime)
    combine = staticmethod(datetime.combine)
    now = staticmethod(datetime.now)


class AuditJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('audit', 'audit-sha', 'audit.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-11T10:00:00+00:00', "
                "'2026-08-11T10:00:00+00:00')"
            )
        self.journal = AuditJournal(self.database)
        self.products = ExcelProductCatalog(self.database)
        self.sales = SalesInventory(self.database)
        self.receipts = ReceiptInventory(self.database)
        self.catalog = SharedCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def create_product(self, stock=10):
        return self.products.create_product(
            "KLOK-01", article="KLOK-ARTICLE", brand="Klokers",
            category="Часы", cell="A-01", stock=stock,
            actor_id="user-1", actor_name="Максим", actor_type="user",
        )

    def events(self, **filters):
        return self.journal.list_events(limit=100, **filters)["events"]

    def test_create_update_delete_are_structured_and_actor_is_snapshot(self):
        product = self.create_product(stock=0)
        self.products.update_product(
            product["id"], price=1350, category="Новые часы",
            actor_id="user-1", actor_name="Максим", actor_type="user",
        )
        self.products.delete_product(
            product["id"], actor_id="user-1", actor_name="Максим"
        )
        events = self.events(entity_type="product", entity_id=product["id"])
        self.assertEqual([event["action"] for event in events], [
            "deleted", "updated", "created",
        ])
        self.assertEqual(events[1]["changes"]["price"]["after"], "1350")
        self.assertEqual(events[1]["changes"]["category"]["after"], "Новые часы")
        self.assertEqual(events[0]["actor_display_name_snapshot"], "Максим")
        self.assertEqual(events[0]["object_label_snapshot"], "KLOK-01")
        self.assertIsNone(self.products.get_product(product["id"]))

    def test_whitelist_and_sensitive_values_never_enter_event(self):
        changes = whitelisted_changes(
            "product",
            {"name": "Old", "password": "before", "token": "a"},
            {"name": "New", "password": "after", "token": "b"},
        )
        self.assertEqual(changes, {"name": {"before": "Old", "after": "New"}})
        self.journal.record(
            "product", "42", "updated", "Safe product",
            before={"name": "Old"}, after={"name": "New"},
            metadata={
                "article": "SAFE-42", "api_token": "never", "nested": {
                    "password_hash": "never", "safe": "ok",
                },
            }, actor_name="Auditor",
        )
        event = self.events(entity_id="42")[0]
        serialized = str(event)
        self.assertNotIn("never", serialized)
        self.assertEqual(event["metadata"]["nested"], {"safe": "ok"})

    def test_transaction_rollback_removes_event_and_failed_sale_has_no_success(self):
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as connection:
                self.journal.record(
                    "product", "rollback", "updated", "Rollback",
                    before={"price": 1}, after={"price": 2},
                    connection=connection,
                )
                raise RuntimeError("rollback")
        self.assertEqual(self.events(entity_id="rollback"), [])

        product = self.create_product(stock=2)
        before_count = len(self.events(entity_type="sale"))
        with self.assertRaises(RuntimeError):
            self.sales.create_sale(
                {
                    "id": "failed-sale", "source": "Tictactoy",
                    "order_number": "FAIL", "product_name": "KLOK-01",
                }, product["id"], 1, 1000, user_name="Максим",
                failure_hook=lambda _connection: (_ for _ in ()).throw(
                    RuntimeError("failed")
                ),
            )
        self.assertEqual(len(self.events(entity_type="sale")), before_count)

    def test_sale_actions_are_single_meaningful_events_with_source(self):
        product = self.create_product(stock=3)
        self.sales.create_sale(
            {
                "id": "sale-1542", "source": "Wildberries",
                "order_number": "1542", "product_name": "KLOK-01",
            }, product["id"], 1, 1250, user_name="Максим",
        )
        self.sales.update_sale(
            "sale-1542", {"note": "Позвонить клиенту"}, 1, 1250,
            user_name="Максим",
        )
        self.sales.cancel_sale(
            "sale-1542", reason="customer_refused",
            comment="Клиент отказался", user_name="Максим",
        )
        events = self.events(entity_type="sale", entity_id="sale-1542")
        self.assertEqual([event["action"] for event in events], [
            "refused", "comment_added", "created",
        ])
        self.assertEqual(events[0]["source_snapshot"], "Wildberries")
        self.assertEqual(events[0]["status_snapshot"], "refusal")
        self.assertEqual(events[0]["metadata"]["text_snapshot"], "Клиент отказался")
        self.assertEqual(events[1]["metadata"]["text_snapshot"], "Позвонить клиенту")

    def test_receipt_create_update_comment_and_cancel_without_stock_noise(self):
        product = self.create_product(stock=0)
        payload = {
            "id": "receipt-483", "number": "483",
            "receipt_date": "2026-08-11", "comment": "Первый",
        }
        self.receipts.create_receipt(
            payload,
            [{"product_id": product["id"], "quantity": 5, "purchase_price": 700}],
            user_name="Максим",
        )
        self.receipts.update_receipt(
            "receipt-483", {**payload, "comment": "Уточнение"},
            [{"product_id": product["id"], "quantity": 8, "purchase_price": 700}],
            user_name="Максим",
        )
        self.receipts.cancel_receipt("receipt-483", user_name="Максим")
        events = self.events(entity_type="receipt", entity_id="receipt-483")
        self.assertEqual([event["action"] for event in events], [
            "cancelled", "updated", "created",
        ])
        self.assertEqual(events[1]["changes"]["quantity"], {
            "before": 5.0, "after": 8,
        })
        self.assertFalse(any(
            event["entity_type"] == "product" and event["action"] == "updated"
            for event in self.events()
        ))

        draft_payload = {
            "id": "receipt-draft", "number": "DRAFT-1",
            "receipt_date": "2026-08-11", "comment": "",
        }
        positions = [{
            "product_id": product["id"], "quantity": 1, "purchase_price": 500,
        }]
        self.receipts.create_draft(draft_payload, positions, user_name="Максим")
        self.receipts.update_draft(
            "receipt-draft", {**draft_payload, "comment": "Новый комментарий"},
            positions, user_name="Максим",
        )
        draft_events = self.events(entity_id="receipt-draft")
        self.assertEqual(draft_events[0]["action"], "comment_added")

    def test_filters_search_order_and_keyset_pagination(self):
        values = [
            ("1", "2026-08-11T21:14:20+00:00", "KLOK-01", "Максим"),
            ("2", "2026-08-11T21:14:32+00:00", "Продажа #1542", "Анна"),
            ("3", "2026-08-11T20:00:00+00:00", "Приход #483", "Максим"),
        ]
        types = ["product", "sale", "receipt"]
        for entity_type, (entity_id, occurred_at, label, actor) in zip(types, values):
            self.journal.record(
                entity_type, entity_id, "updated", label,
                before={"status": "old"}, after={"status": "new"},
                metadata={"article": "KLOK-01", "number": entity_id},
                actor_name=actor, occurred_at=occurred_at,
                status="new", source="Amazon" if entity_type == "sale" else "",
            )
        ordered = self.journal.list_events(limit=2)
        self.assertEqual([event["entity_id"] for event in ordered["events"]], ["2", "1"])
        older = self.journal.list_events(limit=2, cursor=ordered["next_cursor"])
        self.assertEqual(older["events"][0]["entity_id"], "3")
        self.assertEqual(self.events(query="KLOK-01")[0]["entity_id"], "2")
        self.assertEqual(self.events(actor="Анна")[0]["entity_id"], "2")
        self.assertEqual(self.events(entity_type="sale", source="Amazon")[0]["entity_id"], "2")

    def test_same_timestamp_uses_descending_id_and_no_mutation_api_exists(self):
        for entity_id in ("a", "b"):
            self.journal.record(
                "product", entity_id, "updated", entity_id,
                occurred_at="2026-08-11T12:00:00+00:00",
            )
        events = self.events(entity_type="product")
        self.assertEqual([event["entity_id"] for event in events[:2]], ["b", "a"])
        self.assertFalse(hasattr(self.journal, "update_event"))
        self.assertFalse(hasattr(self.journal, "delete_event"))

    def test_brand_and_category_feed_context_uses_immutable_snapshots(self):
        brand = self.catalog.create_brand("Casio", actor_name="Максим")
        brand_created = web.serialize_journal_event(
            self.events(entity_type="brand", action="created")[0]
        )
        self.assertEqual(brand_created["summary"], "Создан новый бренд")
        self.assertNotIn("— →", brand_created["summary"])

        category = self.catalog.create_brand_category(
            brand["id"], "Ремешки", actor_name="Максим"
        )
        created_event = self.events(entity_type="category", action="created")[0]
        self.assertEqual(
            created_event["metadata"]["brand_name_snapshot"], "Casio"
        )
        self.assertEqual(
            web.serialize_journal_event(created_event)["summary"],
            "Создана новая категория в бренде «Casio»",
        )
        self.assertEqual(
            self.events(entity_type="category", query="Casio")[0]["entity_id"],
            str(category["id"]),
        )

        self.catalog.rename_brand(brand["id"], "CASIO Europe", actor_name="Максим")
        rename_event = self.events(entity_type="brand", action="updated")[0]
        self.assertEqual(
            web.serialize_journal_event(rename_event)["summary"],
            "Бренд переименован: Casio → CASIO Europe",
        )
        unchanged = self.journal.get_event(created_event["id"])
        self.assertEqual(
            web.serialize_journal_event(unchanged)["summary"],
            "Создана новая категория в бренде «Casio»",
        )

    def test_existing_category_link_global_rename_and_scoped_delete_copy(self):
        source_brand = self.catalog.create_brand("Seiko")
        category = self.catalog.create_brand_category(
            source_brand["id"], "Аксессуары"
        )
        casio = self.catalog.create_brand("Casio")
        self.catalog.create_brand_category(casio["id"], "аксессуары")
        linked = self.events(entity_type="category", action="created")[0]
        self.assertEqual(
            web.serialize_journal_event(linked)["summary"],
            "Добавлена в бренд «Casio»",
        )

        self.catalog.rename_category(category["id"], "Ремешки")
        renamed = self.events(entity_type="category", action="updated")[0]
        self.assertEqual(
            web.serialize_journal_event(renamed)["summary"],
            "Категория переименована во всей ERP: Аксессуары → Ремешки",
        )

        self.products.delete_brand_catalog(
            casio["id"], category_id=category["id"]
        )
        deleted = self.events(entity_type="category", action="deleted")[0]
        self.assertEqual(
            web.serialize_journal_event(deleted)["summary"],
            "Удалена из бренда «Casio»",
        )

    def test_create_update_delete_and_old_event_fallback_copy(self):
        product = self.create_product(stock=0)
        created = next(
            item for item in self.events(entity_type="product")
            if item["action"] == "created"
        )
        self.assertEqual(
            web.serialize_journal_event(created)["summary"], "Создан новый товар"
        )
        self.products.update_product(product["id"], stock=1, stock_reason="Тест")
        updated = self.events(entity_type="product", action="updated")[0]
        self.assertEqual(
            web.serialize_journal_event(updated)["summary"], "Остаток: 0 → 1"
        )

        old_id = self.journal.record(
            "category", "old-category", "created", "Старая категория"
        )
        old = web.serialize_journal_event(self.journal.get_event(old_id))
        self.assertEqual(old["summary"], "Создана категория")

        deleted_brand = web.format_journal_event({
            "entity_type": "brand", "action": "deleted",
            "object_label_snapshot": "Casio",
            "metadata": {"products_deleted": 48},
        })
        self.assertEqual(
            deleted_brand["action_text"],
            "Бренд удалён · удалено 48 товаров",
        )

    def test_sale_and_receipt_copy_remains_human_readable(self):
        sale_created = web.format_journal_event({
            "entity_type": "sale", "action": "created",
            "object_label_snapshot": "Продажа #1542", "metadata": {},
        })
        receipt_created = web.format_journal_event({
            "entity_type": "receipt", "action": "created",
            "object_label_snapshot": "Приход #483", "metadata": {},
        })
        self.assertEqual(sale_created["action_text"], "Создана новая продажа")
        self.assertEqual(receipt_created["action_text"], "Создан новый приход")

        status_id = self.journal.record(
            "sale", "1542", "status_changed", "Продажа #1542",
            changes={"status": {"before": "sent", "after": "completed"}},
        )
        status = web.serialize_journal_event(self.journal.get_event(status_id))
        self.assertEqual(
            status["summary"], "Статус: Отправлен → Завершён"
        )

    def test_feed_formatter_distinguishes_create_rename_and_scoped_actions(self):
        cases = [
            (
                {"entity_type": "brand", "action": "created", "changes": {
                    "name": {"before": None, "after": "Casio"},
                }},
                "Создан новый бренд",
            ),
            (
                {"entity_type": "brand", "action": "updated", "changes": {
                    "name": {"before": "Casio", "after": "CASIO Europe"},
                }},
                "Бренд переименован: Casio → CASIO Europe",
            ),
            (
                {"entity_type": "category", "action": "created", "changes": {},
                 "metadata": {"relation_action": "unlinked",
                              "global_category_created": True}},
                "Создана новая глобальная категория",
            ),
            (
                {"entity_type": "category", "action": "created", "changes": {},
                 "metadata": {"relation_action": "created",
                              "global_category_created": True,
                              "brand_name_snapshot": "Casio"}},
                "Создана новая категория в бренде «Casio»",
            ),
            (
                {"entity_type": "category", "action": "created", "changes": {},
                 "metadata": {"relation_action": "linked",
                              "brand_name_snapshot": "Casio"}},
                "Добавлена в бренд «Casio»",
            ),
            (
                {"entity_type": "category", "action": "updated", "changes": {
                    "name": {"before": "Часы", "after": "Наручные часы"},
                }},
                "Категория переименована во всей ERP: Часы → Наручные часы",
            ),
            (
                {"entity_type": "category", "action": "deleted", "changes": {},
                 "metadata": {"brand_name_snapshot": "Casio",
                              "products_deleted": 12}},
                "Удалена из бренда «Casio» · удалено 12 товаров",
            ),
        ]
        base = {
            "occurred_at": "2026-08-12T10:00:00+00:00",
            "actor_type": "user", "metadata": {},
        }
        for event, expected in cases:
            payload = web.serialize_journal_event({**base, **event})
            self.assertEqual(payload["summary"], expected)
            self.assertNotIn("— →", payload["summary"])

    def test_feed_formatter_keeps_updates_and_handles_incomplete_history(self):
        base = {
            "occurred_at": "2026-08-12T10:00:00+00:00",
            "actor_type": "user", "metadata": {},
        }
        product_create = web.serialize_journal_event({
            **base, "entity_type": "product", "action": "created",
            "changes": {"name": {"before": None, "after": "KLOK-01"}},
        })
        product_update = web.serialize_journal_event({
            **base, "entity_type": "product", "action": "updated",
            "changes": {"stock": {"before": 0, "after": 1}},
        })
        sale_status = web.serialize_journal_event({
            **base, "entity_type": "sale", "action": "status_changed",
            "changes": {"status": {"before": "sent", "after": "completed"}},
        })
        incomplete = web.serialize_journal_event({
            **base, "entity_type": "category", "action": "created",
            "changes": {},
        })
        self.assertEqual(product_create["summary"], "Создан новый товар")
        self.assertEqual(product_update["summary"], "Остаток: 0 → 1")
        self.assertEqual(
            sale_status["summary"], "Статус: Отправлен → Завершён"
        )
        self.assertEqual(incomplete["summary"], "Создана категория")


class AuditJournalUiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}
        )
        self.environment.start()
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        AuditJournal(self.database).record(
            "product", "deleted-product", "deleted", "KLOK-01",
            object_secondary="KLOK-ARTICLE",
            metadata={"article": "KLOK-ARTICLE"}, actor_name="Максим",
        )
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_page_navigation_timeline_controls_and_read_only_api(self):
        response = self.client.get("/app/journal")
        self.assertEqual(response.status_code, 200)
        source = response.get_data(as_text=True)
        self.assertIn("Все", source)
        self.assertIn("Товары", source)
        self.assertIn("Продажи", source)
        self.assertIn("Приход", source)
        self.assertIn("Период", source)
        self.assertIn("Фильтры", source)
        self.assertIn("data-event-id=\"1\"", source)
        self.assertIn("journal-event-icon", source)
        self.assertIn("journal-event-copy", source)
        self.assertIn("journal-event-title", source)
        self.assertIn("journal-event-summary", source)
        self.assertIn("journal-event-meta", source)
        self.assertIn("journal-event-chevron", source)
        self.assertIn("journal-drawer", source)
        self.assertIn('class="journal-filters" hidden', source)
        self.assertIn('aria-controls="journalFilters"', source)
        self.assertIn('role="group" aria-label="Период"', source)
        self.assertIn('placeholder="Поиск по журналу"', source)
        self.assertNotIn(">Найти<", source)
        self.assertIn('product: "Открыть товар"', source)
        self.assertIn('sale: "Открыть продажу"', source)
        self.assertIn('receipt: "Открыть приход"', source)
        self.assertIn('detailValue("Было", change.before, false)', source)
        self.assertIn('detailValue("Стало", change.after, true)', source)
        self.assertIn("changedFieldsLabel(fieldChanges.length)", source)
        self.assertNotIn('document.createElement("input")', source)
        self.assertIn('link.className = "journal-object-link"', source)
        self.assertNotIn("<style", source)
        self.assertNotIn("Экспорт журнала", source)
        with web.app.test_request_context("/app/journal"):
            labels = [item["label"] for item in web.get_navigation_items()]
        self.assertEqual(labels, [
            "Заказы", "Задачи", "Товары", "Продажи", "Аналитика", "Инвентаризация", "Приход",
            "Журнал", "Входящие", "Ремонт", "Клиенты", "SMS", "Закупки", "Команда", "Настройки",
        ])
        self.assertEqual(self.client.post("/api/journal").status_code, 405)
        self.assertEqual(self.client.delete("/api/journal/1").status_code, 405)

    def test_python36_datetime_supports_empty_event_and_filtered_journal(self):
        patches = (
            mock.patch("app.services.audit_journal.datetime", Python36Datetime),
            mock.patch.object(web, "datetime", Python36Datetime),
        )
        with patches[0], patches[1]:
            self.assertEqual(self.client.get("/app/journal").status_code, 200)
            self.assertEqual(
                self.client.get(
                    "/api/journal?entity_type=product&date_from=2026-08-01"
                    "&date_to=2026-08-31&q=KLOK"
                ).status_code,
                200,
            )
            with self.database.transaction() as connection:
                connection.execute("DELETE FROM erp_audit_events")
            response = self.client.get("/app/journal")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Событий пока нет", response.get_data(as_text=True))

    def test_deleted_object_detail_is_readable_without_broken_link(self):
        response = self.client.get("/api/journal/1")
        self.assertEqual(response.status_code, 200)
        event = response.get_json()["data"]
        self.assertEqual(event["object_label_snapshot"], "KLOK-01")
        self.assertTrue(event["object_deleted"])
        self.assertEqual(event["object_url"], "")

    def test_supported_entities_have_urls_and_multi_change_payload(self):
        journal = AuditJournal(self.database)
        product_event = journal.record(
            "product", "product-1", "updated", "Product one",
            changes={
                "price": {"before": "1250", "after": "1350"},
                "category": {"before": "Часы", "after": "Аксессуары"},
            },
        )
        sale_event = journal.record(
            "sale", "sale-1", "status_changed", "Продажа #1",
            changes={"status": {"before": "sent", "after": "completed"}},
        )
        receipt_event = journal.record(
            "receipt", "receipt-1", "updated", "Приход #1",
            changes={"quantity": {"before": 5, "after": 8}},
        )
        with mock.patch.object(web, "ExcelProductCatalog") as products, \
                mock.patch.object(web, "SalesInventory") as sales, \
                mock.patch.object(web, "ReceiptInventory") as receipts:
            products.return_value.get_product.return_value = {"id": "product-1"}
            sales.return_value.get_sale.return_value = {"id": "sale-1"}
            receipts.return_value.get_receipt.return_value = {"id": "receipt-1"}
            product = self.client.get(
                "/api/journal/{}".format(product_event)
            ).get_json()["data"]
            sale = self.client.get(
                "/api/journal/{}".format(sale_event)
            ).get_json()["data"]
            receipt = self.client.get(
                "/api/journal/{}".format(receipt_event)
            ).get_json()["data"]
        self.assertEqual(len(product["field_changes"]), 2)
        self.assertEqual(product["object_url"], "/app/products?product_id=product-1")
        self.assertEqual(sale["object_url"], "/app/sales?q=sale-1")
        self.assertEqual(
            receipt["object_url"], "/app/receipts?receipt_id=receipt-1"
        )

    def test_context_filters_and_search_are_server_side(self):
        response = self.client.get(
            "/api/journal?entity_type=product&action=deleted&actor=Максим&q=KLOK"
        )
        self.assertEqual(response.status_code, 200)
        events = response.get_json()["data"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["entity_type"], "product")

    def test_tabs_period_and_empty_filter_state_are_preserved(self):
        response = self.client.get(
            "/app/journal?entity_type=product&date_from=2026-08-01"
            "&date_to=2026-08-31&q=missing"
        )
        self.assertEqual(response.status_code, 200)
        source = response.get_data(as_text=True)
        self.assertIn(
            'class="journal-tab erp-section-tab active" href="#" '
            'data-journal-tab="product"'
            ' aria-current="page"',
            source,
        )
        self.assertIn('name="date_from" value="2026-08-01"', source)
        self.assertIn('name="date_to" value="2026-08-31"', source)
        self.assertIn('name="q" value="missing"', source)
        self.assertIn("По выбранным фильтрам событий нет", source)
        self.assertIn("Сбросить фильтры", source)

        filtered = self.client.get("/app/journal?action=deleted")
        self.assertEqual(filtered.status_code, 200)
        self.assertIn(
            'class="journal-filter-count" aria-label="Активных фильтров: 1">1',
            filtered.get_data(as_text=True),
        )

    def test_confirmed_photo_actions_are_recorded_without_image_payload(self):
        product = {
            "id": "photo-product", "excel_name_raw": "KLOK Photo",
            "excel_article": "PHOTO-1",
        }
        with mock.patch.object(
            web,
            "current_auth_user",
            return_value={"id": "user-1", "email": "max@example.test"},
        ):
            with web.app.test_request_context("/api/products/photo-product"):
                for action in ("add", "replace", "remove"):
                    web.record_product_photo_audit(product, action, "Bitrix")
        events = AuditJournal(self.database).list_events(
            entity_type="product", entity_id="photo-product", limit=10,
        )["events"]
        self.assertEqual([event["action"] for event in events], [
            "photo_removed", "photo_replaced", "photo_added",
        ])
        self.assertNotIn("base64", str(events).casefold())


if __name__ == "__main__":
    unittest.main()
