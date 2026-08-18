# Продуктовые модули Vechasu ERP

Статус: `current` для commit `975ef2572edfbf3568c5fac430d31f9d79af1d23`.

Это краткая карта функций, найденных в текущем `origin/main`. Она описывает
доступные маршруты и действия, но не подтверждает состояние production или
внешних систем. Главная серверная реализация находится в `app/web.py`; там, где
логика выделена, ниже также указан сервисный модуль.

## Подтверждённые модули

| Модуль | Назначение и статус | URL и основные действия | Код | Целевые тесты | Ограничения |
| --- | --- | --- | --- | --- | --- |
| Заказы | `current`: карточка заказа, подтверждение в Bitrix, каскадное сопоставление с единым каталогом и проведение одной многопозиционной продажи TicTacToy | `/orders`, `/app/orders`, `/order/<id>`; POST для product map, статуса и атомарного проведения продажи с защитой от дублей | `app/web.py`, `app/clients/bitrix_orders.py`, `app/templates/orders.html`, `app/services/shared_catalog.py`, `app/services/sales_inventory.py` | `tests/test_bitrix_orders.py`, `tests/test_internal_orders.py`, `tests/test_order_tictactoy_sale.py`, `tests/test_sales_inventory.py` | Проведение требует сопоставления всех товаров и достаточных агрегированных остатков; источником локального остатка является ERP, а связь с МойСклад дополнительна |
| Товары | `current`: каталог операционных товаров, карточки и изображения | `/warehouse`, `/app/products`; поиск, фильтры, сортировка, просмотр, создание, редактирование, архивирование, изменение остатка; `/products` перенаправляет на `/warehouse` | `app/web.py`, `app/catalog/application.py`, `app/services/excel_product_catalog.py`, `app/templates/warehouse.html` | `tests/test_excel_product_catalog.py`, `tests/test_product_deletion.py`, `tests/test_product_photo_ui.py`, `tests/test_warehouse_product_photos.py` | Часть интеграционных данных приходит из Bitrix и МойСклад; их доступность этим документом не проверялась |
| Бренды | `current`: справочник брендов и связь с товарами/категориями | `/warehouse?view=brands`; создание, переименование, привязка категории, удаление с серверными проверками; JSON API `/api/v1/brands` | `app/web.py`, `app/catalog/application.py`, `app/templates/warehouse_brands.html` | `tests/test_brand_management.py`, `tests/test_catalog_cascade_unification.py` | Удаление может блокироваться связанными товарами; правила подтверждаются сервером, не только формой |
| Категории | `current`: глобальные категории и разрез по брендам | `/warehouse?view=categories`; создание, переименование, связь с брендом, удаление; JSON API `/api/v1/categories` и `/api/v1/category-overviews` | `app/web.py`, `app/catalog/application.py`, `app/services/category_consolidation.py`, `app/templates/warehouse_categories.html` | `tests/test_category_management.py`, `tests/test_category_integrity_repair.py`, `tests/test_category_consolidation.py` | Исторические дубли и миграции требуют отдельных безопасных процедур |
| Остатки | `current`: текущий остаток и журнал движений товара | `/warehouse`, `/stock-operations`, `/api/v1/products/<id>/movements`; ручное оприходование/списание и движения от продаж/приходов | `app/web.py`, `app/services/sales_inventory.py`, `app/services/receipt_inventory.py`, `app/catalog_db.py` | `tests/test_sales_inventory.py`, `tests/test_unified_catalog_inventory.py`, `tests/test_warehouse_initial_stock.py` | Источники данных смешанные; внешние остатки МойСклад не объявляются здесь источником истины |
| Приходы | `current`: создание, редактирование, отмена и просмотр приходов | `/receipts`, `/app/receipts`, `/receipts/report`, `/products/receipts/new`; формы, Excel preview/post, изображения; JSON API `/api/v1/receipts` | `app/web.py`, `app/services/excel_receipt_import.py`, `app/services/receipt_inventory.py`, `app/templates/receipts.html` | `tests/test_excel_receipt_import.py`, `tests/test_receipts_ui_regressions.py`, `tests/test_stage2_receipts_api.py`, `tests/test_receipt_recovery.py` | Несколько исторических и Excel-сценариев сосуществуют; операции записи требуют серверной валидации и backup для миграций |
| Продажи | `current`: ручные и автоматические продажи по каналам | `/sales`, `/app/sales`; создание/редактирование ручной продажи, статус, отмена, удаление, возврат; JSON API `/api/v1/sales` | `app/web.py`, `app/sales_reporting/`, `app/services/sales_inventory.py`, `app/templates/sales.html` | `tests/test_sales_inventory.py`, `tests/test_sales_status_visual_system.py`, `tests/test_sales_server_filters.py`, `tests/test_stage2_sales_api.py` | Каналы и типы продаж имеют различающиеся поля; внешние записи в рамках аудита не выполнялись |
| Возвраты и отмены | `current`: обратные операции по продажам и отмена проведённых операций | `/sales/cancel`, `/sales/return`, `/api/v1/sales/<id>/cancel`, `/api/v1/sales/<id>/returns`; отмена прихода выполняется из модуля приходов | `app/web.py`, `app/services/sales_inventory.py`, `app/services/receipt_inventory.py` | `tests/test_sales_inventory.py`, `tests/test_sales_receipts_enhancements.py`, `tests/test_unified_catalog_inventory.py` | Допустимость зависит от состояния записи; интерфейсное подтверждение не заменяет серверные ограничения |
| Ремонты | `current`: полный цикл ремонтного обращения | `/app/repairs`; создание, редактирование, статусы, действия, архив/восстановление, логистика и вложения; JSON API `/api/v1/repairs`; авторизованный поиск товара `/api/v1/repairs/catalog` начинается с двух символов и возвращает до 20 записей | `app/web.py`, `app/services/repair_cases.py`, `app/services/excel_product_catalog.py`, `app/templates/repair.html` | `tests/test_repairs_full_cycle.py`, `tests/test_stage2_repairs_api.py`, `tests/test_catalog_filtering.py`, `tests/test_legacy_repair_import.py` | Хранилище включает JSON и файлы вложений; импорт legacy-данных является отдельной операцией |
| Журнал | `current`: аудит действий и просмотр событий | `/journal`, `/app/journal`, `/api/v1/journal`; фильтры, cursor-навигация, карточка события | `app/web.py`, `app/services/audit_journal.py`, `app/templates/journal.html` | `tests/test_audit_journal.py` | Полнота журнала зависит от того, вызывает ли конкретный write-path аудит |
| Авторизация | `current`: регистрация, подтверждение, вход, выход, восстановление пароля и приглашения | `/register`, `/verify-email/<token>`, `/login`, `/logout`, `/forgot-password`, `/reset-password/<token>`, `/settings/invitations` | `app/auth.py`, `app/templates/auth_base.html`, `app/templates/login.html`, `app/templates/register.html` | `tests/test_auth_registration.py`, `tests/test_registration_browser.py`, `tests/test_settings_contract.py` | Большинство прикладных маршрутов закрывает общий `before_request`; административная модель ролей ограничена текущими проверками приглашений |
| Аналитика и отчёты | `partial`: sales/receipts reports реализованы; analytics route ссылается на отсутствующий шаблон | `/analytics`, `/sales/report`, `/sales/report.xlsx`, `/sales/report.pdf`, `/receipts/report` | `app/web.py`, `app/sales_reporting/`, `app/templates/sales_report.html`, `app/templates/receipts_report.html` | `tests/test_sales_reporting_contract.py`, `tests/test_sales_kpis.py`, `tests/test_report_receipt_catalog_filters.py` | `analytics.html` отсутствует; production numbers не проверялись |
| Настройки | `current`: название ERP/компании, тема, навигация и приглашения | `/settings`, `/app/settings`, `/api/v1/settings`; чтение и изменение настроек | `app/web.py`, `app/system_settings/`, `app/templates/settings.html` | `tests/test_settings_contract.py`, `tests/test_global_notifications.py` | Настройки хранятся в runtime JSON; часть действий с приглашениями требует admin access |

## Не включено как готовое

- Полная React-переработка из документов 2026-07-29/30 не считается текущим
  продуктовым состоянием.
- PostgreSQL, S3-compatible off-site backup и единый источник истины не
  подтверждены как внедрённые решения.
- Фактический production commit и доступность внешних интеграций — `unknown`.
- `/overview` и `/analytics` ссылаются на отсутствующие в Git
  `overview.html` и `analytics.html`; эти UI нельзя считать
  подтверждённо работающими.

Связанные документы: [архитектура](../architecture/README.md),
[UX](../ux/README.md), [Definition of Done](../quality/definition-of-done.md).

## Заказ Bitrix → продажа TicTacToy

Сопоставление в карточке заказа использует тот же серверный каскад
`SharedCatalog`, что и форма продаж: активный бренд → его категория → активный
товар из `catalog_excel_products`. Поиск выполняется на сервере по названию,
артикулу и штрихкоду; нулевой остаток не скрывает товар. В HTML не встраивается
полный каталог, а выбранная запись восстанавливается запросом по ID.

Связь хранит отдельно стабильный `bitrix_product_id` товара/предложения,
`bitrix_sku_id`, диагностический `bitrix_order_line_id`, внутренние `product_id`,
`brand_id`, `category_id` ERP и необязательный `moysklad_product_id`.
Переименование не ломает связь. Старые записи только с `moysklad_product_id`
сохраняются и разрешаются через единый каталог лишь при однозначном совпадении;
неоднозначные, отсутствующие и архивные связи требуют ручного выбора. Поэтому
разрушающая миграция `product_mappings.json` не нужна.

Подтверждение статуса `A` само остаток не меняет. Отдельное проведение создаёт
одну запись `erp_sales`, несколько `erp_sale_items` и движения по каждой строке
в одной SQLite-транзакции. Количество одинаковых товаров проверяется суммарно,
а снимок названий, идентификаторов, цен и данных заказа сохраняется в продаже.
Источник истины для этой операции — локальный каталог ERP; отдельное списание
МойСклад и legacy JSON-операция не создаются.

Повторное проведение защищено нормализованным источником `tictactoy`, внешним
ID заказа, idempotency key, повторной проверкой внутри `BEGIN IMMEDIATE` и
условным обновлением `stock >= quantity`. Отказ проведённого заказа блокируется:
сотрудник сначала отменяет связанную продажу штатным механизмом, который
возвращает остаток обратным движением и сохраняет историю.
