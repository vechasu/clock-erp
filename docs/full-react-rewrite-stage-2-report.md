# Stage 2 — товары, приходы и продажи

Дата завершения: 2026-07-30

Ветка: `feature/full-react-rewrite`

База этапа: `5908ed0`

## Границы этапа

Перенесены только три согласованных модуля: товары, приходы и продажи. React работает параллельно на `/app/products`, `/app/receipts` и `/app/sales`. Flask/Jinja-страницы `/warehouse`, `/receipts` и `/sales` не изменены и не заменены. Production, deploy, база данных и реальные операции Bitrix/МойСклад не затрагивались.

## Товары

- серверные поиск, фильтры, сортировка, пагинация, фасеты и сводные показатели;
- создание, просмотр, редактирование и безопасное архивирование карточек;
- основное изображение и просмотр галереи;
- создание бренда и категории без изменения схемы БД;
- массовое изменение бренда, категории и ячейки до 200 выбранных товаров;
- сохранение видимости, порядка и ширины столбцов в `localStorage`;
- desktop-таблица и отдельные мобильные карточки.

API: `GET|POST /api/products`, `GET|PATCH|DELETE /api/products/{id}`, `PATCH /api/products/bulk`, `GET|POST /api/brands`, `GET|POST /api/categories` и соответствующие `/api/v1` aliases.

## Приходы

- серверные поиск, фильтры, сортировка и пагинация;
- создание прихода из нескольких позиций;
- каскад Бренд → Категория → Товар;
- переход к созданию нового товара или справочника;
- редактирование однопозиционного прихода и удаление с сохранением remote-first семантики МоегоСклада;
- JPEG/PNG до 3 МБ для единственной позиции;
- суммы, количество, статусы, desktop и mobile представления.

API: `GET|POST /api/receipts`, `GET|PATCH|DELETE /api/receipts/{id}`, `GET /api/receipts/catalog` и `/api/v1` aliases.

## Продажи

- вкладки источников, серверные поиск, фильтры, сортировка и пагинация;
- ручная продажа с атомарным изменением остатка;
- редактирование разрешённых полей ручных и автоматических продаж;
- source-specific удаление и возврат проведённой продажи;
- серверная проверка доступного остатка;
- каскадные страна, регион и город для Tictactoy;
- суммы, статусы, desktop и mobile представления.

API: `GET|POST /api/sales`, `GET|PATCH|DELETE /api/sales/{id}`, `POST /api/sales/{id}/returns`, `GET /api/sales/catalog`, `GET /api/sales/sources`, `GET /api/sales/locations` и `/api/v1` aliases.

## Общие компоненты

Используются единые `AppShell`, `DataTable`, `TablePagination`, `Modal`, `ConfirmDialog`, `Toast`, `PageState`, `ImageUploader`, `LiveSearch`, `FilterPanel`, `DateRangePicker`, `BrandSelect`, `CategorySelect`, `ProductSelect`, `EntitySelect`, `ActionMenu`, `EditButton` и `DeleteButton`. Формы построены на React Hook Form + Zod, серверное состояние — TanStack Query, таблицы — TanStack Table.

## Производительность

Добавлены отдельные backend-тесты для 10 000 товаров и 100 000 продаж. Каждый endpoint обязан вернуть страницу из 50 записей менее чем за 5 секунд. Локально оба теста вместе с генерацией фикстур заняли 2,159 с; полный процесс — 2,54 с. UI не загружает полные коллекции и запрашивает только текущую серверную страницу.

## Проверки

- Python: 109 связанных regression/API тестов — успешно;
- крупные наборы: 10 000 товаров и 100 000 продаж — успешно;
- frontend unit: 5 файлов, 6 тестов — успешно;
- Playwright E2E: 36 сценариев на 1440×900, 1024×768, 768×1024 и 390×844 — успешно;
- `pnpm typecheck` — успешно;
- `pnpm lint` — успешно;
- production build: 183 модуля, JS 485,93 kB / gzip 142,58 kB — успешно;
- `git diff --check` и Python compile — успешно;
- внешние записи Bitrix/МойСклад не выполнялись; интеграции в тестах подменены.

## Зафиксированные основные версии

- React `19.2.8`, React DOM `19.2.8`;
- TypeScript `6.0.3`, Vite `8.1.5`;
- React Router DOM `7.18.2`;
- TanStack Query `5.101.4`, TanStack Table `8.21.3`;
- React Hook Form `7.83.0`, Zod `4.4.3`;
- Vitest `4.1.10`, Playwright `1.62.0`;
- ESLint `9.39.5`, Prettier `3.9.6`;
- pnpm `11.9.0`; lockfile сохранён в `frontend/pnpm-lock.yaml`.

## Аудит зависимостей

`pnpm audit` обнаружил 2 high:

1. `react-router` `>=7.12.0 <8.3.0`, GHSA-qwww-vcr4-c8h2. Advisory относится только к unstable RSC API; Stage 2 их не использует. Исправление требует перехода на React Router `8.3.0`, поэтому потенциально ломающий major upgrade автоматически не применялся.
2. транзитивный `brace-expansion <=5.0.7`, GHSA-mh99-v99m-4gvg, приходит через ESLint/minimatch и относится к dev toolchain. Исправление доступно в `5.0.8`; force override автоматически не применялся.

`pnpm audit --fix --force` не запускался. Перед merge нужен отдельный dependency-upgrade PR с повторным полным набором проверок.

## Снимки до и после

| Модуль | Jinja до | React после |
|---|---|---|
| Товары | [before-products.png](screenshots/stage-2/before-products.png) | [after-products.png](screenshots/stage-2/after-products.png) |
| Приходы | [before-receipts.png](screenshots/stage-2/before-receipts.png) | [after-receipts.png](screenshots/stage-2/after-receipts.png) |
| Продажи | [before-sales.png](screenshots/stage-2/before-sales.png) | [after-sales.png](screenshots/stage-2/after-sales.png) |

Снимки сделаны на одинаковых локальных фикстурах. Для воспроизводимости используется `tests/stage2_preview_server.py`; он создаёт временную БД, не читает production-секреты и не пишет в `instance/`.

## Ограничения и откат

- React-маршруты не подключены к рабочим Flask URL и не входят в production deploy.
- Продажи пока агрегируют существующие источники в памяти; проверка 100 000 записей проходит установленный лимит, но нормализация в отдельное хранилище остаётся будущим архитектурным этапом.
- Jinja остаётся полным fallback. Откат Stage 2 — убрать `/app/*` сборку и новые `/api/*` маршруты, не затрагивая текущие страницы и данные.
