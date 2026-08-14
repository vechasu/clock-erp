# Архитектурные риски

Оценка относится к baseline `3158cb7`; `effort` — порядок устранения, не estimate.

| Risk | Impact | Likelihood | Effort | Urgency | Evidence / mitigation direction |
| --- | --- | --- | --- | --- | --- |
| Hardcoded secret-like order-status token in `web.py` | critical | high | small | now | Credential boundary violated; rotate externally and load from environment without printing value ([`app/web.py`](../../app/web.py), [`app/config.py`](../../app/config.py)) |
| `web.py` is a 17.4k-line cross-domain composition/route/business/persistence module | high | high | large | now | 363 functions, 143 rules and broad imports; extract characterized slices ([analysis](web-py-decomposition.md)) |
| Remote writes and local state are not atomic/idempotent | critical | medium | large | now | Orders/receipts/catalog can partially save; introduce adapter commands, operation ledger/outbox/compensation per flow ([flows](request-flows.md), [integrations](integration-boundaries.md)) |
| JSON stores are operational databases with uneven locking/backup | high | medium | large | next | Repairs have `flock`+atomic replace, other files vary; define ownership, locking and migration plans ([data](data-and-transactions.md)) |
| Inventory has service-mediated atomic paths plus legacy/manual remote paths | high | medium | large | now | Protect one stock command boundary and preserve movements invariant ([`sales_inventory.py`](../../app/services/sales_inventory.py), [`app/web.py`](../../app/web.py)) |
| Broken registered pages `/overview`, `/orders`, `/analytics` | high | high | small | now | Missing templates confirmed; separate product fix with route contract tests ([current state](current-state.md#подтверждённые-дефекты-контрактов)) |
| Mixed product identity/source-of-truth across ERP, Bitrix and МойСклад | high | high | large | now | Make ownership ADR per entity and mapping invariant ([data](data-and-transactions.md), [ADR candidates](../decisions/README.md)) |
| Inconsistent API generations and error/pagination contracts | medium | high | medium | next | `/api` and `/api/v1` aliases plus v1-only endpoints; publish compatibility manifest before changes ([`app/web.py`](../../app/web.py)) |
| Large Jinja pages mix HTML/CSS/JS and duplicate state handling | medium | high | large | next | Extract shared primitives incrementally; do not force React rewrite ([frontend](frontend-boundaries.md)) |
| Service-to-service import cycle in product classification/sync | medium | medium | medium | next | Introduce neutral DTO/policy or dependency inversion after characterization ([`product_classification.py`](../../app/services/product_classification.py), [`bitrix_erp_product_sync.py`](../../app/services/bitrix_erp_product_sync.py)) |
| Integration retry/error/logging policies differ | high | high | medium | next | Standard adapter protocol and safe error taxonomy; preserve read-only client strengths ([integrations](integration-boundaries.md)) |
| Audit completeness is caller-dependent | high | medium | medium | next | Mutation use cases should own audit append in same local transaction ([`audit_journal.py`](../../app/services/audit_journal.py)) |
| SQLite serialized writers and in-memory caches limit scale-out | medium | medium | large | later | Measure contention/volume before DB change; no premature PostgreSQL migration ([`catalog_db.py`](../../app/catalog_db.py), [`app/web.py`](../../app/web.py)) |
| Tests import `app.web` broadly and app construction has global side effects | high | high | medium | now | 36 test files reference `app.web`; add app factory/route contract seams incrementally ([tests](../../tests), [decomposition](web-py-decomposition.md)) |
| No confirmed private off-site backup or restore rehearsal | critical | medium | medium | now | Local deploy backup is insufficient for site loss; owner must approve encrypted off-site policy and drills ([`scripts/deploy.sh`](../../scripts/deploy.sh), [ADR candidates](../decisions/README.md)) |
| Auth store, routes, mail, CLI and global guard share one module | high | medium | medium | next | Separate adapters/use cases only after Repairs pilot; preserve security behavior ([`app/auth.py`](../../app/auth.py)) |

No risk above was remediated by this documentation PR. Production likelihood and
actual incident history remain unknown unless supported by repository tests/docs.
