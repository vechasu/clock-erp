# Auth, orders, customers and comments migrations

Статус: `current` для PR-A после baseline `b3a1fd809aa2d3a7980616724a97439722de72f7`.

## Результат

Auth, orders snapshot и customers переведены на отдельные versioned migrations.
Order comments получили отдельный migration ID в catalog ledger. Runtime stores
теперь только проверяют ledger и семантический schema contract; при старой,
частичной, неизвестной схеме или изменённом checksum они завершаются ошибкой
`migration required` и ничего не исправляют.

Migration IDs:

- `2026-08-26-auth-baseline-v1` — `auth.db`;
- `2026-08-26-orders-customers-baseline-v1` — `orders.db`;
- `2026-08-26-order-comments-baseline-v1` — `catalog.db`.

Каждый ID имеет immutable checksum, состояние `applying/applied/failed`, время,
application commit и details. Auth/orders migration выполняется одной SQLite
transaction под отдельным filesystem lock. Catalog comments использует
существующий catalog runner и его fail-closed ledger/guard.

## Call graph до и после

До PR-A:

```text
Gunicorn worker import → configure_auth → AuthStore._ensure_schema → 10 DDL
каждый request → get_auth_store → AuthStore._ensure_schema → 10 DDL
orders/customer HTTP или background job → initialize → 12 schema statements
catalog migration dispatcher → _ensure_order_comment_schema → ALTER/index DDL
```

После PR-A:

```text
deploy, service stopped
  ├─ catalog migration runner → comments migration
  ├─ auth domain runner → auth ledger/schema
  └─ orders domain runner → orders/customers ledger/schema

Gunicorn worker import → AuthStore → read-only ledger + schema validation
auth request → cached AuthStore → DML/SELECT only
orders/customer request/background → initialize → read-only validation
comment request → guarded CatalogDatabase validation → DML/SELECT only
```

Runtime DDL trace после миграции: auth `0`, orders `0`, customers `0`, comments
`0`. Catalog legacy bootstrap остаётся отдельным P1-контуром: при штатном
production guard он не выполняется, но fail-open при потере sentinel будет
устранён следующим PR.

## Baseline contract

Existing database признаётся текущей только после проверки:

- точного набора domain tables;
- имён, типов, nullable/default/primary-key semantics всех колонок;
- named и unique indexes;
- auth foreign keys;
- допустимых role/state/active значений;
- заполненных и уникальных `(source, external_order_id)` заказов;
- `quick_check=ok` и `foreign_key_check=0`;
- известного ledger ID, checksum и состояния `applied`.

Известны только два legacy upgrade shape: первоначальная таблица `users` и
первоначальная `orders_snapshot`. Все прочие частичные состояния блокируются до
изменения файла и не получают ledger. Production current shape получает только
ledger rows; бизнес-таблицы и данные не переписываются.

Orders schema сравнивается семантически: порядок колонок не входит в contract.
Fresh bootstrap использует фактический production order колонок, production
таблица ради косметического порядка не перестраивается.

Comments используют обычный composite unique index
`(external_system, external_id)` без `WHERE`: SQLite 3.7.17 разрешает несколько
строк с `NULL external_id`, но блокирует повтор реального внешнего ID.

## Production baseline до deploy

- runtime: Python 3.6.8, SQLite 3.7.17;
- commit: `db28de11299351675fad99778b3b26a46ef3ba82`, Git clean, service active;
- catalog: 49 tables, 80 named indexes, comments 6, sync states 5;
- orders: 3 tables, 9 named indexes, orders 50, customers 51;
- auth: 5 tables, 5 named indexes, users 1, sessions 18;
- products 4 724; active stock sum 1 052 679; sales 33; movements 1 394;
- active inventories/items: 0/0;
- все три DB: `quick_check=ok`, foreign key violations `0`.

Персональные данные и содержимое комментариев в отчёты не входят.

## Deploy и rollback

`scripts/deploy.sh` определяет domain migration по изменению auth/orders runner
или stores. До обновления кода он:

1. делает exact-runtime rehearsal на независимых SQLite `.backup` копиях;
2. останавливает service;
3. создаёт проверенные backups catalog/auth/orders;
4. применяет catalog и domain runners до запуска новых workers;
5. сравнивает агрегаты до/после;
6. запускает service и выполняет post-deploy verify.

Любая ошибка до health-check восстанавливает все затронутые DB и прежний commit
как один coordinated rollback, затем возвращает service в `active`. Межфайловая
транзакция не обещается; именно поэтому сохраняются и восстанавливаются все три
verified backups.

## Ограничение

Этот slice не удаляет catalog `SCHEMA` и остальные catalog `_ensure_*`, не
меняет роли, order/customer/comment business logic, синхронизацию Bitrix/WB,
UI, склад, продажи, приходы или ремонты. Следующий PR обязан убрать fail-open
catalog bootstrap при потере runtime sentinel.

Backend score после подтверждённого deploy: 77/100 (до PR-A 73/100). Оценка
учитывает устранение двух P0 runtime-DDL путей и отдельный comments baseline;
catalog sentinel fail-open остаётся P1.
