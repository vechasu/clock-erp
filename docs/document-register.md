# Реестр документации Vechasu ERP

Реестр составлен 2026-08-14 по чистому `origin/main` на commit
`975ef2572edfbf3568c5fac430d31f9d79af1d23`. Production не проверялся: задача
не разрешает SSH или deploy. Дата в колонке «Проверено» взята только из самого
документа либо обозначает текущую сверку с кодом; выдуманных дат нет.

Статусы: `current`, `draft`, `deprecated`, `archive`, `unknown` — определения
приведены в [главной навигации](README.md#статусы-документов).

| Текущий путь | Назначение | Подтверждение кодом | Статус | Проверено | Связанный модуль | Рекомендуемый раздел | Обновление | Противоречие | Действие |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AGENTS.md` | Правила работы Codex с проектом | Не код; применимые инструкции прочитаны полностью | `current` | 2026-08-14 | весь проект | корень | да | Да: верхний автоматический режим расходится с нижними правилами и `docs/agent-pipeline.md` | Оставить; владельцу унифицировать правила |
| `README.md` | Корневая точка входа | Содержит компактную карту основной документации | `current` | 2026-08-14 | весь проект | корень | нет | нет | Оставить минимальным |
| `.github/pull_request_template.md` | Шаблон описания и проверок Pull Request | `.github/workflows/tests.yml` подтверждает CI; разрешения на выпуск задаются не кодом | `current` | 2026-08-14 | quality, operations | `.github/pull_request_template.md` | да | Да: запрещает автоматические merge/deploy, а приоритетный раздел `AGENTS.md` требует полный автоматический выпуск | Оставить на месте; унифицировать после решения владельца |
| `.github/ISSUE_TEMPLATE/codex-task.yml`, `config.yml` | Шаблон продуктовой задачи и настройки создания Issues | Структура используется GitHub; поведение внешнего UI не проверялось | `current` | 2026-08-14 | product, quality | `.github/ISSUE_TEMPLATE/` | да | Да: прямо говорит, что задача не разрешает автоматический deploy, в отличие от приоритетного раздела `AGENTS.md` | Оставить на месте; унифицировать формулировки после решения владельца |
| `docs/README.md` | Карта разделов, статусы и правила документации | Сверено с деревом `origin/main` | `current` | 2026-08-14 | документация | `docs/README.md` | нет | нет | Оставить |
| `docs/document-register.md` | Инвентаризация и план будущего размещения | Сверено с Git-деревом и выбранными реализациями | `current` | 2026-08-14 | документация | `docs/document-register.md` | поддерживать | нет | Обновлять при каждом изменении документов |
| `docs/product/README.md` | Карта существующих продуктовых модулей, URL, действий и тестов | Сверено с Flask routes, services, templates и test tree | `current` | 2026-08-14 | product | без изменений | поддерживать | нет | Обновлять при изменении подтверждённого product contract |
| `docs/architecture/README.md` | Фактическая архитектура и границы current/legacy/planned/unknown | Сверено с `app/`, `frontend/`, routes и storage code | `current` | 2026-08-14 | architecture | без изменений | поддерживать | нет | Не добавлять production-факты без проверки |
| `docs/design/README.md` | Текущие tokens, primitives, shell, page chrome и UI states | Сверено с CSS, shared JS и Jinja templates | `current` | 2026-08-14 | design | без изменений | поддерживать | нет | Поддерживать общий визуальный контракт без изменения page-specific поведения |
| `docs/ux/README.md` | Навигация, query state, поиск, фильтры, таблицы и responsive behavior | Сверено с routes, templates, shared JS и browser tests | `current` | 2026-08-14 | UX | без изменений | поддерживать | нет | Фиксировать общий visual contract отдельно от различий поведения страниц |
| `docs/quality/definition-of-done.md` | Практический checklist готовности изменения | Команды и примеры сверены с CI, package scripts и test tree | `current` | 2026-08-14 | quality | без изменений | поддерживать | нет | Применять только релевантные проверки |
| `docs/agent-pipeline.md` | Процесс ветка → проверки → Draft PR → merge → deploy | `.github/workflows/tests.yml` и `scripts/deploy.sh` существуют; управленческие разрешения кодом не подтверждаются | `unknown` | 2026-08-14 | разработка, operations | `docs/operations/agent-pipeline.md` | да | Да: запрещает автоматические merge/deploy, а приоритетный раздел `AGENTS.md` требует их автоматически | Не перемещать до решения владельца |
| `docs/article-duplicates-audit-2026-08-11.md` | Снимок дублей артикулов production | Защита новых дублей есть в `app/services/excel_product_catalog.py`; сами production-строки не перепроверялись | `archive` | 2026-08-11 | каталог товаров | `docs/archive/audits/article-duplicates-2026-08-11.md` | нет | нет | Переместить после подтверждения политики архивов |
| `docs/bitrix_catalog_dry_run.md` | Результат read-only dry-run Bitrix | `scripts/bitrix_catalog_dry_run.py` существует; внешние числа не перепроверялись | `archive` | 2026-07-20 | Bitrix catalog | `docs/archive/audits/bitrix-catalog-dry-run-2026-07-20.md` | нет | нет | Архивировать на следующем этапе |
| `docs/bitrix_catalog_endpoint_verification.md` | Проверка внешнего export endpoint | `bitrix/catalog-export.php` существует; внешний endpoint и server state не проверялись | `archive` | 2026-07-20 | Bitrix catalog | `docs/archive/audits/bitrix-catalog-endpoint-2026-07-20.md` | да | нет | Сохранить как историческое доказательство |
| `docs/bitrix_catalog_import_report.md` | Отчёт о production-импорте каталога | Импортёр и схема есть; production-результаты не перепроверялись | `archive` | 2026-07-20 | Bitrix catalog, database | `docs/archive/operations/bitrix-catalog-import-2026-07-20.md` | нет | нет | Архивировать после подтверждения |
| `docs/bitrix_catalog_research.md` | Сводка исследования каталога и предложений | Клиент, импортёр и sync-код существуют; документ смешивает снимок и планы | `unknown` | 2026-07-20 | Bitrix catalog | `docs/technical/integrations/bitrix-catalog.md` | да | Частично: «текущий код» относится к старому baseline | Разделить актуальный контракт и архив исследования |
| `docs/bitrix_catalog_server_research.md` | Снимок структуры сервера Bitrix | Диагностический PHP-скрипт существует; сервер не проверялся | `archive` | 2026-07-20 | Bitrix catalog | `docs/archive/audits/bitrix-server-2026-07-20.md` | да | нет | Архивировать; удалить абсолютные server paths из будущего current-документа |
| `docs/bitrix_catalog_sync.md` | Runbook ручной синхронизации | `scripts/sync_bitrix_catalog.py`, cursor, `updated_from`, `include_inactive` и тест существуют | `current` | 2026-08-14 | Bitrix catalog | `docs/operations/bitrix-catalog-sync.md` | да | нет | На следующем этапе переместить и повторно проверить команды |
| `docs/bitrix_excel_product_reconciliation.md` | Dry-run сопоставления Bitrix и Excel | Сервис/скрипт сопоставления есть; локальный Excel и результаты не проверялись | `archive` | 2026-07-22 | каталог, Excel | `docs/archive/audits/bitrix-excel-reconciliation-2026-07-22.md` | да | Да: содержит абсолютный локальный путь к исходному Excel | Архивировать; privacy/path review |
| `docs/bitrix_orders_import_research.md` | Исследование чтения заказов Bitrix и проект хранения | Клиенты заказов и маршруты есть; предлагаемое хранилище не подтверждено | `unknown` | 2026-07-20 | Bitrix orders | `docs/technical/integrations/bitrix-orders.md` | да | Частично: текущие детали требуют повторной сверки | Разделить факты, историю и предложения |
| `docs/catalog_data_quality_report.md` | Production-аудит качества каталога | Audit-скрипт/сервис существуют; метрики production не перепроверялись | `archive` | 2026-07-20 | каталог, database | `docs/archive/audits/catalog-data-quality-2026-07-20.md` | нет | нет | Архивировать после подтверждения |
| `docs/critical_products_excel_receipt_recovery.md` | Отчёт о восстановлении данных и изменении Excel-прихода | Сервисы recovery/import существуют; production-состояние историческое | `archive` | неизвестно | товары, приходы, operations | `docs/archive/operations/products-excel-recovery.md` | да | нет | Сохранить как incident report; добавить дату при подтверждении |
| `docs/full-react-rewrite-api-map.md` | Целевой REST API и частичный срез Stage 2 | Многие `/api/v1` routes есть, но заявленный полный контракт не реализован | `draft` | 2026-07-30 | API, frontend | `docs/technical/api/full-react-target.md` | да | Да: описывает удалённые/отсутствующие React feature-модули как Stage 2 | Не использовать как current API; переснять фактический manifest |
| `docs/full-react-rewrite-audit.md` | Технический аудит старого baseline и целевая архитектура | Часть архитектуры узнаваема, но номера строк, размеры, route count и frontend inventory устарели | `archive` | 2026-07-29 | весь проект | `docs/archive/audits/full-react-rewrite-2026-07-29.md` | да | Да: утверждает отсутствие `frontend/package.json`/React-инфраструктуры и старые counts; текущий Git содержит их | Архивировать без переписывания истории |
| `docs/full-react-rewrite-feature-matrix.md` | Матрица parity для программы React | Baseline `2212988`; текущие routes и UI существенно изменены | `archive` | 2026-07-29 | product, frontend | `docs/archive/product/full-react-feature-matrix-2026-07-29.md` | да | Да: статусы переноса относятся к прошлому этапу | Создать новую матрицу только при возобновлении программы |
| `docs/full-react-rewrite-risk-register.md` | Риски планируемой React/PostgreSQL программы | Риски частично применимы, но программа и owners не подтверждены | `draft` | 2026-07-29 | architecture, security | `docs/architecture/full-react-risk-register.md` | да | нет | Сохранить как proposal до решения владельца |
| `docs/full-react-rewrite-roadmap.md` | План 21 этапа React/PostgreSQL-перехода | План не является реализацией; текущий статус программы неизвестен | `draft` | 2026-07-29 | product, architecture | `docs/product/full-react-roadmap.md` | да | Частично: часть этапов исторически выполнялась/откатывалась | Перебазировать план только по решению владельца |
| `docs/full-react-rewrite-stage-1-baseline.md` | Отчёт первого этапа React-программы | Vite-инфраструктура есть, но feature source tree из отчёта отсутствует | `archive` | 2026-07-29 | frontend, quality | `docs/archive/reports/full-react-stage-1-2026-07-29.md` | нет | Да: baseline не отражает текущий frontend | Архивировать |
| `docs/full-react-rewrite-stage-2-report.md` | Отчёт Stage 2 по товарам, приходам и продажам | Текущий `frontend/src` не содержит описанных feature modules | `archive` | 2026-07-30 | frontend, API | `docs/archive/reports/full-react-stage-2-2026-07-30.md` | да | Да: реализованный тогда React-срез отсутствует в source tree `origin/main` | Архивировать; владельцу подтвердить историю отката |
| `docs/full-react-rewrite-ui-map.md` | Карта старого UI и проект React-компонентов | Jinja/CSS существуют, но baseline и будущий component list не являются текущим стандартом | `archive` | 2026-07-29 | design, UX, frontend | `docs/archive/design/full-react-ui-map-2026-07-29.md` | да | Частично: смешаны исторические факты и будущие правила | Разделить при создании утверждённых design/UX docs |
| `docs/mvp-performance-audit.md` | Замеры и оптимизации четырёх MVP-разделов | Benchmark-скрипт и server pagination существуют; числа не повторялись | `archive` | 2026-07-31 | performance | `docs/archive/audits/mvp-performance-2026-07-31.md` | да | нет | Архивировать; методику вынести отдельно при новых замерах |
| `docs/screenshots/**` | Визуальные снимки UI, включая compact modals, Stage 2 и отдельные страницы | Файлы присутствуют; соответствие текущему UI и отсутствие чувствительных данных визуально не проверялись | `unknown` | неизвестно | design, UX, quality | `docs/archive/screenshots/` либо утверждённый visual baseline | да | Stage 2 снимки относятся к историческому React-этапу | Провести privacy и актуальность review до перемещения |
| `docs/owner_feedback_audit.md` | Отчёт о реализации пожеланий владельца | Часть маршрутов/хранилищ существует; ветка и поведение исторические | `archive` | 2026-07-22 | product, UX | `docs/archive/audits/owner-feedback-2026-07-22.md` | да | Частично: состояние «до/после» не равно текущему стандарту | Архивировать |
| `docs/products_ui_regression_audit.md` | Разбор регрессии `/products` после PR #9 | Текущий `/products` снова перенаправляет на `/warehouse` | `archive` | неизвестно | товары, UX | `docs/archive/audits/products-ui-regression.md` | нет | Нет для исторического отчёта; его промежуточное состояние устарело | Архивировать |
| `docs/receipt-catalog-audit-pr116.md` | Разбор PR #116 и routing React/Jinja | Текущие `/products`, `/sales`, `/receipts` обслуживаются Jinja, а `/app/*` перенаправляет на `/app/products` | `archive` | 2026-07-30 | receipts, frontend | `docs/archive/audits/receipt-catalog-pr116.md` | да | Да: раздел «исправление подключает React» не соответствует текущему routing | Архивировать; не использовать как current architecture |
| `docs/unified-catalog-migration.md` | Runbook миграции `unified_catalog_v1` | Скрипт, таблицы links/ambiguities и сервис чтения существуют | `current` | 2026-08-14 | database, catalog | `docs/operations/unified-catalog-migration.md` | да | нет | Переместить на следующем этапе; добавить preflight/owner approval |
| `docs/unified-feedback-audit.md` | Контракт глобальных уведомлений | `_sidebar.html`, `notifications.js` и `tests/test_global_notifications.py` подтверждают ядро | `current` | 2026-08-14 | frontend, UX | `docs/technical/frontend/global-feedback.md` | да | нет | Переместить и отделить UX-правила от реализации |
| `docs/decisions/README.md` | Список неподтверждённых ADR-кандидатов | Предложения не объявлены реализацией | `current` | 2026-08-14 | architecture | `docs/decisions/README.md` | нет | нет | Ждать решений владельца |
| `docs/templates/product-module.md` | Шаблон продуктового модуля | Не применимо | `current` | 2026-08-14 | документация | без изменений | нет | нет | Использовать для новых product docs |
| `docs/templates/technical-document.md` | Шаблон технического документа | Не применимо | `current` | 2026-08-14 | документация | без изменений | нет | нет | Использовать для technical docs |
| `docs/templates/adr.md` | Шаблон ADR с четырьмя допустимыми статусами | Не применимо | `current` | 2026-08-14 | документация | без изменений | нет | нет | Создавать ADR только после подтверждения решения |
| `docs/templates/operations-runbook.md` | Шаблон операционного runbook | Не применимо | `current` | 2026-08-14 | документация | без изменений | нет | нет | Использовать для operations docs |

## Отдельный список противоречий

1. `AGENTS.md` одновременно требует автоматический полный выпуск в приоритетном
   разделе и запрещает автоматические merge/deploy в нижних разделах;
   `docs/agent-pipeline.md` поддерживает второй вариант.
2. Набор `full-react-rewrite-*` фиксирует baseline 2026-07-29/30 и React feature
   tree, которого нет в текущем `origin/main`; текущие production-facing routes
   снова используют Jinja, а `/app/*` не обслуживает описанные страницы.
3. `full-react-rewrite-audit.md` содержит точные counts, line references и
   утверждение об отсутствии frontend package infrastructure, которые не
   соответствуют текущему дереву.
4. `receipt-catalog-audit-pr116.md` утверждает, что исправление подключает React
   к `/products`, `/sales`, `/receipts`; текущий код обслуживает эти адреса
   Jinja-маршрутами.
5. `bitrix_excel_product_reconciliation.md` содержит абсолютный путь к локальному
   пользовательскому файлу; это историческое происхождение данных, а не
   переносимый runbook.

Отдельный пробел реализации, выявленный при сверке: current routes `/overview`,
`/orders` и `/analytics` ссылаются на отсутствующие templates. Код в рамках
документационной задачи не изменяется.

Незакоммиченная дизайн-система, `PageHeader`, CSS и `.save` из старого worktree
не исследовались, не копировались и не использовались как источник истины.
