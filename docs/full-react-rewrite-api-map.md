# Проект REST API для полной React-переработки

Версия документа: 2026-07-29. Это контрактный проект, не реализованный API. Он покрывает фактические функции baseline `origin/main@2212988`; новые CRUD-возможности для users/companies/customers/brands не считаются утверждёнными требованиями.

## 1. Общие правила

- Base URL: `/api/v1`; same-origin HTTPS.
- Формат: JSON UTF-8, даты ISO 8601 UTC, деньги и точные количества — decimal strings, не binary float.
- Success: `{"data": ..., "meta": {"request_id": "...", ...}}`.
- Error: `application/problem+json`: `type`, `title`, `status`, `detail`, stable `code`, `field_errors`, `request_id`.
- Авторизация: secure HttpOnly Flask session cookie. Все unsafe methods требуют `X-CSRF-Token`. Token выдаётся session endpoint и не хранится в `localStorage`.
- Roles: `employee`, `admin`; до публикации утверждается permission matrix (`*.read`, `*.write`, `integrations.execute`, `users.admin`, `settings.admin`). Текущее broad employee access нельзя неявно сузить без product approval, но least privilege нужен до external exposure.
- Collection query: `page` (1), `page_size` (20, max 200), repeatable `filter[...]`, `sort=field,-field`, `q`; response meta: `page`, `page_size`, `total`, `pages`. Journals/jobs могут использовать `cursor`/`next_cursor`.
- Mutation concurrency: `version`/`If-Match`; idempotent commands accept `Idempotency-Key`.
- `400` malformed request, `401` no session, `403` permission/CSRF, `404`, `409` state/version/duplicate, `422` field/business validation, `429`, `502/503/504` integration, `500`.
- Lists use stable secondary sort by immutable ID. Empty list is `200`, not `404`.
- Delete defaults to archive/soft-delete where current data/history requires it. Hard delete is a separate admin-only command.
- Sensitive values, raw integration tokens and unredacted remote response bodies are never returned.

## 2. Auth

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /auth/session` | Current user, permissions, CSRF, theme/navigation capabilities | `200 {user,permissions,csrf_token,expires_at}` or `401` | public-with-session; `users` |
| `POST /auth/login` | `{email,password,next?}` | `200 session`; `401 INVALID_CREDENTIALS`, `403 USER_DISABLED`, `429` | public; `users`,`auth_attempts` |
| `POST /auth/logout` | no body, CSRF | `204`; `403 CSRF_INVALID` | authenticated; session |
| `POST /auth/register` | first-admin or invitation `{token?,first_name,last_name,email,password}` | `201 {user}`; `409 FIRST_ADMIN_EXISTS/INVITATION_USED`; `422` | public + CSRF bootstrap; `users`,`invitations` |
| `POST /auth/invitations/validate` | `{token,email?}` | `200 {valid,email,role,expires_at}`; `404/410` | public rate-limited; invitations |
| `POST /auth/password/change` | `{current_password,new_password}` | `204`; `422` | authenticated; users; future parity-safe addition |
| `GET /auth/csrf` | Refresh CSRF without exposing session internals | `200 {csrf_token}` | authenticated |

## 3. Users, roles and companies

Текущий UI реализует invitations, а не user CRUD/role administration. Endpoints с пометкой `FUTURE` нельзя включать без решения по RBAC/tenancy.

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /users/invitations` | `state,page,sort` | paged invitations | admin; invitations |
| `POST /users/invitations` | `{email?,role,expires_in}` | `201 {invitation,one_time_url}` | admin; invitations |
| `DELETE /users/invitations/{id}` | revoke active invitation | `204`; `409 NOT_ACTIVE` | admin; invitations |
| `GET /users` | FUTURE list `q,role,active` | paged redacted users | admin; users |
| `GET /users/{id}` | FUTURE detail | user | self/admin; users |
| `PATCH /users/{id}` | FUTURE `{first_name,last_name,role?,active?,version}` | user; `409` | self subset/admin; users |
| `GET /roles` | Enumerate approved role/permissions | role list | authenticated; static policy |
| `GET /companies/current` | Current 3-field profile, not tenant | `{name,legal_name,address,version}` | employee read; settings store |
| `PATCH /companies/current` | Existing settings form fields | updated profile | settings write; settings store |
| `GET /companies` | FUTURE only if multi-tenancy approved | paged companies | platform admin; future company table |

## 4. Products, brands and categories

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /products` | `q,brand,category,cell,match_status,stock_state,active,sort,page,page_size` | product summaries + facets/totals | product.read; `catalog_excel_products` |
| `POST /products` | `{name,article,brand,category,cell,stock?,image_id?}` | `201 product`; `409 duplicate` | product.write; Excel product + movement if stock |
| `GET /products/{id}` | Full Excel/Bitrix fields, gallery, properties, matches | product detail | product.read; Excel/Bitrix catalog tables |
| `PATCH /products/{id}` | Editable fields + `version` | updated product; `409 VERSION_CONFLICT` | product.write |
| `DELETE /products/{id}` | Current local deactivate/archive | `204`; `409 PRODUCT_REFERENCED` | product.write |
| `POST /products/{id}/restore` | Restore inactive product if current behavior later needs it | product | product.write |
| `PUT /products/{id}/bitrix-match` | `{catalog_product_id,decision,reason?}` | product + audit | product.match; product/match audit |
| `DELETE /products/{id}/bitrix-match` | Explicit unlink | product + audit | product.match |
| `PUT /products/{id}/moysklad-mapping` | `{moysklad_product_id,confirmed}` | mapping; duplicate `409` | integrations.mapping; mapping table |
| `PATCH /products:bulk` | `{ids,changes,version_map}` | per-item/atomic policy result | product.write; products |
| `GET /brands` | `q,active,sort,page`; distinct exact values/counts | brand facets | product.read; derived product fields |
| `GET /brands/{encoded-name}` | Derived brand summary | brand + counts | product.read |
| `PATCH /brands/{encoded-name}` | FUTURE canonical rename/merge only after data policy | job/result | data-admin; classification audit |
| `GET /categories` | `source,parent_id,tree,q,active` | category tree + counts | product.read; categories/product join |
| `GET /categories/{id}` | Category path/children/counts | category | product.read |
| `PUT /products/{id}/categories` | `{primary_category_id,category_ids}` only if approved editor | updated category links | product.write; join table |

## 5. Inventory and warehouse

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /inventory/balances` | `q,product_ids,brand,category,cell,stock_state,as_of?,sort,page` | balances + totals | inventory.read; products/movements |
| `GET /inventory/balances/{product_id}` | Current balance and recent movements | balance | inventory.read |
| `POST /inventory/adjustments` | `{product_id,quantity_after|delta,reason,occurred_at?}` + idempotency | `201 movement,balance`; `409 stock/version` | inventory.write; product/manual operation/movement |
| `GET /inventory/movements` | cursor; `product_id,type,source,user,date_from,date_to` | cursor page | inventory.read; movement + legacy journal adapter |
| `GET /inventory/movements/{id}` | Full audit/detail | movement | inventory.read |
| `GET /warehouse/products` | Compatibility projection of products + stock/cell; same filters | page + facets | warehouse.read |
| `GET /warehouse/products/{id}` | Modal/detail projection | product | warehouse.read |
| `PATCH /warehouse/products/{id}/cell` | `{cell,version}` | product | warehouse.write |
| `PATCH /warehouse/products:bulk` | `{ids,cell?,brand?,category?,...}` | atomic/per-row documented result | warehouse.write |
| `POST /warehouse/products/{id}/archive` | Current archive command | `202/204` | warehouse.write |
| `GET /warehouse/cells` | `q,category,occupied` | cells/counts | warehouse.read; future normalized cell table/legacy JSON |
| `PUT /warehouse/category-cell-mappings/{category-key}` | `{cell}` | mapping | warehouse.write; category-cell mapping |
| `DELETE /warehouse/category-cell-mappings/{category-key}` | remove mapping | `204` | warehouse.write |

## 6. Receipts and imports

Оба текущих receipt subsystem должны быть доступны через один contract с `source_system`/`workflow`, но нельзя смешивать их writes до reconciliation.

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /receipts` | `q,status,workflow,date_from,date_to,sort,page` | receipts + totals | receipt.read; local DB + legacy adapter |
| `POST /receipts` | Manual/catalog receipt `{number?,date,items[],counterparty?,warehouse?,workflow}` | `201 receipt`; integration state | receipt.write; receipt/stock/external refs |
| `GET /receipts/{id}` | Header/items/operations/integration state | receipt detail | receipt.read |
| `PUT /receipts/{id}` | Editable legacy receipt + version/idempotency | receipt; `409 posted/remote conflict` | receipt.write |
| `DELETE /receipts/{id}` | Existing delete semantics; orchestration status | `202 job` or `204`; partial error explicit | receipt.write |
| `POST /receipts/{id}/post` | Post draft/manual receipt once | posted receipt; `409 ALREADY_POSTED` | receipt.write; receipt rows/movements |
| `GET /receipts/{id}/operations` | Stock/external operations | list | receipt.read |
| `POST /imports/receipts/preview` | multipart legacy Excel + parsing options | `202 import job` or bounded sync preview | import.execute |
| `POST /imports/excel-receipts` | multipart xlsx/xlsm; hash dedup | `202 {job_id,draft_id?}`; `409 DUPLICATE_FILE` | import.execute; draft/BLOB metadata |
| `GET /imports/excel-receipts/{draft_id}` | Draft summary and paged rows `status,page,sort` | draft + rows/totals | receipt.read |
| `POST /imports/excel-receipts/{draft_id}/post` | idempotent post command | `202 job`/`201 receipt`; validation errors | receipt.write |
| `DELETE /imports/excel-receipts/{draft_id}` | discard unposted draft per retention policy | `204`; `409 POSTED` | receipt.write |

## 7. Sales

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /sales` | `q,source,status,date_from,date_to,product,brand,category,sort,page` | sales + source/status totals | sale.read; DB + legacy adapters |
| `POST /sales` | `{source:"manual",occurred_at,items[{product_id,quantity,unit_price}],customer?,metadata}` | `201 sale`; insufficient stock `409` | sale.write; sales/items/products/movements |
| `GET /sales/{id}` | Header/items/returns/source metadata | sale | sale.read |
| `PUT /sales/{id}` | Full editable manual sale + version | sale; stock conflict | sale.write |
| `PATCH /sales/{id}` | Source-specific permitted overrides/status/metadata | sale | sale.write; override adapter until migrated |
| `DELETE /sales/{id}` | Current source-specific deletion; stock restoration where applicable | `204`; `409` | sale.write |
| `POST /sales/{id}/returns` | `{items?|"all",reason}` + idempotency | `201 return,sale,balance_changes` | sale.write; items/movements |
| `GET /sales/{id}/movements` | Audit stock effects | movement list | sale.read |
| `GET /sales/sources` | Current source tabs/capabilities | list | sale.read; static/domain config |

## 8. Orders

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /orders` | `q,status,date_from,date_to,sort,page`; backed by Bitrix cache | page + integration freshness | order.read; remote orders/cache |
| `GET /orders/{id}` | Bitrix detail + mappings/writeoff state | order | order.read |
| `PATCH /orders/{id}/status` | `{status,version?}` + idempotency | order/integration receipt; `502/504` | order.write; Bitrix |
| `PUT /orders/{id}/items/{item_key}/product-mapping` | `{product_id}` | mapping | order.write; local mapping |
| `DELETE /orders/{id}/items/{item_key}/product-mapping` | unlink | `204` | order.write |
| `POST /orders/{id}/write-offs` | `{items[{item_key,product_id,quantity}],reason?}` | `202 operation` with per-item state | inventory/order.write; MoySklad + outbox + movements |
| `GET /orders/{id}/write-offs` | Reconciliation/status | operations | order.read |

## 9. Repairs

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /repairs` | `q,status,carrier,date_from,date_to,sort,page` | cases + counts | repair.read; repair store/table |
| `POST /repairs` | case fields + attachment IDs | `201 repair`; `422` | repair.write |
| `GET /repairs/{id}` | Detail/timeline/logistics/attachments | repair | repair.read |
| `PUT /repairs/{id}` | fields + version | repair; `409` | repair.write |
| `DELETE /repairs/{id}` | Existing delete with explicit attachment retention | `204` | repair.write |
| `PATCH /repairs/{id}/status` | `{status,note?,version}` | repair/timeline | repair.write |
| `POST /repairs/{id}/actions` | `{action,payload}` current workflow actions | repair/timeline; `409 INVALID_TRANSITION` | repair.write |
| `POST /repairs/{id}/logistics` | `{carrier,tracking_number?,direction?,date,note?}` | `201 entry` | repair.write |
| `DELETE /repairs/{id}/logistics/{entry_id}` | FUTURE only if current edit policy requires | `204` | repair.write |
| `POST /repairs/{id}/attachments` | multipart upload to quarantine | `201 attachment`; `413/415/422` | repair.write; file metadata/storage |
| `GET /repairs/{id}/attachments/{attachment_id}` | Authorized download | file stream/redirect | repair.read |
| `DELETE /repairs/{id}/attachments/{attachment_id}` | Explicit deletion under retention policy | `204` | repair.write |

## 10. Customers

Текущая система не имеет customer entity: customer fragments находятся в Bitrix orders, sales metadata и repair JSON. До identity/dedup policy разрешён только projection/read.

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /customers/search` | FUTURE projection `q,email,phone,source,page` without automatic merge | source-scoped matches | customer.read; source projections |
| `GET /customers/{source}/{external_id}` | Source-specific customer snapshot | customer + related entity links | customer.read |
| `POST /customers/merge` | FUTURE, only separate approved migration | merge plan/job | data-admin; future customer model |

## 11. Reports

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /reports/sales` | `date/source/status/product/group_by` | paged rows + exact totals | report.read; sales/items |
| `GET /reports/receipts` | date/status/workflow/group filters | rows + totals | report.read; receipts |
| `GET /reports/inventory` | as-of/filter/group | balances/valuation if approved | report.read; products/movements |
| `GET /reports/analytics` | date/source metrics and series | KPIs/series/freshness | report.read; aggregated read models |
| `GET /reports/stock-movements` | cursor/filter | journal rows/totals | report.read |
| `POST /reports/{report}/exports` | `{format:"xlsx"|"pdf",filters,columns,locale}` | `202 export_job` | report.export |

## 12. Files and images

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `POST /files` | multipart, `purpose`, checksum; quarantine/MIME/size scan | `201 file metadata`; `413/415/422` | corresponding write permission |
| `GET /files/{id}` | authorized download or short-lived URL | stream/redirect; `404` hides access | entity read; file metadata |
| `DELETE /files/{id}` | delete unreferenced/retention-permitted file | `204`; `409 REFERENCED` | file.delete |
| `POST /images` | JPEG/PNG/WebP, decode, strip metadata, variants | `201 image + variants` | product.write |
| `GET /images/{id}` | `variant=thumbnail|preview|original` | bytes/redirect + ETag/cache | entity read/public policy |
| `PUT /products/{id}/images` | `{image_ids,primary_image_id,order}` | gallery | product.write; image links |
| `DELETE /products/{id}/images/{image_id}` | unlink/delete by policy | `204` | product.write |
| `GET /images/products/{id}/thumbnail` | Compatibility replacement of current proxy | optimized image/fallback | product.read |

## 13. Settings

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /settings` | Company profile, navigation config, non-secret capabilities | settings + versions | settings.read |
| `PATCH /settings` | Approved fields only | settings | settings.write/admin policy |
| `GET /settings/navigation` | Current item order/visibility/required flags | list | authenticated |
| `PATCH /settings/navigation` | `{items:[{key,visible,order?}],version}` | list; `409` | settings.write |
| `GET /settings/themes` | Supported theme tokens/IDs | list | authenticated |
| `PUT /settings/me/theme` | Optional server preference; current localStorage can remain client-only | preference | authenticated |

## 14. Integrations

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `GET /integrations` | Health/config presence, never secrets | adapters + last sync/failure | integrations.read/admin |
| `GET /integrations/{name}/health` | bounded read-only diagnostic | health/freshness | admin |
| `GET /integrations/{name}/jobs` | cursor/status/type | jobs | integrations.read |
| `POST /integrations/bitrix-catalog/sync-jobs` | `{mode,cursor?,dry_run}` | `202 job` | integrations.execute |
| `POST /integrations/bitrix-orders/refresh-jobs` | bounded refresh | `202 job` | integrations.execute |
| `POST /integrations/moysklad/catalog-mapping-jobs` | `{dry_run,scope}` | `202 job` | integrations.execute |
| `POST /integrations/moysklad/reconciliation-jobs` | read/reconcile plan, apply separate | `202 job` | integrations.execute/data-admin |
| `GET /integrations/jobs/{id}` | progress/counters/errors/redacted log | job | integration read |
| `POST /integrations/jobs/{id}/cancel` | cooperative cancel if safe | job/`409` | integrations.execute |
| `POST /integrations/jobs/{id}/retry` | retry failed idempotent steps | new job | integrations.execute |

## 15. Generic imports and exports

| Method / URL | Назначение; query/body | Ответ / коды | Права; модели |
|---|---|---|---|
| `POST /imports` | multipart + `{type,mode,dry_run}` | `202 import job` | import.execute |
| `GET /imports/{id}` | state, validation summary, downloadable error file | job | import.read |
| `GET /imports/{id}/rows` | `status,page,sort` | paged parsed rows | import.read |
| `POST /imports/{id}/apply` | approved idempotent apply | `202 job`; `409` | import.execute |
| `POST /imports/{id}/cancel` | cancel un-applied job | job | import.execute |
| `POST /exports` | `{type,format,filters,columns}` | `202 export job` | export.execute |
| `GET /exports/{id}` | progress/expiry/checksum | job | export.read |
| `GET /exports/{id}/download` | short-lived authorized artifact | stream/redirect | export.read |
| `DELETE /exports/{id}` | remove artifact under retention rules | `204` | owner/admin |

## 16. State machines and errors that must be contractual

| Domain | Required stable codes |
|---|---|
| Auth | `INVALID_CREDENTIALS`, `USER_DISABLED`, `RATE_LIMITED`, `CSRF_INVALID`, `INVITATION_EXPIRED`, `INVITATION_USED` |
| Products | `PRODUCT_NOT_FOUND`, `DUPLICATE_SOURCE_KEY`, `MAPPING_CONFLICT`, `PRODUCT_REFERENCED`, `VERSION_CONFLICT` |
| Inventory | `INSUFFICIENT_STOCK`, `INVALID_QUANTITY`, `ADJUSTMENT_DUPLICATE`, `STOCK_VERSION_CONFLICT` |
| Sales | `SALE_NOT_EDITABLE`, `SALE_ALREADY_RETURNED`, `RETURN_EXCEEDS_SOLD`, `SALE_SOURCE_UNSUPPORTED` |
| Receipts | `RECEIPT_ALREADY_POSTED`, `DUPLICATE_FILE`, `IMPORT_ROW_INVALID`, `REMOTE_DOCUMENT_CONFLICT` |
| Orders | `BITRIX_UNAVAILABLE`, `ORDER_STATUS_REJECTED`, `WRITEOFF_PARTIAL`, `ORDER_ITEM_UNMAPPED` |
| Repairs | `INVALID_STATUS_TRANSITION`, `ATTACHMENT_TYPE_REJECTED`, `ATTACHMENT_TOO_LARGE`, `TRACKING_ENTRY_INVALID` |
| Integration | `REMOTE_RATE_LIMITED`, `REMOTE_TIMEOUT`, `REMOTE_AUTH_FAILED`, `REMOTE_SCHEMA_CHANGED`, `RECONCILIATION_REQUIRED` |

## 17. Contract and compatibility gates

1. OpenAPI generated/validated in CI; TypeScript client generated or type-checked from the same schema.
2. Every current form action has one endpoint and one service command; no business validation lives only in Zod.
3. Jinja and React use the same `/api/v1` during parallel run where practical.
4. Contract tests cover query/filter/sort/page semantics, decimal/time normalization, permissions, CSRF and error codes.
5. Integration writes require idempotency, outbox state and reconciliation endpoint.
6. Existing page URLs remain UI routes or documented 301/route aliases; `/api/v1` is never shadowed by SPA fallback.
7. Endpoint removal requires version/deprecation window; additive changes remain backward compatible through rollback window.
