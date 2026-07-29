# Первый контролируемый этап React-переработки

Дата фиксации: 2026-07-29.

Ветка: `feature/full-react-rewrite`.

Исходный baseline: `origin/main@22129883e6eac32c1ead0d0f23f8f0f1f1c2e6e1`
(`Вернуть фильтр товаров в наличии (#110)`).

## Сводка выводов обязательного аудита

1. Фактическая архитектура — синхронный Flask-монолит. `app/web.py`
   объединяет HTTP-маршруты, Jinja-rendering, бизнес-логику, файловые
   хранилища, интеграции и формирование отчётов. Frontend состоит из Jinja,
   inline CSS/JavaScript и общих CSS/JS-компонентов.
2. Данные распределены между двумя SQLite-базами (`catalog.db`, `auth.db`),
   JSON-файлами в `instance/`, файловыми вложениями и внешними сущностями
   Bitrix/МойСклад. Единого источника истины пока нет.
3. ORM отсутствует. Схема управляется прямым `sqlite3`, DDL/`PRAGMA` и
   runtime-изменениями таблиц. PostgreSQL и Alembic отсутствуют.
4. Основные модули: auth, Bitrix orders/catalog, Excel-каталог товаров,
   склад/остатки, продажи, приходы двух разных workflows, ремонт, отчёты,
   аналитика, настройки/navigation и адаптеры МойСклад.
5. Критические риски: секретоподобный токен в коде, 21 unsafe route без
   текущей CSRF-проверки, широкие employee-права, несколько источников истины,
   конкурентные JSON writes, недостижимый legacy-код, неатомарные внешние
   операции и отсутствие проверенного restore production backup.
6. Блокеры: неизвестны production OS/Gunicorn/systemd/Nginx-конфигурация,
   фактическая production schema/counts, RPO/RTO и restore rehearsal; не
   утверждены ownership данных, granular RBAC/tenancy, retention PII/files,
   staging topology и безопасные Bitrix/МойСклад sandboxes.
7. Roadmap задаёт 21 этап: фиксация поведения → screenshots →
   characterization → security containment → API contracts → services →
   repositories/ORM → React infrastructure → design system → shell/auth →
   продуктовые модули → integrations/jobs → PostgreSQL → controlled cutover
   и позднее удаление legacy.
8. Сохранению подлежат все 68 Flask route rules, поля, таблицы, колонки,
   фильтры, сортировка, pagination, live search, CRUD/mass actions,
   imports/exports, изображения, отчёты, роли/session, settings, три темы,
   desktop/mobile behavior и все source-specific бизнес-правила.
9. Без дополнительных тестов нельзя переносить stock/sales/returns,
   оба receipt workflows, order write-off/status, external sync/mapping,
   reports/PDF, repair attachments, auth/session/CSRF, responsive tables,
   combobox/modals и runtime JSON stores.
10. По коду без обращения к пользователю разрешаются вопросы о фактических
    routes, исполняемых final bindings, текущей схеме SQLite, полях и UI
    states, отсутствии самостоятельных CRUD для brands/categories/customers,
    отсутствии CDEK/email API и текущих интеграционных endpoints. Ownership,
    tenancy, retention, SLA, staging и production recovery требуют решения
    владельца/оператора.

## Защита исходного состояния

Основной пользовательский checkout не переключался и не очищался. В момент
старта он находился на `agent/warehouse-filter-badge` и содержал следующие
изменения, которые не включены в текущий этап:

```text
M app/clients/bitrix_catalog.py
M app/services/bitrix_catalog_importer.py
M app/services/bitrix_erp_product_sync.py
M app/services/catalog_reader.py
M app/services/excel_product_catalog.py
M app/services/product_classification.py
M app/templates/_catalog_combobox.html
M app/templates/warehouse.html
M app/web.py
M scripts/sync_bitrix_products.py
M tests/test_bitrix_catalog.py
M tests/test_bitrix_catalog_importer.py
M tests/test_bitrix_erp_product_sync.py
M tests/test_excel_product_catalog.py
M tests/test_warehouse_pagination.py
?? docs/full-react-rewrite-*.md
```

Для переработки создана отдельная worktree от актуального `origin/main`.
Production, `.env`, runtime `instance/`, Bitrix и МойСклад не читались и не
изменялись.

SHA-256 обязательных документов после включения в ветку:

```text
53a573a66b8d6c08a2d2c8f5d365816162d22616a785d571794b4ad010486a24  full-react-rewrite-api-map.md
bb26f0fbe5f3d1798aecc0870ad84eccf7dd04a05b322be5f411a0ece4d87297  full-react-rewrite-audit.md
c7c84b93bc8da24187d303f61dfd2614eda05734e45054721bed577bd4af818b  full-react-rewrite-feature-matrix.md
47d7d11d89cec3221d67c06bd7c659be72610cd8e8aefe718e7b40c783eefe27  full-react-rewrite-risk-register.md
310e613e50fcdfd3370daa57c9521aeb2f25f79f18961eb448eee5e08b9c3f39  full-react-rewrite-roadmap.md
2e5ca18b6aac1d6092aefe5abe837671859f32cf1e71e90b0a4846fe10aea2c8  full-react-rewrite-ui-map.md
```

Feature matrix дополнена явным правилом: все бизнес-функции имеют статус
переноса `NOT_STARTED`; frontend infrastructure имеет статус `TESTED`.

## URL и characterization baseline

Полный machine-readable URL manifest и обязательные legacy templates
зафиксированы в:

```text
tests/fixtures/characterization/legacy_routes.json
```

Карта 19 групп критических сценариев, их текущих routes, существующих test
evidence и статусов находится в:

```text
tests/fixtures/characterization/critical_scenarios.json
```

`tests/test_rewrite_characterization.py` запрещает незаметно удалить или
переименовать текущий route/template, проверяет наличие всех шести audit
documents и валидирует ссылки critical-scenario → существующее test evidence.
Characterization остаётся `IN_PROGRESS`: один URL-manifest не заменяет
будущие state/data/visual fixtures каждого write-flow.

## Baseline тестов backend

Команда:

```text
python -m unittest discover -s tests -v
```

До изменения:

```text
Ran 347 tests
339 passed, 4 skipped, 4 errors
```

Три ошибки были вызваны отсутствующим в локальном Python 3.9 venv пакетом
`reportlab`; одна — запретом socket bind в предыдущем sandbox.

После добавления characterization:

```text
Ran 351 tests
344 passed, 4 skipped, 3 errors
```

Все четыре новых characterization tests прошли. Socket/browser-like test
прошёл в разрешённой среде. Три исходные ошибки `reportlab` сохранились;
регрессии backend не обнаружено. Python-зависимости не устанавливались.

## React/Vite infrastructure

Создан изолированный `frontend/` с каталогами `app`, `api`, `components`,
`features`, `pages`, `hooks`, `types`, `schemas`, `utils`, `styles`, `assets`
и `test`.

Настроены:

- React entry point только для будущего `/app/`;
- React Router с controlled fallback для ещё не перенесённых маршрутов;
- TanStack Query provider;
- типизированный same-origin `/api/v1` client и Zod validation;
- TypeScript project references;
- Vite production build с base `/app/`;
- ESLint, accessibility rules и Prettier;
- Vitest + React Testing Library;
- Playwright для `1440×900`, `1024×768`, `768×1024`, `390×844`;
- CI job для locked install, typecheck, lint, unit tests и build;
- `pnpm-lock.yaml`;
- локальное хранение Playwright Chromium внутри ignored
  `frontend/.playwright-browsers`, без глобальной установки.

Flask не обслуживает `/app/` в этом commit, поэтому рабочие Jinja routes и
production routing не изменены.

## Точные версии основных зависимостей

Runtime:

```text
react 19.2.8
react-dom 19.2.8
react-router-dom 7.18.2
@tanstack/react-query 5.101.4
@tanstack/react-table 8.21.3
react-hook-form 7.83.0
@hookform/resolvers 5.5.7
zod 4.4.3
```

Tooling:

```text
node 24.14.0 (bundled local runtime, system Node не менялся)
pnpm 11.9.0
typescript 6.0.3
vite 8.1.5
@vitejs/plugin-react 6.0.4
vitest 4.1.10
@playwright/test 1.62.0
eslint 9.39.5
typescript-eslint 8.65.0
prettier 3.9.6
```

TypeScript 7.0.2 намеренно не выбран: текущий `typescript-eslint 8.65.0`
объявляет совместимость `<6.1.0`. ESLint 9.39.5 выбран вместо 10.8.0, потому
что текущий `eslint-plugin-jsx-a11y 6.10.2` ещё не объявляет поддержку ESLint
10.

## Проверки frontend

```text
pnpm run typecheck     PASS
pnpm run lint          PASS
pnpm run format:check  PASS
pnpm run test          PASS — 2 files, 3 tests
pnpm run build         PASS — 76 modules, JS 259.02 kB / gzip 82.21 kB
pnpm run test:e2e      PASS — 8 tests, 4 viewports
```

## Аудит зависимостей

`pnpm audit --json` выполнен без `--fix` и без force. После выбора
совместимых актуальных версий остаются две high advisory:

1. `react-router 7.18.2` — RSC-mode CSRF advisory. Текущий frontend использует
   только declarative `BrowserRouter`, не React Server Components/actions, и
   не подключён к рабочим routes. Патч указан аудитом только для ещё не
   опубликованной ветки 8.x; риск должен повторно оцениваться перед
   подключением auth/API.
2. `brace-expansion 1.1.17` — dev-only DoS advisory в lint dependency tree.
   Текущая ветка `eslint-plugin-jsx-a11y` удерживает старый minimatch.
   Ломающее transitive override не применялось.

Критических advisory нет. Также `pnpm peers check` сообщает два upstream
peer mismatch в optional WASM fallback Vite/Rolldown (`@emnapi/core` и
`@emnapi/runtime` ожидаются 2.x alpha, устанавливаются 1.11.1). Native
macOS/CI build и tests проходят; alpha-пакеты напрямую не добавлялись.

## Визуальный baseline

Существующие эталоны сохранены без изменений:

```text
docs/screenshots/products-daily-ui-1440.png
docs/screenshots/excel-receipt-preview-1280.png
docs/screenshots/repair-ui-1440.jpg
docs/screenshots/repair-ui-390.jpg
```

UI map фиксирует обязательные будущие viewports и states. Полная
авторизованная baseline-съёмка legacy UI остаётся `BLOCKED` до
детерминированной staging-копии без PII и test accounts.

## Rollback первого этапа

Этап не меняет данные, backend, Jinja templates, Flask routes или production.
Rollback не требует восстановления базы:

1. не подключать `/app/` к Flask/Nginx;
2. закрыть или откатить commit ветки `feature/full-react-rewrite`;
3. не публиковать frontend artifact;
4. текущая система продолжает работать на Jinja без переключения feature
   flag.

`node_modules`, build output, browser binaries и test reports игнорируются и
не входят в release.

## Следующий этап

Не переносить товары. Сначала завершить characterization fixtures для
write-flows и visual baseline, затем отдельными проверяемыми PR выполнить
security containment и заморозить API/auth contracts. Только после этих
gates допустимы design system и React shell.
