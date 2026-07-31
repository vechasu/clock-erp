# Аудит производительности MVP-разделов

Дата замера: 31 июля 2026. Область: только «Продажи», «Товары», «Приход» и «Настройки».

## Методика

Замеры выполнены Flask test client в production-конфигурации на одинаковом временном наборе: 4 600 товаров, 20 000 продаж и 20 000 приходов. Внешние интеграции не вызывались. Для каждого сценария записаны три запроса, размер HTTP-ответа и SQLite trace. Повторить замер:

```bash
python scripts/benchmark_mvp_performance.py
```

## До изменений

| Сценарий | Время, мс (три запуска) | Ответ | SQL на запрос |
| --- | ---: | ---: | ---: |
| Товары, первая страница | 35,74 / 30,53 / 30,66 | 240 605 B | 129 (15 SELECT, 104 schema) |
| Товары, поиск | 67,30 / 66,84 / 67,18 | 207 172 B | 129 (15 SELECT, 104 schema) |
| Товары, фильтр + сортировка | 26,22 / 25,22 / 25,27 | 239 612 B | 129 (15 SELECT, 104 schema) |
| Продажи, первая страница | 823,20 / 822,80 / 826,51 | 59 921 B | 364 (22 SELECT, 312 schema) |
| Продажи, поиск | 858,22 / 860,85 / 859,77 | 1 строка | 364 (22 SELECT, 312 schema) |
| Приход, первая страница | 456,98 / 435,42 / 451,94 | 51 187 B | 240 (12 SELECT, 208 schema) |
| Приход, поиск | 412,55 / 407,15 / 427,18 | 1 строка | 240 (12 SELECT, 208 schema) |
| Настройки, HTML | 21,30 / 2,08 / 1,47 | 51 580 B | 0 |

При первом открытии React-страницы делали два API-запроса: текущая страница и ненужный в этот момент справочник брендов.

## Найденные причины

1. Каждый новый service object повторно запускал всю SQLite schema bootstrap: 104 schema-операции в «Товарах», 312 в «Продажах» и 208 в «Приходе».
2. «Продажи» повторно читали те же JSON-файлы, связы товаров и заново строили 20 000 нормализованных записей на каждую страницу и поиск.
3. «Приход» повторно сериализовывал весь JSON-журнал и выполнял связы с каталогом, прежде чем отрезать 50 строк ответа.
4. Products API отдавал в cell facets `GROUP_CONCAT` имён всех 4 600 товаров, хотя React использовал только ячейку и счётчик. Из-за этого ответ достигал 240 KB при 50 строках.
5. Закрытые filters и modal forms монтировали `CatalogCascade` заранее. Это давало лишний brand request и ненужные React render/form calculations.
6. Поиск имел debounce, но fetch не получал `AbortSignal`; поздний старый ответ мог продолжать нагружать сервер и сеть.
7. Мутации ждали полных invalidation/refetch до закрытия формы, хотя сервер уже вернул изменённую строку.
8. «Настройки» не имели медленного SQL/API: задержку давал полный HTML POST/redirect/reload при изменении одного поля.

Классического N+1 «один SELECT на строку» не найдено. Главной повторяющейся нагрузкой была schema bootstrap и повторная нормализация всего журнала.

## Изменения

- `CatalogDatabase` кэширует факт проверки schema между service instances по абсолютному пути, device и inode. Новый файл БД автоматически повторно проверяется.
- Нормализованные sales/receipt records кэшируются по пути, inode, `mtime_ns` и размеру исходных файлов/БД. Любая запись сбрасывает связанный cache. Ключ содержит полный tenant-specific путь, поэтому данные разных хранилищ не смешиваются.
- Большие `IN` для legacy links и products-by-id разбиты на порции по 500, что совместимо с production SQLite 3.7.
- `/api/v1/products`, `/api/v1/sales` и `/api/v1/receipts` возвращают только page slice и meta `page`, `page_size`, `total`, `pages`, `total_pages`. Добавлены обратно совместимые aliases `search`, `sort`, `order`; старые `q`, `sort_by`, `sort_dir` сохранены.
- V1 Products API не передаёт неиспользуемые `item_names` в cell facets. Legacy `/api/products` сохраняет старое поле для обратной совместимости.
- Search debounce унифицирован на 250 ms. React Query `AbortSignal` передаётся в list API и в единый `CatalogCascade`; устаревший fetch реально отменяется.
- Brand/category/product queries имеют разные `staleTime` (5 min / 2 min / 30 s); categories не запашиваются без brand, products — без brand + category. Существующая точечная invalidation после create сохранена.
- Скрытые filters и тяжёлые modal forms не монтируются до первого открытия. Initial API count для каждой из трёх React pages снижен с 2 до 1.
- Строка, отредактированная в «Товарах», «Продажах» или «Приходе», сразу заменяется в query cache. Modal/toast закрываются до background revalidation. Существующие атомарные SQLite transactions, idempotency keys и блокировка submit-кнопки сохранены.
- Настройки и navigation JSON кэшируются по fingerprint. Новый `PATCH /api/v1/settings` валидирует и сохраняет только изменённые поля. Sidebar brand и navigation switches обновляются без page reload; HTML POST/redirect остался fallback.
- Route-level lazy loading уже был корректно включён и сохранён. Skeleton, previous rows во время refetch, локальные errors/retry и button-only pending state уже были реализованы и не заменялись таймерами.
- Excel/PDF для товаров уже формируются отдельными server endpoints и не загружают полный каталог в React. Этот путь не изменялся.

## После изменений

| Сценарий | Холодный, мс | Тёплые, мс | Ответ | SQL: холодный / тёплый | Улучшение |
| --- | ---: | ---: | ---: | ---: | ---: |
| Товары, страница | 32,34 | 20,74 / 20,80 | 45 422 B | 129 / 10 | ответ −81%, schema после warm-up −104 |
| Товары, поиск | 53,76 | 54,16 / 59,51 | 12 685 B | 10 / 10 | ответ −94%, latency около −17% |
| Товары, фильтр + sort | 15,79 | 15,89 / 15,50 | 44 432 B | 10 / 10 | latency около −38% |
| Продажи, страница | 610,13 | 75,90 / 76,77 | 61 592 B | 52 / 0 | холодный −26%, тёплый −91% |
| Продажи, поиск | 116,30 | 117,12 / 116,05 | 1 668 B | 0 / 0 | latency −86% |
| Приход, страница | 330,64 | 35,85 / 36,72 | 53 606 B | 50 / 0 | холодный −27%, тёплый −92% |
| Приход, поиск | 17,43 | 17,35 / 17,25 | 1 446 B | 0 / 0 | latency −96% |
| Настройки, HTML | 20,94 | 1,88 / 1,77 | 53 482 B | 0 / 0 | открытие было быстрым; save/toggle теперь без reload |

Цели для повторного открытия (<200 ms), первой таблицы (<700 ms), поиска (<300 ms после debounce) и фильтра/страницы (<400 ms) достигнуты в production-shaped benchmark. Самый медленный холодный сценарий — 610 ms для 20 000 продаж.

## SQL query plans и индексы

`EXPLAIN QUERY PLAN` подтвердил:

- products page: `idx_catalog_excel_products_listing_name (active=?)`;
- category filter: covering `idx_catalog_excel_products_category_id (category_id, active, id)`;
- receipt legacy links: unique autoindex `(entity_type, entity_id, position_index)`;
- существующие taxonomy/stock/sale/receipt operations уже имеют индексы на `product_id`, `brand_id`, `category_id`, `status + created_at/receipt_date`, внешние ID и barcode.

Новые индексы не добавлялись: планы уже используют нужные индексы, а измеренные задержки были в schema bootstrap, JSON normalization и payload. Добавлять индекс без медленного query plan означало бы ухудшить write path без выигрыша. Schema/data migration не требуется.

## Frontend production build

Production build остался route-split. Размеры изменённых chunks: CatalogCascade 7,32 KB / 2,75 KB gzip; Products 17,06 / 5,67; Receipts 17,93 / 5,85; Sales 24,12 / 6,78. Весь asset set: 599 143 B против 598 069 B до изменений (+1 074 B, +0,18%). Dev server в production не используется: Flask отдаёт hashed Vite assets из `app/static/react`.

## Проверки и риски

Автотесты покрывают pagination/search/sort/combined filters, 10 000 товаров, 100 000 продаж, 20 000 приходов, response bounds, cache reuse/invalidation, atomic sales/receipt inventory operations, idempotency, settings PATCH, shared cascade creation/invalidation, AbortSignal и mobile layout. Multi-tenant HTTP context в текущем Flask слое не реализован; существующая `tenant_id`-scope в inventory транзакциях и тестах не изменялась.

Остаточный риск: первый после process restart запрос продаж/приходов ещё один раз нормализует весь JSON-журнал. При 20 000 строках это 610/331 ms и укладывается в цель. Если журналы вырастут на порядок, следующий обоснованный шаг — миграция legacy JSON в SQLite с server-side indexed filtering, а не новые кэши или UI-анимации.
