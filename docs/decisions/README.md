# Кандидаты архитектурных решений

Все кандидаты имеют статус `proposed`: код показывает факты, но не заменяет
решение владельца. После подтверждения каждый оформляется отдельным документом
по [ADR template](../templates/adr.md). Baseline фактов — `3158cb7`, обзор —
[`docs/architecture`](../architecture/README.md).

## 1. ERP как источник истины

- **Контекст:** товары, остатки, заказы и операции распределены между двумя
  SQLite, JSON/files, Bitrix и МойСклад ([data map](../architecture/data-and-transactions.md)).
- **Варианты:** объявить ERP owner всех entities; оставить external ownership;
  определить owner отдельно для каждой entity/projection.
- **Рекомендация:** третий вариант; local sales/receipts/movements уже могут быть
  ERP-owned, Bitrix orders/catalog payload и MoySklad documents — external.
- **Последствия:** потребуются mapping, reconciliation and conflict rules; общая
  фраза «ERP — truth» запрещена без entity matrix.
- **Подтверждение владельца:** owner, update direction and conflict authority для
  product identity, stock, order, sale, receipt and repair.

## 2. Модульный монолит

- **Контекст:** Flask монолит уже имеет несколько application/service seams, но
  основной HTTP graph сосредоточен в `web.py` ([current state](../architecture/current-state.md)).
- **Варианты:** сохранить mixed monolith; модульный монолит; services rewrite.
- **Рекомендация:** modular monolith with incremental extraction.
- **Последствия:** больше явных ports/adapters и package ownership, без distributed
  transactions/network complexity.
- **Подтверждение владельца:** целевые module owners и запрет преждевременного
  выделения сервисов.

## 3. Роль Flask, Jinja и React

- **Контекст:** Flask/Jinja/vanilla JS обслуживают UI; React dependency/pages нет,
  Vite проверяет marker ([frontend audit](../architecture/frontend-boundaries.md)).
- **Варианты:** Jinja-first; bounded React islands; full React rewrite.
- **Рекомендация:** Jinja-first; React только по отдельному bounded ADR/use case.
- **Последствия:** текущие templates/URLs сохраняются, shared JS/CSS выделяются
  постепенно; Vite marker не считается product runtime.
- **Подтверждение владельца:** нужен ли React вообще и какой измеримый сценарий
  оправдывает его добавление.

## 4. Направление зависимостей

- **Контекст:** routes напрямую знают services, schema, JSON and clients; найден
  import cycle classification↔sync ([current state](../architecture/current-state.md#связанность)).
- **Варианты:** оставить свободные imports; layers-only; module use cases + ports.
- **Рекомендация:** route → application → domain/ports → adapters; cross-module
  writes only through use cases.
- **Последствия:** dependency injection and DTO overhead, зато тестируемые seams и
  возможность удалить cycle.
- **Подтверждение владельца:** допустимые cross-module reads and enforcement
  mechanism (review/lint/tests).

## 5. Серверная пагинация

- **Контекст:** page/per-page contracts coexist with journal cursor and different
  API limits ([frontend audit](../architecture/frontend-boundaries.md)).
- **Варианты:** one offset contract; cursor everywhere; contract by list type.
- **Рекомендация:** offset for bounded/admin lists, cursor for append-heavy journal;
  preserve current query names until versioned change.
- **Последствия:** documentation and response metadata must be explicit; no single
  accidental universal helper.
- **Подтверждение владельца:** thresholds, max page sizes and API compatibility.

## 6. Транзакционная модель остатков

- **Контекст:** Sales/Receipts update stock+movements atomically in SQLite, while
  manual/order/MoySklad paths cross transaction boundaries
  ([transaction audit](../architecture/data-and-transactions.md)).
- **Варианты:** local DB authoritative with async remote sync; remote authoritative;
  current dual-write with compensation.
- **Рекомендация:** first establish one Inventory command/ledger and explicit
  idempotent external operation protocol; authority awaits ADR #1.
- **Последствия:** characterization, operation keys, reconciliation/compensation;
  no data migration in extraction PRs.
- **Подтверждение владельца:** stock authority, acceptable lag and failure policy.

## 7. Хранение ремонтов

- **Контекст:** repairs currently use locked atomic JSON plus attachment files,
  not SQLite ([data audit](../architecture/data-and-transactions.md)).
- **Варианты:** retain hardened JSON; migrate to existing SQLite; new database.
- **Рекомендация:** retain JSON during route extraction; evaluate SQLite migration
  later using volume/concurrency/recovery evidence.
- **Последствия:** repository port isolates current format; multi-host scaling
  remains constrained until a separate migration.
- **Подтверждение владельца:** retention, concurrency target, attachments policy,
  migration and rollback acceptance.

## 8. Интеграционные адаптеры

- **Контекст:** timeout/retry/error/idempotency policy varies, clients are often
  constructed in handlers ([integration audit](../architecture/integration-boundaries.md)).
- **Варианты:** continue per-call clients; common base class; small typed ports with
  shared policy components.
- **Рекомендация:** domain-specific ports/adapters plus shared safe HTTP policy,
  normalized errors and operation IDs; avoid a god client.
- **Последствия:** mock contract suites, redaction and compensation become explicit.
- **Подтверждение владельца:** retry budgets, audit/log retention and failure UX.

## 9. Private off-site backups

- **Контекст:** repository confirms local deploy backup, not off-site copy or
  restore rehearsal ([deploy script](../../scripts/deploy.sh),
  [risk register](../architecture/risks.md)).
- **Варианты:** local only; encrypted private object storage; managed backup.
- **Рекомендация:** encrypted private off-site copies for SQLite/JSON/uploads with
  retention, integrity checks and restore drills; provider remains open.
- **Последствия:** credentials, cost, data classification, monitoring and tested
  recovery procedure.
- **Подтверждение владельца:** RPO/RTO, provider/region, retention, encryption,
  access and drill schedule.

## 10. Стратегия разделения `web.py`

- **Контекст:** 17,428 lines, 363 functions, 143 URL rules and 36 directly coupled
  test files ([decomposition audit](../architecture/web-py-decomposition.md)).
- **Варианты:** big-bang rewrite; domain-by-domain moves; leave unchanged.
- **Рекомендация:** characterization-first extraction, Repairs pilot, then Journal,
  Catalog reads, Inventory, Receipts/Sales; compatibility wrappers and unchanged
  URL/endpoint names ([roadmap](../architecture/roadmap.md)).
- **Последствия:** more small PRs and temporary facades, but reversible changes and
  no required downtime/data migration.
- **Подтверждение владельца:** pilot order, acceptable PR size and metric for
  completion of compatibility layer removal.
