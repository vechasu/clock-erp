# Frontend-границы

## Фактическая роль технологий

| Technology | Role now | Evidence |
| --- | --- | --- |
| Flask/Jinja | Production-facing server rendering for products, sales, receipts, repairs, journal, settings, reports and auth | Routes in [`app/web.py`](../../app/web.py), Blueprint in [`app/auth.py`](../../app/auth.py), templates in [`app/templates`](../../app/templates) |
| Vanilla JavaScript | Page behavior, forms/modals, fetch API, query state, notifications, navigation, combobox and period control | Large inline scripts in [`warehouse.html`](../../app/templates/warehouse.html), [`sales.html`](../../app/templates/sales.html), [`receipts.html`](../../app/templates/receipts.html); shared [`app/static/js`](../../app/static/js) |
| CSS/design tokens | Shared theme/component primitives plus substantial page-local styles | [`erp-components.css`](../../app/static/css/erp-components.css), [`themes.css`](../../app/static/css/themes.css), templates |
| TypeScript/Vite | Build marker and tests for base server layout | [`frontend/src/base-layout.ts`](../../frontend/src/base-layout.ts), [`frontend/src/main.ts`](../../frontend/src/main.ts), [`frontend/src/base-layout.test.ts`](../../frontend/src/base-layout.test.ts) |
| React | Not present in package dependencies or `frontend/src`; not a current UI runtime | [`frontend/package.json`](../../frontend/package.json), [`frontend/src`](../../frontend/src) |

React is therefore neither a confirmed production contour nor an active feature
experiment in this baseline. Old `full-react-rewrite-*` documents are historical
proposals/reports and are classified in the [document register](../document-register.md).
Adopting “Jinja + React” as target requires an owner decision; it is not inferred
from the Vite marker.

## Server/client responsibilities

The server owns authentication, authorization/CSRF, validation, filtering,
pagination, domain mutation and view-model construction. Templates render the
initial state. Browser JavaScript manages interactive controls and sends form or
JSON requests; it must not be treated as the authority for inventory rules
([`app/auth.py`](../../app/auth.py), [`app/web.py`](../../app/web.py),
[`app/services/sales_inventory.py`](../../app/services/sales_inventory.py)).

Query parameters are part of working UI contracts: list pages preserve search,
source/status/category/product/date/sort/page state, while journal uses cursor.
Coverage exists in [`tests/test_unified_server_pagination.py`](../../tests/test_unified_server_pagination.py),
[`tests/test_sales_server_filters.py`](../../tests/test_sales_server_filters.py)
and [`tests/test_audit_journal.py`](../../tests/test_audit_journal.py).

The API surface is uneven: several resources expose both `/api/...` and
`/api/v1/...`, settings and category-overviews are v1-only, and form endpoints
remain alongside APIs ([route manifest](../../app/web.py)). There is no OpenAPI
document in the repository. Page scripts consequently know route-specific
payload/error shapes.

## Duplication and size

The three largest templates are warehouse (~9.3k lines), sales (~7.9k) and
receipts (~6.0k); the shared components stylesheet is ~5.8k lines. These are
line-count observations of the baseline, not complexity scores. Modal, filter,
table, feedback and form behavior exists both page-locally and in shared files.
Shared sidebar/theme/notification/combobox/period primitives reduce duplication,
but do not yet form a complete component boundary
([`app/templates`](../../app/templates), [`app/static`](../../app/static)).

## Protected frontend contracts

- base layout/navigation: [`test_base_layout_regression.py`](../../tests/test_base_layout_regression.py), [`frontend/src/base-layout.test.ts`](../../frontend/src/base-layout.test.ts);
- tokens/components/notifications: [`test_erp_design_system_v1.py`](../../tests/test_erp_design_system_v1.py), [`test_global_notifications.py`](../../tests/test_global_notifications.py);
- pagination/filter/query preservation: [`test_unified_server_pagination.py`](../../tests/test_unified_server_pagination.py), [`test_sales_server_filters.py`](../../tests/test_sales_server_filters.py);
- product photos and page contracts: [`test_product_photo_ui.py`](../../tests/test_product_photo_ui.py), [`test_warehouse_product_photos.py`](../../tests/test_warehouse_product_photos.py);
- receipts/sales/repairs API-driven UI contracts: [`test_receipts_ui_regressions.py`](../../tests/test_receipts_ui_regressions.py), [`test_stage2_sales_api.py`](../../tests/test_stage2_sales_api.py), [`test_stage2_repairs_api.py`](../../tests/test_stage2_repairs_api.py).

Browser tests exist for selected interactions, but the current route defects for
`overview`, `orders` and `analytics` show that template existence is not a global
CI invariant ([current state](current-state.md#подтверждённые-дефекты-контрактов)).
