# Wildberries automatic synchronization

FAST: systemd oneshot every five minutes, `/orders/new` plus batches of 100
known active order IDs from `/orders/status`. FULL: hourly at minute 02, FAST
plus existing 14-day supply recovery and a rotating batch of 100 terminal orders.
Terminal history has no creation-date cutoff: N terminal orders are revisited
in at most ceil(N/100) successful hourly passes (API failures can delay this).
Terminal wbStatus values: sold, canceled, canceled_by_client, declined_by_client,
defect. Other/unknown statuses continue to be polled. Supplier complete/sorted
are not terminal. Source: https://dev.wildberries.cn/docs/openapi/orders-fbs
No returns/inventory operations are created from WB statuses.

`python -m scripts.wb_orders_sync --mode fast|full` does not import Flask.
HTTP fallback and recovery import share an OS flock on `instance/.wb-sync.lock`.
Override only for tests using WB_SYNC_LOCK_PATH. Manual full sync still runs in
the HTTP request; no background Gunicorn thread is introduced. A busy scheduled
run exits zero, logging that it was skipped; it does not write SQLite diagnostics
concurrently with the lock owner. SQLite transactions contain no network calls.

Existing payloads from orders/new are not replaced. The status patch updates only
supplier_status, wb_status, status, status_name, synced_at, wb_status_checked_at,
wb_raw status keys, wb_status_raw, and the status history. All mappings, product
payloads, sale references, ERP comments and history survive. wb_status_history
is appended only on a changed pair; same-status polling updates freshness only.
Original WB created_at is preserved. No schema migration is introduced.

FAST request budget: 100 HTTP attempts / 180 seconds; FULL: 300 / 420 seconds.
HTTP timeout for CLI: connect 3.05s/read 10s, 2 retries for temporary errors only,
existing exponential backoff/Retry-After (capped at 30s), 210ms request spacing.
Systemd forcibly bounds FAST to 240 seconds and FULL to 480 seconds. No Restart
policy; an error in this service does not stop/restart clock-erp. New schemas,
secrets, dependencies, WB writes and automatic sale posting are not introduced.

Diagnostics distinguish success/partial/error/running, persist last attempt and
last success, counters and full-recovery errors independently. FAST success does
not hide failed FULL recovery. UI warns after 15 minutes without success and
refreshes diagnostics once a minute while the page is visible. Per-order freshness
is wb_status_checked_at; service success does not certify every historical order.

## Measured before deployment

Production read-only inventory: 14 WB snapshots, 11 active. Isolated server
workspace, same Python 3.6.8 / SQLite 3.7.17, synthetic 11-active-order fixture,
5 FAST runs with fake zero-latency WB transport:
median 8.67ms wall / 4.70ms CPU; peak RSS 23,352 KiB (whole process incl fixtures).
2 API calls; 13 SELECTs including diagnostics; 11 snapshot UPDATEs and 2 metadata
INSERT OR REPLACE writes; 4 short transactions. No catalog writes in this case.
100-active-order local fixture: 7.53ms wall / 7.29ms CPU, 2 API calls, 102 SELECTs,
100 snapshot UPDATEs + 2 metadata writes; macOS peak RSS 31,326,208 bytes.
Network latency is NOT included; real WB timing is reported by each CLI run.
Reproduce: `python -m scripts.wb_sync_benchmark --orders 11` (temporary DBs only).

## Installation and safety gates

Before production: inspect full diff, required PR checks, isolated WB tests,
production Python compatibility, and `systemd-analyze verify deploy/systemd/vechasu-wb*`.
Back up runtime databases, configuration and code using the existing deploy flow.
Record orders, mapping/sales counts and protected payload fields before enabling.
Deploy merged source via scripts/deploy.sh. Its controlled app restart is needed
to load the shared HTTP lock/status logic; timer services do not restart the app.
Install only the four reviewed units, daemon-reload, enable/start the two timers.
Persistent=true catches a missed calendar trigger; no RandomizedDelaySec because
production systemd is version 219. FULL is offset to reduce lock collisions.
Check service exit/result/journal, timer NextElapseUSecRealtime, HTTP login 200,
clock-erp active, snapshot uniqueness, unchanged mappings/sales, and wb:5681311322.

## Rollback

Disable and stop only vechasu-wb-sync.timer and vechasu-wb-full-sync.timer.
If a WB job is still active and must be interrupted, stop only its corresponding
vechasu-wb-*-sync.service; SQLite rolls back any uncommitted transaction.
Do not stop clock-erp or restore a stale database over current employee work.
The same new code retains manual full sync with timers disabled. If code rollback
is necessary, revert the task commit through a reviewed PR and normal deploy;
use the recorded deployment backup for investigation, not blind data replacement.

## Reviewed unit files

### deploy/systemd/vechasu-wb-full-sync.service

```ini
[Unit]
Description=Vechasu WB hourly recovery and terminal reconciliation
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/clock-erp
EnvironmentFile=/etc/clock-erp/clock-erp.env
ExecStart=/opt/clock-erp/venv/bin/python -m scripts.wb_orders_sync --mode full
TimeoutStartSec=480
Nice=10
IOSchedulingClass=idle
CPUQuota=25%
MemoryLimit=256M

```

### deploy/systemd/vechasu-wb-full-sync.timer

```ini
[Unit]
Description=Vechasu WB recovery hourly at minute two

[Timer]
OnCalendar=*-*-* *:02:00
AccuracySec=5s
Persistent=true
Unit=vechasu-wb-full-sync.service

[Install]
WantedBy=timers.target

```

### deploy/systemd/vechasu-wb-sync.service

```ini
[Unit]
Description=Vechasu WB FAST synchronization
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/clock-erp
EnvironmentFile=/etc/clock-erp/clock-erp.env
ExecStart=/opt/clock-erp/venv/bin/python -m scripts.wb_orders_sync --mode fast
TimeoutStartSec=240
Nice=10
IOSchedulingClass=idle
CPUQuota=25%
MemoryLimit=256M

```

### deploy/systemd/vechasu-wb-sync.timer

```ini
[Unit]
Description=Vechasu WB synchronization every five minutes

[Timer]
OnCalendar=*-*-* *:00/5:00
AccuracySec=5s
Persistent=true
Unit=vechasu-wb-sync.service

[Install]
WantedBy=timers.target

```
