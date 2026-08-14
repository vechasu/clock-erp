# Архитектура Vechasu ERP

Статус: `current` для commit `3158cb7de5328bd71d28fd6bff30b46edc27fb9b`.
Дата проверки: 2026-08-14. Источник истины аудита — дерево `origin/main`, а не
production и не локальные ветки.

Vechasu ERP — синхронный Flask-монолит с Jinja UI, vanilla JavaScript, SQLite,
runtime JSON-файлами и прямыми HTTP-интеграциями. Основной composition root и
большинство HTTP-контрактов находятся в [`app/web.py`](../../app/web.py),
авторизация подключена Blueprint из [`app/auth.py`](../../app/auth.py). React в
текущем исходном дереве отсутствует: `frontend/` содержит TypeScript/Vite marker
для проверки общей server-rendered раскладки, но не feature pages
([`frontend/package.json`](../../frontend/package.json),
[`frontend/src`](../../frontend/src)).

## Навигация по аудиту

- [Фактическое состояние и слои](current-state.md)
- [Карта модулей](module-map.md)
- [Потоки запросов](request-flows.md)
- [Данные и транзакции](data-and-transactions.md)
- [Frontend-границы](frontend-boundaries.md)
- [Интеграционные границы](integration-boundaries.md)
- [Риски](risks.md)
- [Целевое состояние](target-state.md)
- [Разделение `web.py`](web-py-decomposition.md)
- [Эволюционный roadmap](roadmap.md)
- [ADR-кандидаты](../decisions/README.md)

## Краткий вывод

Система уже содержит полезные зачатки модульного монолита: application-объекты
для каталога и настроек, HTTP adapter для отчётов, транзакционные сервисы продаж
и приходов, отдельные клиенты интеграций. Однако [`app/web.py`](../../app/web.py)
остаётся границей почти всех доменов: 17 428 строк, 363 top-level функции и 143
регистрации URL (137 decorators и 6 `add_url_rule`). Он одновременно валидирует
HTTP, строит view-model, содержит правила, пишет JSON, вызывает SQLite-сервисы и
внешние API. Эти числа получены AST-проверкой текущего baseline; метод и карта
выноса описаны в [отдельном документе](web-py-decomposition.md).

Главная безопасная стратегия — сохранить Flask, URL, JSON-контракты и Jinja,
вводя модульные границы небольшими PR. Пилот — Repairs: правила и файловые
операции уже вынесены в [`app/services/repair_cases.py`](../../app/services/repair_cases.py),
но HTTP и view-model всё ещё сосредоточены в `web.py`; это даёт измеримый вынос
без вмешательства в транзакционное ядро остатков. Обоснование — в
[roadmap](roadmap.md#выбор-пилота).

## Статусы

- `current` — контракт, UI, данные и тесты подтверждены деревом.
- `partial` — работает только часть сценария или границы неоднородны.
- `legacy` — используется, но имеет параллельный новый путь.
- `broken` — зарегистрированный контракт не может завершиться в текущем дереве.
- `planned` — есть предложение, но нет реализации.
- `unknown` — факт нельзя подтвердить репозиторием.

Production commit, доступность внешних систем и off-site backups остаются
`unknown`: в рамках аудита production не исследовался. Старые React-документы —
исторические предложения и не доказывают текущий production-контур
([реестр документов](../document-register.md)).
