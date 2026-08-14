# Данные и транзакции

## Хранилища и источник истины

| Область | Фактическое хранение | Текущий owner/truth | Evidence |
| --- | --- | --- | --- |
| ERP catalog, brands/categories, sales, receipts, stock movements, audit | SQLite, default path из `CatalogDatabase`; raw SQL, без ORM | Для внутренних sales/receipts и их stock effects — `catalog_excel_products.stock` + `catalog_stock_movements`; для полной product identity ownership остаётся mixed | [`app/catalog_db.py`](../../app/catalog_db.py), [`sales_inventory.py`](../../app/services/sales_inventory.py), [`receipt_inventory.py`](../../app/services/receipt_inventory.py) |
| Imported Bitrix catalog | `catalog_*` SQLite tables with `external_source/external_*_id` | Bitrix — source external payload; local DB — imported projection | [`catalog_db.py`](../../app/catalog_db.py), [`bitrix_catalog_importer.py`](../../app/services/bitrix_catalog_importer.py) |
| Auth/users/sessions | Separate SQLite database | `AuthStore` database | [`app/auth.py`](../../app/auth.py) |
| Repairs | `instance/repair_cases.json`, lock file, attachments directory | JSON file is current durable case store; this audit confirms it was **not** migrated to SQLite | [`app/web.py`](../../app/web.py), [`repair_cases.py`](../../app/services/repair_cases.py) |
| Settings and compatibility state | `settings.json`, product mappings, warehouse cells/category cells, taxonomy, add requests/created times, legacy sales/overrides/receipts/stock operations | File-specific; some overlap with SQLite creates dual-read/compatibility risk | [`app/web.py`](../../app/web.py), [`receipt_recovery.py`](../../app/services/receipt_recovery.py) |
| МойСклад | Remote products, metadata, images, stock documents and stock views | External for its objects; code does not establish it as sole ERP truth | [`app/clients/moysklad.py`](../../app/clients/moysklad.py) |
| Orders | Bitrix endpoints plus local JSON mappings/writeoff history | Bitrix for order data; local files for ERP mapping/history | [`app/web.py`](../../app/web.py), [`app/clients/bitrix_orders.py`](../../app/clients/bitrix_orders.py) |

Следовательно, решение «ERP — единый источник истины» кодом не принято. Оно
требует entity-by-entity ADR, а не общей декларации
([ADR-кандидаты](../decisions/README.md)).

## SQLite schema

[`app/catalog_db.py`](../../app/catalog_db.py) создаёт schema idempotently SQL-
строками. Группы таблиц: imported catalog/categories/properties/offers/images/
prices and MoySklad mappings; Excel batches/products/import drafts/receipts and
audit rows; ERP brands/categories; `erp_sales`/items; `erp_receipts`/items/
recovery audit; `catalog_stock_movements`; `erp_audit_events`; schema migration
markers. Foreign keys включаются в connection setup; schema использует
`CASCADE`, `SET NULL` и `RESTRICT` по смыслу связи. Search/list indexes есть на
articles/names/barcodes/brand, listing filters, statuses/dates, product movement
and audit cursor/filter columns.

Auth schema в [`app/auth.py`](../../app/auth.py) содержит users, invitations,
auth attempts, tokens and sessions. Это отдельная transaction domain: cross-DB
atomicity с catalog database отсутствует.

ORM нет. Rows и dict payloads передаются между raw-SQL services и routes.
Schema evolution выполняется `initialize()`/version markers и отдельными
scripts, а не Alembic или другой общей framework
([`app/catalog_db.py`](../../app/catalog_db.py), [`scripts`](../../scripts)).

## Транзакционные границы

`CatalogDatabase.transaction()` открывает connection, выполняет
`BEGIN IMMEDIATE`, commit on success и rollback on exception
([`app/catalog_db.py`](../../app/catalog_db.py)).

| Операция | В одной local transaction | Вне границы / риск |
| --- | --- | --- |
| Create sale | stock validation/decrement, sale, items, movements; audit может использовать тот же connection | Remote effects/legacy compatibility outside; SQLite serializes writers ([`sales_inventory.py`](../../app/services/sales_inventory.py)) |
| Return/cancel sale | state checks, stock restoration, movements, sale metadata/status | JSON metadata carries part of lifecycle; callers must preserve contracts ([`sales_inventory.py`](../../app/services/sales_inventory.py)) |
| Post/update/cancel receipt | receipt/items, product stock, movements | МойСклад document creation/update/delete is orchestrated by HTTP layer and cannot roll back with SQLite ([`receipt_inventory.py`](../../app/services/receipt_inventory.py), [`app/web.py`](../../app/web.py)) |
| Catalog import/edit/delete | Relevant catalog rows and audit in service transaction | Image and МойСклад/Bitrix writes occur separately ([`excel_product_catalog.py`](../../app/services/excel_product_catalog.py), [`app/web.py`](../../app/web.py)) |
| Journal event | Its own transaction, or caller-supplied connection | Events are not DB triggers; omitted call means missing history ([`audit_journal.py`](../../app/services/audit_journal.py)) |
| Repair mutation | `flock` → read → callback → temporary file replace | Lock is filesystem/single-host oriented; attachment writes and case JSON are not one transaction ([`repair_cases.py`](../../app/services/repair_cases.py), [`app/web.py`](../../app/web.py)) |
| Auth mutation | Explicit `BEGIN IMMEDIATE`/commit/rollback in `AuthStore` | Email delivery is external and separate; no cross-database transaction ([`app/auth.py`](../../app/auth.py)) |

## Inventory invariants

`SalesInventory` rejects insufficient stock and conflicting cancellation/return;
`ReceiptInventory` changes stock together with movements; foreign keys restrict
deleting referenced products/items. These invariants are characterized in
[`tests/test_sales_inventory.py`](../../tests/test_sales_inventory.py) and
[`tests/test_unified_catalog_inventory.py`](../../tests/test_unified_catalog_inventory.py).
They protect service-mediated paths, not every legacy JSON or remote operation.

## Partial-save and concurrency risks

1. Order stock writeoff loops through МойСклад documents and then appends local
   history; a later failure leaves earlier remote writes applied
   ([`app/web.py`](../../app/web.py)).
2. Receipt/catalog remote writes and local SQLite transactions cannot be atomic;
   there is no outbox/saga/idempotency ledger shared with remote systems
   ([`app/web.py`](../../app/web.py), [`app/clients/moysklad.py`](../../app/clients/moysklad.py)).
3. JSON stores use a mix of temp+replace, file locks and direct write; guarantees
   are inconsistent. Multi-process/multi-host concurrency is not uniformly
   protected ([`app/web.py`](../../app/web.py), [`repair_cases.py`](../../app/services/repair_cases.py)).
4. SQLite `BEGIN IMMEDIATE` intentionally serializes writers. This is safe for
   local invariants but becomes a throughput/latency constraint as writes grow
   ([`app/catalog_db.py`](../../app/catalog_db.py)).
5. Local deploy backup exists, but private off-site retention and restore drills
   are not implemented or provable from repository code
   ([`scripts/deploy.sh`](../../scripts/deploy.sh), [risks](risks.md)).
