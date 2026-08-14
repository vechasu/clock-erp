# Интеграционные границы

## Matrix

| Integration | Status / purpose / direction | Entry and launch | IDs and source of truth | Resilience, errors, logs, tests | ERP transaction effect |
| --- | --- | --- | --- | --- | --- |
| Bitrix catalog | `current`, read catalog; image mutations exist | Manual Flask CLI/scripts and synchronous page/API calls ([client](../../app/clients/bitrix_catalog.py), [scripts](../../scripts)) | Bitrix product/category/property/offer/file IDs mapped into local catalog tables; Bitrix owns exported payload, SQLite is projection ([importer](../../app/services/bitrix_catalog_importer.py)) | HTTPS GET, connect/read timeout, configurable retries/backoff for transient statuses; sanitized logger/errors; mocked tests in [`test_sync_bitrix_catalog.py`](../../tests/test_sync_bitrix_catalog.py) and [`test_bitrix_product_gallery_integration.py`](../../tests/test_bitrix_product_gallery_integration.py). Image POST has timeout and structured errors, no retry | Import uses local transactions, but remote image write and local metadata are not one transaction |
| Bitrix orders/status | `partial`; read latest/detail, write status | Synchronous `/orders`, repair order lookup and order status route; dry-run script ([orders client](../../app/clients/bitrix_orders.py), [route](../../app/web.py)) | Bitrix order/product IDs; no durable local order aggregate | Read client: HTTPS, timeout, retries/Retry-After, safe errors, mocked [`test_bitrix_orders.py`](../../tests/test_bitrix_orders.py). Status writer: timeout only, broad error handling, no idempotency/retry; hardcoded secret-like token is a critical boundary violation | Remote status has no local transaction. Order stock writeoff can partially apply several МойСклад documents |
| МойСклад | `current`; read/update products/images/cells, create/update/delete stock documents | Synchronous routes and manual scripts ([client](../../app/clients/moysklad.py), [uses](../../app/web.py)) | MoySklad UUIDs mapped to local products; ownership differs by use case and is not formally decided | Fixed 8s timeout; no common retry policy; GET/PUT often print and return `None`, POST raises; delete 404 is idempotent success. Tests use fakes/mocks, e.g. [`test_moysklad_catalog_mapping.py`](../../tests/test_moysklad_catalog_mapping.py) and [`test_moysklad_receipt_parallelism.py`](../../tests/test_moysklad_receipt_parallelism.py) | External operations cannot commit/rollback with SQLite or JSON; partial-save compensation is path-specific |
| Wildberries | `partial` business source, integration `planned/unknown` | Sales forms/API accept source label; no client/sync/schedule found ([sales service](../../app/services/sales_inventory.py), [template](../../app/templates/sales.html)) | External order/sticker strings only; external truth unknown | No retries/timeouts/logging because no adapter. Source behavior tested in [`test_sales_inventory.py`](../../tests/test_sales_inventory.py) and [`test_stage2_sales_api.py`](../../tests/test_stage2_sales_api.py) | Local sale transaction only; no external transaction |
| Amazon | `partial` business source, integration `planned/unknown` | Sales forms/API accept source/country/platform; no client/sync/schedule found ([route](../../app/web.py), [template](../../app/templates/sales.html)) | External order/country/platform strings; external truth unknown | No adapter resilience. Contract tests in [`test_stage2_sales_api.py`](../../tests/test_stage2_sales_api.py) and filter tests | Local sale transaction only |
| СДЭК | `planned/unknown` integration; currently free-text/enum delivery method/carrier | Repairs and sales payload fields, no API client ([repair rules](../../app/services/repair_cases.py), [sales template](../../app/templates/sales.html)) | Waybill/carrier text; ownership unknown | No API, retry, timeout or integration tests; field behavior covered in [`test_repairs_full_cycle.py`](../../tests/test_repairs_full_cycle.py) and [`test_sales_receipts_enhancements.py`](../../tests/test_sales_receipts_enhancements.py) | No external effect |
| Email/notifications/SMS | SMTP email is `current` for auth; SMS is `planned/unknown`; browser toasts are local UI, not external messaging | Auth registration/reset invokes configured SMTP; no background queue ([auth](../../app/auth.py)); notifications are browser JS ([notifications](../../app/static/js/notifications.js)) | Auth tokens/users local; email delivery provider truth outside ERP | SMTP handling is synchronous/path-specific; auth tests mock delivery ([`test_auth_registration.py`](../../tests/test_auth_registration.py)). No SMS adapter found | User/token transaction and email delivery are separate; failed delivery cannot be atomically rolled back with DB |

## Boundary findings

1. Integration construction is not centralized: handlers instantiate clients,
   making policy and testing uneven ([`app/web.py`](../../app/web.py)).
2. Read-only Bitrix clients have the clearest contract (HTTPS, timeout, retry,
   safe error). This should be the minimum adapter standard
   ([`bitrix_orders.py`](../../app/clients/bitrix_orders.py),
   [`bitrix_catalog.py`](../../app/clients/bitrix_catalog.py)).
3. Write idempotency is not expressed by a common operation key/outbox. A local
   SQLite commit cannot establish exactly-once execution remotely.
4. No scheduler/worker definition is present; actual cron/systemd schedules are
   `unknown`, not “none” ([scripts](../../scripts), [current state](current-state.md#запуск-и-фоновые-задачи)).
5. Logs are a mix of Python logging, `print`, exceptions and UI messages. Remote
   response bodies can reach diagnostics; a common redaction/error model is
   absent ([clients](../../app/clients), [`app/web.py`](../../app/web.py)).
