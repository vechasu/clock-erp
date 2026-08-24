# Production-safe SQLite migrations

Статус: `current` для migration preflight и deploy gate.

## Production runtime

На 2026-08-24 production-приложение использует Python 3.6.8 и встроенный
SQLite 3.7.17. Системный `sqlite3` также имеет версию 3.7.17. Целевой версией
совместимости считается `sqlite3.sqlite_version` из virtualenv Gunicorn, а не
версия SQLite на машине разработчика. SQLite, Python и ОС этим контуром не
обновляются.

## Источник схемы и журнал

Историческая схема остаётся в `app/catalog_db.py`: строка `SCHEMA` и методы
`CatalogDatabase._ensure_*`. Они нужны для безопасного завершения старых
additive upgrades, но больше не должны впервые исполняться новым worker на
production.

Новый журнал `erp_migration_ledger` хранит стабильный ID, название, checksum,
состояние, время применения и commit приложения. Историческая таблица
`erp_schema_migrations` сохраняется без выдуманного backfill. Первый журналируемый
этап — `2026-08-24-production-schema-baseline-v1`: он выполняет существующий
schema path, проверяет фактический контракт и только после успеха получает
состояние `applied`.

Строки `applying` и `failed`, неизвестный ID и изменённый checksum являются
неоднозначным состоянием и останавливают deploy. Runner не угадывает результат
и не исправляет production автоматически.

## Обязательный preflight

`scripts/migration_preflight.py preflight` запускается кодом нового commit до
`git merge`, остановки и reload production-сервиса. Он работает тем же Python и
SQLite, которыми запускается Gunicorn:

1. проверяет точную SQLite version и запрещённый для 3.7 SQL;
2. создаёт SQLite `.backup` production-каталога;
3. проверяет копию через `quick_check` и `foreign_key_check`;
4. применяет зарегистрированные миграции к rehearsal-копии;
5. повторяет их и сравнивает schema fingerprint и бизнес-агрегаты;
6. создаёт fresh database и сравнивает таблицы, колонки, внешние ключи,
   индексы и triggers со схемой обновлённой копии;
7. сохраняет отчёт и завершает работу ненулевым кодом при любом расхождении.

Успешные и неуспешные rehearsal-каталоги хранятся в выделенном каталоге с
правами `0700` и удаляются через семь дней. При ошибке путь остаётся в отчёте
для диагностики. Исходная production-база preflight не изменяется.

Static CI guard дополняет, но не заменяет exact-runtime rehearsal. Он запрещает
partial indexes, modern UPSERT, `RETURNING`, generated columns, `STRICT`,
`WITHOUT ROWID`, новые формы `ALTER TABLE` и window functions в migration SQL.

## Worker guard

После успешной production migration runner атомарно записывает marker и
sentinel рядом с `catalog.db`. При их наличии обычный
`CatalogDatabase.initialize()` работает только как read-only verifier:

- сверяет ledger ID и checksum;
- сверяет checksum schema source;
- проверяет schema fingerprint и обязательный контракт;
- не запускает DDL.

Изменение schema source без нового production apply приводит к отказу запуска,
а не к скрытой миграции внутри Gunicorn worker.

## Deploy и rollback

Порядок `scripts/deploy.sh`:

`PRECHECK → BACKUP → MIGRATION PREFLIGHT → APPLICATION UPDATE → PRODUCTION
MIGRATION → SERVICE START → HEALTH CHECK → POST-DEPLOY INTEGRITY`.

Перед production apply сервис остановлен, делается отдельный проверенный
rollback-backup и фиксируются агрегаты товаров, продаж, строк продаж, движений,
заказов, приходов, ремонтов, комментариев и активных инвентаризаций. После
миграции агрегаты должны совпасть побайтно в JSON-отчёте.

Baseline не является одной общей транзакцией: исторические `executescript` и
rebuild-операции имеют собственные границы. При прерывании ledger остаётся в
`failed`; безопасный rollback — остановленный сервис, сохранение failed database
для расследования и восстановление проверенного файла backup. После старта
проверяются сервис, HTTP 200, ledger, marker, `quick_check`,
`foreign_key_check`, schema fingerprint и агрегаты.

Изменение любого старого `scripts/migrate_*.py` блокируется deploy, пока оно не
будет явно включено в versioned preflight. Это предотвращает появление второго
неучтённого migration path.

## Добавление следующей миграции

Отдельный PR должен:

1. добавить новый неизменяемый ID и checksum в registry;
2. описать транзакционность, прерывание и backup recovery;
3. добавить fresh/upgrade/repeat/interruption/parity tests;
4. пройти static guard и exact SQLite 3.7.17 rehearsal;
5. не изменять существующую запись и checksum baseline;
6. обновить этот runbook при изменении операционного контракта.
