# Аудит Vechasu

Дата аудита: 22 июля 2026 года.  
Объект аудита: фактическое содержимое рабочей копии `/Users/maksim/Projects/clock-erp`.  
Текущий commit: `5351ff23343b5566597c01c9c28c7adc47992a82`.  

> Важная оговорка о срезе: рабочая копия находится на `main`, локальная ветка на один commit позади локально известного `origin/main` (`a94a6b2`), а `app/templates/warehouse.html` содержит существующие незакоммиченные изменения пользователя. Аудит описывает именно эффективное состояние этой рабочей копии. Файл пользователя не изменялся. Production и внешние API не опрашивались.

## 1. Executive summary

Сейчас Vechasu — это рабочая внутренняя операционная система одного интернет-магазина, быстро выросшая из Flask-приложения в монолит с заказами, складом, приходами, продажами, ремонтами, отчётами и отдельным локальным каталогом. Это не прототип интерфейса: код действительно читает заказы Bitrix, читает и изменяет данные МоегоСклада, ведёт локальные операции и формирует отчёты. Но это также ещё не самостоятельный ERP-продукт и не SaaS: основная бизнес-логика сосредоточена в одном файле, существенные данные хранятся в общих JSON-файлах, пользователей и разграничения доступа нет, а модель компании/tenant отсутствует.

По состоянию репозитория систему следует классифицировать как **внутренний single-company продукт ранней эксплуатационной зрелости**. Каталожная подсистема, добавленная позже, заметно лучше структурирована и протестирована, чем основной операционный контур.

Три сильные стороны:

1. Реализован полезный сквозной операционный интерфейс, а не набор заглушек: 39 Flask-маршрутов, включая 23 POST-маршрута, покрывают реальные действия склада, приходов, продаж и ремонтов (`app/web.py:370-7499`).
2. Импорт каталога Bitrix имеет хорошие локальные свойства: read-only клиент с timeout/retry, пагинация, нормализация, SQLite-транзакции, внешние ключи, идентичность по внешнему ID, payload hash и журнал запусков (`app/clients/bitrix_catalog.py:316-434`, `app/services/bitrix_catalog_importer.py:76-648`, `app/catalog_db.py:11-242`).
3. В репозитории появился безопасный инженерный процесс: изолированный CI без секретов и внешних записей, тесты каталога, проверки синтаксиса и документированный deploy-guard (`.github/workflows/tests.yml:15-63`, `scripts/deploy.sh:19-178`, `docs/agent-pipeline.md`).

Пять главных рисков:

1. **Нет authentication, authorization и object-level permissions.** Любой клиент, имеющий сетевой доступ к Flask-приложению, может читать персональные данные и вызывать внешние складские записи/удаления (`app/web.py:26`, полный список маршрутов `app/web.py:370-7495`).
2. **Multi-tenancy отсутствует на всех уровнях.** Нет `company_id`, tenant middleware, tenant-aware уникальности, отдельных credentials, scopes, файлов, кэшей или фоновых задач (`app/catalog_db.py:14-209`, `app/web.py:34-51`, `app/config.py:1-9`).
3. **Складские операции не атомарны и недостаточно идемпотентны.** Внешний документ МоегоСклада создаётся до локальной фиксации; при ошибке или параллельном запросе возможны дубли, частичные списания и расхождения (`app/web.py:412-559`, `app/web.py:1443-1562`, `app/web.py:5640-6323`).
4. **Операционные данные в JSON не имеют транзакций, схемы и общей блокировки.** Конкурентные read-modify-write могут терять изменения; повреждённый JSON часто молча трактуется как пустой набор (`app/web.py:650-670`, `app/web.py:1349-1378`, `app/web.py:2070-2090`, `app/web.py:4678-4696`).
5. **Production-контур непереносим по одному репозиторию.** Нет app server, systemd unit, reverse proxy/TLS, Docker/Compose, health/readiness endpoint, backup/restore runbook и конфигурации `vechasu.com`/`app.vechasu.com`; deploy только перезапускает внешний, неописанный сервис (`scripts/deploy.sh:33-177`).

Готовность к собственной инфраструктуре: **нет, только после обязательной стабилизации безопасности, данных и runtime-контура**. Существующий скрипт подтверждает, что одиночный сервер уже использовался, но не позволяет воспроизвести инфраструктуру с нуля.

Готовность к подключению второй компании: **нет**. Подключение второго набора credentials к текущему процессу приведёт либо к замене первого клиента, либо к смешиванию кэшей, SQLite-строк, JSON-файлов, настроек и отчётов.

Главный ограничитель: **отсутствие идентичности пользователя и компании в модели данных и в каждом запросе**. Пока это не исправлено, перенос на публичный `app.vechasu.com` увеличивает риск, а добавление второго клиента создаёт прямую угрозу утечки и смешивания данных.

### Оценки зрелости

| Область | Оценка | Обоснование |
|---|---:|---|
| Архитектурная зрелость | 3/10 | Есть выделенные клиенты и сервисы каталога, но основной backend — `app/web.py` на 7 499 строк с UI, domain, persistence и внешними side effects в одном модуле. |
| Качество кода | 4/10 | Код читаем и содержит серверную валидацию отдельных полей; одновременно есть повторно определённые функции, глобальное состояние, непоследовательная обработка ошибок и крупные функции до 748 строк. |
| Тестируемость | 4/10 | Объявлен 71 тест, хорошо покрывающий каталог; практически весь основной ERP-контур, permissions, складские гонки и JSON persistence не покрыты. |
| Безопасность | 1/10 | Нет входа, ролей, CSRF, rate limiting и tenant isolation; найден используемый hardcoded credential и секреты в Git-истории. Плюс — параметризованный SQL каталога и HTML-sanitizer описаний. |
| Эксплуатационная готовность | 2/10 | Есть deploy-guard и CI, но нет воспроизводимого runtime, наблюдаемости, общего backup/restore и корректных health/readiness checks. |
| Готовность к нескольким компаниям | 0/10 | Tenant-сущность и tenant key отсутствуют во всех постоянных хранилищах и интеграциях. |
| Готовность к подключению второго клиента | 1/10 | Адаптеры Bitrix/МойСклад уже выделяются, что можно использовать при доработке, но безопасный onboarding сейчас невозможен. |

## 2. Паспорт репозитория

| Параметр | Фактическое состояние | Доказательство |
|---|---|---|
| Основные языки | Python, HTML/Jinja, JavaScript и CSS внутри шаблонов; один production PHP endpoint; Bash | `app/`, `app/templates/`, `bitrix/catalog-export.php`, `scripts/deploy.sh` |
| Backend | Flask, один глобальный `Flask` app | `app/web.py:24-26` |
| Frontend | Server-rendered HTML/Jinja, встроенные CSS/JS; отдельной сборки нет | `app/templates/*.html`; 18 629 строк HTML |
| База | SQLite только для каталога; JSON-файлы для большинства операций; Bitrix и МойСклад остаются внешними системами записи | `app/catalog_db.py:7-209`, `.gitignore:15-47`, `app/web.py` |
| ORM | Отсутствует; сырой `sqlite3` и SQL | `app/catalog_db.py:1-3`, `app/services/catalog_reader.py:149-189` |
| Package manager | `pip` + `requirements.txt`; lock-файла/хешей нет | `requirements.txt:1-5`, `.github/workflows/tests.yml:50-54` |
| Runtime | CI: Python 3.10; локально найден Python 3.9.6 без зависимостей; production version не описан | `.github/workflows/tests.yml:17-38`; выполненная проверка интерпретатора |
| Зависимости | `python-dotenv`, `requests`, `flask` без версий; `openpyxl==3.0.10`, `reportlab==3.5.68` | `requirements.txt:1-5` |
| Сборка | Нет frontend/backend build; приложение запускается как Python source | отсутствие build-конфигурации; `app/web.py:7498-7499` |
| Тесты | `unittest`, fake/mock clients, временные SQLite | `tests/`, `.github/workflows/tests.yml:56-63` |
| Линтер/форматтер/type-check | Не настроены; `ruff`, `mypy`, `pylint`, `flake8` локально не найдены | отсутствие конфигов и результат `command -v` |
| Контейнеризация | Отсутствует | Dockerfile/Compose не найдены |
| CI/CD | GitHub Actions: install, `unittest`, `compileall`; deploy автоматикой не вызывается | `.github/workflows/tests.yml` |
| Deployment | Bash push + SSH + fast-forward + syntax parse + restart systemd + HTTP GET | `scripts/deploy.sh:19-177` |
| Внешние сервисы | Tictactoy/Bitrix custom API, МойСклад REST API, HeadHunter areas API; GitHub Actions | `app/web.py:28-30`, `app/clients/moysklad.py:5-6`, `app/web.py:2782-2794` |
| Email/платежный провайдер/object storage | Не найдены | поиск по репозиторию; payment только поле заказа |

### Размер

Подсчёт выполнен по 72 отслеживаемым файлам текущего checkout, без `.git`, локального `.env`, баз и ignored `instance`-данных.

| Тип | Файлов | Строк | Размер |
|---|---:|---:|---:|
| Python | 30 | 12 867 | 428 937 байт |
| HTML/Jinja | 17 | 18 629 | 623 923 байта |
| PHP | 2 | 1 108 | 40 015 байт |
| Markdown | 12 | 1 372 | 98 388 байт |
| JSON, отслеживаемые справочники | 2 | 15 044 | 738 815 байт |
| Bash | 1 | 178 | 4 891 байт |
| Прочее | 8 | 241 | около 6,1 КБ |
| Итого | 72 | 49 439 | 1 941 116 байт |

Основные области: `app/` — 33 файла/29 324 строки, `tests/` — 9/1 248, `scripts/` — 8/1 515, `docs/` — 9/1 131. `app/web.py` содержит 7 499 строк; три крупнейших шаблона (`sales.html`, `receipts.html`, `warehouse.html`) содержат более 4 000 строк каждый.

### Git

- Ветка: `main`; commit `5351ff2`; локально известный `origin/main` впереди на один commit `a94a6b2`.
- Исходный status до создания отчёта: ` M app/templates/warehouse.html`.
- История: 105 коммитов (97 обычных, 8 merge) с 8 по 21 июля 2026 года, 6 авторских identities; 57 коммитов затрагивали `app/web.py`.
- 29 коммитов сделаны 17 июля; есть шесть merge `production-snapshot/main`. Это свидетельствует о быстром переносе production-изменений и высоком темпе, но не о длительно проверенной стабильности.
- `README.md`, `app/init__.py` и `app/sync.py` пусты. Файл `type` пуст.
- В рабочей копии существует ignored `.env`; содержимое не читалось. `.env` был добавлен в первом commit и удалён следующим, поэтому реальные непустые значения четырёх credentials остались в Git-истории (значения `[REDACTED]`).

## 3. Как запустить проект

### Подтверждено кодом

1. Нужен Python и системный `pip`. CI использует Python 3.10 (`.github/workflows/tests.yml:34-38`).
2. Установка Python-зависимостей: `python -m pip install -r requirements.txt` (`.github/workflows/tests.yml:50-54`). Репозиторий не фиксирует transitive dependencies.
3. Переменные из `.env.example`: `MOYSKLAD_TOKEN`, `BITRIX_WEBHOOK_URL`, `BITRIX_REST_URL`, `BITRIX_ORDERS_URL`, `BITRIX_ORDER_URL`, `BITRIX_API_MAX_RETRIES`, `BITRIX_LOGIN`, `BITRIX_PASSWORD`, `BITRIX_EXCHANGE_URL`, `BITRIX_CATALOG_URL`, `BITRIX_CATALOG_TOKEN` (`.env.example:1-11`).
4. Дополнительно код читает `BITRIX_ORDERS_TOKEN` и `CATALOG_DATABASE_PATH`, которых нет в `.env.example` (`scripts/bitrix_orders_dry_run.py:126-132`, `app/catalog_db.py:213-216`).
5. Фактический web entry point: `python app/web.py`; он слушает только `127.0.0.1:5050` и включает `debug=True` (`app/web.py:7498-7499`). Это режим разработки, не безопасный production command.
6. `app/main.py` не запускает web: он создаёт `BitrixClient` и делает сетевой connection check (`app/main.py:1-5`).
7. SQLite каталога создаётся автоматически при write-import через `CatalogDatabase.initialize()`; отдельной migration-команды нет (`app/catalog_db.py:227-229`, `app/services/bitrix_catalog_importer.py:97`).
8. Основные JSON-файлы создаются обработчиками в `instance/`. Seed-команды нет.
9. Тестовая команда CI: `python -m unittest discover -s tests -v`; compile check: `python -m compileall app scripts tests` (`.github/workflows/tests.yml:59-63`).
10. Ручной каталог: `venv/bin/python scripts/sync_bitrix_catalog.py` после начального импорта (`docs/bitrix_catalog_sync.md:1-24`).

### Написано только в документации или внешне подразумевается

- Документация предполагает существующий `venv/bin/python`, но инструкции создания venv отсутствуют (`docs/bitrix_catalog_sync.md:3-7`). В локальной рабочей копии venv не найден.
- Production якобы использует systemd service `clock-erp` в `/opt/clock-erp`, однако unit-файл не входит в Git (`AGENTS.md`, `scripts/deploy.sh:42-43`).
- Исторические docs сообщают об успешном production-каталоге и отдельных backup-файлах. Это отчёты прошлых запусков, не воспроизводимая политика backup/restore (`docs/bitrix_catalog_import_report.md:28-67`).

### Отсутствует или неясно

- Поддерживаемая ОС, точная production-версия Python, app server (`gunicorn`/`uwsgi`/аналог), systemd unit, пользователь и права процесса.
- Reverse proxy, TLS, DNS, firewall, `vechasu.com` и `app.vechasu.com` — строки доменов в репозитории отсутствуют.
- Порядок начального создания всех JSON, миграция существующего `instance`, seed/test data.
- Миграции схемы SQLite и команда schema upgrade.
- Очереди, worker, scheduler, cron/timer — отсутствуют.
- Production dependency install и системные пакеты. Для PDF требуются `/usr/share/fonts/dejavu/DejaVuSans*.ttf` (`app/web.py:4107-4131`).
- Секрет-менеджер, backup, restore, retention и disaster recovery.

### Расхождения

- Deploy проверяет `127.0.0.1:5000`, прямой Flask запуск слушает `5050` (`scripts/deploy.sh:44-47`, `app/web.py:7499`). Несовпадение может объясняться внешним unit-файлом, но он отсутствует.
- `.env.example` предлагает настраиваемые Bitrix order URLs, однако production web-путь использует hardcoded Tictactoy URLs и hardcoded update credential (`app/web.py:28-32`). Улучшенный `BitrixOrdersReadOnlyClient` задействован только dry-run script, не UI (`scripts/bitrix_orders_dry_run.py:126-133`).
- Настройки `company_name`, `erp_name`, `low_stock_threshold` сохраняются, но вне страницы settings не читаются; branding и low-stock behavior фактически не меняются (`app/web.py:7395-7451`; поиск использований).
- `product_mappings.json` web-часть хранит в `app.instance_path`, а dry-run scripts читают из корневого `instance/` (`app/web.py:644-670`, `scripts/bitrix_orders_dry_run.py:139-147`).
- `README.md` пуст и не является инструкцией запуска.

## 4. Карта архитектуры

```text
Оператор в браузере
  -> reverse proxy / systemd (существование предполагается, конфигурации в Git нет)
  -> Flask app: app/web.py
       -> Jinja-шаблоны с встроенными CSS/JavaScript
       -> глобальные in-process кэши заказов и склада
       -> JSON-файлы instance/*
       -> SQLite instance/catalog.db
       -> синхронные внешние вызовы
            -> Tictactoy custom order API / update status API
            -> МойСклад REST API (товары, остатки, приходы, списания)
            -> hh.ru areas API (справочник регионов)

Ручные CLI-команды
  -> read-only Bitrix catalog export PHP endpoint
  -> BitrixCatalogImporter
  -> SQLite catalog.db / catalog_sync_runs
```

### Компоненты

| Компонент | Назначение и entry point | Зависимости/данные | Основные риски |
|---|---|---|---|
| Flask-монолит | Все пользовательские маршруты и большая часть domain logic; `app/web.py:370-7495` | Flask, шаблоны, JSON, SQLite, внешние API | God module, нет auth/tenant/CSRF, side effects в request thread |
| HTML UI | Таблицы, формы, drawers/modals, client-side поиск; `app/templates/` | Server context, встроенный JS/CSS | Шаблоны до 4,7 тыс. строк, нет asset build/CSP; одно место stored XSS |
| Operational Bitrix adapter | Чтение списка/карточки, смена статуса; `app/web.py:28-39,294-367` | Hardcoded Tictactoy endpoints | Старый небезопасный путь обходит новый read-only client, нет pagination/auth на GET |
| Bitrix catalog exporter | Защищённый GET endpoint в Bitrix; `bitrix/catalog-export.php` | Bitrix modules, iblock 5 | Жёсткая привязка к Tictactoy; изменения цены могут не менять cursor |
| Catalog client/importer | Нормализация, retry, pagination, import modes; `app/clients/bitrix_catalog.py`, `app/services/bitrix_catalog_importer.py` | `requests`, raw SQLite | Нет tenant key/locking/scheduler; транзакция на batch/page, не на весь sync |
| Catalog DB/reader | 11 таблиц каталога и read UI; `app/catalog_db.py`, `app/services/catalog_reader.py` | `instance/catalog.db` | Нет migration framework/backup policy; глобальная уникальность |
| МойСклад client | Чтение товаров/остатков и запись документов; `app/clients/moysklad.py` | Один глобальный bearer token | Первый склад/организация, нет retry/rate handling/idempotency key, разные error contracts |
| JSON persistence | Ремонты, приходы, продажи, overrides, ячейки, журнал, settings | `instance/*.json`, иногда `app/instance` | Нет общей блокировки, constraints, tenant, backup, audit; молчаливое обнуление при ошибке |
| Reporting | HTML, XLSX, PDF, analytics; `app/web.py:3392-4369,6713-6953` | JSON + live warehouse, openpyxl/reportlab/fonts | Полные выборки в память, synchronous, данные не являются бухгалтерским ledger |
| CLI/scripts | Import, incremental sync, dry-run, quality audit, cleanup | env, SQLite, external read-only APIs | Запускаются вручную; scheduler/lock/alert отсутствуют |
| CI | Изолированные tests/compile; `.github/workflows/tests.yml` | GitHub-hosted Python 3.10 | Нет lint/type/security/dependency scan; основной ERP не покрыт |
| Deploy | `scripts/deploy.sh` | SSH root, Git, systemd, curl | Невоспроизводимая topology, restart downtime, нет backup/migration/TLS/readiness |

Границы модулей неравномерны. Каталог имеет client/service/database/reader, а заказы, склад, продажи, ремонты и отчётность смешаны в `app/web.py`. Источники истины также разделены неявно: заказ — Bitrix; товар/остаток — МойСклад; content-каталог — SQLite; оперативные документы Vechasu — JSON.

## 5. Карта бизнес-домена

| Сущность | Реализация и ключевые поля | Связи/целостность | Доступ, company, audit |
|---|---|---|---|
| Company / Organization / Tenant / Workspace | **Отсутствует.** `company_name` — строка настройки, не сущность (`app/web.py:7126-7130,7421-7451`) | Нет ID/FK/tenant scope | Любой requester меняет; company binding нет; audit нет |
| User | **Отсутствует** | Нет | Нет authentication |
| Role / Permission | **Отсутствует** | Нет | Нет authorization |
| Customer | Не отдельная сущность: поля `customer/phone/email/address/city` в remote order; `client_name/client_phone` в repair JSON (`app/web.py:211-291,2326-2371`) | Нет FK/дедупликации/consent/retention | Доступны всем; tenant/audit нет |
| Order | Remote Bitrix object, не хранится как модель; `id`, number, status, paid, products, total (`app/web.py:211-332`) | Локально связан только строковыми ID в mapping/stock operations | Любой читает/меняет статус; company нет; dry-run предлагает, но не реализует storage |
| Order item | Embedded `products`; ID/name/price/quantity | Mapping Bitrix ID -> МойСклад ID в JSON | Constraints нет |
| Product | Два представления: remote МойСклад warehouse item и `catalog_products` SQLite (`app/web.py:1792-1957`, `app/catalog_db.py:30-57`) | Catalog имеет FK к категориям/offers/etc.; связь с МойСклад через mapping | Глобально; catalog sync history частичный |
| Variant | `catalog_offers` + property/image/price tables (`app/catalog_db.py:99-127`) | FK cascade к product | Реализовано, но production export возвращает `offers=[]` (`bitrix/catalog-export.php:690`) |
| Inventory | Не локальная таблица; live `stock/reserve/quantity` МоегоСклада | Локальный журнал не источник истины | Глобальный account; audit частичный JSON |
| Warehouse | **Отдельной сущности нет.** Client берёт первую organization и первый store (`app/clients/moysklad.py:213-230`) | Явного store ID в документах Vechasu нет | Company binding нет |
| Location / Cell | `warehouse_cells.json`, `warehouse_category_cells.json` + строковый attribute МоегоСклада | Ключ product ID/category path; FK нет | Все могут менять; local-before-remote divergence |
| Stock movement | External `loss`/`enter` + `stock_operations.json`; поля id/time/product/type/quantity/before/after/source/reason/document IDs | Журнал ограничен 1000 строками (`app/web.py:1375-1378`) | Все могут создавать; actor/company нет; записи изменяемы/удаляемы |
| Sale | Manual sale JSON и automatic view, производный от stock writeoff (`app/web.py:2534-2605,3002-3388,3429-3698`) | Manual sale не меняет остаток; automatic override связан ID операции | CRUD без actor/company/audit |
| Payment | **Модели нет.** Только remote `paid/payment` order fields (`app/clients/bitrix_orders.py:267-268`) | Нет payment transactions | Не управляется |
| Receipt | JSON `receipts.json`; number/date/positions/prices/total/MoySklad document ID (`app/web.py:6176-6257`) | Внешний `enter` + локальный журнал, без общей транзакции | CRUD без actor/company; delete удаляет audit rows |
| Repair | JSON `repair_cases.json`; number, customer, product, issue, costs, status, comments (`app/web.py:2268-2531`) | Нет FK/order relation, unique constraint или history | CRUD/жёсткое удаление всем |
| Integration | Env/constants, не сущность | Один credential context на весь process | Не tenant-scoped; audit конфигурации нет |
| Sync job | `catalog_sync_runs`: mode/status/cursors/counts/errors/details (`app/catalog_db.py:192-209`) | Связан логически, не FK | Только catalog; actor/tenant нет |
| Report | Вычисляется на запрос из JSON/live data | Persistent snapshot/ledger нет | Доступен всем |
| Audit log | **Отсутствует как системная сущность.** Есть ограниченный stock journal и catalog sync history | Не immutable; deletes/updates стирают историю | Actor/IP/tenant нет |
| Settings | `settings.json` и `navigation_settings.json` | Global key/value | Любой requester меняет; audit/tenant нет |

Ключевая проблема модели: нет единого transaction ledger. Продажа, приход, внешний складской документ и локальный журнал — отдельные записи, которые могут расходиться. Денежные значения в каталоге сохраняются строкой, но в sales/receipts после `Decimal` снова превращаются в `float` (`app/web.py:2893-2933,4699-4703`).

## 6. Основные пользовательские и системные потоки

### 1. Создание или импорт заказа

**Не реализовано.** UI читает список и карточку непосредственно из Tictactoy API (`get_orders`, `get_order`; `app/web.py:294-332`). `scripts/bitrix_orders_dry_run.py` умеет только безопасно классифицировать до 10 заказов и не пишет данные (`scripts/bitrix_orders_dry_run.py:126-249`). Локальной транзакции, идемпотентного upsert и полной истории заказов нет.

### 2. Синхронизация заказов с Bitrix

Trigger — GET `/` или `/order/<id>`. Список запрашивается синхронно и кэшируется в памяти на 60 секунд; карточка имеет timeout 1 секунда (`app/web.py:294-332`). Status POST вызывает custom endpoint и затем обновляет кэш (`app/web.py:335-367,620-640`). Pagination, cursor, retry и persistent sync state отсутствуют. Ошибка списка превращается в cached/empty response, поэтому сбой может выглядеть как отсутствие заказов. Повтор status POST не имеет idempotency key.

### 3. Импорт товаров и остатков

- Bitrix content: ручной CLI постранично читает защищённый export, нормализует и пишет SQLite. Batch/page атомарен; payload hash и unique external ID обеспечивают повторяемость (`scripts/import_bitrix_catalog.py:58-132`, `scripts/sync_bitrix_catalog.py:106-167`). Incremental cursor продвигается только при полном успехе parent run, но страницы до ошибки уже committed и безопасно проходят повторно. Цена-only change может быть пропущен (`bitrix/catalog-export.php:394`, `docs/bitrix_catalog_sync.md:78-81`).
- МойСклад stock: `/warehouse` синхронно читает до 1000 products и stock rows, сопоставляя по code/article/name; данные кэшируются на 300 секунд (`app/web.py:1792-1957`). Это не импорт в локальную БД, пагинации сверх 1000 нет.

### 4. Складское поступление

Trigger — POST `/receipts/create`. Вход: date, note, positions или browser-supplied import payload. Новые товары могут быть созданы заранее в МойСклад. Затем создаётся один external `enter`; после успеха пишутся `receipts.json` и по одному `stock_operations` (`app/web.py:5640-6323`). Внешний вызов и локальные файлы не транзакционны. При crash после external success остаётся orphan document; повтор создаёт дубль. Receipt number генерируется по текущему JSON без lock (`app/web.py:4706-4724`).

### 5. Перемещение между ячейками

**Физическое перемещение не реализовано.** POST `/warehouse/cell` меняет только label ячейки: сначала локальный JSON, затем attribute товара в МойСклад (`app/web.py:1098-1140`). При внешней ошибке local и remote расходятся. Перемещения между складами/ячейками как stock movement нет.

### 6. Резервирование и списание

Резерв только отображается из stock report; создание/снятие резерва отсутствует. Списание выполняется либо ручной корректировкой остатка, либо по заказу. Ручной POST доверяет `current_stock` из браузера и создаёт `loss` на разницу (`app/web.py:1443-1546`). Заказное списание предварительно проверяет mapping/остаток, затем последовательно создаёт loss на каждую строку (`app/web.py:412-540`).

Критическая race/partial-failure проблема: `is_order_stock_written_off()` проверяет локальный журнал без lock. Два запроса могут пройти проверку одновременно. Если первая позиция заказа записана, а вторая упала, следующий retry увидит любую запись order ID и сочтёт весь заказ уже списанным (`app/web.py:424-431,505-540,1394-1401`).

### 7. Продажа

Manual sale POST пишет только `manual_sales.json`; склад не уменьшается (`app/web.py:3002-3104`). Automatic sale — это представление успешного order writeoff плюс editable override (`app/web.py:3429-3698`). Отдельного sale transaction/status/payment/refund нет. Идемпотентность manual sale отсутствует.

### 8. Возврат

**Отсутствует.** Нет маршрутов, сущности, reverse stock movement или связи с payment.

### 9. Ремонт

GET/POST CRUD над общим `repair_cases.json`; номер `R-<year>-<sequence>` рассчитывается по длине списка, поэтому конкурентные requests могут создать одинаковый номер (`app/web.py:2268-2377`). Update/status/delete переписывают весь файл, audit trail нет (`app/web.py:2380-2531`).

### 10. Пользователь и роль

**Отсутствуют.** Все routes публичны на уровне приложения.

### 11. Отчёты

Sales report строится из полного stock journal + manual sales + overrides + live warehouse, фильтруется in-memory и экспортируется в HTML/XLSX/PDF (`app/web.py:3429-4369`). Receipt report и analytics аналогично читают полные JSON (`app/web.py:4784-4856,6727-6953`). Нет snapshot consistency: внешний stock может измениться в ходе отчёта. Экспорт строится синхронно и полностью в памяти.

### 12. Ошибки интеграций

Catalog read-only clients имеют bounded retry, Retry-After и безопасные тексты (`app/clients/bitrix_catalog.py:332-369`, `app/clients/bitrix_orders.py:44-98`). Основной МойСклад client retry не имеет; GET/PUT часто печатают response body и возвращают `None`, POST бросает `HTTPError` (`app/clients/moysklad.py:15-74`). Web обычно ловит широкий `Exception`, пишет `print` и иногда возвращает текст исключения пользователю. Correlation ID, persistent error queue, reconciliation job и alert отсутствуют.

## 7. Интеграции

### Bitrix: оперативные заказы

- Назначение: список/карточка заказа, status update, последующее ручное списание.
- Направление: GET в Bitrix/Tictactoy; POST status из Vechasu.
- Authentication: основной GET-код не передаёт credentials; status использует literal token `[REDACTED]` (`app/web.py:28-32,344-352`). **Не подтверждено**, что endpoint принимает этот token сейчас.
- Mapping: `normalize_order()` понимает `FIO`, `PHONE`, `EMAIL`, `ADDRESS`, `CITY`, status codes и embedded products (`app/web.py:168-291`).
- Pagination: отсутствует; docs и dry-run прямо фиксируют fixed recent window (`app/clients/bitrix_orders.py:100-137`).
- Retry: основной web-код не имеет; новый read-only client имеет exponential backoff, но не подключён к UI.
- Idempotency: status — нет; stock writeoff — слабая локальная проверка после факта.
- Риск single-client: максимальный — домены, status mapping, property codes и token зашиты.

### Bitrix: каталог

- Endpoint `bitrix/catalog-export.php`: GET-only, bearer token, `hash_equals`, limit 1..200, page/total/has_more/next_page, include inactive, updated_from (`bitrix/catalog-export.php:29-68,87-125,398-428,695-706`).
- Credentials: env или внешний PHP config path; значение в Git отсутствует (`bitrix/catalog-export.php:44-56`).
- Mapping: категории, свойства, изображения, цены, stock metadata; purchase prices отделяются и не импортируются как sale price (`app/clients/bitrix_catalog.py:55-272`).
- Retry/timeout: `(3.05,20)`, до 3 retry для timeout/connection/429/5xx (`app/clients/bitrix_catalog.py:319-369`).
- Partial errors: page transaction rolls back; parent cursor не двигается, ранее committed pages переобрабатываются.
- Ограничение: `updated_from` смотрит только element `TIMESTAMP_X`; периодический full verify необходим.
- Single-client coupling: iblock `5`, base URL и внешний config path Tictactoy зашиты (`bitrix/catalog-export.php:5-9,50-51`); `CatalogReader` также показывает iblock `5` (`app/services/catalog_reader.py:251-254`).

### МойСклад

- Authentication: один global bearer из `MOYSKLAD_TOKEN` (`app/config.py:4-6`, `app/clients/moysklad.py:8-13`).
- Направление: GET products/metadata/stock/store/organization; POST/PUT/DELETE product, folder, attribute, enter, loss.
- Pagination: основной warehouse ограничен `limit=1000`; mapping loader paginates offset до короткой страницы (`app/web.py:1809-1813`, `app/services/moysklad_catalog_mapping.py:15-27`).
- Store selection: первый доступный organization и store, без конфигурации (`app/clients/moysklad.py:213-230`).
- Timeout: 8 секунд на каждый request. Retry/backoff/429 handling отсутствуют.
- Idempotency: external idempotency key не передаётся. Документные номера/operation IDs генерируются только локально после или рядом с запросом.
- Logging: часть методов печатает response body и продуктовые данные (`app/clients/moysklad.py:23-25,641-653`).
- Partial failure: компенсации/outbox/reconciliation нет.
- Tenant risk: credentials, cache, store/org и mappings global.

### HeadHunter areas API

GET `https://api.hh.ru/areas` при открытии sales, если tracked cache старше 30 дней; timeout 15 секунд, затем fallback на старый cache или список регионов без городов (`app/web.py:2701-2881`). Authentication/retry/monitoring нет. Вызов read-only, URL фиксирован; tenant-specific geography не поддерживается.

### Email, files, payments, notifications, monitoring, auth providers

- Email/notifications: не найдены.
- File storage: пользовательские файлы не сохраняются; Excel читается в память. Catalog хранит external image URLs, не сами файлы (`app/catalog_db.py:129-152`). Object storage отсутствует.
- Payments: только отображаемые поля Bitrix order, payment provider/webhook отсутствует.
- Monitoring/error tracking: отсутствуют.
- Authentication providers: отсутствуют.
- Webhooks: endpoint Vechasu для входящих webhook не найден; verification/signature поэтому отсутствуют.

## 8. Готовность к нескольким компаниям

Фактическая модель: **multi-tenancy отсутствует**. Это один process, один набор env credentials, одна SQLite, один набор JSON и общие in-memory caches.

| Проверка | Результат |
|---|---|
| Таблицы с `company_id`/tenant key | 0 из 11 catalog tables |
| JSON records с company key | Не предусмотрено кодом |
| Определение tenant в HTTP | Отсутствует |
| Tenant middleware/global scope/policy | Отсутствует |
| Tenant в background/CLI | Отсутствует |
| Tenant-aware unique constraints | Отсутствуют; external IDs и МойСклад mapping global |
| Credentials integrations | Global env |
| Cache isolation | Нет; `ORDERS_CACHE` и `WAREHOUSE_CACHE` process-global |
| File isolation | Нет; общие `instance/*.json` и `catalog.db` |
| Realtime/WebSocket | Не реализованы |
| Logs/reports isolation | Нет |
| Roles within company | Users/roles отсутствуют |
| Super-admin boundary | Отсутствует |

### Конкретные cross-tenant vulnerabilities

| Сценарий | Код | Вероятность | Ущерб | Стратегия исправления |
|---|---|---:|---|---|
| Пользователь компании B открывает глобальный `/`, `/repair`, `/sales`, отчёты и видит A | `app/web.py:370-407,2158-2265,3872-3879,4375-4668` | Неизбежно при общем deployment | Утечка заказов, контактов, финансов и ремонтов | Company + membership, tenant context, обязательный query scope, object policy tests |
| B вызывает POST с ID товара A и создаёт loss/enter в глобальном аккаунте | `app/web.py:412-559,1443-1562,5640-6323` | Высокая | Неверные остатки и финансовый ущерб | Tenant-scoped integration account/store, server-side object lookup, idempotent command layer |
| Переключение env credentials на B оставляет caches/JSON/catalog A | `app/web.py:34-46`, все `instance/*` | Неизбежно | Смешанные отчёты и mappings | Tenant-keyed storage/cache или отдельные DB/schema до общего UI |
| Одинаковый Bitrix external ID B конфликтует с A | `app/catalog_db.py:56`, categories/properties/offers unique constraints | Высокая | Перезапись/отказ импорта/ошибочная связь | Добавить `company_id` во все natural unique indexes |
| Один МойСклад product ID может быть связан только глобально | `app/catalog_db.py:177-190` | Средняя/высокая | Невозможный onboarding или неверный mapping | Unique `(company_id, moysklad_product_id)` |
| CLI sync B пишет в global DB и берёт global cursor A | `scripts/sync_bitrix_catalog.py:23-45,106-167` | Неизбежно | Пропуски/смешивание sync | Tenant required argument/context, per-tenant run/lock/cursor/credentials |
| Первая organization/store выбирается независимо от клиента | `app/clients/moysklad.py:213-230` | Высокая даже в одном account | Документ в неверном юрлице/складе | Явные tenant settings `organization_id`, `store_id`, validation |

**Можно ли прямо сейчас подключить вторую компанию без риска смешивания данных? Нет.**

Минимальные условия: authentication; Company/Membership/Role; tenant context во всех HTTP и CLI; tenant key во всех domain tables/records; tenant-aware unique constraints; раздельные integration credentials и store/org settings; миграция JSON в tenant-aware transactional storage; tenant-keyed caches; object-level authorization; tenant isolation tests; backup/restore и monitoring. Отдельные deployments могли бы временно изолировать данные, но это противоречит цели «без форка» и не создаёт продуктовую multi-tenancy.

## 9. Жёсткие привязки к Tictactoy

| Тип | Элемент | Доказательство | Рекомендация |
|---|---|---|---|
| Нормальная конфигурация клиента | Bitrix/MойСклад credentials | `.env.example`, `app/config.py` | Хранить per-tenant encrypted credentials, не process-global env при multi-tenant |
| Ошибочно зашито | Order URLs и status URL | `app/web.py:28-30` | Перенести в integration config tenant |
| Ошибочно зашито | Status update credential `[REDACTED]` | `app/web.py:32,344-352` | Немедленно rotate; env/secret manager; удалить зависимость от literal |
| Ошибочно зашито | Bitrix iblock `5`, Tictactoy base URL и config path | `bitrix/catalog-export.php:5-9,50-51`; `app/services/catalog_reader.py:253` | Параметризовать adapter deployment/client config |
| Ошибочно зашито | Названия `ТТТ ERP`, `TTT`, localStorage keys | `app/templates/base.html:5-12`, `app/templates/_sidebar.html:50-59,401-414,481` | Tenant branding/UI config; namespace storage by app/user/company |
| Ошибочно зашито | Тексты operation reason/source `ТТТ ERP` | `app/web.py:507,1511-1541` | Формировать из tenant/integration name |
| Ошибочно зашито | Sales source `Tictactoy` и delivery `ТТТ Экспресс` | `app/web.py:2968-2978,3342-3348`; `app/templates/sales.html:2121,2691,2851` | Tenant-managed sales channels/delivery methods |
| Ошибочно зашито | Global company defaults | `app/web.py:7126-7130,7447-7451` | Company entity + tenant settings; применять настройки в layout |
| Ошибочно зашито | Production IP, root SSH, path/service | `scripts/deploy.sh:5-7,33-43` | Deployment inventory/environment config, non-root deploy identity |
| Требует обобщения | Bitrix status codes N/A/T/D/C и property codes FIO/PHONE/... | `app/web.py:168-175,188-253` | Per-integration mapping, сохраняя raw external code |
| Требует обобщения | Brand = первый segment product folder, category = остальные | `app/web.py:1840-1851` | Tenant-specific catalog mapping rules |
| Требует обобщения | Первый МойСклад store/organization | `app/clients/moysklad.py:213-230` | Обязательные configured IDs per tenant |
| Требует обобщения | Россия/RUB и `₽` в UI/reports | `app/web.py:2608-2698,2936-2962,3917-3920` | Tenant timezone/locale/currency; хранить ISO currency |
| Можно оставить adapter-модулем | Bitrix PHP catalog export/normalizer | `bitrix/catalog-export.php`, `app/clients/bitrix_catalog.py` | Версионированный Tictactoy/Bitrix adapter с config, не core domain |
| Можно оставить adapter-модулем | МойСклад API payloads | `app/clients/moysklad.py` | Integration provider layer с tenant context/idempotency |

Настройки `company_name` и `erp_name` сейчас создают лишь видимость конфигурируемости: sidebar и titles остаются TTT. Это не onboarding компании.

## 10. Авторизация и безопасность

### Реестр

| ID | Уровень | Проблема | Доказательство | Возможный сценарий | Рекомендация |
|---|---|---|---|---|---|
| SEC-01 | Critical | Полное отсутствие authentication/authorization | `app = Flask(...)` и 39 routes без login/policy (`app/web.py:26,370-7495`) | При доступности порта атакующий читает PII и создаёт/удаляет реальные складские документы | До публичного DNS закрыть perimeter; затем session/OIDC auth, roles, object policies, deny-by-default |
| SEC-02 | High | Используемый hardcoded order status credential | `app/web.py:32,344-352`; значение `[REDACTED]` | Читатель repo вызывает custom status endpoint и меняет реальные заказы, если credential активен | Rotate, revoke, env/secret manager, server verification/least privilege |
| SEC-03 | High | Четыре непустых credentials были committed в `.env` | commits `33f573c...` и удаления `e931fda...`; значения `[REDACTED]` | Секреты доступны всем, кто имеет историю/клон, даже после удаления файла | Подтвердить ротацию; при необходимости history rewrite по отдельному плану после revoke |
| SEC-04 | High | CSRF-защиты нет на 23 POST routes | POST routes `app/web.py:412-7421`; CSRF library/token отсутствует | После добавления login злоумышленник заставляет оператора провести loss/delete/status change | SameSite cookies + CSRF tokens + Origin checks; API-specific auth |
| SEC-05 | High | Stored XSS в warehouse history | reason приходит из form и пишется в JSON (`app/web.py:1449,1529-1546`), затем вставляется через `innerHTML` без escaping (`app/templates/warehouse.html:3023-3041`) | Payload в причине выполняется в браузере оператора | DOM `textContent`/escaping; CSP; тест stored XSS |
| SEC-06 | High | Нет tenant isolation/object authorization | Раздел 8; нет company/user model | Пользователь B читает/изменяет объект A по ID | Tenant scopes + membership + object policy tests |
| SEC-07 | High | Небезопасная согласованность внешних складских операций | External write до local journal, нет idempotency (`app/web.py:505-540,6206-6287`) | Повтор/сбой даёт двойное или частичное списание/приход | Idempotency keys, command ledger/outbox, reconciliation, locks |
| SEC-08 | Medium | Нет rate limiting/anti-automation; upload может потреблять ресурсы | 15 MB XLSX полностью читается в RAM; до 5000 rows (`app/web.py:4894-4924,5222-5233`) | Неаутентифицированный клиент параллельно грузит zip-heavy XLSX/тяжёлые отчёты | Auth, request/body/time limits, rate limit, async scanning/import |
| SEC-09 | Medium | Secret management только `.env`; permissions/rotation неизвестны | `app/config.py:1-9`, scripts `load_dotenv` | Секреты копируются между hosts, остаются в backups/process env | Secret manager или root-readable env file, rotation inventory, startup validation |
| SEC-10 | Medium | Ошибки/response bodies и business fields печатаются | `app/clients/moysklad.py:23-25,51-53,641-653`; многочисленные `print` | Logs содержат внешние ответы, IDs и потенциальные персональные/коммерческие данные | Structured redacted logging, classification/retention, no raw bodies by default |
| SEC-11 | Medium | Audit trail/backup/delete controls отсутствуют | repair hard delete, receipt delete removes operation rows (`app/web.py:2510-2531,6610-6681`) | Действие невозможно атрибутировать/восстановить | Append-only audit with actor/company, soft delete, backup/restore |
| SEC-12 | Low | External links/images доверяют imported URLs | `app/templates/catalog_detail.html:3-9` | Tracking/malicious content открывается оператором | URL allowlist/proxy при необходимости; уже есть `rel` для links |

`Critical` для SEC-01 обусловлен конкретным сценарием: web routes сами выполняют `POST /entity/loss`, `/entity/enter` и delete в МойСклад. Если приложение гарантированно изолировано firewall/VPN, текущая вероятность ниже, но перед публичным `app.vechasu.com` риск становится прямым.

### Остальные проверки

- Password hashing/JWT/refresh/session cookies: отсутствуют вместе с users. Flask `SECRET_KEY` не задан, но session сейчас не используется.
- CORS: расширение/headers не настроены; browser same-origin default не заменяет auth/CSRF.
- SQL injection: пользовательские catalog filters передаются параметрами; динамические table/field names берутся из внутренних констант (`app/services/catalog_reader.py:82-173`). Подтверждённой SQL injection не найдено.
- Command injection: request handlers не запускают shell/subprocess; подтверждённой проблемы не найдено.
- SSRF: пользователь не управляет server-side URL; Bitrix URLs задаются окружением, hh URL фиксирован. Внешняя конфигурация всё равно требует allowlist/HTTPS validation. Order read-only client HTTPS проверяет (`app/clients/bitrix_orders.py:27-36`), catalog client — нет.
- Upload/path traversal: filename не используется как filesystem path; workbook читается из `BytesIO`. Path traversal не найден. ZIP/XML resource risk остаётся.
- XSS catalog descriptions: реализован allowlist sanitizer, tests предусмотрены (`app/services/catalog_reader.py:9-54`, `tests/test_catalog_interface.py`).
- Webhook verification: входящих webhooks нет.
- Brute force: login отсутствует; rate limiting понадобится вместе с ним.
- Dependency risks: offline CVE scan не выполнен, `pip-audit` не установлен. Две версии закреплены, три core зависимости — нет; lock/hashes отсутствуют.
- Персональные данные: order/repair contacts выводятся и хранятся без access control, retention и delete policy.

## 11. Схема данных и миграции

### SQLite-каталог

Схема создаётся одной строкой `SCHEMA` через `CREATE TABLE IF NOT EXISTS`; migration framework и версия схемы отсутствуют (`app/catalog_db.py:11-210,227-229`). Всего 11 domain tables:

1. `catalog_categories` — внешний ID/source, hierarchy, active/path/timestamps; unique `(external_source, external_category_id)`.
2. `catalog_products` — content и external identity/hash/timestamps; unique `(external_source, external_product_id)`.
3. `catalog_product_categories` — M:N, composite PK.
4. `catalog_properties` — definitions; unique `(external_source, external_property_id)`.
5. `catalog_product_property_values` — unique `(product_id, property_id)`.
6. `catalog_offers` — variants; unique `(external_source, external_offer_id)`.
7. `catalog_offer_property_values` — unique `(offer_id, property_id)`.
8. `catalog_images` — ровно один owner product/offer через CHECK.
9. `catalog_prices` — product/offer owner, amount/currency as text.
10. `catalog_moysklad_mappings` — one-to-one product и global unique MoySklad product ID.
11. `catalog_sync_runs` — runs/cursors/counters/error summary/details.

Положительные свойства: `PRAGMA foreign_keys=ON`, `busy_timeout=5000`, `BEGIN IMMEDIATE`, rollback, FK cascade для зависимых records, CHECK для booleans и image/price owner (`app/catalog_db.py:12-209,218-242`). Catalog import атомарен внутри одного batch (`app/services/bitrix_catalog_importer.py:97-133`).

### Проблемы схемы

| Проблема | Доказательство | Риск/рекомендация |
|---|---|---|
| Нет migration/version table | `initialize()` только `executescript(CREATE IF NOT EXISTS)` | Новая колонка/constraint не попадёт в существующую БД; ввести Alembic или контролируемые SQL migrations до переноса |
| Нет tenant key | Все таблицы `app/catalog_db.py:14-209` | Все unique/index/FK глобальны; спроектировать `company_id NOT NULL` и tenant-aware uniqueness до второго клиента |
| Timestamps — произвольный TEXT | `created_at`, `updated_at`, external dates | UTC ISO используется импортёром, но DB не проверяет формат; стандартизовать UTC и тип/validation |
| Prices — TEXT без precision/currency constraints | `app/catalog_db.py:154-170` | Не гарантируется Decimal format/ISO currency/one base price; decimal/numeric + CHECK/unique policy |
| Images/prices не имеют natural unique | `app/catalog_db.py:129-175` | Возможны duplicate URLs/price types; добавить нужные unique с tenant/product/offer |
| `catalog_sync_runs` без индексов по mode/status/cursor/time | `app/catalog_db.py:192-209` | По мере роста поиск последнего run деградирует; добавить targeted indexes |
| `normalized_payload_json/details_json` — TEXT без JSON validation | `app/catalog_db.py:49-50,208` | Некорректный JSON обнаруживается только в приложении; JSON type/check в выбранной DB |
| Мягкого удаления нет | Products имеют external `active`; остальные entities hard/cascade | Для audit/retention нужны status/deleted_at, не общий cascade на бизнес-документы |
| Reader не инициализирует/мигрирует DB | `app/services/catalog_reader.py:70-79,201-209` | Старую/частичную schema UI не восстановит; startup readiness + migrations |

Cascade для catalog content уместен, потому что строки являются производными от source catalog. Для будущих orders, stock ledger, sales, payments и audit cascade-delete был бы опасен и не должен копироваться автоматически.

### JSON-данные

JSON не имеет constraints, FK, indexes, version/schema, timestamps единообразного формата или transaction boundary. Деньги и quantities часто `float`; время хранится как local `datetime.now().strftime(...)` без timezone (`app/web.py:517,1531,2329-2330`). Несколько функций при parse error возвращают `{}`/`[]`, скрывая повреждение (`app/web.py:650-663,2070-2083,2542-2554`). Большинство saves — прямой overwrite, только overrides/navigation/region cache используют temp+replace.

Пути также неоднородны: часть привязана к `PROJECT_ROOT`, часть к current working directory, mapping — к Flask `app.instance_path` (`app/web.py:644-647,1340-1346,2063-2067,2534-2539`). Запуск из другого cwd способен читать/создавать другой набор данных.

Seed scripts отсутствуют. Cleanup script создаёт проверенную копию только для одной catalog property cleanup operation, а не общую backup policy (`scripts/cleanup_empty_catalog_properties.py:27-64`).

## 12. Фоновые задачи и синхронизация

Worker, очередь, Redis, Celery/RQ, scheduler, cron и systemd timer в репозитории отсутствуют. `app/sync.py` пуст. Единственная фоновая по смыслу работа — ручной CLI `scripts/sync_bitrix_catalog.py`; docs прямо говорят, что расписание не создавалось (`docs/bitrix_catalog_sync.md:76-113`).

### Catalog sync

- Retry: HTTP retry в read-only client; application-level retry только повторным запуском.
- Transactions: одна SQLite transaction на batch/page import; progress parent run в отдельных transactions.
- Cursor: `cursor_to` parent run записывается после полного успеха; failure сохраняет `cursor_to=NULL` (`scripts/sync_bitrix_catalog.py:91-167`).
- Idempotency/dedup: unique external identity + payload hash; подтверждено test intent (`tests/test_sync_bitrix_catalog.py`).
- Lock/concurrency: нет process lock. Два sync могут взять один cursor и параллельно обновлять global rows; SQLite сериализует write transactions, но не предотвращает duplicate runs/last-writer semantics.
- Dead-letter/cancellation/timeout: нет. Есть bounded HTTP timeout, но нет task deadline/cancel.
- Monitoring: run rows есть, alert/age check нет.
- Tenant context: отсутствует.

### Риски событий

| Риск | Состояние |
|---|---|
| Двойной заказ | Локального импорта заказов нет; duplicate writeoff заказа возможен из-за race |
| Двойное списание | Высокий риск при concurrent POST/retry до локального journal |
| Пропущенная синхронизация | Заказы не синхронизируются; catalog price-only change может быть пропущен |
| Повторный webhook | Webhooks отсутствуют; защиты нет |
| Частичная синхронизация | Catalog повторяем; order writeoff/receipt — неатомарны и частичны |
| Гонка остатков | `current_stock` приходит из формы; lock/version check нет |
| Зависшая задача | Web request ждёт последовательные external calls; central timeout/task watchdog нет |
| Бесконечный retry | Catalog retry bounded; mapping pagination не имеет max pages, но завершится на короткой странице |
| Неверный tenant | Неизбежно при втором клиенте — tenant context отсутствует |

Для надёжного stock flow нужен не просто background queue, а persistent command/operation ledger: unique idempotency key, expected version/current stock fetched server-side, explicit state `pending/external_succeeded/local_succeeded/failed/reconcile`, external document ID и reconciliation worker.

## 13. Тесты и качество

### Инвентаризация

В 9 файлах статически найден 71 метод `test_*` в 14 `unittest.TestCase` classes:

| Область | Тестов | Тип |
|---|---:|---|
| Bitrix catalog normalization/client/matching | 16 | unit/contract с fake data |
| Catalog importer/modes/atomicity | 12 | integration с временной SQLite |
| Bitrix orders dry-run/client/matching | 18 | unit/contract с fake sessions |
| Catalog data quality/cleanup | 3 | integration, backup в temp |
| Catalog schema/constraints | 4 | integration SQLite |
| Catalog import CLI logic | 4 | unit/integration fake client |
| Catalog reader/routes/sanitizer | 5 | integration Flask/SQLite с mocks |
| МойСклад catalog mapping | 4 | integration local SQLite/snapshot |
| Incremental catalog sync | 5 | integration fake client/SQLite |

UI/E2E/browser tests, migration tests, load tests и dedicated security tests отсутствуют. Есть один sanitizer test и credential-safe error tests, но это не системный security suite.

### Выполненные команды

- `python3 -m unittest discover -s tests -v` с пустыми Bitrix/МойСклад env: **Ran 12, 4 passed, 8 import errors**. Четыре schema tests прошли. Остальные 8 modules не импортировались из-за `ModuleNotFoundError: requests`; это проблема локального окружения, не доказательство падения самих 67 методов.
- Устанавливать dependencies запрещено, готовый venv не найден. Для полного запуска нужны `pip install -r requirements.txt` в отдельном окружении.
- `python3 -m pip check`: `No broken requirements found`, но в системном Python не установлены даже declared packages; результат не подтверждает requirements set.
- In-memory `compile()` для 30 Python-файлов `app/scripts/tests`: 30 проверено, 0 syntax failures.
- JSON parse: 2 tracked JSON, 0 failures.
- `bash -n scripts/deploy.sh`: успешно.
- `git diff --check`: успешно на существующем user diff до отчёта.
- PHP lint не выполнен: `php` не установлен.
- Flask route smoke и Jinja parse не выполнены: `flask`/`jinja2` не установлены.
- Lint/type/dependency vulnerability scan не выполнены: инструменты не настроены/не установлены.

Покрытие не измерялось; процент не приводится.

### Критические пробелы

| Область | Покрытие |
|---|---|
| Tenant isolation | 0; tenant отсутствует |
| Permissions/authentication/CSRF | 0 |
| Order writeoff partial failure/concurrency/idempotency | 0 |
| Warehouse current-stock validation/races | 0 |
| Receipt create/update/delete compensation | 0 |
| Repair/sales/JSON concurrent writes/corruption | 0 |
| Manual sale vs inventory consistency | 0 |
| Backup/restore/migrations | 0 |
| МойСклад main write client retry/429/contracts | 0 |
| Catalog import/retry/idempotency | Хорошее относительное покрытие |

## 14. Поддерживаемость кода

Основной structural smell — не язык или Flask, а отсутствие слоёв для operational domain. Из 12 867 Python-строк 7 499 находятся в `app/web.py`. Шаблоны содержат собственные большие UI subsystems, поэтому изменение одной операции требует синхронно понимать route, JSON shape, external API и inline JS.

### Десять наиболее проблемных участков

| # | Путь/участок | Причина и влияние | Направление | Срочность |
|---|---|---|---|---|
| 1 | `app/web.py` целиком | 7 499 строк; routing/domain/persistence/reporting/integrations; высокий regression radius | Постепенно выделить modules/services/repositories/commands, сохраняя routes | High |
| 2 | `app/web.py:4889-5636` | `receipts_import_preview` 748 строк с parsing, matching, aggregation, response | Pure parser/validator service + bounded DTO + unit tests | Medium |
| 3 | `app/web.py:5641-6323` | `receipt_create` 683 строки и внешние/local side effects | Receipt command service, transaction state/idempotency/reconciliation | Critical/High |
| 4 | `app/web.py:412-559` | Sequential multi-item stock writeoff без atomic business state | Order fulfillment aggregate + operation ledger/locks | Critical/High |
| 5 | `app/clients/moysklad.py:5-655` | Client смешивает transport, lookup, folder rules, payloads; GET/PUT/POST errors различны | Один session/transport policy, typed errors, provider services | High |
| 6 | `app/web.py:724-828,1637-1732,1792-2052` | `get_warehouse_items`, cell helpers и tree funcs определены повторно; ранний код фактически dead/сломанный | Удалить после characterization tests; один warehouse service | Medium |
| 7 | `app/templates/sales.html`, `receipts.html`, `warehouse.html` | 4–4,7 тыс. строк каждый, inline CSS/JS, трудно тестировать и использовать CSP | Разделить templates/components/static assets; browser tests | Medium |
| 8 | `app/web.py:3429-3698` и `4376-4668` | Почти дублированное построение sales records для report/page | Единый query/view-model service | Medium |
| 9 | Все `load_*/save_*` JSON helpers | Разные paths, error semantics, atomicity; silent empty fallback | Единый repository с schema/version/lock; затем relational migration | High |
| 10 | `app/catalog_db.py:11-210` + deploy | Schema-as-create, migration lifecycle отсутствует | Versioned migrations, startup/readiness check, backup-before-migrate | High |

Дополнительные признаки: 7 имён функций повторно определены в `app/web.py`; global caches/state; imports внутри функций; marker comments `V1/V2/FINAL OVERRIDES`; `base.html` фактически не используется как общий layout; настройки branding не применяются. TODO/FIXME/HACK почти нет, но отсутствие пометок не означает отсутствие debt.

Типизация отсутствует. Ошибки часто ловятся `except Exception`; некоторые функции маскируют повреждение данных пустым списком. Имена в новой catalog subsystem последовательнее, чем в старом web module.

## 15. Производительность и масштабирование

### Подтверждено кодом

| Проблема | Доказательство | Эффект |
|---|---|---|
| Warehouse ограничен первыми 1000 products/stock rows | `app/web.py:1809-1813` | После 1000 позиции отсутствуют, а не paginated |
| Synchronous external I/O в request | orders/warehouse/mapping/receipts/writeoff | Медленная страница занимает worker; external outage снижает capacity |
| Для каждого enter/loss дополнительно читаются первая org/store | `app/clients/moysklad.py:240-304,307-411` | Минимум 3 HTTP calls на operation; multi-item order — ещё больше |
| Reports читают все local arrays и live warehouse, строятся в RAM | `app/web.py:3429-3862,3883-4369` | Memory/latency растут линейно |
| XLSX upload полностью читается в bytes | `app/web.py:4916-4924` | До 15 MB compressed + workbook expansion на request |
| Repair/sales/receipts/stock operations фильтруются in-memory | соответствующие loaders/routes | Нет server pagination/indexes |
| In-process cache не общий между workers | `app/web.py:34-46` | Дубли external calls и разные snapshots |
| Stock journal принудительно обрезается до 1000 | `app/web.py:1375-1378` | Производительность ограничена ценой потери истории |

### Вероятно

- `CatalogReader.list_products` использует несколько correlated scalar subqueries на строку и `%LIKE%` по JSON/text (`app/services/catalog_reader.py:84-173`). При 4–5 тыс. товарах приемлемо, при десятках/сотнях тысяч потребуются query plan/FTS/joins.
- Catalog sync хранит `details_json` со всеми item results для каждого batch; история может расти без retention.
- PDF table с полной выборкой sales может потребовать много памяти/CPU и не иметь ограничения по строкам.
- Embedded JSON write целиком становится всё дороже и расширяет окно race.

### Требует профилирования

- Реальный N+1 внутри Bitrix PHP endpoint: основные данные батчатся, но inherited SEO вычисляется на каждый product (`bitrix/catalog-export.php:654-658`).
- Реальное время/лимиты МоегоСклада и rate-limit thresholds.
- Размер JSON/SQLite, latency pages и worker concurrency в production.
- Индексы для planned tenant-aware queries.

Масштабирование web workers без замены JSON persistence ухудшит корректность: каждый process получит собственный cache, а concurrent file writes станут чаще. Сначала нужна data consistency, затем горизонтальное масштабирование.

## 16. Наблюдаемость и эксплуатация

| Capability | Состояние |
|---|---|
| Structured logging | Нет; `print` и отдельные `app.logger` |
| Error tracking | Нет |
| Metrics/traces | Нет |
| Health endpoint | Нет; deploy GET business pages |
| Readiness | Нет; business routes могут вернуть HTTP 200 с пустыми fallback data |
| Queue monitoring | Очередей нет |
| Audit log | Частичный mutable stock JSON; catalog sync runs |
| Alerting | Нет |
| Correlation/request/operation ID | Только UUID отдельных records, не request correlation |
| Log rotation/retention | Не в repo |
| Backup | Разовые catalog copies в scripts/docs, не системная policy |
| Restore/DR/runbook | Нет |

Ответы на операторские вопросы:

- **Как узнать, что sync перестал работать?** Автоматически никак. Нужно вручную смотреть age/status `catalog_sync_runs`; alert отсутствует. Orders sync как job вообще отсутствует.
- **Как найти неимпортированный заказ?** Нельзя надёжно: локальной таблицы заказов и sync cursor/run нет. Можно только повторно спросить ограниченное окно Bitrix/dry-run и сопоставлять вручную.
- **Как определить причину расхождения остатков?** Сравнить МойСклад documents с максимум 1000 локальными stock operations и application logs. Reconciliation/actor/version snapshot нет; удалённые receipt rows исчезают.
- **Как восстановиться после сбоя?** Полного подтверждённого процесса нет. Git восстанавливает code, но ignored `instance` содержит state и не разворачивается из repo. Deploy rollback откатывает только code commit (`scripts/deploy.sh:49-79`).
- **Как проверить backup?** В repo нет регулярной restore drill. Хеш копии в cleanup script проверяет копирование, но не полноценный запуск/восстановление (`scripts/cleanup_empty_catalog_properties.py:44-64`).

Нужны structured events по `request_id`, `company_id`, `user_id`, `operation_id`, external document ID; SLO/alerts на sync age/errors, stock command failures, external latency/429/5xx; отдельные `/healthz` и `/readyz`; централизованные redacted logs и проверяемый restore runbook.

## 17. Готовность к собственной инфраструктуре

### Фактическая/предполагаемая topology

```text
Internet/оператор
  -> [reverse proxy/TLS: не в репозитории]
  -> systemd clock-erp [unit и app server не в репозитории]
  -> Flask app на loopback
  -> /opt/clock-erp/instance (JSON + SQLite, по коду)
  -> внешние Bitrix/МойСклад/hh.ru
```

### Что реализовано

- Защитный deploy script: clean/branch checks, ff-only, syntax parse, restart, service active, два HTTP GET, code rollback (`scripts/deploy.sh`).
- GitHub Actions без production secrets/записей (`.github/workflows/tests.yml`).
- Приложение bind на loopback при direct run.
- Environment-style credentials и ignored local data.

### Частично реализовано

- App service существует по имени в инструкциях/script, но definition/runtime не versioned.
- Rollback откатывает code, но не schema/data/config.
- HTTP checks доказывают ответ страниц, но не readiness внешних integrations/data.
- Разовые catalog backups существуют исторически, но не покрывают все JSON и restore.

### Чего нет

Dockerfile/Compose, reproducible image; app server dependency/command; reverse proxy; TLS/ACME; DNS/domain config; database service; Redis; object storage; persistent volume spec; secrets delivery; migrations-on-deploy; zero-downtime; regular backups; tested restore; monitoring/alerts; CI artifact/deploy promotion; `vechasu.com` public site; `app.vechasu.com` routing; IaC/runbooks.

### Блокеры безопасного переноса

1. Закрытая/internal access model нельзя переносить на публичный app domain без auth/authorization/CSRF.
2. `instance` — набор ignored mutable файлов без inventory/backup/restore/migration.
3. Runtime нельзя воспроизвести из Git; port discrepancy 5050/5000.
4. Secret rotation и management не завершены доказуемо.
5. Нет data reconciliation перед/после переноса.
6. Tests основного ERP и production smoke/readiness недостаточны.
7. Нет monitoring, RPO/RTO и restore drill.

Рекомендуемая минимальная topology без преждевременной сложности: один managed/самостоятельный relational database, 1–2 app processes за reverse proxy/TLS, отдельный scheduler/worker для sync/reconciliation, централизованные logs/metrics, encrypted backups и object storage только если появятся реальные файлы. Конкретный cloud provider по репозиторию определить нельзя и сейчас выбирать необязательно.

## 18. Функциональная инвентаризация

| Модуль | Функция | UI | Backend | База/источник | Тесты | Состояние | Tenant-ready | Примечание |
|---|---|---|---|---|---|---|---|---|
| Заказы | Список/карточка | Да | Да | Remote Bitrix/cache | Dry-run client only | Частично | Нет | Нет persistence/pagination/retry в UI |
| Заказы | Смена статуса | Да | Да | Custom Bitrix POST | Нет | Работает по коду, небезопасно | Нет | Hardcoded token/status mapping |
| Заказы | Product mapping | Да | Да | JSON | Matching tests отдельно | Частично | Нет | Два несовпадающих path mapping |
| Заказы | Списание | Да | Да | МойСклад + JSON | Нет | Частично/риск | Нет | Partial/race/idempotency |
| Товары | Warehouse list/search/filter | Да | Да | Live МойСклад/cache | Нет | Работает по коду | Нет | До 1000; local template dirty |
| Товары | Add/edit/archive | Да | Да | МойСклад | Нет | Работает по коду, риск | Нет | Нет auth/rollback/idempotency |
| Ячейки | Product/category cell | Да | Да | JSON + МойСклад attr | Нет | Частично | Нет | Не physical move, divergence |
| Остатки | Ручной enter/loss | Да | Да | МойСклад + JSON | Нет | Частично/риск | Нет | Browser current stock trusted |
| Резервы | Просмотр | Да | Да | МойСклад stock report | Нет | Частично | Нет | Изменение резерва отсутствует |
| Каталог | Bitrix export/import | CLI/UI preview | Да | SQLite | Да | Работает по коду | Нет | Лучший по качеству модуль |
| Каталог | Карточки/filters | Да | Да | SQLite | Да | Работает по коду | Нет | Pagination до 100/page |
| Каталог | МойСклад mapping | Да | Да | SQLite + read snapshot | Да | Частично | Нет | Manual confirm local-only |
| Приход | Manual single/multi | Да | Да | МойСклад + JSON | Нет | Частично/риск | Нет | Нет atomicity/idempotency |
| Приход | Excel preview/import | Да | Да | Upload memory + МойСклад | Нет | Частично | Нет | Preview browser payload не trusted artifact |
| Приход | Update/delete | Да | Да | МойСклад + JSON | Нет | Частично | Нет | Update только single-position |
| Продажи | Manual CRUD | Да | Да | JSON | Нет | Работает по коду как registry | Нет | Не меняет inventory |
| Продажи | Automatic from writeoff | Да | Да | Derived JSON | Нет | Частично | Нет | Не полноценная sale entity |
| Продажи | HTML/XLSX/PDF report | Да | Да | JSON + live data | Нет | Работает по коду | Нет | Deps/fonts/runtime не проверены |
| Ремонты | CRUD/status/search | Да | Да | JSON | Нет | Работает по коду | Нет | PII, hard delete, no audit |
| Analytics | Sales/receipts/stock cards | Да | Да | Derived | Нет | Работает по коду | Нет | Не financial ledger |
| Settings | Navigation visibility | Да | Да | JSON | Нет | Работает по коду | Нет | Global |
| Settings | Company/ERP/threshold | Да | Да | JSON | Нет | Заглушка/частично | Нет | Значения не применяются вне page |
| Returns | Возврат | Нет | Нет | Нет | Нет | Отсутствует | Нет | — |
| Payments | Транзакции/возвраты | Нет | Нет | Нет | Нет | Отсутствует | Нет | Только remote display field |
| Users | Login/invites/profile | Нет | Нет | Нет | Нет | Отсутствует | Нет | Блокер |
| Roles | RBAC/object permissions | Нет | Нет | Нет | Нет | Отсутствует | Нет | Блокер |
| Audit | Actor/action history | Частично stock | Частично | Mutable JSON | Нет | Отсутствует системно | Нет | Блокер второго клиента |
| Background sync | Catalog manual CLI | Нет | Да | SQLite runs | Да | Частично | Нет | Нет scheduler/alert/lock |
| Order import | Persistent sync | Нет | Dry-run | Нет | Да dry-run | Заглушка/research | Нет | Не production flow |

## 19. Что отсутствует для подключения второго клиента

### Обязательное до подключения

- [ ] Ввести `Company`, `User`, `Membership`, `Role/Permission` и безопасный login/invite lifecycle.
- [ ] Выбрать tenant из trusted session/subdomain, а не form/query; требовать tenant context в каждом command/query/job.
- [ ] Перенести operational JSON в transactional tenant-aware хранилище; мигрировать Tictactoy как tenant 1 с reconciliation.
- [ ] Добавить `company_id NOT NULL` во все core records и tenant-aware FK/unique/index.
- [ ] Реализовать object-level authorization и отрицательные cross-tenant tests для каждого route/service.
- [ ] Разделить Bitrix/МойСклад credentials, endpoints, iblock/pipeline/fields, store/organization и status mappings по company.
- [ ] Tenant-keyed caches, sync cursors, operation IDs, audit logs, reports и file namespaces.
- [ ] Исправить stock/receipt idempotency, partial failure и reconciliation до импорта второго набора остатков.
- [ ] Устранить hardcoded/current historical secrets и подтвердить rotation.
- [ ] Регулярный encrypted backup всех authoritative data + успешный restore drill.
- [ ] Health/readiness, monitoring и alerts на integrations/sync/stock command failures.
- [ ] Critical flow tests: auth, permissions, isolation, receipts/writeoffs, retries/idempotency, migrations.
- [ ] Повторяемая команда/API onboarding company без копирования code или ручного редактирования global files.

### Желательно до подключения

- [ ] UI приглашений и role management.
- [ ] Dry-run начального импорта и reconciliation report до commit.
- [ ] Config UI/validation для status/field/delivery/channel mappings.
- [ ] Tenant timezone, locale, currency и brand settings реально применяются.
- [ ] Feature flags/modules per company с server-side enforcement, не только скрытием nav.
- [ ] Support/admin tooling с явным impersonation audit, если оно действительно нужно.
- [ ] Data retention/export/delete policy для PII.

### Можно после пилотного подключения

- [ ] Расширенный white-label branding.
- [ ] Billing/тарифы, если появится коммерческая необходимость.
- [ ] Дополнительные integrations, advanced analytics, уведомления.
- [ ] Self-service onboarding вместо контролируемого операционного onboarding.

### Не нужно делать сейчас

- Отдельный fork/deployment кода на компанию как целевая модель.
- Marketplace integrations, универсальный workflow-builder, Kubernetes, event streaming platform.
- Сложный billing до доказанного платного сценария.
- Массовый redesign всех экранов до обеспечения данных/доступа.

## 20. Приоритетный реестр проблем

Effort предполагает одного разработчика, знакомого с кодом, без закупки внешних сервисов; data migration и согласование API могут увеличить оценку.

| ID | Проблема | Категория | Severity | Business impact | Evidence | Effort | Dependency | Рекомендация |
|---|---|---|---|---|---|---|---|---|
| P-01 | Нет authentication/authorization | Security | Critical | Публичный app раскрывает PII и external writes | SEC-01 | L | Role decisions | Закрыть perimeter, затем auth + RBAC/object policies |
| P-02 | Tenant model отсутствует | Architecture/Security | Critical | Второй клиент смешивает все данные | Раздел 8 | XL | P-01, data design | Company/membership/context + tenant-aware storage |
| P-03 | Stock/receipt side effects неатомарны | Data integrity | High | Двойные/частичные списания, неверные остатки | `app/web.py:412-559,5640-6323` | L | Operation model | Idempotent command ledger + reconciliation |
| P-04 | JSON persistence конкурентно небезопасен | Data integrity | High | Lost updates/corruption/history loss | Все `load_*/save_*` | L | DB selection/migration | Перенести core domain в relational DB |
| P-05 | Hardcoded и historical secrets | Security | High | Изменение заказов/компрометация integrations | `app/web.py:32`; Git history | XS rotation; L history cleanup | Credential owner | Revoke/rotate сначала; затем secret management |
| P-06 | Нет системного backup/restore | Resilience | High | Невосстановимая потеря operational state | Раздел 16/17 | M | Storage inventory | Automated backup + restore drill/RPO/RTO |
| P-07 | Нет monitoring/alerts/readiness | Operations | High | Сбои sync незаметны, false-green deploy | `scripts/deploy.sh:154-173` | M | Runtime/logs | Metrics/events/health/readiness/alerts |
| P-08 | CSRF + stored XSS | Security | High | Browser compromise/невольные external actions | SEC-04/05 | M | P-01 partly | CSRF, safe DOM, CSP, tests |
| P-09 | Tictactoy config в code | Product architecture | High | Onboarding требует code changes | Раздел 9 | M | P-02 | Versioned tenant integration config |
| P-10 | Runtime/deploy невоспроизводим | Infrastructure | High | Небезопасный перенос/rollback | Раздел 17 | M | P-05/P-06 | Versioned app server/service/container + TLS |
| P-11 | Orders не хранятся и list не paginated | Product/Data | High | Пропуск истории/невозможна поддержка | `app/web.py:294-332`; docs order research | L | Bitrix contract, P-02 | Persistent order sync + cursor/upsert |
| P-12 | Migration framework отсутствует | Data/Operations | High | Schema drift, опасный deploy | `app/catalog_db.py:227-229` | M | DB decision | Versioned migrations + rollback/backup tests |
| P-13 | МойСклад выбирает первый store/org | Data integrity | High | Документ в неверном складе/юрлице | `app/clients/moysklad.py:213-230` | S | Tenant config | Explicit required IDs + validation |
| P-14 | Основные ERP flows без tests | Quality | High | Regression в остатках/PII/permissions | Раздел 13 | L | Service extraction | Characterization/integration/concurrency tests |
| P-15 | Main web обходит безопасный Bitrix order client | Integration | Medium | Нет retry/auth/URL hygiene в рабочем UI | `app/web.py:294-367` vs client | S/M | Endpoint contract | Один adapter для UI/CLI |
| P-16 | Dependency reproducibility/CVE visibility низкая | Supply chain | Medium | Разный runtime, неизвестные уязвимости | `requirements.txt` | S | Supported Python | Pin/lock/update via tested process; audit |
| P-17 | Synchronous I/O/full in-memory reports | Performance | Medium | Worker starvation/slow UI | Раздел 15 | M | Metrics first | Timeouts/cache/job exports/pagination |
| P-18 | Нет immutable actor audit | Compliance/Support | Medium | Нельзя расследовать/откатить действие | repair/receipt delete | M/L | P-01/P-02 | Append-only audit per company/user |
| P-19 | Settings company/ERP/threshold косметические | Product | Medium | Ложное ощущение onboarding/config | `app/web.py:7395-7451` | S | P-02 | Применять tenant config или убрать обещание |
| P-20 | Duplicate/dead code и god templates | Maintainability | Medium | Медленные risky changes | Раздел 14 | M/L | Tests first | Постепенная декомпозиция, не rewrite |

Главная зависимость реестра: P-01/P-02 должны сформировать security boundary; P-03/P-04 — гарантии данных; только затем публичный infrastructure move и второй tenant безопасны.

## 21. План работ

План ориентирован на бизнес-цель и допускает постепенное извлечение из монолита. Полная перепись не требуется.

### Этап 0. Немедленное снижение рисков

| Задача | Цель/компоненты | Зависимости | Критерий готовности | Effort | Риск и причина этапа |
|---|---|---|---|---|---|
| Ограничить network access к текущему app | Firewall/VPN/reverse-proxy auth, пока нет app auth | Доступ к infra | Неаутентифицированный Internet не видит ни один route | XS/S | Немедленно снижает SEC-01 до реализации auth |
| Rotate все current/historical credentials | Bitrix status/catalog/orders, МойСклад | Владельцы аккаунтов | Старые значения отвергаются; repo code literal больше не нужен | XS/S | Credential compromise уже доказуем по history/current code |
| Инвентаризировать и backup весь `instance` | JSON, SQLite, service env | Storage access | Encrypted backup + hash + successful restore в isolated environment | S/M | Предотвращает невосстановимую потерю |
| Ввести emergency audit/reconciliation для stock docs | Order writeoff/receipts vs МойСклад document IDs | Read-only API | Отчёт находит orphan/duplicate/partial операции без writes | M | Неправильные остатки — приоритет продукта |
| Защитить опасные routes | Временный deny/role gate, CSRF/Origin, stored XSS fix | Access model minimum | External mutation нельзя вызвать анонимно/cross-site; XSS test | M | Утечка/неверные остатки до большой архитектуры |

### Этап 1. Стабилизация Tictactoy

| Задача | Цель/компоненты | Зависимости | Критерий готовности | Effort | Почему здесь |
|---|---|---|---|---|---|
| Authentication + Tictactoy users/roles | User/Membership/RBAC, actor audit | Product role matrix | Все routes deny-by-default; warehouse write roles tested | L | Сначала защищает текущего клиента |
| Спроектировать tenant-aware relational core с tenant 1 | Orders, operations, receipts, sales, repairs, settings | Data inventory | Schema уже содержит company boundary, хотя client один | L | Избегает повторной migration на этапе 3 |
| Мигрировать JSON с reconciliation | Versioned import, backups, checksums/counts | Новая schema | Old/new totals/IDs согласованы, rollback tested | L | Устраняет corruption/races Tictactoy |
| Idempotent stock command ledger | Writeoff/enter/update/delete | Relational core | Duplicate request создаёт один external doc; partial failure recoverable | L | Correctness до инфраструктуры/tenant |
| Persistent order import | Bitrix adapter, order/items/sync runs | API pagination/DATE_UPDATE | Полный repeatable import без дублей; fixed window limitation решена | L | Поддержка и отчёты не зависят от ephemeral window |
| Characterization/critical tests | Routes/services/integrations | Service boundaries | Permissions, stock, receipts, retries, concurrency green | L | Safety net перед переносом |
| Structured logging/audit foundation | Request/user/company/operation IDs | Auth/core | Любое write действие прослеживается без secrets/PII overflow | M | Нужна для эксплуатации |

### Этап 2. Собственная инфраструктура

| Задача | Цель/компоненты | Зависимости | Критерий готовности | Effort | Почему здесь |
|---|---|---|---|---|---|
| Зафиксировать runtime artifact | App server, Python/deps/fonts, service/container | Stage 1 tests | Чистый host разворачивается повторяемо из commit/artifact | M | Перенос должен быть воспроизводим |
| Database/storage/secret provisioning | Relational DB, encrypted config, volumes | Core schema | Нет secrets в image/Git; startup validates config | M | Authoritative data вне code checkout |
| Reverse proxy, TLS, domains | `app.vechasu.com`; public `vechasu.com` отдельно | Auth/runtime/DNS | TLS, redirects, headers, upload/body/time limits verified | M | Публичный DNS только после security boundary |
| Health/readiness/monitoring | App, DB, sync, integrations | Structured telemetry | Alerts tested; deploy не green при unusable DB/schema | M | Оператор видит сбой |
| Versioned migration/deploy/rollback | CI artifact, migrate, smoke | Backups/tests | Backup-before-migrate, compatible rollback/runbook | M/L | Code rollback без data rollback недостаточен |
| Backup/DR | DB + required files/config metadata | Storage | Регулярный backup, retention, quarterly restore drill, RPO/RTO | M | Production criterion |

### Этап 3. Multi-tenancy

| Задача | Цель/компоненты | Зависимости | Критерий готовности | Effort | Почему здесь |
|---|---|---|---|---|---|
| Enforce tenant context | HTTP session/subdomain, services, CLI/jobs | Company/membership | Любая query/command без tenant fails closed | L | Основа изоляции |
| Tenant-aware constraints/scopes | Все tables/indexes/repositories | Migration framework | Automated schema audit не находит global business unique | L | DB должна запрещать cross-tenant ошибки |
| Split integration config/secrets | Provider accounts/endpoints/store/status mappings | Secret system | Credentials загружаются только для active tenant; audit access | M/L | Нельзя менять env process на request |
| Isolate caches/files/logs/reports | Cache keys, exports, sync cursors | Tenant context | Company B fixture не влияет на A ни на одном слое | M | Закрывает non-DB leakage |
| Cross-tenant security suite | Positive/negative routes/jobs/migrations | Все выше | ID A под user B всегда 404/403, including reports/exports/jobs | L | Definition of Done multi-tenancy |

### Этап 4. Onboarding второго клиента

| Задача | Цель/компоненты | Зависимости | Критерий готовности | Effort | Почему здесь |
|---|---|---|---|---|---|
| Repeatable company provisioning | Company/admin/roles/features/config | Stage 3 | Одна audited command/workflow, без code fork/manual DB edit | M | Реальная product capability |
| Integration discovery/config | Bitrix/МойСклад или alternatives | Клиентский доступ | Read-only connection/test mappings; explicit org/store/status config | M | Различия клиента не должны становиться hardcode |
| Safe initial import | Orders/products/stock dry-run + reconciliation | Importers/backup | Preview approved, counts/checksums, rerun idempotent | M/L | Не смешать/не исказить начальные данные |
| Limited pilot | Малое число users/processes, feature flags | Monitoring/support | 2–4 недели без cross-tenant incident/stock discrepancy | M | Доказывает готовность перед масштабированием |
| Onboarding/runbook/support | Owner, rollback, escalation | Pilot learnings | Другой оператор повторяет процесс по docs | S/M | Убирает зависимость от автора |

### Этап 5. Дальнейшее развитие продукта

После доказанного второго клиента: refunds/returns; полноценные payments/shipments; configurable channels/workflows в пределах реальных различий; self-service onboarding; product analytics; billing при необходимости; дополнительные providers. Каждая функция должна опираться на tenant/auth/audit foundation и подтверждённый спрос.

## 22. Что не следует сейчас разрабатывать

1. **Микросервисы.** Текущая проблема — boundaries и data guarantees внутри одного процесса; network boundaries добавят distributed transactions и observability burden.
2. **Kubernetes.** Один app + DB + worker не требует оркестратора такого уровня; важнее reproducible service, backup и monitoring.
3. **Полная перепись frontend/backend.** Catalog subsystem доказывает, что постепенное выделение модулей возможно. Rewrite отложит auth/tenant/stock correctness.
4. **Сложный billing.** До второго клиента неизвестны тариф и необходимость автоматического взимания.
5. **Universal workflow builder.** Сначала status/field mapping двух реальных компаний; иначе получится недоказанная платформа.
6. **Marketplace integrations.** Сначала стабилизировать два существующих adapter pattern и tenant secrets.
7. **Event streaming/data warehouse.** Текущие объёмы не подтверждают потребность; сначала transactional ledger и обычные audit events.
8. **Горизонтальное autoscaling.** JSON storage и process-local cache делают multiple workers менее корректными. Сначала storage/locking.
9. **Глубокий UI redesign/white label.** Компания/роль/данные/backup важнее косметики; минимальный tenant branding достаточно после Stage 3.
10. **Сложная realtime/WebSocket подсистема.** Нет требования, а tenant-separated channels добавят security surface.
11. **Оптимизация catalog query без профиля.** При исторически документированных ~4 684 товарах сначала собрать metrics/query plans.

## 23. Критерии готовности

### Definition of Done: перенос на собственную инфраструктуру

| Критерий | Статус сейчас | Проверка готовности |
|---|---|---|
| Reproducible runtime/app server | Не выполнено | Чистый host/image запускает pinned artifact |
| Reverse proxy/TLS для app domain | Невозможно определить | External TLS/security header test |
| `vechasu.com` и `app.vechasu.com` routing | Не выполнено в repo | DNS + HTTP/TLS acceptance |
| Authentication/authorization/CSRF | Не выполнено | Negative anonymous/role tests |
| Secrets вне Git/image и rotated | Частично | Rotation evidence + secret scan |
| Versioned migrations | Не выполнено | Upgrade/rollback against production-like copy |
| Все authoritative data inventoried | Частично | Manifest JSON/DB/files/external sources |
| Automated backup | Не выполнено системно | Scheduled encrypted backup succeeds |
| Tested restore/RPO/RTO | Не выполнено | Isolated restore drill with measured time/data loss |
| Health/readiness | Не выполнено | DB/schema required; external degradation explicit |
| Monitoring/alerts/logs | Не выполнено | Synthetic failure triggers actionable alert |
| Safe deploy/rollback | Частично | Artifact promotion, migration compatibility, data-safe rollback |
| Critical tests green | Не выполнено | CI includes auth/stock/receipt/migration tests |
| External API writes disabled in tests | Выполнено в CI design | CI env/fakes and network deny verified |
| Load/capacity baseline | Не выполнено | Agreed concurrent users/report/import test |
| Operator runbook | Не выполнено | Fresh operator performs deploy/recovery |

### Definition of Done: подключение второго клиента

| Критерий | Статус сейчас | Проверка готовности |
|---|---|---|
| Данные компаний изолированы | Не выполнено | Cross-tenant suite + DB constraints |
| Object-level authorization | Не выполнено | User B cannot enumerate/read/write A by IDs |
| Фоновые задачи имеют tenant context | Не выполнено | Job without tenant rejected; per-tenant cursor/lock |
| Уникальность tenant-aware | Не выполнено | Schema audit of every natural unique/index |
| Integration credentials разделены | Не выполнено | Per-tenant encrypted lookup/access audit |
| Client settings не hardcoded | Не выполнено | Tictactoy-specific grep limited to its config/adapter fixtures |
| Company создаётся repeatably | Не выполнено | Automated provisioning rerunnable/idempotent |
| Безопасный initial import | Не выполнено | Dry-run, approval, idempotent apply, reconciliation |
| Audit trail | Не выполнено | Actor/company/before-after/operation IDs immutable |
| Backup + restore | Не выполнено | Tenant-aware restore scenario tested |
| Monitoring | Не выполнено | Per-tenant sync/write health and alerts |
| Critical processes tested | Частично только catalog | Auth/isolation/stock/orders/import/migrations green |
| No code fork/deployment fork required | Не выполнено | Second client provisioned by config/data only |
| Timezone/currency/store/status mapping | Не выполнено | Client acceptance tests |
| Support/rollback plan | Не выполнено | Pilot runbook and rollback rehearsal |

## 24. Вопросы владельцу проекта

Ниже только вопросы, на которые нельзя достоверно ответить по репозиторию. Они не меняют вывод о текущей технической готовности, но нужны для проектирования целевого состояния.

### Продуктовые

1. Какие модули обязательны для первого коммерчески пригодного релиза Vechasu, а какие можно временно оставить только Tictactoy?
2. Какая система должна быть источником истины для заказа, товара, цены и остатка после переноса: Vechasu, Bitrix или МойСклад?
3. Какие процессы возврата, оплаты, доставки и резервирования реально используются Tictactoy и должны войти в целевую модель, даже если их сейчас нет в коде?
4. Какие гарантии правильности остатков приемлемы для бизнеса: строго синхронная фиксация, eventual consistency или ручное согласование исключений?

### Инфраструктурные

5. Как выглядит фактическая текущая production-топология вне репозитория: ОС, reverse proxy, WSGI-сервер, firewall, TLS, мониторинг и место хранения данных?
6. Кто управляет DNS-зонами `vechasu.com` и `app.vechasu.com`, и готовы ли сертификаты/записи для переключения?
7. Каковы фактические объёмы: одновременные пользователи, заказов и складских операций в день, товаров, размер SQLite/JSON и пиковые размеры отчётов/импортов?
8. Какие требуются RPO, RTO, окно обслуживания, срок хранения резервных копий и географическое размещение данных?

### Интеграционные

9. Есть ли документированные контракты и владельцы фактически используемых Bitrix order endpoints, включая окно выборки, пагинацию, статусы и семантику повторной записи?
10. Какие организации, склады, проекты и типы цен в аккаунте МоегоСклада должны выбираться вместо текущего правила «первый доступный»?
11. Известны ли договорные rate limits, webhook-возможности и гарантии идемпотентности Bitrix и МоегоСклада для используемых тарифов?
12. Какие интеграции будут у второй компании и совместимы ли их поля, статусы, склады и каталог с текущими адаптерами?

### Данные и безопасность

13. Кто сейчас имеет сетевой доступ к приложению и существует ли внешний слой authentication/reverse-proxy access control, отсутствующий в репозитории?
14. Были ли обнаруженные credentials из Git-истории и hardcoded token отозваны и перевыпущены? Подтверждение ротации в репозитории отсутствует.
15. Каковы юридические требования к персональным данным заказов и ремонтов: основание обработки, retention, удаление, экспорт и аудит доступа?
16. Какие резервные копии реально существуют сейчас, где они хранятся, шифруются ли и когда последний раз проходило полное тестовое восстановление?

### Подключение второго клиента

17. Разрешено ли компаниям иметь общий каталог/справочники, или изоляция должна быть абсолютной для всех сущностей и файлов?
18. Какие роли и матрица полномочий нужны внутри клиента, и требуется ли отдельный оператор Vechasu с ограниченным support-доступом?
19. Каковы ожидаемые дата пилота, объём начального импорта, допустимый downtime и критерии успешности второй компании?
20. Нужны ли второй компании собственные branding, домен, timezone, валюта, нумерация документов и биллинг уже в пилоте?

## 25. Индекс доказательств

### Ключевые файлы

| Путь | Назначение | Почему важен |
|---|---|---|
| `AGENTS.md` | Локальные правила разработки/deploy | Фиксирует production path, service, запреты на deploy и безопасный pipeline |
| `requirements.txt` | Python dependencies | Показывает Flask/requests/openpyxl/reportlab и отсутствие закрепления большинства версий |
| `.github/workflows/tests.yml` | Единственный CI workflow | Подтверждает Python 3.10, пустые integration env и запуск `unittest`/`compileall` |
| `.env.example` | Шаблон runtime config | Подтверждает набор integration variables без раскрытия значений |
| `.gitignore` | Исключения Git | Подтверждает исключение `.env` и большей части runtime data |
| `app/web.py` | Flask app, routes, domain/integration/persistence logic | Главный monolith; авторизация отсутствует, external writes и JSON persistence находятся здесь |
| `app/templates/_sidebar.html` | Общая навигация | Показывает фактические модули интерфейса |
| `app/templates/warehouse.html` | Warehouse UI и JavaScript | Содержит клиентскую логику stock operations и подтверждённый stored-XSS sink |
| `app/templates/sales.html` | Sales UI/report client logic | Один из трёх крупнейших шаблонов, отражает значительную UI complexity |
| `app/templates/receipts.html` | Receipts/import UI | Связан с крупным синхронным import flow в `web.py` |
| `app/catalog_db.py` | SQLite schema initialization | Единственное формальное реляционное ядро: 11 таблиц, constraints и транзакции catalog subsystem |
| `app/services/catalog_reader.py` | Чтение/поиск каталога | Параметризованный SQL и allowlist sanitizer описаний |
| `app/services/bitrix_catalog_importer.py` | Импорт каталога | Транзакционный batch import, режимы обновления, hash и sync run |
| `app/clients/bitrix_catalog.py` | Read-only catalog client | HTTPS bearer, pagination, timeout/retry и normalization |
| `app/clients/bitrix_orders.py` | Безопасный read-only orders client | Более качественный adapter используется dry-run, но не основным UI flow |
| `app/clients/moysklad.py` | МойСклад adapter | Global token, read/write operations, отсутствие retry и выбор первого org/store |
| `scripts/sync_bitrix_catalog.py` | Ручная catalog sync CLI | Cursor только после полного успеха; scheduler/lock отсутствуют |
| `scripts/bitrix_orders_dry_run.py` | Безопасная проверка orders mapping | Подтверждает dry-run путь и расхождение пути к mappings с web app |
| `scripts/deploy.sh` | Deploy/rollback script | Единственное описание production topology; SSH, `main`, systemd, curl, code-only rollback |
| `bitrix/catalog-export.php` | Bitrix-side catalog endpoint | Hardcoded client configuration, bearer verification, pagination, неполный incremental criterion |
| `tests/` | Unit/integration checks | Покрывает catalog adapters/import/DB/sync и orders dry-run, но не основные write flows |
| `docs/bitrix_orders_import_research.md` | Исследование orders import | Документирует ограниченное окно выгрузки и ещё не реализованное durable storage proposal |
| `instance/` | Локальные runtime JSON stores | Глобальные mutable business data без tenant key/schema/transaction; содержимое не читалось |

### Выполненные команды и результаты

Все команды выполнялись локально в `/Users/maksim/Projects/clock-erp`; внешние API и production не вызывались. Значения `.env`, credentials и персональные данные не читались и не выводились.

| Команда или группа | Краткий результат |
|---|---|
| `pwd` | Подтверждён корень `/Users/maksim/Projects/clock-erp` |
| `git status --short --branch` | Изначально `main`, ветка отстаёт от локального `origin/main` на 1; уже был изменён `app/templates/warehouse.html` |
| `git branch --show-current`; `git rev-parse HEAD`; `git rev-parse origin/main` | `main`; HEAD `5351ff23343b5566597c01c9c28c7adc47992a82`; локальный ref `origin/main` указывает на `a94a6b2…` |
| `git rev-list --count HEAD`; анализ `git log`/`git shortlog` | 105 commits: 97 non-merge и 8 merge; история 2026-07-08…2026-07-21; 6 author identities; 57 commits затрагивают `app/web.py` |
| `git ls-files`; `rg --files`; `wc -l/-c` по типам | 72 tracked files, 1 941 116 bytes, 49 439 строк; основные breakdown приведены в разделе 2 |
| `find`/`rg --files` по `app`, `tests`, `scripts`, `docs`, `instance` | Инвентаризированы code/config/tests/docs и только имена runtime JSON; содержимое runtime `instance/` не читалось |
| `sed`, `nl -ba`, `rg` по routes/functions/config/security/tenant keywords | Просмотрены entry points, 39 routes, adapters, persistence, templates, deploy, tests; Company/User/Role/auth/tenant/queue не найдены |
| Python AST-скан `app/**/*.py`, `scripts/**/*.py`, `tests/**/*.py` | 30 Python-файлов; 7 повторно определённых имён функций в `app/web.py`; статически найден 71 test method в 14 классах/9 файлах |
| Python `compile()` всех 30 Python-файлов при `PYTHONDONTWRITEBYTECODE=1` | Успешно, 0 syntax failures; файлов не создано |
| JSON parse для tracked `*.json` | 2 tracked JSON-файла успешно разобраны, 0 ошибок |
| `PYTHONDONTWRITEBYTECODE=1 ... python3 -m unittest discover -s tests -v` с пустыми integration env | Запуск не завершён полноценно: `Ran 12`, 4 DB tests прошли, 8 test modules дали import ERROR из-за `ModuleNotFoundError: requests`; 67 методов не загрузились |
| `python3 -m pip check` | Команда успешна: `No broken requirements found`; результат малоинформативен, так как project dependencies не установлены |
| `bash -n scripts/deploy.sh` | Успешная syntax check |
| `command -v php` | PHP отсутствует; lint `bitrix/catalog-export.php` не выполнен |
| `command -v ruff mypy pylint flake8 pip-audit gitleaks` | Эти инструменты отсутствуют; lint/type/dependency/full secret checks не выполнены |
| Таргетированный `rg` secret-pattern scan текущего дерева | Найден hardcoded token-like credential в `app/web.py:32`; значение в отчёте заменено на `[REDACTED]`; private/cloud key patterns не найдены |
| `git log`/`git show` для истории `.env` с выводом только имён переменных | В первом commit исторически были непустые МойСклад/Bitrix credentials; значения не выводились; последующее удаление найдено |
| `git diff -- app/templates/warehouse.html`; `git diff origin/main -- ...` | Подтверждено, что изменение шаблона существовало до аудита и не относится к отчёту; файл не изменялся аудитором |
| `git diff --check` | Успешно на момент промежуточной проверки; повторяется после завершения отчёта |
| `git status --short` | До создания отчёта: только ` M app/templates/warehouse.html`; после — дополнительно `?? CODEX_AUDIT_VECHASU.md` |

Ограничение воспроизводимости: repository не содержит lockfile/готового окружения, а установка dependencies запрещена условиями задачи. Поэтому Flask/Jinja smoke, полный test suite, coverage, PHP lint, линтеры, type-check, dependency audit и полноценный secret scanner не выполнялись. Это не меняет статические выводы о наличии/отсутствии auth, tenant keys, транзакционных границ и deployment artifacts, но снижает уверенность в runtime-совместимости всех маршрутов.

## 26. Пакет данных для внешнего консультанта

# ДАННЫЕ ДЛЯ ПЕРЕДАЧИ CHATGPT

Ниже самодостаточная выжимка аудита. Она основана на commit `5351ff23343b5566597c01c9c28c7adc47992a82` и рабочем дереве на момент проверки. Секреты и содержимое operational data намеренно исключены.

## 1. Краткое описание продукта

Vechasu — внутренняя операционная ERP интернет-магазина Tictactoy на Flask. Интерфейс покрывает просмотр/изменение Bitrix-заказов, склад и ячейки, документы поступления/списания в МоемСкладе, продажи, ремонты, отчёты, каталог и глобальные настройки. Это полезная работающая по коду внутренняя система, но не готовая multi-tenant SaaS-платформа: большинство operational данных хранится в глобальных JSON-файлах, каталог — в SQLite, заказы читаются удалённо, а authentication/authorization отсутствуют.

Ближайшая цель — перенести приложение на собственную инфраструктуру, использовать `vechasu.com` для публичного сайта и `app.vechasu.com` для приложения, сохранить Tictactoy и подключить вторую компанию без fork. Текущее заключение: перенос в публичный production и второй клиент небезопасны до устранения security/data-integrity блокеров. **Подключить вторую компанию прямо сейчас без риска смешивания данных — нет.**

## 2. Полный стек

| Слой | Факт |
|---|---|
| Языки | Python, HTML/Jinja, inline CSS/JavaScript, PHP, Bash, JSON/Markdown |
| Backend | Flask; единый global `app` и большая часть routes/business logic в `app/web.py` |
| Frontend | Server-rendered Jinja; standalone templates с крупными inline CSS/JS; bundler отсутствует |
| API clients | `requests`; Bitrix orders/catalog, МойСклад, hh.ru areas |
| Operational storage | Глобальные JSON в `instance/`; remote Bitrix/МойСклад остаются частью фактического state |
| Catalog DB | SQLite через raw `sqlite3`, 11 таблиц; ORM отсутствует |
| Data/import libraries | `openpyxl`; `reportlab`; `python-dotenv` |
| Runtime | Python; CI заявляет 3.10, локально проверялся system Python 3.9.6; production app server не определён |
| Dependencies | `requirements.txt`; большинство версий не закреплено, lockfile отсутствует |
| Tests | Built-in `unittest`; GitHub Actions запускает discovery и `compileall` |
| Lint/type | Конфигурации и доступные локальные tools не найдены |
| Container/IaC | Docker/Compose/Kubernetes/Terraform отсутствуют |
| Deploy | Bash + SSH + Git fast-forward + systemd restart + curl; hardcoded host/path/service |
| Workers/queue/cache | Worker, broker, scheduler, Redis отсутствуют; есть process-local TTL caches |
| Observability | `print`/basic app logs; metrics/traces/error tracker/alerts/readiness отсутствуют |

## 3. Текстовое дерево ключевых директорий

```text
clock-erp/
├── app/
│   ├── web.py                         # Flask app, 39 routes, основная ERP-логика
│   ├── main.py                        # отдельная проверка Bitrix connection
│   ├── catalog_db.py                  # SQLite DDL/transactions каталога
│   ├── sync.py                        # пустой модуль
│   ├── clients/
│   │   ├── bitrix_catalog.py          # read-only catalog API client
│   │   ├── bitrix_orders.py           # read-only orders client для dry-run
│   │   └── moysklad.py                # read/write client МоегоСклада
│   ├── services/
│   │   ├── bitrix_catalog_importer.py # transactional catalog import
│   │   ├── catalog_reader.py          # catalog queries/sanitization
│   │   └── moysklad_catalog_mapping.py
│   └── templates/                     # Jinja + крупные inline CSS/JS
├── bitrix/
│   └── catalog-export.php             # endpoint на стороне Bitrix
├── instance/                          # global mutable JSON runtime data
├── scripts/                           # deploy, sync, dry-run, cleanup/import helpers
├── tests/                             # 9 test files; catalog/orders-centric
├── docs/                              # исследования и исторические инструкции
├── .github/workflows/tests.yml        # единственный CI workflow
├── requirements.txt
└── CODEX_AUDIT_VECHASU.md             # этот отчёт
```

Объём tracked repository: 72 файла, 1 941 116 bytes, 49 439 строк. `app/web.py` — 7 499 строк; крупнейшие templates: `sales.html` 4 756, `receipts.html` 4 103, `warehouse.html` около 4 тыс. строк.

## 4. Архитектура

```text
Неаутентифицированный пользователь браузера
  -> Flask routes + Jinja + inline JavaScript (`app/web.py`, `app/templates/`)
      -> встроенная business/orchestration logic
          -> JSON files (`instance/`: операции, ячейки, ремонты, продажи, настройки)
          -> SQLite catalog (`catalog.db`, raw sqlite3)
          -> Bitrix custom order endpoints (read + status write)
          -> МойСклад REST API (products/folders/stock/enter/loss/delete)
          -> hh.ru areas API (справочник, 30-day file cache)

Ручной CLI `scripts/sync_bitrix_catalog.py`
  -> Bitrix PHP catalog export
  -> page normalization/importer
  -> SQLite catalog transaction per batch + sync_runs/cursor
```

HTTP, UI, domain orchestration, external I/O и persistence смешаны в `app/web.py`. Catalog subsystem выделен лучше: client → importer/reader → SQLite. Фоновых процессов нет: reports, import и external writes выполняются синхронно в web request либо вручную через CLI. Общих service transaction boundaries между local state и remote APIs нет.

## 5. Главные сущности базы данных

Формальная SQLite-схема определена в `app/catalog_db.py` и создаётся через `CREATE TABLE IF NOT EXISTS`, без versioned migrations:

| Сущность/таблица | Назначение и связи |
|---|---|
| `catalog_categories` | Иерархия категорий через self-FK `parent_id`; external ID/name |
| `products` | Основная карточка товара; external ID, content, timestamps, payload hash |
| `product_categories` | M:N products ↔ categories, composite key/FK |
| `properties` | Определения свойств |
| `product_property_values` | Значения свойств продукта, FK |
| `offers` | Варианты/SKU, FK на product |
| `offer_property_values` | Значения свойств offer, FK |
| `images` | Изображения product/offer; ограничение owner type, но нет unique порядка/URL |
| `prices` | Цены product/offer; сумма хранится text, currency/unit |
| `moysklad_mappings` | Связка catalog product с global unique МойСклад ID |
| `sync_runs` | Запуски импорта, status/counts/cursor/error |

Operational сущности не являются таблицами: warehouse cells, stock operations, repairs, manual sales, sales overrides, receipts и settings — структуры JSON без schema/FK/tenant/actor constraints. Orders/order items не персистируются: они нормализуются из удалённого ответа в памяти. Нет таблиц Company, User, Role, Permission, Customer, Payment, Integration Account или Audit Event. Все 11 таблиц SQLite также не имеют `company_id`.

## 6. Модель авторизации

Модели авторизации **нет**. В коде не обнаружены login/logout, User, password hash, session identity, JWT, RBAC/policies, route guards, object-level permission или super-admin boundary. Все 39 Flask routes доступны по самому факту сетевого доступа; 23 routes принимают POST и часть выполняет записи в Bitrix/МойСклад или local operational state. CSRF protection и rate limiting отсутствуют. Возможный внешний reverse-proxy control вне repository — **не подтверждено** и не может считаться application authorization.

## 7. Модель multi-tenancy

Multi-tenancy **отсутствует**. Tenant не определяется в HTTP, CLI или cache; нет middleware/context/policies. Нет company key в SQLite и JSON. Settings, filenames, integration credentials, endpoints, organization/store selection, report scope, sync cursor и process caches глобальны. Уникальные ограничения catalog mapping глобальны. Роль пользователя и company membership отсутствуют.

Конкретный cross-tenant сценарий: если в существующий process направить данные/credentials второй компании, те же routes читают и перезаписывают общие JSON, используют общий cache и первый доступный МойСклад org/store; отчёты агрегируют общий набор. Разделить компании фильтром UI невозможно — boundary отсутствует на storage, service и authorization уровнях.

## 8. Интеграции

| Интеграция | Реализация | Основные свойства/риски |
|---|---|---|
| Bitrix orders | Прямые `requests` в `app/web.py` | Hardcoded Tictactoy endpoints; list без доказанной pagination; status write; timeout, но нет единого retry/idempotency; заказы не хранятся |
| Bitrix orders dry-run | `app/clients/bitrix_orders.py`, `scripts/bitrix_orders_dry_run.py` | HTTPS, bearer option, retry/timeout; безопаснее, но main UI его обходит |
| Bitrix catalog | PHP `bitrix/catalog-export.php` + Python client/importer/CLI | Bearer, pagination, retry 429/5xx, batch transactions; offers empty; cursor manual; incremental filter может пропустить price-only changes |
| МойСклад | `app/clients/moysklad.py` | Global bearer; read/write; timeout 8 s; нет retry/429 policy; mixed error semantics; первый org/store; main warehouse limit 1000 |
| hh.ru areas | Прямой GET в `app/web.py` | 30-day local cache; нужен для repair geography; нет отдельного adapter/monitoring |
| XLSX | `openpyxl` import/export | Upload до 15 MB читается целиком; preview до 5 000 rows; receipt apply создаёт remote/local state неатомарно |
| PDF | `reportlab` | Синхронная генерация в памяти; hardcoded Linux font paths |
| Email/payments/notifications/object storage/auth providers/monitoring | Не найдены | Функциональность отсутствует либо находится вне repository — не подтверждено |

Credentials приходят из global environment, кроме обнаруженного hardcoded status token `[REDACTED]`. В ранней Git-истории были непустые `.env` credentials `[REDACTED]`; их ротация не подтверждена.

## 9. Как выполняются фоновые задачи

Worker, queue, scheduler, cron definitions, dead-letter queue и distributed lock отсутствуют. Catalog sync запускается вручную командой `scripts/sync_bitrix_catalog.py`; каждая page импортируется транзакционно, parent cursor продвигается только при полном успехе, но process lock/automatic retry/alert отсутствуют. Orders загружаются синхронно при HTTP-запросе и кэшируются в process на 60 секунд; warehouse — на 300 секунд. Stock/receipt remote writes, reports и file imports также синхронны. Поэтому зависший внешний API занимает web worker, два процесса не разделяют cache/lock, повторный HTTP submit может дублировать side effect.

## 10. Как разворачивается приложение

Единственная автоматизация — `scripts/deploy.sh`: проверяет clean Git state, push ветки `main`, SSH на hardcoded host, fast-forward checkout в `/opt/clock-erp`, Python syntax parse, restart systemd service `clock-erp`, затем curl. Это кодовый deploy с попыткой code rollback; backup/migration/restore данных, artifact/image, dependency installation, readiness и zero-downtime strategy отсутствуют. Script ожидает порт 5000, тогда как прямой dev entry в `app/web.py` использует 127.0.0.1:5050 и `debug=True`; реальный service unit/app server не хранится в repo.

Нет Dockerfile/Compose, reverse-proxy/TLS config, DB/volume provisioning, Redis, object storage, service manifest, log rotation, backup schedule, IaC или domain configuration для `vechasu.com`/`app.vechasu.com`. Фактическая production topology вне script — **не подтверждена**. Перенос должен выполнять отдельно одобренный deployment process; этот аудит production не подключал и ничего не разворачивал.

## 11. Результаты тестов и проверок

- Статически найден 71 `unittest` method в 14 классах/9 файлах. Основной фокус: Bitrix catalog client/importer/DB/sync, orders client/dry-run и МойСклад mapping. Нет tests auth, tenant isolation, permissions, stock commands, receipts, repairs, sales, JSON concurrency, migrations, CSRF/XSS или E2E.
- `python3 -m unittest discover -s tests -v` в безопасном окружении: `Ran 12`; 4 catalog DB tests прошли, 8 test modules завершились import ERROR из-за отсутствующего `requests`; остальные test methods не загрузились. Dependencies не устанавливались по ограничению задачи.
- Python syntax: 30 файлов скомпилированы через `compile()` без записи bytecode, 0 failures.
- Tracked JSON: 2 файла разобраны, 0 failures. Runtime JSON contents не читались и не модифицировались.
- `bash -n scripts/deploy.sh`: успешно. `git diff --check`: успешно на промежуточной проверке и повторяется в финале.
- `python3 -m pip check`: формально успешно, но не доказывает полноту среды, поскольку project packages отсутствуют.
- Не выполнены: Flask/Jinja smoke, полный suite/coverage, PHP lint, lint/type-check, dependency vulnerability audit, exhaustive secret scan. Причины: отсутствующие dependencies/tools/runtime и запрет их установки.
- Внешние API, production, deploy, migrations и write operations не вызывались.

## 12. Топ-15 проблем с severity

| № | Severity | Проблема | Главное доказательство/ущерб |
|---|---|---|---|
| 1 | Critical | Нет authentication/authorization | Все routes unguarded; публичный доступ означает PII exposure и external writes |
| 2 | Critical | Нет tenant model/isolation | Ни одна DB/JSON/HTTP/integration/cache boundary не знает company |
| 3 | High | Неатомарные stock/receipt side effects | `app/web.py:412-559,5641-6323`; partial remote/local success и double submit |
| 4 | High | Конкурентно небезопасное JSON storage | Read-modify-overwrite без общей блокировки/transaction; lost update/corruption |
| 5 | High | Hardcoded и исторические credentials | `app/web.py:32`, ранняя Git-история; значения `[REDACTED]` |
| 6 | High | Нет системного backup/проверенного restore | Operational state распределён по JSON/SQLite/external APIs |
| 7 | High | Нет monitoring/alerts/readiness | Сломанный sync или data drift оператор автоматически не увидит |
| 8 | High | CSRF и stored XSS | Нет CSRF; unsafe `innerHTML` в `warehouse.html:3023-3041` для stored operation fields |
| 9 | High | Tictactoy config зашита в code/global settings | Endpoints/statuses/brand/defaults требуют code/config process changes |
| 10 | High | Runtime/deploy невоспроизводим | Нет app server manifest/lock/container/TLS; deploy зависит от mutable host |
| 11 | High | Orders не сохраняются и list pagination не доказана | Потеря истории/окна, слабая диагностика и отчётность |
| 12 | High | Нет versioned migrations | Только `CREATE TABLE IF NOT EXISTS`; schema drift и небезопасный deploy |
| 13 | High | МойСклад выбирает первый org/store | Документ может попасть в неверное юрлицо/склад |
| 14 | High | Критические ERP writes почти не тестируются | Regression может изменить реальные остатки и PII |
| 15 | Medium | Main UI обходит более безопасный Bitrix orders client | Дублирование HTTP logic, хуже retry/auth/URL validation |

## 13. Топ-15 рекомендуемых задач

Порядок важен; задача следующего этапа не заменяет незавершённые data/security prerequisites.

1. Немедленно закрыть текущий app сетевым access control до появления application auth.
2. Отозвать/перевыпустить все current и historical Bitrix/МойСклад credentials; удалить hardcoded secret из будущей конфигурации через secret store.
3. Инвентаризировать authoritative JSON/SQLite/external state, сделать encrypted backup и провести isolated restore drill с измеренными RPO/RTO.
4. Добавить временную безопасную reconciliation-проверку remote stock documents против local operation IDs, без автоматических writes.
5. Реализовать authentication, deny-by-default RBAC/object policies, CSRF protection и actor identity; закрыть stored XSS.
6. Спроектировать relational operational core сразу с `company_id`, membership, tenant-aware FK/unique constraints и versioned migrations.
7. Мигрировать global JSON в DB через repeatable import, checksums/count reconciliation, backup и tested rollback.
8. Ввести idempotent command/operation ledger для loss/enter/receipt/status writes с external ID, state machine, retry и recovery partial failure.
9. Сделать durable paginated order import/upsert с cursor, source payload/version и reconciliation вместо ephemeral list.
10. Покрыть permissions, tenant isolation, inventory concurrency, receipts, retries/idempotency и migrations automated tests с fake/network deny.
11. Зафиксировать reproducible runtime: supported Python, pinned/locked dependencies, WSGI server, fonts и immutable artifact/service/container.
12. Настроить DB/storage/secret provisioning, reverse proxy, TLS и разделённые `vechasu.com`/`app.vechasu.com` после security boundary.
13. Добавить structured logs с request/user/company/operation IDs, health/readiness, metrics и проверяемые alerts без утечки PII/secrets.
14. Enforce tenant context в HTTP/services/CLI/jobs/cache/files/reports/integration credentials и доказать отрицательными cross-tenant tests.
15. Создать repeatable onboarding второй компании: explicit integration mappings/org/store, dry-run initial import, reconciliation, pilot, support/rollback runbook — без fork кода.

## 14. Список неизвестных

- Фактическая production topology, WSGI/reverse proxy/TLS/firewall и внешний authentication layer.
- Владельцы/состояние DNS `vechasu.com` и `app.vechasu.com`.
- Реальные нагрузки, объёмы данных, latency/error rate API и capacity requirements.
- Установленные на production Python/package версии и точный launch command service.
- RPO/RTO, retention, фактические backup copies и результат последнего restore drill.
- Ротация обнаруженных current/historical credentials.
- Полные Bitrix order API contracts: pagination, fixed window, rate limits, webhook, idempotency и owners.
- Требуемые МойСклад organization/store/project/price type и rate-limit contract.
- Источник истины и правила reconciliation для orders/products/prices/stock.
- Полные процессы reservation, returns, payments, delivery и cancellation.
- Фактическая модель пользователей/ролей и юридические требования к PII.
- Нужна ли абсолютная изоляция каталогов или допустимы shared reference data.
- Интеграции, объёмы, timezone/currency/status mappings/roles второй компании.
- Дата и success criteria пилота, допустимый downtime/cutover plan.
- Нужны ли branding/custom domain/billing второй компании на первом этапе.

## 15. Пути к ключевым файлам

- Flask/domain/routes: `app/web.py`.
- Главный warehouse XSS evidence: `app/templates/warehouse.html:3005,3023-3041`.
- Catalog schema: `app/catalog_db.py`.
- Catalog importer/reader: `app/services/bitrix_catalog_importer.py`, `app/services/catalog_reader.py`.
- Bitrix clients: `app/clients/bitrix_catalog.py`, `app/clients/bitrix_orders.py`.
- МойСклад client: `app/clients/moysklad.py`.
- Catalog sync/dry-run: `scripts/sync_bitrix_catalog.py`, `scripts/bitrix_orders_dry_run.py`.
- Production script: `scripts/deploy.sh`.
- Bitrix endpoint: `bitrix/catalog-export.php`.
- CI/tests: `.github/workflows/tests.yml`, `tests/`.
- Runtime stores: `instance/` (только состав исследован; contents исключены).
- Полный аудит с evidence: `CODEX_AUDIT_VECHASU.md`.

## 16. Хеш текущего Git-коммита

`5351ff23343b5566597c01c9c28c7adc47992a82` (`main`; локальный `origin/main` на момент начала аудита был на один commit впереди).

## 17. Незакоммиченные изменения

Да. До аудита уже существовало изменение `app/templates/warehouse.html`; аудитор его не менял. Единственный созданный аудитом файл — `CODEX_AUDIT_VECHASU.md`. Ожидаемый финальный `git status --short`:

```text
 M app/templates/warehouse.html
?? CODEX_AUDIT_VECHASU.md
```
