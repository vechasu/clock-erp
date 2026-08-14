# Эволюционный roadmap

Статус: `proposed`. Каждый этап — отдельный small/reviewable PR от актуального
`main`; функциональная миграция данных и изменение внешних contracts запрещены
без отдельного решения ([target state](target-state.md)).

## Выбор пилота

Выбран **Repairs**, не Authentication. Repairs уже имеет 1,000+ строк pure-ish
rules, migration and atomic file helpers outside `web.py`, один основной Jinja
screen и focused form/API tests
([`repair_cases.py`](../../app/services/repair_cases.py),
[`test_repairs_full_cycle.py`](../../tests/test_repairs_full_cycle.py),
[`test_stage2_repairs_api.py`](../../tests/test_stage2_repairs_api.py)). Поэтому
можно сначала переместить adapters, не меняя storage.

Authentication уже Blueprint, но [`app/auth.py`](../../app/auth.py) одновременно
владеет security-critical store, sessions, CSRF/global guard, email, routes and
CLI. Ошибка затронет все ERP URLs. Его extraction risk выше, несмотря на
существующий Blueprint. Repairs имеет coupling к Bitrix order query and uploads,
но эти зависимости можно передать ports/factories. Поэтому Repairs — лучший
пилот **для разделения `web.py`**, а Auth — более поздний самостоятельный stream.

## Этапы

| Stage / size | Exact scope and functions | Contracts preserved | Characterization / risk | Rollback and done |
| --- | --- | --- | --- | --- |
| 0. Manifest, 1 small PR | Add generated/asserted endpoint-method-template/API response manifest; no moves | Every endpoint name, URL, method, redirects, error envelope; document broken templates as expected failure | Existing route tests plus explicit inventory of [`app/web.py`](../../app/web.py); risk low | Revert tests only. Done when manifest catches accidental removal |
| 1. Repairs presenters, 1 medium PR | Move `prepare_repair_case`, filters/options/payload serializers into `repairs/presentation.py`; compatibility imports in `web` | `/app/repairs`, form field names, JSON keys/status codes | Repair full-cycle + Stage2 API tests; risk stale view-model | Revert imports/move. Done with byte/semantic-equivalent responses for fixtures |
| 2. Repairs routes, 1 medium PR | Move `repair_page/add/update/status/action/logistics_add/delete/attachment` and `api_repair_*` into route factory/Blueprint; inject repository/order query/upload/auth actor | Exact URLs, Flask endpoint names, CSRF, flash/query messages, upload limits and errors | Add endpoint manifest, auth and upload traversal tests; risk endpoint-name/url drift | Keep wrappers/explicit endpoint names; revert registration. Done with unchanged tests and no storage diff |
| 3. Repairs repository boundary, 1 small-medium PR | Wrap `get/load/save/mutate_repair_cases` and attachment paths behind file repository; retain JSON schema and `flock`/temp replace | Existing JSON bytes/schema/migration/backup behavior | Legacy import, concurrency and failure tests; risk file locking/atomicity | Switch injected adapter back. Done with fixture round-trip and failure parity |
| 4. Journal, 1 small PR | Move `journal_page`, `api_journal_collection/event` and filters/presenters; inject `AuditJournal` | URLs, cursor encoding, filters, JSON envelope, Jinja context | [`test_audit_journal.py`](../../tests/test_audit_journal.py); risk cursor/endpoint drift | Compatibility wrappers. Done when current tests run without importing journal internals from web |
| 5. Catalog reads, 2–3 medium PRs | First serializers/query helpers; then warehouse/catalog GET routes and read APIs; leave writes | URLs/query parameters/pagination/sort/template context and image proxy semantics | Catalog/pagination/photo tests; risk cache/query regression and remote N+1 | Route-by-route rollback. Done with manifest and query snapshots |
| 6. Inventory command boundary, 2 medium PRs | Introduce `AdjustStock` facade over current local/remote paths; then route `warehouse_update_stock` and movement queries through it | Stock validation, quantities, movement/history shape, MoySklad document semantics | Inventory concurrency/insufficient stock/remote failure characterization; risk critical partial effects | Feature-free adapter switch back; no data migration. Done only with documented compensation/idempotency behavior |
| 7. Receipts, 2–4 medium PRs | Extract HTTP/presenters, then use cases around existing `ReceiptInventory`; isolate MoySklad adapter | Form/API/Excel/report URLs, idempotency keys, stock effects, JSON compatibility | Receipt API/import/recovery/inventory tests; risk multi-system partial save | Per-route wrappers and current repository adapters. Done with invariant and failure-path tests |
| 8. Sales, 3–5 medium PRs | Extract source normalization/presenters, routes, then application facade around `SalesInventory` and reporting | Form/API/report contracts, WB/Amazon fields, cancel/return rules, pagination | Sales inventory/API/report/filter suites; risk highest local domain breadth | Incremental routes; compatibility re-exports. Done with stock+movement invariants unchanged |
| 9. Auth, 2–4 medium PRs | Split store/session/mail/use cases inside existing Blueprint package; move global web guard only last | Security headers/cookies, CSRF, rate limits, redirects, CLI | Full auth + browser/security characterization; risk system-wide lockout | Maintain old Blueprint/config facade. Done with no policy relaxation |
| 10. Orders/integrations, several medium PRs | First adapter ports/config secret remediation; then order query/status/writeoff use cases; page repair is separate product PR | URLs and external IDs; define idempotency/compensation before moving writes | Mocked timeout/retry/partial-write tests; risk critical | Keep old adapters selectable until parity; no remote tests. Done with no secret in code and explicit operation ledger decision |
| 11. App factory cleanup, 1–2 medium PRs | Register module routes, remove obsolete globals/wrappers and break classification cycle | Import path/app object compatibility for deployment/tests | Full mandatory CI; risk initialization/order | Revert composition only. Done when `web.py` is composition/compatibility, not domain implementation |

## Gate for every stage

No URL or method change; no silent API payload/error change; no data migration;
external systems mocked; targeted characterization plus mandatory CI green;
diff documents rollback; deploy is an independent owner-authorized operation.
Stage 6 onward is blocked until pilot metrics show fewer `web.py` functions/imports
without increasing escaped domain dependencies.
