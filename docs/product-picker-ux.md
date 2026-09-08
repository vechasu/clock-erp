# Единый выбор товара: аудит и контракт

База аудита: origin/main 3c87292, затем обновление без конфликтов до 92b3595.
Работа выполнена в отдельном worktree; исходные незакоммиченные изменения не включены.

## Согласованное изменение сценария

Аудит обнаружил, что несохранённая новая поставка предлагала импорт Bitrix,
способный создать ERP-товар через SupplyEngine.resolve_bitrix. ERP-selector
уже был доступен после сохранения черновика. Максим отдельно подтвердил:
убрать Bitrix из интерфейса поставки, сохранить порядок «название/комментарий →
сохранить черновик → добавлять существующие ERP-товары».
Старые backend endpoints не удалены; Excel-импорт не затронут.

## Товары → Bitrix

warehouse.html → searchBitrixProducts → GET /api/v1/bitrix-products/search?q=…
→ api_bitrix_products_search → _bitrix_single_client().search_products(limit=20)
→ read-only Bitrix → _bitrix_single_source_payload → data[] → компактный список.
Минимум два символа либо числовой ID; debounce 300 мс, AbortController и версия запроса.

Выбор → loadBitrixProduct → GET /api/v1/bitrix-products/<id>
→ api_bitrix_product_preview → get_product + BitrixERPProductSync.preview_single
→ чтение Bitrix/локального каталога → duplicate/existing/ambiguous/changes и поля
→ правая карточка, прежние условия действий и выбора справочников.

Подтверждение → submitBitrixImport → POST /api/v1/bitrix-products/<id>/import
с прежним {action, brand_id, category_id} и CSRF → api_bitrix_product_import
→ повторная проверка taxonomy/duplicate, подготовка фото, apply_single, CatalogDatabase
→ прежние 201/200 либо validation/duplicate/API error → переход /warehouse с success.
Обновление и открытие доступны по прежним условиям existing/duplicate/ambiguous.
Товары в удалённом Bitrix не создаются; stock из Bitrix не записывается этой операцией.

## Приход → новая поставка

supplies.html → openSupply(): только UI, current=null, items=[], записи в БД ещё нет.
Название и комментарий остаются прежними полями.
«Сохранить черновик» → save() → POST /api/v1/receipts/supplies {title, comment}
→ supplies() → SupplyEngine.create → erp_receipts (draft, прежние ID/number/actor/time).
Затем прежний PATCH /api/v1/receipts/supplies/<id> {title, comment, items}
→ supply() → SupplyEngine.update/_replace → erp_receipt_items → data → openSupply.
Пустой черновик разрешён; stock не меняется.

«Добавить товар» → локальный searchProducts → прежний GET
/api/v1/catalog/options?type=product&limit=200[&q=…] → существующий catalog handler
→ локальный каталог ERP → data[] → pickerProduct и правая карточка.
В supply нет поиска/импорта Bitrix или МойСклад.
Фильтры доступности не добавлены, нулевые остатки не исключены.

Подтверждение → прежний submit handler → POST
/api/v1/receipts/supplies/<current.id>/items {product_id, quantity},
Idempotency-Key + CSRF → add_item() → SupplyEngine.add_item → транзакция текущей
поставки → data → current/items, таблица, итог, сообщение и обновление списка.
SessionStorage сохраняет неопределённый результат с теми же payload/key для повтора.
Количество: integer 1…2147483647, ручной ввод и ±1; backend validation неизменна.
Повторный товар увеличивает количество существующей позиции; UI показывает подтверждение.
Несохранённые правки черновика требуется сохранить перед открытием selector, как раньше.
Удаление и редактирование позиций остаются локальными до прежнего PATCH при сохранении.

«Провести» → прежний save() → POST /api/v1/receipts/supplies/<id>/post
→ post() → SupplyEngine.post/ReceiptInventory → движения/остатки/статус/timestamps
→ openSupply(id). Повторное проведение защищено существующим engine.
В проведённой поставке поля и удаление закрыты; добавление разрешено существующим
контрактом и сразу увеличивает stock только на delta, сохраняя старые движения.
GET существующих поставок, snapshots, журнал и исторические данные не изменены.

## Реализация

Общими являются только product-picker.css и product-picker.js: размеры, колонки,
метаданные, фото/fallback, renderResults/preview/highlight. Контроллеры раздельные:
Bitrix остаётся в warehouse.html, supply — в supplies.js.
Список имеет собственный scroll; модалки ограничены viewport. Фото списка 64 px
(56 px на узком экране), выбранного товара 160 px, object-fit:contain.
Название ограничено двумя строками в результатах и переносится в карточке.
Состояние выбранного товара сбрасывается при новом поиске/закрытии; действия защищены
от double submit, запросы — от устаревших ответов. ESC и focus используют существующую
ERP modal shell/нативный dialog соответственно.
После добавления снова включаются quantity inputs таблицы: раньше renderItems() при
busy=true оставлял их disabled до переоткрытия; backend и допустимые количества не менялись.

Изменены frontend: app/templates/{warehouse,supplies}.html,
app/static/js/{supplies,product-picker}.js, app/static/css/{supplies,product-picker}.css.
Production backend, API, сервисы, модели, миграции и конфигурация не изменены.
Изменения Python относятся только к изолированному preview fixture.

## Проверки и ограничения

- 27 supply backend tests: stock, draft/post, повторы, concurrency, rollback, CSRF/roles.
- 73 receipt-related tests; две проверки прежней кнопки Bitrix обновлены под отдельно
  согласованный сценарий ERP и дополнены запретом кнопки Bitrix.
- 26 frontend unit tests.
- 111 Bitrix-related backend tests и 16 single-product import tests, включая taxonomy,
  duplicate, update, stock preservation и rollback. Использован штатный
  scripts/run_backend_tests.py (временные БД, внешняя сеть запрещена).
- 9 Playwright tests: создание/сохранение/редактирование/проведение/повторное открытие,
  существующая проведённая поставка, повторный товар, потеря ответа и refresh,
  отсутствие записей при cancel, оба picker, ошибки, taxonomy и duplicate states,
  защита double submit, устаревшие search/preview ответы.
- Viewport bounds: 1536×960, 1440×900, 390×844, 320×740; исходное тестовое изображение
  1200×1800 не увеличивает строки. Скриншоты: docs/screenshots/product-picker/*.png.
- Скриншоты сняты с реальных шаблонов приложения в локальном Chromium; данные и
  изображение фиктивные, Bitrix замокан. Production-товары/поставки не создавались.
- TypeScript E2E и frontend lint; JS syntax, Python compile, Jinja parse, diff check.
  Локальные зависимости переиспользованы из существующей установки той же версии.
  Общий CI запускается после публикации PR; его результат проверяется отдельно.

Откат: возврат предыдущего релиза штатным deploy/backup процессом; миграции не нужны.
