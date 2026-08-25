# Runtime DDL audit — 2026-08-25

Статус: `archive evidence`; remediation current на baseline `82152be7`.

PR-A реализован в [`domain-schema-migrations.md`](domain-schema-migrations.md):
auth/orders/customers/comments runtime DDL удалён. Catalog PR-G реализован в
[`catalog-schema-migrations.md`](catalog-schema-migrations.md): `SCHEMA` и все
schema `_ensure_*` перенесены в versioned runner, sentinel fail-open удалён,
runtime DDL allowlist пуст. Таблицы и числа ниже сохранены как исходное
доказательство BEFORE, а не как описание текущего runtime.

Границы: SQLite DDL, schema bootstrap, legacy `_ensure_*`, startup и request
paths. Аудит не меняет production-схему, migration order или бизнес-логику.
Машинный allowlist: [`runtime-ddl-inventory.json`](runtime-ddl-inventory.json).

## Резюме

Полный поиск Python, shell, CI и служебных скриптов нашёл три реально
достижимых runtime schema path:

1. `catalog.db`: legacy `SCHEMA` и 16 DDL `_ensure_*` остаются в коде, но
   production sentinel переводит `CatalogDatabase.initialize()` в read-only
   verifier. Если sentinel потерян, код fail-open вернётся к DDL из каждого
   worker/request service.
2. `auth.db`: `AuthStore._ensure_schema()` выполняет 5 `CREATE TABLE`, 5
   `CREATE INDEX`, возможные `ALTER TABLE` и нормализующий `UPDATE` при импорте
   каждого worker и при каждом новом `AuthStore`, включая каждый HTTP request.
3. `orders.db`: `OrdersSnapshotStore.initialize()` выполняет bootstrap DDL,
   conditional `ALTER TABLE`, три дополнительных индекса и два `UPDATE` при
   чтении заказов/клиентов и в фоновых refresh jobs. Кэша и ledger нет.

SQLAlchemy отсутствует. В clients, settings, sales, receipts, inventory и
background sync отдельного DDL нет: они достигают DDL через один из трёх
initializer paths выше. Repairs используют versioned JSON normalization, а не
SQLite DDL; чтение нормализует в памяти, запись сохраняет текущую JSON schema.

## Метод поиска

Проверены не только имена функций, но и AST/string/call patterns:
`CREATE/ALTER/DROP`, indexes/triggers, `executescript`, dynamic `.format()` DDL,
`PRAGMA`, `sqlite_master`, `initialize`, `migrate_*`, Flask import side effects,
CLI entry points, deploy, systemd и CI. Найдено 543 текстовых совпадения; после
исключения SELECT/DML/test fixtures фактические DDL definitions находятся в
`app/auth.py`, `app/catalog_db.py`, `app/schema_migrations.py` и
`app/services/orders_snapshot.py`.

`ensure_brand`, `ensure_category`, `ensure_model_record`,
`ensure_unique_article`, `_ensure_catalog_batch` и `ensure_batch_is_new` — DML
или validation, не DDL. Они всё равно зафиксированы checksum allowlist, чтобы
новая `_ensure_*` не появилась незаметно.

## Runtime DDL Inventory

| ID | Контур | Файл/функция | SQL/операция | Точка вызова | Production reachable | Worker multiplicity | Ledger | Rehearsal | Риск | Категория | План удаления |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-01 | Auth/users/roles | `app/auth.py:AuthStore._ensure_schema` | 5 tables, 5 indexes, conditional user columns, normalization UPDATE | `configure_auth`; `get_auth_store` | Да: startup и HTTP | Каждый worker; каждый request | Нет | Да, exact runtime | P0 | A | PR-B |
| A-02 | Orders/customers | `app/services/orders_snapshot.py:OrdersSnapshotStore.initialize` | 3 tables, 9 indexes, 6 conditional columns, normalization UPDATE | list/get/count/replace/customer store | Да: HTTP и background refresh | Каждый worker, многократно | Нет | Да, exact runtime | P0 | A | PR-A |
| A-03 | Catalog bootstrap | `app/catalog_db.py:SCHEMA` | 48 application tables, indexes and triggers | `_initialize_schema` | Условно: sentinel missing | Каждый worker/service при потере guard | Только baseline wrapper | Да | P1 | D/A | PR-G |
| A-04 | Catalog dispatcher | `CatalogDatabase._initialize_schema` | `executescript(SCHEMA)` + все следующие ensure | migration runner или unguarded initialize | Условно | Параллельно без sentinel | Baseline только при deploy | Да | P1 | A | PR-G |
| A-05 | Comments | `_ensure_order_comment_schema` | columns + unique/sync indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-A |
| A-06 | Order mappings | `_ensure_order_item_mapping_schema` | rebuild/drop/rename/index | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| A-07 | Brands | `_ensure_brand_image_columns` | columns + unique Bitrix index | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-08 | Inventory | `_ensure_inventory_constraints` | columns, drop/create indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-C |
| A-09 | Audit journal | `_ensure_audit_entity_constraints` | table rebuild + five indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| A-10 | Brand/category | `_ensure_brand_category_relations` | schema-dependent DML backfill | catalog dispatcher | Условно | Как A-04 | Legacy ledger rows only | Да | P1 | A (DML migration) | PR-E |
| A-11 | Excel receipts | `_ensure_excel_receipt_constraints` | two table rebuilds + indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| A-12 | Excel cardinality | `_ensure_excel_cardinality_columns` | dynamic `ALTER TABLE ADD COLUMN` | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-13 | Import drafts | `_ensure_excel_import_draft_schema` | table rebuild/drop/rename/index | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-14 | Product deletion | `_ensure_product_deletion_columns` | dynamic product columns | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-15 | Product workflow | `_ensure_product_workflow_columns` | model columns/index | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-16 | Product images | `_ensure_product_image_columns` | image columns/index | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-E |
| A-17 | Receipts | `_ensure_receipt_constraints` | receipt rebuild/indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| A-18 | Prices/items | `_ensure_optional_price_constraints` | dynamic sale/receipt item rebuilds | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| A-19 | Shared catalog | `_ensure_shared_catalog` | columns/indexes/triggers and taxonomy DML | catalog dispatcher | Условно | Как A-04 | Two legacy rows + baseline | Да | P1 | A | PR-E |
| A-20 | Stock movements | `_ensure_stock_movement_constraints` | movement rebuild + five indexes | catalog dispatcher | Условно | Как A-04 | Baseline only | Да | P1 | A | PR-F |
| B-01 | Versioned runner | `app/schema_migrations.py:apply_migrations` | ledger + registered baseline | deploy preflight/apply | Только deploy | Один runner, file lock | Да | Да | controlled | B | Сохранить |
| C-01 | Auth legacy | `scripts/migrate_auth_mvp.py` | backup + `AuthStore` schema | Manual CLI | Не в startup/deploy | Один CLI | Нет | Нет | P2 | C | PR-B |
| C-02 | Inventory legacy | `migrate_brand_inventory.py` | catalog initialize + checks | Manual CLI | Не в deploy; executable | Один CLI | Нет | Нет | P2 | C | PR-C |
| C-03 | Customers legacy | `migrate_customers.py` | orders initializer + backfill | Manual CLI | Не в deploy; executable | Один CLI | Нет | Нет | P2 | C | PR-A |
| C-04 | Scope legacy | `migrate_inventory_scopes.py` | catalog initialize twice | Manual CLI | Не в deploy; executable | Один CLI | Нет | Нет | P2 | C | PR-C |
| C-05 | Repairs legacy | `migrate_repair_cases.py` | JSON migration, no SQLite DDL | Manual CLI | Не в runtime DDL path | Один CLI | JSON report only | Нет | P2 | C | PR-D |
| C-06 | Unified catalog | `migrate_unified_catalog.py` | catalog initialize + DML + old ledger | Manual CLI | Не в deploy; executable | Один CLI | `erp_schema_migrations` | Нет | P2 | C | PR-E/PR-F |
| E-01 | Tests | test fixtures/temp databases | bootstrap and intentionally corrupt DDL | unittest only | Нет при корректном temp config | test processes | Test-local | Да | P3 | E | Сохранить |

`PRAGMA foreign_keys=ON/OFF` не является изменением `sqlite_master`, но учтён:
connection-level `ON` применяется в catalog/orders/auth/category diagnostics;
legacy table rebuilds временно делают `OFF` и сами управляют commit/rollback.
`PRAGMA user_version`, SQLAlchemy `create_all` и runtime `DROP INDEX/TABLE` вне
catalog legacy dispatcher не найдены.

## Startup Call Graph

Production unit:

```text
systemd clock-erp.service
  └─ gunicorn -w 2 -b 127.0.0.1:5000 app.web:app
      ├─ preload: false; master не импортирует application
      ├─ worker 1 imports app.web
      │   ├─ Flask(__name__)
      │   ├─ configure_auth(app)
      │   │   └─ AuthStore(auth.db)._ensure_schema()        [10 DDL]
      │   └─ CatalogDatabase(catalog.db).initialize()
      │       └─ sentinel → verify ledger/contract/fingerprint [read-only]
      └─ worker 2 выполняет ту же цепочку независимо          [10 DDL]
```

После готовности worker:

```text
HTTP request
  └─ auth before_request
      └─ get_auth_store() → AuthStore._ensure_schema()       [10 DDL/request]

orders/customers request or background refresh
  └─ OrdersSnapshotStore method
      └─ initialize()                                        [12 schema statements/call]

catalog/comment/inventory/sale/receipt service
  └─ CatalogDatabase.initialize()
      └─ runtime guard verifier/cache                         [0 DDL]
```

Auth `executescript` имеет неявные transaction boundaries; затем возможны
`ALTER` и DML. Orders `executescript` также отделён от последующих `ALTER`, DML
и index creation. Ни один контур не имеет migration ledger, lock или explicit
partial-state marker. `IF NOT EXISTS` делает полный-schema повторяемым, но не
решает interruption/race на старой или частичной схеме.

## Exact-runtime rehearsal

Rehearsal выполнен на `.backup` трёх production databases в
`/opt/clock-erp-backups/migration-rehearsals/runtime-ddl-audit-final-ds4NFw`, с
production dependencies, Python 3.6.8, SQLite 3.7.17 и двумя параллельными
processes. DNS и socket egress были заблокированы wrapper-ом.

Результат каждого worker:

- import приложения: 10 auth DDL statements;
- повторный AuthStore/request phase: ещё 10 auth DDL;
- два order initialize: по 14 traced events, включая 12 schema statements и
  transaction boundaries;
- catalog startup и comment service: 0 DDL благодаря runtime guard;
- оба workers завершились без lock/race error.

До/после совпали schema hashes: catalog
`1df746ef…7700b`, orders `1cea3105…d46d`, auth `5f54b024…de7`.
Versioned ledger и schema fingerprint не изменились. Это доказывает отсутствие
фактического drift на полной текущей схеме, но одновременно доказывает, что
auth/orders продолжают исполнять DDL SQL в runtime.

Повторный exact-runtime migration preflight catalog: passed, idempotent=true,
schema parity=true, business snapshot unchanged, quick_check=ok, FK=0.

## Source of Truth Matrix

| Контур | Bootstrap | Runtime ensure | Legacy migration | Versioned ledger | Contract/fingerprint | Фактический source of truth сейчас | Конфликт |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Catalog | `SCHEMA` | 16 DDL ensure + backfill | 4 scripts | Один baseline | Да | production DB + legacy Python, проверенные baseline | Versioned registry ещё не описывает отдельные изменения |
| Comments | `SCHEMA` | comment ensure | нет отдельного script | Только общий baseline | Да, catalog contract | production DB + ensure | Нет отдельного immutable migration ID |
| Inventory | `SCHEMA` | inventory ensure | 2 scripts | Только baseline | Да | production DB + ensure | Legacy scripts остаются executable |
| Sales/receipts/movements | `SCHEMA` | 5 rebuild/ensure paths | unified script | Только baseline | Да | production DB + ensure | Multiple source paths |
| Auth/roles | `AuthStore` SQL | Тот же код | auth script | Нет | Нет | runtime ensure + production DB | DDL при worker/request; нет fail-closed state |
| Orders/customers | `OrdersSnapshotStore` SQL | Тот же код | customers script | Нет | Нет | runtime ensure + production DB | DDL при request/background; fresh SQL drift |
| Repairs | JSON normalization | in-memory migration on read/save | repair script | Нет | `schema_version` в JSON | Python normalization | Не SQLite; version history не immutable registry |
| Settings | JSON/config | Нет DDL | Нет | Нет | Application validation | JSON/config | Вне SQLite migration contour |

Целевой source of truth: immutable per-database migration registry → fresh DB,
upgrade и baseline; contract/fingerprint только проверяют итог; startup/request
только read-only verify.

## Production Schema Drift

Production read-only verification:

- commit `db28de11299351675fad99778b3b26a46ef3ba82`, Git clean;
- service active; Gunicorn master + 2 workers; preload отсутствует;
- catalog: 49 tables, 80 named indexes, 2 triggers, fingerprint
  `7d4d1c7b…3d02`, baseline ledger `applied`, quick_check=ok, FK=0;
- auth: 5 tables/5 named indexes; fresh exact-runtime hash равен production;
- orders: 3 tables/9 named indexes; semantic objects/columns/indexes совпадают,
  но fresh hash отличается от production.

Orders drift ограничен порядком колонок `orders_snapshot`: production получил
`detail_loaded/source/external_order_id/extra_fold/customer_id` через
последовательные `ALTER ADD COLUMN`, fresh bootstrap размещает первые четыре
раньше `payload_json/loaded_at`. Missing tables, columns, indexes или FK не
найдены. Исправлять production rebuild-ом не требуется: PR-A должен сделать
bootstrap каноничным к production order либо контракт должен сознательно
сравнивать семантику, при этом fresh/upgraded parity фиксируется тестом.

`erp_migration_ledger` содержит один baseline; исторический
`erp_schema_migrations` содержит 6 доказанных записей. Они не должны быть
задним числом скопированы в новый ledger.

## Data Safety

Exact-runtime до/после:

| Агрегат | До | После |
| --- | ---: | ---: |
| Товары | 4 718 | 4 718 |
| Сумма активных остатков | 1 131 440 | 1 131 440 |
| Продажи / строки | 30 / 34 | 30 / 34 |
| Движения | 1 310 | 1 310 |
| Заказы / единицы | 50 / 59 | 50 / 59 |
| Клиенты | 50 | 50 |
| Приходы / строки | 0 / 0 | 0 / 0 |
| Комментарии | 4 | 4 |
| Пользователи | 1 | 1 |
| Активные инвентаризации / позиции | 0 / 0 | 0 / 0 |

Repair JSON: 11 записей до/после, не открывался для записи. Во всех трёх DB
quick_check=ok и FK violations=0. Production databases не изменялись.

## Risk Matrix

| Риск | Вероятность | Ущерб | Race/partial | Recovery | Coverage | Перенос |
| --- | --- | --- | --- | --- | --- | --- |
| Auth DDL каждый worker/request | Высокая | Lock/error авторизации, partial user columns | Высокий/средний | Только file backup; ledger нет | Schema tests, новый trace | Средний |
| Orders DDL request/background | Высокая | Lock/error заказов, partial columns/indexes | Высокий/средний | Backup; ledger нет | Existing store tests, новый trace | Средний |
| Catalog guard sentinel lost | Низкая | Большие table rebuilds в workers | Высокий/высокий | Catalog backup + baseline, но runtime fail-open | Migration/guard tests | Высокий |
| Catalog rebuild ensures | Низкая при guard | Data loss/long lock on interruption | Средний/высокий | Deploy rollback backup | Strong rehearsal, weak per-step ledger | Высокий |
| Executable legacy scripts | Средняя (manual) | Обход versioned runner | Низкий–средний | Script-specific | Uneven | Средний |
| Orders fresh column-order drift | Низкая | Test/contract ambiguity | Нет | Bootstrap correction | Новый parity evidence | Низкий |
| Repair JSON runtime normalization | Средняя | Silent shape drift on later write | Lock есть, immutable ledger нет | File backup only in CLI | Repair tests | Средний |

Блокеры до полного перехода: нет multi-database ledger/contract/backup для
auth/orders; catalog guard fail-open при отсутствии sentinel; legacy baseline
слишком крупный и nontransactional; orders bootstrap parity не точная; repair
JSON требует отдельной стратегии, не смешанной с SQLite PR.

## Реализованный подготовительный gate

`scripts/check_runtime_ddl.py` и `runtime-ddl-inventory.json`:

- фиксируют checksum 5 schema containers, 25 функций `ensure_*` и 6 legacy
  migration scripts;
- запрещают новый DDL container в `app/` вне allowlist;
- запрещают новый `_ensure_*` и молчаливое изменение существующего ensure;
- запрещают вызов `apply_migrations` из runtime application code;
- требуют осознанного обновления owner/planned PR при изменении debt;
- запускаются отдельным CI step.

Gate не разрешает долг как норму: checksum не даёт расширить allowlisted block,
а `planned_pr` указывает removal slice. Runtime behavior не изменён.

## План PR-A → PR-G

### PR-A — order comments + orders/customers (P0 first)

- Scope: `_ensure_order_comment_schema`, `OrdersSnapshotStore.initialize`,
  `migrate_customers.py`, comment/customer contracts.
- IDs: catalog `2026-08-26-order-comments-baseline-v1`; orders
  `2026-08-26-orders-customers-baseline-v1`.
- Prerequisite: registry/runner должен поддержать отдельный `orders.db`, его
  `.backup`, ledger, lock, contract and fingerprint; service stopped for apply.
- Before/after: бизнес-колонки неизменны; добавляется только orders ledger;
  orders bootstrap приводится к production column order без production rebuild.
- Runtime: comment ensure удаляется из dispatcher; orders initialize становится
  read-only verifier/cached accessor; HTTP/background DDL=0.
- Partial state: `applying/failed` блокирует startup/deploy; multi-DB failure
  восстанавливает обе проверенные backups, не обещая cross-file transaction.
- Tests: production-shape→current, fresh parity, repeat, interruption, checksum,
  concurrent workers/requests, comments NULL/external uniqueness, customers and
  order payload preservation, exact 3.7.17 no-egress rehearsal.
- Stop: любой schema/aggregate drift, active inventory, unknown state, FK,
  worker DDL или backup mismatch.
- Success: 0 runtime DDL comments/orders; identical semantic and exact canonical
  schema; ledger applied; score +2.

### PR-B — auth and roles

- ID `2026-08-27-auth-roles-baseline-v1` in `auth.db` ledger.
- Move AuthStore bootstrap/columns to runner; constructor becomes read-only;
  cache store per app; archive `migrate_auth_mvp.py`.
- Preserve user/session/token rows and session_version; backup auth WAL safely.
- Tests: active sessions survive, concurrent requests emit 0 DDL, failed
  baseline blocks startup, exact 3.7.17. Score +2.

### PR-C — inventory

- IDs `2026-08-28-inventory-scope-v1` and
  `2026-08-28-inventory-idempotency-v1`.
- Move `_ensure_inventory_constraints`; retire two inventory scripts.
- Active inventory remains deploy blocker; preserve sessions/items/movement
  references. Backup rollback. Score +1.

### PR-D — repairs

- JSON registry IDs for each supported schema transition, beginning
  `2026-08-29-repair-json-baseline-v1`.
- Read path verifies/normalizes without persistence; controlled CLI applies with
  backup/report/lock; archive legacy script. No SQLite change. Score +1.

### PR-E — catalog/taxonomy/import/products

- IDs split by dependency: brand images, shared taxonomy, Excel cardinality,
  import drafts, workflow/images/deletion columns.
- Each migration gets precondition/postcondition and checksum; no single giant
  rebuild PR. Remove corresponding ensure calls only after applied ledger.
- Exact fresh/upgrade parity and catalog aggregates mandatory. Score +2.

### PR-F — orders mappings, sales, receipts, movements and audit

- Separate IDs for mapping rebuild, audit entity rebuild, receipt constraints,
  optional prices/items and movement constraints.
- Service stopped, verified backup, explicit nontransactional recovery for each
  rebuild; preserve all counts/sums/idempotency. Score +2.

### PR-G — final runtime DDL prohibition

- Remove/archival of legacy migration executables and catalog runtime dispatcher;
  fresh DB is migrations-only; startup guard is fail-closed even without marker.
- Empty runtime DDL/legacy ensure allowlist; CI rejects any DDL under runtime
  application directories. Score +2.

## Точный prompt следующего этапа PR-A

```text
ЗАДАЧА: выполни PR-A из docs/runtime-ddl-audit-2026-08-25.md — перенеси
комментарии заказов и orders/customers из runtime DDL в versioned migrations.

Baseline: origin/main после audit/gate PR; production Python 3.6.8, SQLite
3.7.17. Не затрагивай auth, inventory, repairs, catalog taxonomy, sales,
receipts или UI.

1. Добавь безопасный per-database registry/ledger/lock/contract/fingerprint и
backup+rehearsal для instance/orders.db; не обещай cross-file transaction.
2. Зарегистрируй catalog migration
2026-08-26-order-comments-baseline-v1 и orders migration
2026-08-26-orders-customers-baseline-v1 с immutable checksum/state/commit.
3. Baseline обязан проверять фактическую production schema; неизвестное или
частичное состояние fail-closed. Не создавай фиктивные historical entries.
4. Приведи fresh orders bootstrap к подтверждённому production column order без
rebuild production table и без изменения бизнес-данных.
5. После apply убери _ensure_order_comment_schema из runtime dispatcher и
сделай OrdersSnapshotStore.initialize read-only verifier; HTTP, background jobs
и оба Gunicorn workers должны выполнять 0 DDL.
6. Сохрани comment semantics: два local NULL external_id, unique Bitrix ID,
idempotent repeated pull/push. Сохрани все order/customer payloads и counts.
7. Deploy: backup catalog+orders, exact 3.7.17 rehearsal, service stop, apply,
post-check, coordinated rollback обеих DB при любом failure.
8. Тесты: fresh, production-shape upgrade, repeat, partial/interruption,
checksum/unknown state, parallel workers/requests, schema parity, ledger/data
before-after, quick/FK, no-egress, Python 3.6 compile, full backend CI.
9. Отдельный PR; merge/deploy только после зелёного CI и отсутствия активной
инвентаризации. Финально покажи DDL trace=0 для comments/orders runtime paths.
```

## Оценка

До slice: backend 72/100. После audit/gate PR: 73/100 — runtime behavior ещё
содержит P0 debt, но он полностью измерен, закреплён checksum inventory и новый
DDL больше не может попасть в CI незаметно. Целевой результат после PR-G:
ориентировочно 85/100; он зависит от фактических rehearsal и production checks
каждого независимого slice.
