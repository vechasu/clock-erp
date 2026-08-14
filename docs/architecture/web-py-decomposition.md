# Аудит и разделение `app/web.py`

## Quantitative baseline

AST/statistics for [`app/web.py`](../../app/web.py) at `3158cb7`:

| Metric | Value | Method |
| --- | ---: | --- |
| Physical lines | 17,428 | line count |
| Top-level functions | 363 | Python AST `FunctionDef`/`AsyncFunctionDef` |
| Classes | 0 | Python AST top-level `ClassDef` |
| Import statements / imported names | 43 / 104 | Python AST |
| URL registrations | 143 | 137 decorated rules + 6 `add_url_rule`; aliases count separately |
| Direct `connection.execute` calls | 2 | AST calls in web module; many more raw SQL calls live in services |
| `render_template` / `jsonify` calls | 24 / 36 | AST call count |
| Test files referencing `app.web` | 36 | repository text search in [`tests`](../../tests) |

Counts are reproducible observations, not permanent documentation constants.
Recount whenever the baseline changes.

## Responsibilities and existing seams

The module creates/configures Flask, auth, global services/caches/paths; defines
CLI; handles orders, warehouse/catalog/inventory, repairs, sales, receipts,
analytics, taxonomy, reports, journal, settings and API compatibility aliases;
normalizes form/JSON; builds view-models; reads/writes JSON/files; calls external
clients; and renders Jinja/JSON/redirects ([source](../../app/web.py)).

Only two SQL executions are directly in `web.py` (receipt/batch lookup area), so
“raw SQL in routes” is not the dominant problem. The larger issue is that routes
know concrete service schemas, JSON layouts and external client sequences.
Already extracted seams prove incremental movement is viable:
[`CatalogApplication`](../../app/catalog/application.py),
[`SalesReportingRoutes`](../../app/sales_reporting/routes.py),
[`SettingsRoutes`](../../app/system_settings/routes.py), inventory services and
repair rules/file functions in [`app/services`](../../app/services).

View-model construction is concentrated in helpers around warehouse listings,
repair preparation, sale/report records, receipts, analytics and API serializers.
HTTP parsing, business rules and persistence are especially mixed in order
stock writeoff, warehouse stock/product mutations, repair form/API adapters and
legacy receipt/sales compatibility paths ([`app/web.py`](../../app/web.py)).

Global dependencies include Flask `app`, catalog/application/service instances,
settings/report adapters, in-memory caches, filesystem paths, integration
configuration and clients. Importing the module therefore constructs most of
the system and explains broad test coupling.

## Candidate map

| Area | Current functions/routes (representative, exact names) | Target | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| Repairs | `get/load/save/mutate_repair_cases`, `prepare_repair_case`, `repair_page/add/update/status/action/logistics_add/delete/attachment`, `api_repair_*` | `repairs/routes.py`, `application.py`, `repository.py`, presenters | repair rules, filesystem, Bitrix order query, uploads, auth | medium | 1 |
| Journal | `journal_page`, `api_journal_collection`, `api_journal_event`, response/filter helpers | `journal/routes.py`, `application.py` | `AuditJournal`, auth actor, pagination | low | 2 |
| Catalog read | `warehouse_page`, product detail/image/thumbnail, `catalog_page/product_page`, product/category/brand GET APIs and serializers | `catalog/routes.py`, presenters/query service | catalog app/services, Bitrix/MойСклад images, caches | medium | 3 |
| Settings/report adapters | `settings_page`, `api_settings`, sales report rules already delegated | Complete route factories/registration outside web | existing adapter objects | low | opportunistic |
| Inventory commands | `warehouse_update_stock`, movement endpoints, legacy stock operation helpers | `inventory/routes.py`, application command, adapters | SQLite movements/products, МойСклад, journal | high | 4 |
| Receipts | `receipt_*`, `receipts_page/report`, Excel draft/post and `api_receipt_*` | `receipts/*` | catalog, receipt inventory, imports, МойСклад, JSON recovery | high | 5 |
| Sales | `manual_sale_*`, status/cancel/delete/return, `sales_page`, `api_sale_*`, normalization/view-model helpers | `sales/*` | catalog, inventory, reporting, legacy adapters | high | 6 |
| Orders | `get_orders/get_order`, `order_*`, mapping/history helpers, status HTTP function | `orders/*` + Bitrix/MойСклад ports | two external systems, JSON, broken template | critical | after integration boundary |
| Analytics | `build_analytics_data`, `analytics_page` | read-model service + route | sales/receipt/catalog projections, missing template | medium | after page contract decision |
| App composition/shared | app creation, auth setup, response/CSRF/pagination/navigation helpers, caches | `web/app_factory.py`, shared HTTP | every module during transition | high | last consolidation |

## Test coupling

The 36 files importing or patching `app.web` span audit, auth registration,
layout, Bitrix/gallery/catalog, Excel/receipts, repairs, reports, sales, settings,
APIs, pagination and warehouse. Representative high-coupling suites are
[`test_sales_inventory.py`](../../tests/test_sales_inventory.py),
[`test_unified_catalog_inventory.py`](../../tests/test_unified_catalog_inventory.py),
[`test_stage2_sales_api.py`](../../tests/test_stage2_sales_api.py),
[`test_stage2_receipts_api.py`](../../tests/test_stage2_receipts_api.py) and
[`test_repairs_full_cycle.py`](../../tests/test_repairs_full_cycle.py).
Compatibility re-exports and app-factory fixtures should reduce coupling without
rewriting tests wholesale.

Detailed PR stages, contracts and rollback are in [roadmap](roadmap.md).
