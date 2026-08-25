# Catalog schema migrations

Статус: PR catalog runtime-DDL, baseline `82152be7cc1ff7be2c61795d4eeee3d055a48eb8`.

## Результат

Единственный источник истины схемы catalog — deploy-time модуль
`app/catalog_migration_steps.py`, ordered registry `app/schema_migrations.py` и
полный структурный manifest `app/catalog_schema_manifest.json`. Runtime-класс
`CatalogDatabase` больше не содержит `SCHEMA`, schema `_ensure_*`, rebuild или
DDL dispatcher. Любой `connect()`, read или mutation сначала выполняет
read-only readiness validation; неизвестная/старая схема блокируется и не
исправляется.

Migration IDs по порядку:

1. `2026-08-24-production-schema-baseline-v1` — historical fresh/legacy step;
2. `2026-08-26-order-comments-baseline-v1` — comments contract;
3. `2026-08-27-catalog-schema-baseline-v1` — полный verified catalog baseline.

Последний checksum связан одновременно с исходником всех schema steps и
committed manifest. Ledger хранит ID, name, checksum, `applying/applied/failed`,
timestamp, application commit и recovery metadata.

## Call graph

До:

```text
Gunicorn worker / request / service
  → CatalogDatabase.initialize
  → sentinel valid? yes: verify; no: fail open
  → SCHEMA executescript
  → 15 schema _ensure_* methods
  → CREATE / ALTER / DROP / rebuild / backfill
```

После:

```text
deploy, writers stopped
  → verified backup
  → versioned catalog runner
  → migration lock + ledger + catalog migration steps
  → full manifest + quick_check + foreign_key_check

worker / request / catalog read / catalog mutation
  → CatalogDatabase.connect / initialize
  → read-only ledger + full manifest validation
  → DML or SELECT only
```

Sentinel и marker временно остаются диагностическими deploy-артефактами. Они
не читаются runtime readiness path и не способны разрешить DDL. Missing,
corrupt, outdated и wrong-checksum sentinel не меняют схему; readiness решают
только ledger и фактический manifest.

## Полный DDL inventory

Точный SQL находится только в migration module. Runtime gate фиксирует checksum
модуля и manifest и запрещает DDL containers во всём остальном `app/`.

| Ordered step | Область | Операции | Atomicity / recovery |
| --- | --- | --- | --- |
| `CATALOG_SCHEMA_SQL` | fresh catalog | 49 tables, named/unique indexes, 2 triggers | fresh DB; file backup |
| `_apply_audit_entity_constraints` | audit | verified table rebuild + 5 indexes | explicit transaction; backup |
| `_apply_excel_receipt_constraints` | Excel receipt | verified rebuilds/indexes | per-rebuild transaction; backup |
| `_apply_excel_cardinality_columns` | Excel products | additive columns/index | transaction boundary; backup |
| `_apply_excel_import_draft_schema` | Excel drafts | draft/row tables and indexes | controlled step; backup |
| `_apply_product_deletion_columns` | products | additive lifecycle columns | controlled step; backup |
| `_apply_product_workflow_columns` | products | model/workflow columns | controlled step; backup |
| `_apply_product_image_columns` | products | local image metadata | controlled step; backup |
| `_apply_receipt_constraints` | receipts | receipt/item constraints and indexes | verified rebuild; backup |
| `_apply_optional_price_constraints` | sales/receipts | nullable-price rebuilds and indexes | per-table transaction; backup |
| `_apply_shared_catalog` | taxonomy/sales/receipts/movements | columns, indexes, triggers, deterministic backfill | controlled step; backup |
| `_apply_brand_image_columns` | brands | metadata + unique index | controlled step; backup |
| `_apply_brand_category_relations` | taxonomy | relation backfill + legacy marker | transaction; backup |
| `_apply_inventory_constraints` | inventory | scope/snapshot columns and indexes | controlled step; backup |
| `_apply_stock_movement_constraints` | movements | verified movement-type rebuild | explicit transaction; backup |
| `apply_order_comment_constraints` | internal comments | known legacy table rebuild with CHECK/FK preservation | FK-off guarded step; backup |
| `_apply_order_item_mapping_schema` | order-product mapping | canonical rebuild/index | FK-off guarded step; backup |

Все statements совместимы с SQLite 3.7.17: нет partial indexes, UPSERT,
`RETURNING`, `DROP/RENAME COLUMN`, generated/STRICT tables, expression indexes,
window functions или modern ALTER. Active Tictactoy uniqueness реализована
двумя triggers вместо partial index.

## Verified baseline и schema parity

Manifest проверяет 49 tables, все columns (name/type/null/default/PK), foreign
keys, unique/named/automatic indexes, canonical CHECK expressions, 2 triggers и
views. Ошибка показывает первый отличающийся table/object class. Проверка не
зависит от порядка колонок или форматирования SQL.

Production-like и fresh schema выявили известный исторический drift: production
получил часть columns через `ALTER ADD COLUMN`, поэтому порядок отличается;
четыре fresh-only CHECK отсутствуют в production. Fresh definition приведён к
фактическому production contract без rebuild production. После этого
fresh/upgraded structural manifest совпадает.

Production baseline до deploy: Python 3.6.8, SQLite 3.7.17, commit `82152be7`,
Git clean, service active, catalog 228564992 bytes, 49 tables, 80 named indexes,
2 triggers, products 4724, active stock sum 1052679, sales/items 33/37,
movements 1394, comments 6, active inventory/items 0/0, `quick_check=ok`, FK=0.
Содержимое строк, персональные данные и secrets в отчёты не входят.

## Data safety и rollback

Preflight создаёт SQLite `.backup`, проверяет integrity, запускает migration
дважды, создаёт fresh DB, сравнивает полный manifest и расширенные агрегаты.
Deploy останавливает writers, сохраняет отдельный verified catalog backup,
применяет runner до старта workers и сравнивает products/taxonomy/stock,
movement sums, sales/items, receipts/items, inventories/items/adjustments,
audit, mappings и idempotency counts. Любая ошибка восстанавливает backup и
предыдущий commit до HTTP checks.

## Ограничение

PR не меняет business logic, frontend, остатки, продажи, приходы,
инвентаризации, заказы, клиентов, роли, SQLite runtime или external API.
Следующий security slice: MoySklad SSRF/token protection и CI no-egress.
