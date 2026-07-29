# Матрица функционального соответствия Vechasu / Clock ERP

Baseline: `origin/main@22129883e6eac32c1ead0d0f23f8f0f1f1c2e6e1`, 2026-07-29.

Цель матрицы — не допустить исчезновения даже частично реализованных функций. `CONFIRMED` означает, что поведение найдено в коде; `PARTIAL` используется только как характеристика самой функции, а не уверенности аудита.

Сокращения тестов: `U` — unit/service; `API` — API contract/integration; `E2E` — Playwright; `V` — visual screenshot; `I` — integration fake/sandbox; `D` — data reconciliation. Общие React-компоненты описаны в UI map.

Статус переноса на первом контролируемом этапе: `NOT_STARTED` для каждой
строки этой матрицы. Последняя колонка ниже фиксирует статус аудита, а не
готовность React-реализации. Инфраструктура frontend вынесена за пределы
продуктовой матрицы и имеет статус `TESTED`. По мере переноса строка получает
один из статусов `IN_PROGRESS`, `IMPLEMENTED`, `TESTED`, `VERIFIED` или
`BLOCKED`; статус аудита при этом сохраняется отдельно.

| Текущий раздел / функция | Текущее поведение | Текущие файлы и модели | Будущий API | React-страница / компоненты | Тесты | Критерий полного соответствия | Статус аудита |
|---|---|---|---|---|---|---|---|
| Регистрация первого admin | Многошаговая форма создаёт первого admin только при пустой базе | `auth.py:632-716`; `users` | `POST /api/v1/auth/register` | `RegisterPage`, `FormField`, `PasswordField` | U/API/E2E/V | Те же поля, errors, CSRF, role и success redirect | `CONFIRMED` |
| Регистрация по приглашению | Проверяет token/email/expiry/state, затем создаёт employee/admin | `auth.py:254-509, 632-744`; `users`,`invitations` | invitation validate + register | `RegisterPage`, invitation step | U/API/E2E/V | Одноразовость и expiry атомарны, ошибки эквивалентны | `CONFIRMED` |
| Страница успешной регистрации | Показывает success и переход ко входу | `registration_success.html`, `auth.py:748-754` | client route | `RegistrationSuccessPage` | E2E/V | Визуал и navigation совпадают | `CONFIRMED` |
| Вход | Email/password, rate limit, active check, session, safe next | `auth.py:758-798`; `users`,`auth_attempts` | `POST /api/v1/auth/login` | `LoginPage` | U/API/E2E/V/security | 401/429/disabled/redirect/cookie parity | `CONFIRMED` |
| Выход | CSRF-protected session clear | `auth.py:802-805` | `POST /api/v1/auth/logout` | `UserMenu` | API/E2E | Cookie/session invalidated | `CONFIRMED` |
| Session/current user | `before_request` loads user, protects all non-public routes | `auth.py:880-923` | `GET /auth/session`, `GET /auth/csrf` | `AuthProvider`, route guard | API/E2E | Refresh, expiry, 401/403 and CSRF work | `CONFIRMED` |
| Создание приглашения | Admin создаёт expiring invitation и получает link partial | `auth.py:810-846`, `_employee_invitations.html` | `POST /users/invitations` | `InvitationPanel`, `Modal` | U/API/E2E/V | Admin-only, link shown once, expiry parity | `CONFIRMED` |
| Отзыв приглашения | Admin меняет state active→revoked | `auth.py:851-864` | `DELETE /users/invitations/{id}` | `InvitationTable`, `ConfirmDialog` | U/API/E2E | Idempotent revoke и audit | `CONFIRMED` |
| Пользователи/роли | Только auth records и роли employee/admin; list/edit UI отсутствует | `auth.py:138-177, 548-577` | future `/users`, `/roles` | `UsersPage` только после product decision | security/API | Не заявлять current parity как CRUD | `CONFIRMED: PARTIAL` |
| App shell/sidebar | Desktop fixed/collapsed sidebar, active link, logout | `_sidebar.html`, `sidebar.css/js` | `GET /auth/session`, navigation settings | `AppShell`, `Sidebar` | E2E/V/a11y | 228/72px, state persistence, keyboard parity | `CONFIRMED` |
| Mobile navigation | Bottom bar + «Ещё» dialog/focus trap | `_sidebar.html`, `sidebar.css/js` | navigation settings | `MobileNavigation` | E2E/V/a11y | <=767 px layout/focus/escape parity | `CONFIRMED` |
| Темы | classic, klok-green, bn0024-white, persisted localStorage | `themes.css`, `theme.js`, settings | settings/theme client preference | `ThemeProvider`, `ThemePicker` | U/E2E/V | Все tokens/pages/states совпадают | `CONFIRMED` |
| Настройка навигации | Employee включает/выключает пункты, защищённые остаются | `web.py:11519-11960`, `navigation_settings.json` | `GET/PATCH /settings/navigation` | `NavigationSettings` | U/API/E2E/V | Порядок, visibility, current route fallback | `CONFIRMED` |
| Настройки компании | Хранит 3 строковых поля в JSON; не tenancy | `web.py:11519-11909`, `settings.html` | `GET/PATCH /settings/company-profile` | `CompanySettings` | U/API/E2E | Validation/CSRF/persistence parity | `CONFIRMED: PARTIAL` |
| Главная / список заказов | Читает recent Bitrix orders, summary/filter display, 60s cache | `web.py:350-494`, `orders.html` | `GET /orders` | `OrdersPage`, `DataTable`, filters | I/API/E2E/V/perf | Порядок/статусы/суммы/links эквивалентны | `CONFIRMED` |
| Деталь заказа | По ID загружает Bitrix detail в том же template | `order_page`, `web.py:498-520` | `GET /orders/{id}` | `OrderDetailPage/Drawer` | I/API/E2E/V | Все поля/items/errors/return preserved | `CONFIRMED` |
| Сопоставление товара заказа | Сохраняет order-product mapping JSON | `order_product_map`, `web.py:676-730`; mappings JSON | `PUT /orders/{id}/items/{key}/mapping` | `ProductCombobox`, `MappingAction` | U/API/E2E/I | То же matching и repeat behavior | `CONFIRMED` |
| Списание заказа | Для каждой позиции создаёт MoySklad loss, пишет local journal | `order_stock_writeoff`, `web.py:526-672`; stock JSON | `POST /orders/{id}/write-offs` | `OrderWriteoffDialog` | U/API/I/E2E/idempotency | No duplicate, partial failure visible/recoverable | `CONFIRMED` |
| Статус заказа | POST в Bitrix PHP API | `order_status_update`, `web.py:734-753` | `PATCH /orders/{id}/status` | `OrderStatusSelect` | API/I/E2E/security | Same allowed states/error feedback, no embedded secret | `CONFIRMED` |
| Products live partial | `/products` отдаёт full page либо results partial по XHR | `web.py:11165-11227`, `excel_products*.html`; `catalog_excel_products` | `GET /products` | `ProductsPage`, `DataTable`, `LiveSearch` | U/API/E2E/V/perf | Same query/filter/sort/page/count/facets | `CONFIRMED` |
| Product detail | Показывает Excel/Bitrix/cardinality/gallery/properties | `web.py:11289-11300`, `excel_product_detail.html` | `GET /products/{id}` | `ProductDetailPage`, `ImageGallery` | API/E2E/V | Every field/link/image/status preserved | `CONFIRMED` |
| Product delete/archive | Делает local product inactive | `excel_product_delete`, `web.py:11304-11319` | `DELETE /products/{id}` | `DeleteAction`, `ConfirmDialog` | U/API/E2E/D | Visibility/stock/history constraints preserved | `CONFIRMED` |
| Product match | Ручное сопоставление с Bitrix product | `excel_product_match`, `web.py:11323-11351`; match audit | `PUT /products/{id}/bitrix-match` | `ProductMatchDialog` | U/API/E2E/D | External IDs/cardinality/audit parity | `CONFIRMED` |
| Бренды | Facet/field, normalization and repair; самостоятельного CRUD нет | `brand_values.py`, classification services, product templates | `GET /brands`, future controlled `PATCH` | `BrandCombobox`, filters | U/API/E2E | Exact values/counts and no implicit merge | `CONFIRMED: PARTIAL` |
| Категории | Bitrix tree + Excel text category/facet; no standalone UI | `catalog_categories`, product/warehouse templates | `GET /categories`, mapping endpoints | `CategoryTree`, `CategoryCombobox` | U/API/E2E/D | Paths/parents/facets/counts preserved | `CONFIRMED: PARTIAL` |
| Bitrix catalog list | Server page/search/category/active filters, cards/table | `catalog_page`, `catalog.html`, `CatalogReader` | `GET /integrations/bitrix/catalog-products` | `CatalogPage` | U/API/E2E/V/perf | All fields, URLs, filters and page size | `CONFIRMED` |
| Bitrix catalog detail | Product, offers, properties, prices, images | `catalog_product_page`, `catalog_detail.html` | `GET /integrations/bitrix/catalog-products/{id}` | `CatalogDetailPage` | U/API/E2E/V | Nested order/labels/content sanitizer parity | `CONFIRMED` |
| Catalog import preview | GET вызывает remote fetch/import preview | `web.py:11438-11469`, importer, preview template | `POST /imports/bitrix-catalog/jobs` + status | `ImportPreviewPage`, `JobProgress` | U/API/I/E2E/D | Counts/conflicts/errors parity; no side-effect GET | `CONFIRMED` |
| MoySklad mapping preview | Live read MoySklad + match candidates | `web.py:11482-11504`, mapping service | `POST /integrations/moysklad/mappings/preview` | `CatalogMappingPage` | U/API/I/E2E | Same deterministic candidates/counts | `CONFIRMED` |
| MoySklad mapping confirm | Сохраняет mapping в SQLite | `catalog_mapping_confirm`, `catalog_moysklad_mappings` | `PUT /products/{id}/moysklad-mapping` | `MappingConfirmDialog` | U/API/E2E/D | Unique IDs, confirmation metadata/audit | `CONFIRMED` |
| Warehouse list | Local Excel products, server pagination/facets/search/sort | `warehouse_page`, `warehouse.html`, Excel catalog | `GET /warehouse/products` | `WarehousePage`, `ResponsiveTable` | U/API/E2E/V/perf | Desktop/mobile rows and totals identical | `CONFIRMED` |
| Warehouse filter “in stock” | Boolean server filter and badge/count state | `web.py:1353-1579`, warehouse template, filter tests | query `in_stock=true` | `FilterPanel`, `FilterCountBadge` | U/API/E2E/V | URL/form/badge/results match | `CONFIRMED` |
| Warehouse product JSON | Возвращает local detail for modal | `warehouse_product_detail`, `web.py:1718-1725` | `GET /warehouse/products/{id}` | query hook | API/E2E | Same fields/error 404 | `CONFIRMED` |
| Thumbnail proxy | Получает external image и отдаёт private cached response | `warehouse_product_thumbnail`, `web.py:1729-1758` | `GET /images/products/{id}/thumbnail` | `ProductThumbnail` | I/API/E2E/perf | Type/cache/fallback/authorization parity | `CONFIRMED` |
| Category→cell map | Сохраняет cell для category в JSON | `warehouse_update_category_cell`, `web.py:1764-1794` | `PUT /warehouse/category-cell-mappings/{category}` | `CategoryCellEditor` | U/API/E2E | Same normalization/inheritance behavior | `CONFIRMED` |
| Product cell | Меняет local cell | `warehouse_update_cell`, `web.py:1799-1850` | `PATCH /warehouse/products/{id}/cell` | inline edit | U/API/E2E/D | Stock untouched; audit/error parity | `CONFIRMED` |
| Add warehouse product | Создаёт local product from form/image metadata | `warehouse_add_product`, `web.py:1854-1904` | `POST /products` | `ProductFormDrawer` | U/API/E2E/V | Required fields/defaults/card appears | `CONFIRMED` |
| Edit warehouse product | Меняет local product properties/image | `warehouse_edit_product`, `web.py:1908-1983` | `PATCH /products/{id}` | `ProductFormDrawer` | U/API/E2E/D | Field-level parity, concurrency check | `CONFIRMED` |
| Bulk warehouse edit | Массово обновляет выбранные local products | `warehouse_bulk_edit`, `web.py:2003-2228` | `PATCH /warehouse/products:bulk` | `BulkActions`, `BulkEditDialog` | U/API/E2E/D | Atomic result/per-row errors and selection parity | `CONFIRMED` |
| Manual stock adjustment | Меняет stock и пишет manual operation | `warehouse_update_stock`, `web.py:2444-2567`; stock operation table | `POST /inventory/adjustments` | `StockAdjustmentDialog` | U/API/E2E/D | Before/after/delta/reason/audit exact | `CONFIRMED` |
| Archive warehouse product | Local inactive; old MoySklad code unreachable | `warehouse_archive_product`, `web.py:2571-2650` | `POST /products/{id}/archive` | `ArchiveAction` | U/API/E2E | Only actual current local behavior migrated | `CONFIRMED` |
| Warehouse XLSX export | Sync server export current filters up to 100k | `web.py:1608-1636` | `POST /exports/warehouse` | `ReportExport` | API/E2E/file/perf | Columns/order/filter/content checksum parity | `CONFIRMED` |
| Warehouse PDF export | Landscape reportlab export current filters | `web.py:1640-1714` | same job format `pdf` | `ReportExport` | API/E2E/file/V | Cyrillic/font/layout/totals parity | `CONFIRMED` |
| Warehouse map/cells | Cells stored and displayed/filterable; no distinct visual map route | `warehouse_cells.json`, warehouse template | `/warehouse/cells` | `WarehouseCellMap` if retained | U/API/E2E/V | Existing cell interactions retained; no invented scope | `CONFIRMED: PARTIAL` |
| Stock journal | Shows combined stock operations with filters | `stock_operations_page`, `stock_operations.html`, JSON/DB movements | `GET /inventory/movements` | `StockMovementsPage` | API/E2E/V/perf | Order/source/amount filters and history parity | `CONFIRMED` |
| Manual sale create | Form selects products, validates stock, creates sale/decrement | `manual_sale_add`, `SalesInventory`; sales tables | `POST /sales` | `SaleFormDrawer`, `ProductCombobox` | U/API/E2E/D | Atomic stock/sale/movement and totals | `CONFIRMED` |
| Manual sale edit | Rebalances stock/items and updates metadata/legacy paths | `manual_sale_update`, `web.py:5606-5836` | `PUT /sales/{id}` | `SaleFormDrawer` | U/API/E2E/D | Same restrictions, deltas, totals | `CONFIRMED` |
| Manual sale delete | Removes JSON manual sale | `manual_sale_delete`, `web.py:5840-5887` | `DELETE /sales/{id}` | `DeleteAction` | U/API/E2E/D | Source-specific behavior preserved then unified safely | `CONFIRMED` |
| Sale status | Override status for report record | `sale_status_update`, JSON overrides | `PATCH /sales/{id}/status` | `StatusSelect` | U/API/E2E | Same source/reference resolution | `CONFIRMED` |
| Automatic sale edit | Overrides derived automatic sale fields | `automatic_sale_update`, JSON overrides | `PATCH /sales/{id}` | `SaleFormDrawer` | U/API/E2E/D | Derived source remains traceable | `CONFIRMED` |
| Catalog sale delete | Atomic local sale deletion/stock restore | `sale_delete`, `SalesInventory` | `DELETE /sales/{id}` | `DeleteAction` | U/API/E2E/D | Inventory invariant and audit exact | `CONFIRMED` |
| Sale return | Full return to stock with reason | `sale_return`, `SalesInventory` | `POST /sales/{id}/returns` | `ReturnDialog` | U/API/E2E/D | Idempotent, quantity/stock/movement exact | `CONFIRMED` |
| Sales list/source tabs | Merges catalog sales, manual JSON and automatic stock-derived rows | `sales_page`, `build_sales_report_records`, `sales.html` | `GET /sales` | `SalesPage`, `SourceTabs`, table/cards | U/API/E2E/V/D/perf | Counts/totals/sort/filters across all sources | `CONFIRMED` |
| Sales filters/calendar | Period picker, live filters and column preferences | `sales.html`, `period-picker.js`, erp CSS | sales query | `DateRangePicker`, `FilterPanel`, `ColumnSettings` | E2E/V | State, presets, URL and persistence parity | `CONFIRMED` |
| Sales report HTML | Summary/table for filtered merged records | `sales_report_page`, `sales_report.html` | `GET /reports/sales/preview` | `SalesReportPage` | API/E2E/V/D | Same grouping/totals/rounding | `CONFIRMED` |
| Sales XLSX | Sync spreadsheet with report columns | `sales_report_excel`, `web.py:7219-7404` | `POST /exports/sales` | `ReportExport` | U/API/file/D | Sheet/header/rows/totals parity | `CONFIRMED` |
| Sales PDF | Sync A3 landscape PDF | `sales_report_pdf`, `web.py:7408-7700` | export job format PDF | `ReportExport` | API/file/V/D | Font/layout/columns/totals parity | `CONFIRMED` |
| Legacy receipt list | Reads stored receipt records and external warehouse items | `receipts_page`, `receipts.html` | `GET /receipts?system=legacy` | `ReceiptsPage` | API/I/E2E/V | Source/status/rows/actions preserved | `CONFIRMED` |
| Legacy receipt report | HTML report with filters/totals | `receipts_report`, `receipts_report.html` | `GET /reports/receipts/preview` | `ReceiptsReportPage` | API/E2E/V/D | Same filters/groups/totals | `CONFIRMED` |
| Receipt catalog create | Creates receipt from local catalog flow | `receipt_catalog_create`, `web.py:8367-8621` | `POST /receipts` | `ReceiptFormPage` | U/API/E2E/D | Exact stock and receipt effects | `CONFIRMED` |
| Legacy Excel preview | Parses upload, maps columns/products, max 5000 rows | `receipts_import_preview`, `excel_receipt_import` helpers | `POST /imports/receipts/preview` | `ReceiptImportPage`, `FileUploader` | U/API/E2E/file/perf | All validations/warnings/matches identical | `CONFIRMED` |
| Legacy receipt create | Sequential MoySklad enter + local record/journal | `receipt_create`, `web.py:9557-10378` | `POST /receipts` + integration job | `ReceiptFormPage` | U/API/I/E2E/idempotency/D | No silent partial state; same business result | `CONFIRMED` |
| Legacy receipt update | Updates MoySklad enter and local data | `receipt_update`, `web.py:10383-10721` | `PUT /receipts/{id}` | `ReceiptFormPage` | U/API/I/E2E/D | Atomic orchestration/reconciliation | `CONFIRMED` |
| Legacy receipt delete | Deletes external enter and local data | `receipt_delete`, `web.py:10725-10823` | `DELETE /receipts/{id}` | `ConfirmDialog` | U/API/I/E2E/D | Permission/confirmation/partial failure handled | `CONFIRMED` |
| New Excel receipt upload | Upload page and parser preview stored as draft/BLOB | `excel_receipt_new/preview`, templates; draft tables | `POST /imports/excel-receipts` | `ExcelReceiptUploadPage` | U/API/E2E/V/file | Hash dedup, parser counts/errors exact | `CONFIRMED` |
| Excel receipt draft | Shows valid/error/excluded rows and matches | `excel_receipt_draft_page`, preview template | `GET /imports/excel-receipts/{id}` | `ExcelReceiptPreviewPage` | API/E2E/V | Tables/totals/actions/status parity | `CONFIRMED` |
| Post Excel receipt | Atomic creation/match + stock increment + audit | `excel_receipt_post`, service; receipt/stock tables | `POST /imports/excel-receipts/{id}/post` | post action/progress | U/API/E2E/D/idempotency | Same products/quantities/history; one post only | `CONFIRMED` |
| Excel receipt detail | Shows posted receipt and rows | `excel_receipt_page`, detail template | `GET /receipts/{id}` | `ReceiptDetailPage` | API/E2E/V | All rows/matches/stocks/totals preserved | `CONFIRMED` |
| Repair list/workspace | Filters cases, summary, desktop table/mobile cards | `repair_page`, `repair.html`, `RepairCaseStore` | `GET /repairs` | `RepairsPage`, responsive table/cards | U/API/E2E/V | Ordering/filter counts/empty states parity | `CONFIRMED` |
| Repair create | Validates customer/device/status/dates, saves attachments | `repair_add`, `web.py:3628-3716` | `POST /repairs` | `RepairFormDrawer`, upload | U/API/E2E/V/security | Fields/defaults/attachments/audit parity | `CONFIRMED` |
| Repair update | Updates case fields | `repair_update`, `web.py:3720-3759` | `PUT /repairs/{id}` | `RepairFormDrawer` | U/API/E2E | Concurrency and current behavior parity | `CONFIRMED` |
| Repair status/action | Status and workflow action transitions | `repair_status/action`, `web.py:3797-3943` | `PATCH /repairs/{id}/status`, `/actions` | status/action menus | U/API/E2E | Allowed transitions/timestamps/messages exact | `CONFIRMED` |
| Repair logistics | Adds carrier/tracking timeline entry | `repair_logistics_add`, `web.py:3947-4007` | `POST /repairs/{id}/logistics` | `LogisticsForm` | U/API/E2E | Free-text carrier incl. CDEK preserved | `CONFIRMED` |
| Repair attachments | Save/list/download up to limits | `web.py:3400-3507,4044-4078`; local files | `/repairs/{id}/attachments`, `/files/{id}` | `FileUploader`, attachment list | U/API/E2E/security | Auth, filename, membership, limits, download parity | `CONFIRMED` |
| Repair delete | Deletes case via JSON store | `repair_delete`, `web.py:4011-4040` | `DELETE /repairs/{id}` | `ConfirmDialog` | U/API/E2E/D | Attachment retention/deletion decision explicit | `CONFIRMED` |
| Analytics | Aggregates sales/receipts/warehouse in Python | `analytics_page`, helpers, `analytics.html` | `GET /reports/analytics` | `AnalyticsPage`, KPI/cards/tables | U/API/E2E/V/D/perf | Date/source totals and rounding exact | `CONFIRMED` |
| Customers | Data embedded in orders/sales/repairs; no entity/CRUD | templates/JSON/Bitrix payloads | future `GET /customers` only after identity rules | `CustomerSummary` | D/privacy/API | No false merge; embedded display preserved | `CONFIRMED: PARTIAL` |
| Companies | Three settings strings; no FK/isolation | settings JSON/template | company profile; tenancy TBD | `CompanySettings` | security/D | Current label preserved; no claimed tenancy | `CONFIRMED: PARTIAL` |
| Sources of sales | Source tabs and source-specific edit semantics | `sales.html`, report builders, JSON/DB | `/sales?source=...`, source metadata | `SourceTabs`, source badge | U/API/E2E/V/D | Exact counts/labels/actions per source | `CONFIRMED` |
| Images/gallery | Bitrix URLs, MoySklad image upload/proxy, local metadata | clients, catalog/excel templates, web upload | `/images`, product image endpoints | `ImageUploader`, `ImageGallery` | API/I/E2E/V/security | Order, primary/fallback/MIME/dimensions parity | `CONFIRMED` |
| Excel exports | Warehouse/sales generated in request | `web.py:1608-1636,7219-7404` | `/exports` jobs | `ReportExport` | file/API/D | Current content plus async delivery | `CONFIRMED` |
| PDF exports | Warehouse/sales reportlab | `web.py:1640-1714,7408-7700` | `/exports` jobs | `ReportExport` | file/V/D | Cyrillic, layout and totals parity | `CONFIRMED` |
| CDEK | Только строка перевозчика/доставки; API отсутствует | repair/order fields | none until approved | existing text field | E2E | Current free-text behavior remains | `CONFIRMED: ABSENT INTEGRATION` |
| Email | User/customer email data, отправки нет | auth/orders/repair | none until approved | email `FormField` | U/E2E/privacy | Storage/display validation only | `CONFIRMED: ABSENT INTEGRATION` |
| Bitrix catalog CLI | Dry-run/import/sync/data quality/reconciliation | clients/services/scripts | integration jobs/admin endpoints optional | `IntegrationJobsPage` | U/I/D | Same counts/cursors/conflicts; no accidental writes | `CONFIRMED` |
| Data repair CLI | Numeric brand/classification/legacy repair tools | services/scripts/audit tables | internal admin jobs, not public API | optional admin job UI | U/D/rollback | Dry-run, audit and reversible application | `CONFIRMED` |
| Deploy/backup/health | Script backup + code update + restart + checks | `scripts/deploy.sh` | operational, not app API | none | staging drill | Existing safety plus tested restore/atomic releases | `CONFIRMED` |

## Coverage gate

Новая реализация считается функционально полной только когда каждая строка:

1. имеет утверждённый API contract или явную пометку «frontend-only/absent by design»;
2. имеет characterization fixture текущего поведения;
3. прошла U/API/E2E/D/V проверки, перечисленные в строке;
4. имеет owner и rollback flag;
5. не использует параллельный write path к той же сущности;
6. подтверждена владельцем продукта на обезличенном production-like dataset.
