#!/usr/bin/env bash

set -Eeuo pipefail

readonly EXPECTED_BRANCH="main"
readonly REMOTE_NAME="origin"
readonly SERVER="root@46.254.17.40"

fail() {
    printf 'DEPLOY_ERROR: %s\n' "$*" >&2
    exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || fail "Локальная папка не является Git-репозиторием"
current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ "$current_branch" == "$EXPECTED_BRANCH" ]] \
    || fail "Ожидалась локальная ветка main, текущая ветка: ${current_branch:-detached HEAD}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] \
    || fail "Локальный репозиторий содержит незакоммиченные изменения"

printf 'PRECHECK: pushing %s/%s\n' "$REMOTE_NAME" "$EXPECTED_BRANCH"
git push "$REMOTE_NAME" "$EXPECTED_BRANCH"

ssh \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    "$SERVER" \
    'bash -s' <<'REMOTE_SCRIPT'
set -Eeuo pipefail

readonly EXPECTED_BRANCH="main"
readonly REMOTE_NAME="origin"
readonly PROJECT_DIR="/opt/clock-erp"
readonly SERVICE_NAME="clock-erp"
readonly BACKUP_DIR="/opt/clock-erp-backups"
readonly REHEARSAL_ROOT="$BACKUP_DIR/migration-rehearsals"
readonly RETENTION_TOOL="/usr/local/sbin/clock-erp-backup-retention"
readonly RETENTION_CRON="/etc/cron.d/clock-erp-backup-retention"
readonly RETENTION_LOGROTATE="/etc/logrotate.d/clock-erp-backup-retention"
readonly MAX_BACKUP_DISK_USAGE=85
readonly BITRIX_ENDPOINT_SOURCE="$PROJECT_DIR/bitrix/catalog-export.php"
readonly BITRIX_ENDPOINT_TARGET="/var/www/admin/data/www/tictactoy.ru/api/catalog-export.php"
readonly BITRIX_COMMENT_ENDPOINT_SOURCE="$PROJECT_DIR/bitrix/order-comments.php"
readonly BITRIX_COMMENT_ENDPOINT_TARGET="/var/www/admin/data/www/tictactoy.ru/api/order-comments.php"
readonly BITRIX_ORDERS_EXPORT_SOURCE="$PROJECT_DIR/bitrix/orders-export.php"
readonly BITRIX_ORDERS_EXPORT_TARGET="/var/www/admin/data/www/tictactoy.ru/api/orders-export.php"
readonly CUSTOMERS_CRON="/etc/cron.d/clock-erp-customers"
readonly CUSTOMERS_LOGROTATE="/etc/logrotate.d/clock-erp-customers"
readonly SMS_CRON="/etc/cron.d/clock-erp-sms"
readonly SMS_LOGROTATE="/etc/logrotate.d/clock-erp-sms"
readonly SERVICE_ENV_FILE="/etc/clock-erp/clock-erp.env"
readonly MAIL_CRON="/etc/cron.d/clock-erp-mail"
readonly MAIL_LOGROTATE="/etc/logrotate.d/clock-erp-mail"
readonly HEALTHCHECK_URLS=(
    "http://127.0.0.1:5000/register"
    "http://127.0.0.1:5000/login"
)

FAILURE_STAGE="PRECHECK"
PREVIOUS_COMMIT=""
CURRENT_COMMIT=""
FETCHED_COMMIT=""
PRODUCTION_SQLITE_VERSION=""
RELEASE_DIR=""
BACKUP_TOOL_SOURCE=""
SERVICE_STOPPED=0
DEPLOY_UPDATED=0
CATALOG_MIGRATION_REQUIRED=0
DOMAIN_MIGRATION_REQUIRED=0
PURCHASES_MIGRATION_REQUIRED=0
CUSTOMERS_MIGRATION_REQUIRED=0
SERVICES_MIGRATION_REQUIRED=0
MAIL_MIGRATION_REQUIRED=0
CUSTOMERS_REBUILD_REQUIRED=0
SMS_MIGRATION_REQUIRED=0
UNREGISTERED_MIGRATION_CHANGE=0
CATALOG_MIGRATION_STARTED=0
DOMAIN_MIGRATION_STARTED=0
PURCHASES_MIGRATION_STARTED=0
CUSTOMERS_MIGRATION_STARTED=0
SMS_MIGRATION_STARTED=0
SERVICES_MIGRATION_STARTED=0
MAIL_MIGRATION_STARTED=0
CATALOG_ROLLBACK_BACKUP=""
AUTH_ROLLBACK_BACKUP=""
ORDERS_ROLLBACK_BACKUP=""
TASKS_ROLLBACK_BACKUP=""
TASKS_DATABASE_EXISTED=0
PURCHASES_ROLLBACK_BACKUP=""
PURCHASES_DATABASE_EXISTED=0
CUSTOMERS_ROLLBACK_BACKUP=""
SMS_ROLLBACK_BACKUP=""
SMS_DATABASE_EXISTED=0
SERVICES_ROLLBACK_BACKUP=""
SERVICES_DATABASE_EXISTED=0
DEPLOY_SAFETY_DIR=""
MAIL_ROLLBACK_BACKUP=""
MAIL_DATABASE_EXISTED=0
DATA_SNAPSHOT_BEFORE=""
DATA_SNAPSHOT_AFTER=""
PYTHON_BIN=""
BITRIX_ENDPOINT_BACKUP=""
BITRIX_ENDPOINT_UPDATED=0
BITRIX_COMMENT_ENDPOINT_BACKUP=""
BITRIX_COMMENT_ENDPOINT_UPDATED=0
BITRIX_COMMENT_TARGET_EXISTED=0
BITRIX_ORDERS_EXPORT_BACKUP=""
BITRIX_ORDERS_EXPORT_UPDATED=0
BITRIX_ORDERS_EXPORT_TARGET_EXISTED=0

cleanup_release() {
    if [[ -n "$RELEASE_DIR" && "$RELEASE_DIR" == "$BACKUP_DIR/temporary/release-"* ]]; then
        rm -rf -- "$RELEASE_DIR"
        RELEASE_DIR=""
    fi
    if [[ -n "$BACKUP_TOOL_SOURCE" && "$BACKUP_TOOL_SOURCE" == "$BACKUP_DIR/temporary/retention-"*.py ]]; then
        rm -f -- "$BACKUP_TOOL_SOURCE"
        BACKUP_TOOL_SOURCE=""
    fi
}

rollback() {
    local exit_code=$?
    trap - ERR
    set +e
    printf 'ROLLBACK: stage=%s exit_code=%s\n' "$FAILURE_STAGE" "$exit_code" >&2

    if [[ "$SERVICE_STOPPED" != "1" && ( "$CATALOG_MIGRATION_STARTED" == "1" || "$DOMAIN_MIGRATION_STARTED" == "1" || "$PURCHASES_MIGRATION_STARTED" == "1" || "$CUSTOMERS_MIGRATION_STARTED" == "1" || "$SMS_MIGRATION_STARTED" == "1" || "$SERVICES_MIGRATION_STARTED" == "1" || "$MAIL_MIGRATION_STARTED" == "1" ) ]]; then
        systemctl stop "$SERVICE_NAME"
        SERVICE_STOPPED=1
    fi
    if [[ "$CATALOG_MIGRATION_STARTED" == "1" && -f "$CATALOG_ROLLBACK_BACKUP" ]]; then
        local failed_database="${CATALOG_ROLLBACK_BACKUP%.db}-failed.db"
        cp -p instance/catalog.db "$failed_database"
        cp -p "$CATALOG_ROLLBACK_BACKUP" instance/catalog.db
        sqlite3 instance/catalog.db "PRAGMA quick_check;" | grep -qx "ok"
        printf 'ROLLBACK_OK: restored verified catalog database backup\n' >&2
    fi
    if [[ "$DOMAIN_MIGRATION_STARTED" == "1" && -f "$AUTH_ROLLBACK_BACKUP" ]]; then
        local failed_auth="${AUTH_ROLLBACK_BACKUP%.db}-failed.db"
        cp -p instance/auth.db "$failed_auth"
        cp -p "$AUTH_ROLLBACK_BACKUP" instance/auth.db
        sqlite3 instance/auth.db "PRAGMA quick_check;" | grep -qx "ok"
        printf 'ROLLBACK_OK: restored verified auth database backup\n' >&2
    fi
    if [[ "$DOMAIN_MIGRATION_STARTED" == "1" && -f "$ORDERS_ROLLBACK_BACKUP" ]]; then
        local failed_orders="${ORDERS_ROLLBACK_BACKUP%.db}-failed.db"
        cp -p instance/orders.db "$failed_orders"
        cp -p "$ORDERS_ROLLBACK_BACKUP" instance/orders.db
        sqlite3 instance/orders.db "PRAGMA quick_check;" | grep -qx "ok"
        printf 'ROLLBACK_OK: restored verified orders database backup\n' >&2
    fi
    if [[ "$DOMAIN_MIGRATION_STARTED" == "1" ]]; then
        if [[ "$TASKS_DATABASE_EXISTED" == "1" && -f "$TASKS_ROLLBACK_BACKUP" ]]; then
            cp -p "$TASKS_ROLLBACK_BACKUP" instance/tasks.db
            sqlite3 instance/tasks.db "PRAGMA quick_check;" | grep -qx "ok"
            printf 'ROLLBACK_OK: restored verified tasks database backup\n' >&2
        elif [[ "$TASKS_DATABASE_EXISTED" == "0" && -f instance/tasks.db ]]; then
            rm -f -- instance/tasks.db
            printf 'ROLLBACK_OK: removed newly created tasks database\n' >&2
        fi
    fi
    if [[ "$PURCHASES_MIGRATION_STARTED" == "1" ]]; then
        if [[ "$PURCHASES_DATABASE_EXISTED" == "1" && -f "$PURCHASES_ROLLBACK_BACKUP" ]]; then
            cp -p "$PURCHASES_ROLLBACK_BACKUP" instance/purchases.db
            sqlite3 instance/purchases.db "PRAGMA quick_check;" | grep -qx "ok"
            printf 'ROLLBACK_OK: restored verified purchases database backup\n' >&2
        elif [[ "$PURCHASES_DATABASE_EXISTED" == "0" && -f instance/purchases.db ]]; then
            rm -f -- instance/purchases.db
            printf 'ROLLBACK_OK: removed newly created purchases database\n' >&2
        fi
    fi
    if [[ "$CUSTOMERS_MIGRATION_STARTED" == "1" && -f "$CUSTOMERS_ROLLBACK_BACKUP" ]]; then
        cp -p "$CUSTOMERS_ROLLBACK_BACKUP" instance/customers.db
        sqlite3 instance/customers.db "PRAGMA quick_check;" | grep -qx "ok"
        printf 'ROLLBACK_OK: restored verified customer registry backup\n' >&2
    fi
    if [[ "$SMS_MIGRATION_STARTED" == "1" ]]; then
        if [[ "$SMS_DATABASE_EXISTED" == "1" && -f "$SMS_ROLLBACK_BACKUP" ]]; then
            cp -p "$SMS_ROLLBACK_BACKUP" instance/sms.db
            sqlite3 instance/sms.db "PRAGMA quick_check;" | grep -qx "ok"
            printf 'ROLLBACK_OK: restored verified SMS database backup\n' >&2
        elif [[ "$SMS_DATABASE_EXISTED" == "0" && -f instance/sms.db ]]; then
            rm -f -- instance/sms.db
            printf 'ROLLBACK_OK: removed newly created SMS database\n' >&2
        fi
    fi
    if [[ "$SERVICES_MIGRATION_STARTED" == "1" ]]; then
        if [[ "$SERVICES_DATABASE_EXISTED" == "1" && -f "$SERVICES_ROLLBACK_BACKUP" ]]; then
            cp -p "$SERVICES_ROLLBACK_BACKUP" instance/services.db
            sqlite3 instance/services.db "PRAGMA quick_check;" | grep -qx "ok"
            printf 'ROLLBACK_OK: restored verified services database backup\n' >&2
        elif [[ "$SERVICES_DATABASE_EXISTED" == "0" && -f instance/services.db ]]; then
            rm -f -- instance/services.db
            printf 'ROLLBACK_OK: removed newly created services database\n' >&2
        fi
    fi
    if [[ "$MAIL_MIGRATION_STARTED" == "1" ]]; then
        if [[ "$MAIL_DATABASE_EXISTED" == "1" && -f "$MAIL_ROLLBACK_BACKUP" ]]; then
            cp -p "$MAIL_ROLLBACK_BACKUP" instance/mail.db
            sqlite3 instance/mail.db "PRAGMA quick_check;" | grep -qx "ok"
            printf 'ROLLBACK_OK: restored verified mail database backup\n' >&2
        elif [[ "$MAIL_DATABASE_EXISTED" == "0" && -f instance/mail.db ]]; then
            rm -f -- instance/mail.db
            printf 'ROLLBACK_OK: removed newly created mail database\n' >&2
        fi
    fi
    if [[ "$BITRIX_ENDPOINT_UPDATED" == "1" && -f "$BITRIX_ENDPOINT_BACKUP" ]]; then
        install -o admin -g admin -m 0640 \
            "$BITRIX_ENDPOINT_BACKUP" "$BITRIX_ENDPOINT_TARGET"
    fi
    if [[ "$BITRIX_COMMENT_ENDPOINT_UPDATED" == "1" ]]; then
        if [[ -f "$BITRIX_COMMENT_ENDPOINT_BACKUP" ]]; then
            install -o admin -g admin -m 0640 \
                "$BITRIX_COMMENT_ENDPOINT_BACKUP" "$BITRIX_COMMENT_ENDPOINT_TARGET"
        elif [[ "$BITRIX_COMMENT_TARGET_EXISTED" == "0" ]]; then
            rm -f -- "$BITRIX_COMMENT_ENDPOINT_TARGET"
        fi
    fi
    if [[ "$BITRIX_ORDERS_EXPORT_UPDATED" == "1" ]]; then
        if [[ -f "$BITRIX_ORDERS_EXPORT_BACKUP" ]]; then
            install -o admin -g admin -m 0640 \
                "$BITRIX_ORDERS_EXPORT_BACKUP" "$BITRIX_ORDERS_EXPORT_TARGET"
        elif [[ "$BITRIX_ORDERS_EXPORT_TARGET_EXISTED" == "0" ]]; then
            rm -f -- "$BITRIX_ORDERS_EXPORT_TARGET"
        fi
    fi
    if [[ "$DEPLOY_UPDATED" == "1" && -n "$PREVIOUS_COMMIT" ]]; then
        if [[ -z "$(git status --porcelain --untracked-files=normal)" ]]; then
            git reset --hard "$PREVIOUS_COMMIT"
            printf 'ROLLBACK_OK: restored code commit %s\n' "$PREVIOUS_COMMIT" >&2
        else
            printf 'ROLLBACK_BLOCKED: server source tree became dirty\n' >&2
        fi
    fi
    if [[ "$SERVICE_STOPPED" == "1" ]]; then
        systemctl start "$SERVICE_NAME"
        SERVICE_STOPPED=0
    elif [[ "$DEPLOY_UPDATED" == "1" ]]; then
        systemctl restart "$SERVICE_NAME"
    fi
    systemctl is-active --quiet "$SERVICE_NAME" \
        && printf 'ROLLBACK_OK: service active\n' >&2
    cleanup_release
    exit "$exit_code"
}
trap rollback ERR

check_backup_disk_usage() {
    local usage
    usage="$(df -P "$BACKUP_DIR" | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')"
    [[ "$usage" =~ ^[0-9]+$ ]] \
        || { printf 'BACKUP_ERROR: cannot determine disk usage\n' >&2; return 1; }
    (( usage < MAX_BACKUP_DISK_USAGE )) \
        || { printf 'BACKUP_ERROR: disk usage is %s%%\n' "$usage" >&2; return 1; }
}

stable_data_snapshot() {
    "$PYTHON_BIN" scripts/data_safety_snapshot.py --instance-dir instance |
        "$PYTHON_BIN" -c 'import json,sys; data=json.load(sys.stdin); data.get("catalog",{}).pop("audit_events",None); data.get("auth",{}).pop("sessions",None); print(json.dumps(data,sort_keys=True,separators=(",",":")))'
}

printf 'PRECHECK: repository, service, disk, active operations\n'
cd "$PROJECT_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1
server_branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ "$server_branch" == "$EXPECTED_BRANCH" ]] \
    || { printf 'Server branch must be main: %s\n' "$server_branch" >&2; false; }
server_source_status="$(
    git status --porcelain --untracked-files=normal |
        awk 'substr($0, 4, 9) != "instance/" { print }'
)"
[[ -z "$server_source_status" ]] \
    || { printf 'Server source tree is dirty; deployment stopped\n' >&2; false; }
systemctl is-active --quiet "$SERVICE_NAME"
PREVIOUS_COMMIT="$(git rev-parse HEAD)"
mkdir -p "$BACKUP_DIR" "$BACKUP_DIR/temporary" "$REHEARSAL_ROOT"
chmod 700 "$BACKUP_DIR" "$BACKUP_DIR/temporary" "$REHEARSAL_ROOT"
check_backup_disk_usage

git fetch "$REMOTE_NAME"
FETCHED_COMMIT="$(git rev-parse "$REMOTE_NAME/$EXPECTED_BRANCH")"
BACKUP_TOOL_SOURCE="$(mktemp "$BACKUP_DIR/temporary/retention-XXXXXX.py")"
git show "${FETCHED_COMMIT}:scripts/retain_erp_backups.py" > "$BACKUP_TOOL_SOURCE"
chmod 700 "$BACKUP_TOOL_SOURCE"
python3 -m py_compile "$BACKUP_TOOL_SOURCE"
changed_files="$(git diff --name-only "$PREVIOUS_COMMIT" "$FETCHED_COMMIT")"
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/(catalog_db|catalog_migration_steps|schema_migrations)\.py|app/catalog_schema_manifest\.json|scripts/migration_preflight\.py)$'; then
    CATALOG_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/(sms_migrations\.py|services/sms\.py)|scripts/migrate_sms\.py)$'; then
    SMS_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/(mail_migrations\.py|services/mail\.py)|scripts/(migrate_mail\.py|mail_worker\.py))$'; then
    MAIL_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/(auth|domain_schema_migrations)\.py|app/services/orders_snapshot\.py|scripts/domain_migration_preflight\.py)$'; then
    DOMAIN_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/purchases_migrations\.py|app/services/purchases\.py|scripts/migrate_purchases\.py)$'; then
    PURCHASES_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/customer_registry_migrations\.py|app/services/customer_registry\.py|scripts/(customer_registry_schema|backfill_customers)\.py)$'; then
    CUSTOMERS_MIGRATION_REQUIRED=1
    customer_rebuild_version="$(sqlite3 instance/customers.db "SELECT value FROM registry_meta WHERE key='identity_rebuild_version';" 2>/dev/null || true)"
    if [[ "$customer_rebuild_version" != "safe-identity-v3" ]]; then
        CUSTOMERS_REBUILD_REQUIRED=1
    fi
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^(app/services/service_vault\.py|scripts/migrate_services_vault\.py)$'; then
    SERVICES_MIGRATION_REQUIRED=1
fi
if printf '%s\n' "$changed_files" | grep -Eq \
    '^scripts/migrate_(brand_inventory|inventory_scopes|repair_cases|unified_catalog)\.py$'; then
    UNREGISTERED_MIGRATION_CHANGE=1
fi
if [[ "$UNREGISTERED_MIGRATION_CHANGE" == "1" ]]; then
    printf '%s\n' \
        'PRECHECK_FAILED: changed legacy migration script is not registered in production preflight' >&2
    false
fi
if [[ -f instance/catalog.db ]]; then
    active_inventory_count="$(
        sqlite3 instance/catalog.db \
            "SELECT COUNT(*) FROM erp_inventory_sessions WHERE status = 'active';"
    )"
    if [[ "$active_inventory_count" != "0" ]]; then
        printf 'DEPLOY_BLOCKED: %s active inventory session(s)\n' \
            "$active_inventory_count" >&2
        sqlite3 instance/catalog.db \
            "SELECT 'DEPLOY_BLOCKED_DETAILS: sessions=' || COUNT(*) || \
             ', items=' || COALESCE(SUM(item_count),0) FROM ( \
             SELECT s.id, COUNT(i.id) AS item_count \
             FROM erp_inventory_sessions s \
             LEFT JOIN erp_inventory_items i ON i.session_id=s.id \
             WHERE s.status='active' GROUP BY s.id);" >&2
        false
    fi
fi

printf 'BACKUP: retention, disk guard, daily backup\n'
FAILURE_STAGE="BACKUP"
python3 "$BACKUP_TOOL_SOURCE" --backup-root "$BACKUP_DIR" --apply

check_backup_disk_usage
python3 "$BACKUP_TOOL_SOURCE" --backup-root "$BACKUP_DIR" \
    --project-root "$PROJECT_DIR" --create-daily --apply
if [[ -x venv/bin/python ]]; then
    PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
else
    PYTHON_BIN="python3"
fi
python3 "$BACKUP_TOOL_SOURCE" --backup-root "$BACKUP_DIR" \
    --project-root "$PROJECT_DIR" \
    --create-temporary "pre-services-vault-${FETCHED_COMMIT:0:12}" --apply
DATA_SNAPSHOT_BEFORE="$(stable_data_snapshot)"
printf 'DATA_BEFORE=%s\n' "$DATA_SNAPSHOT_BEFORE"

printf 'MIGRATION PREFLIGHT: stage release and rehearse exact runtime\n'
FAILURE_STAGE="MIGRATION PREFLIGHT"
RELEASE_DIR="$(mktemp -d "$BACKUP_DIR/temporary/release-XXXXXX")"
chmod 700 "$RELEASE_DIR"
git archive "$FETCHED_COMMIT" | tar -x -C "$RELEASE_DIR"
PRODUCTION_SQLITE_VERSION="$(
    "$PYTHON_BIN" -c 'import sqlite3; print(sqlite3.sqlite_version)'
)"
printf 'PRODUCTION_SQLITE=%s\n' "$PRODUCTION_SQLITE_VERSION"
(
    cd "$RELEASE_DIR"
    "$PYTHON_BIN" -m compileall -q app scripts
    "$PYTHON_BIN" - <<'PYTHON_CHECK'
import ast
from pathlib import Path
from jinja2 import Environment
for path in sorted(Path('app').rglob('*.py')):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
environment = Environment()
for path in sorted(Path('app/templates').glob('*.html')):
    environment.parse(path.read_text(encoding='utf-8'))
PYTHON_CHECK
)
printf 'SERVICES VAULT PREFLIGHT: protected key, database copy and systemd wiring\n'
FAILURE_STAGE="SERVICES VAULT PREFLIGHT"
[[ -f "$SERVICE_ENV_FILE" ]] \
    || { printf 'SERVICE_VAULT_PREFLIGHT_FAILED: protected EnvironmentFile is missing\n' >&2; false; }
[[ "$(systemctl show "$SERVICE_NAME" -p EnvironmentFiles | sed 's/^[^=]*=//')" == *"$SERVICE_ENV_FILE"* ]] \
    || { printf 'SERVICE_VAULT_PREFLIGHT_FAILED: systemd does not load the protected EnvironmentFile\n' >&2; false; }
DEPLOY_SAFETY_DIR="$(mktemp -d "$BACKUP_DIR/deploy-safety-XXXXXX")"
chmod 700 "$DEPLOY_SAFETY_DIR"
cp -p "$SERVICE_ENV_FILE" "$DEPLOY_SAFETY_DIR/clock-erp.env-before"
chmod 600 "$DEPLOY_SAFETY_DIR/clock-erp.env-before"
if [[ -f instance/services.db ]]; then
    sqlite3 instance/services.db ".backup '$DEPLOY_SAFETY_DIR/services-before.db'"
    chmod 600 "$DEPLOY_SAFETY_DIR/services-before.db"
    sqlite3 "$DEPLOY_SAFETY_DIR/services-before.db" "PRAGMA quick_check;" | grep -qx "ok"
else
    printf 'SERVICE_VAULT_PREFLIGHT_FAILED: Services database is missing\n' >&2
    false
fi
PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/service_vault_preflight.py" \
    --environment-file "$SERVICE_ENV_FILE" \
    --database "$DEPLOY_SAFETY_DIR/services-before.db" \
    --report "$DEPLOY_SAFETY_DIR/services-preflight.json"
SERVICE_VAULT_KEY="$(PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" - "$SERVICE_ENV_FILE" <<'PYTHON_KEY'
import sys
from scripts.service_vault_preflight import load_key
sys.stdout.write(load_key(sys.argv[1]))
PYTHON_KEY
)"
export SERVICE_VAULT_KEY
WB_API_TOKEN="$("$PYTHON_BIN" - "$SERVICE_ENV_FILE" <<'PYTHON_WB_KEY'
import os
import stat
import sys

path = sys.argv[1]
details = os.stat(path)
if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
    raise SystemExit("WB_SECRET_PREFLIGHT_FAILED: protected EnvironmentFile permissions")
values = []
with open(path, "r", encoding="utf-8") as source:
    for line in source:
        if line.startswith("WB_API_TOKEN="):
            values.append(line.rstrip("\r\n").split("=", 1)[1])
if len(values) != 1 or not values[0] or values[0] != values[0].strip():
    raise SystemExit("WB_SECRET_PREFLIGHT_FAILED: WB_API_TOKEN must appear exactly once")
sys.stdout.write(values[0])
PYTHON_WB_KEY
)"
export WB_API_TOKEN
printf 'WB_SECRET_PREFLIGHT_OK\n'
PREFLIGHT_REPORT="$(mktemp "$REHEARSAL_ROOT/preflight-report-XXXXXX.json")"
chmod 600 "$PREFLIGHT_REPORT"
if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]; then
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" \
        "$RELEASE_DIR/scripts/migration_preflight.py" preflight \
        --database "$PROJECT_DIR/instance/catalog.db" \
        --source-root "$RELEASE_DIR" \
        --app-commit "$FETCHED_COMMIT" \
        --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
        --rehearsal-root "$REHEARSAL_ROOT" \
        --retention-days 7 \
        --report "$PREFLIGHT_REPORT"
fi
if [[ "$DOMAIN_MIGRATION_REQUIRED" == "1" ]]; then
    [[ -f instance/auth.db && -f instance/orders.db ]] \
        || { printf 'DOMAIN_PREFLIGHT_FAILED: auth.db or orders.db is missing\n' >&2; false; }
    DOMAIN_PREFLIGHT_REPORT="$(mktemp "$REHEARSAL_ROOT/domain-preflight-report-XXXXXX.json")"
    chmod 600 "$DOMAIN_PREFLIGHT_REPORT"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" \
        "$RELEASE_DIR/scripts/domain_migration_preflight.py" preflight \
        --auth-database "$PROJECT_DIR/instance/auth.db" \
        --orders-database "$PROJECT_DIR/instance/orders.db" \
        --tasks-database "$PROJECT_DIR/instance/tasks.db" \
        --app-commit "$FETCHED_COMMIT" \
        --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
        --rehearsal-root "$REHEARSAL_ROOT" \
        --report "$DOMAIN_PREFLIGHT_REPORT"
fi
if [[ "$PURCHASES_MIGRATION_REQUIRED" == "1" ]]; then
    purchases_rehearsal="$RELEASE_DIR/purchases-rehearsal.db"
    if [[ -f instance/purchases.db ]]; then
        sqlite3 instance/purchases.db ".backup '$purchases_rehearsal'"
    fi
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" \
        "$RELEASE_DIR/scripts/migrate_purchases.py" apply \
        --database "$purchases_rehearsal"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" \
        "$RELEASE_DIR/scripts/migrate_purchases.py" verify \
        --database "$purchases_rehearsal"
fi
if [[ "$CUSTOMERS_MIGRATION_REQUIRED" == "1" ]]; then
    customers_rehearsal="$RELEASE_DIR/customers-rehearsal.db"
    sqlite3 instance/customers.db ".backup '$customers_rehearsal'"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/customer_registry_schema.py" apply --database "$customers_rehearsal"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/customer_registry_schema.py" verify --database "$customers_rehearsal"
fi
if [[ "$SMS_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" \
        "$RELEASE_DIR/scripts/migrate_sms.py" rehearse \
        --database "$PROJECT_DIR/instance/sms.db"
fi
if [[ "$SERVICES_MIGRATION_REQUIRED" == "1" ]]; then
    services_rehearsal="$RELEASE_DIR/services-rehearsal.db"
    if [[ -f instance/services.db ]]; then
        sqlite3 instance/services.db ".backup '$services_rehearsal'"
    fi
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/migrate_services_vault.py" apply --database "$services_rehearsal"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/migrate_services_vault.py" verify --database "$services_rehearsal"
fi
if [[ "$MAIL_MIGRATION_REQUIRED" == "1" ]]; then
    mail_rehearsal="$RELEASE_DIR/mail-rehearsal.db"
    if [[ -f instance/mail.db ]]; then
        sqlite3 instance/mail.db ".backup '$mail_rehearsal'"
    fi
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/migrate_mail.py" apply --database "$mail_rehearsal"
    PYTHONPATH="$RELEASE_DIR" "$PYTHON_BIN" "$RELEASE_DIR/scripts/migrate_mail.py" verify --database "$mail_rehearsal"
fi

printf 'APPLICATION UPDATE: fast-forward to verified commit\n'
FAILURE_STAGE="APPLICATION UPDATE"
git merge --ff-only "$FETCHED_COMMIT"
CURRENT_COMMIT="$(git rev-parse HEAD)"
[[ "$CURRENT_COMMIT" == "$FETCHED_COMMIT" ]]
if [[ "$CURRENT_COMMIT" != "$PREVIOUS_COMMIT" ]]; then
    DEPLOY_UPDATED=1
fi
if printf '%s\n' "$changed_files" | grep -q '^requirements\.txt$'; then
    "$PROJECT_DIR/venv/bin/pip" install --disable-pip-version-check --only-binary=:all: 'cryptography==35.0.0'
fi
install -o root -g root -m 0755 scripts/retain_erp_backups.py "$RETENTION_TOOL"
install -o root -g root -m 0644 ops/clock-erp-backup-retention.cron "$RETENTION_CRON"
install -o root -g root -m 0644 \
    ops/clock-erp-backup-retention.logrotate "$RETENTION_LOGROTATE"
install -o root -g root -m 0644 ops/clock-erp-customers.cron "$CUSTOMERS_CRON"
install -o root -g root -m 0644 ops/clock-erp-customers.logrotate "$CUSTOMERS_LOGROTATE"
install -o root -g root -m 0644 ops/clock-erp-sms.cron "$SMS_CRON"
install -o root -g root -m 0644 ops/clock-erp-sms.logrotate "$SMS_LOGROTATE"
install -o root -g root -m 0644 ops/clock-erp-mail.cron "$MAIL_CRON"
install -o root -g root -m 0644 ops/clock-erp-mail.logrotate "$MAIL_LOGROTATE"

if [[ -f "$BITRIX_ENDPOINT_SOURCE" && -f "$BITRIX_ENDPOINT_TARGET" ]]; then
    /opt/php81/bin/php -l "$BITRIX_ENDPOINT_SOURCE" >/dev/null
    BITRIX_ENDPOINT_BACKUP="$BACKUP_DIR/bitrix-catalog-$(date +%Y%m%d-%H%M%S).php"
    cp -p "$BITRIX_ENDPOINT_TARGET" "$BITRIX_ENDPOINT_BACKUP"
    chmod 600 "$BITRIX_ENDPOINT_BACKUP"
    install -o admin -g admin -m 0640 \
        "$BITRIX_ENDPOINT_SOURCE" "$BITRIX_ENDPOINT_TARGET"
    BITRIX_ENDPOINT_UPDATED=1
fi
if [[ -f "$BITRIX_COMMENT_ENDPOINT_SOURCE" ]]; then
    /opt/php81/bin/php -l "$BITRIX_COMMENT_ENDPOINT_SOURCE" >/dev/null
    if [[ -f "$BITRIX_COMMENT_ENDPOINT_TARGET" ]]; then
        BITRIX_COMMENT_TARGET_EXISTED=1
        BITRIX_COMMENT_ENDPOINT_BACKUP="$BACKUP_DIR/bitrix-comments-$(date +%Y%m%d-%H%M%S).php"
        cp -p "$BITRIX_COMMENT_ENDPOINT_TARGET" "$BITRIX_COMMENT_ENDPOINT_BACKUP"
        chmod 600 "$BITRIX_COMMENT_ENDPOINT_BACKUP"
    fi
    install -o admin -g admin -m 0640 \
        "$BITRIX_COMMENT_ENDPOINT_SOURCE" "$BITRIX_COMMENT_ENDPOINT_TARGET"
    BITRIX_COMMENT_ENDPOINT_UPDATED=1
fi
if [[ -f "$BITRIX_ORDERS_EXPORT_SOURCE" ]]; then
    /opt/php81/bin/php -l "$BITRIX_ORDERS_EXPORT_SOURCE" >/dev/null
    if [[ -f "$BITRIX_ORDERS_EXPORT_TARGET" ]]; then
        BITRIX_ORDERS_EXPORT_TARGET_EXISTED=1
        BITRIX_ORDERS_EXPORT_BACKUP="$BACKUP_DIR/bitrix-orders-export-$(date +%Y%m%d-%H%M%S).php"
        cp -p "$BITRIX_ORDERS_EXPORT_TARGET" "$BITRIX_ORDERS_EXPORT_BACKUP"
        chmod 600 "$BITRIX_ORDERS_EXPORT_BACKUP"
    fi
    install -o admin -g admin -m 0640 \
        "$BITRIX_ORDERS_EXPORT_SOURCE" "$BITRIX_ORDERS_EXPORT_TARGET"
    BITRIX_ORDERS_EXPORT_UPDATED=1
fi

if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" || "$DOMAIN_MIGRATION_REQUIRED" == "1" || "$PURCHASES_MIGRATION_REQUIRED" == "1" || "$CUSTOMERS_MIGRATION_REQUIRED" == "1" || "$SMS_MIGRATION_REQUIRED" == "1" || "$SERVICES_MIGRATION_REQUIRED" == "1" || "$MAIL_MIGRATION_REQUIRED" == "1" ]]; then
    printf 'PRODUCTION MIGRATION: stop service, backup, apply verified migrations\n'
    FAILURE_STAGE="PRODUCTION MIGRATION"
    systemctl stop "$SERVICE_NAME"
    SERVICE_STOPPED=1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        printf 'Service did not stop before production migration\n' >&2
        false
    fi
    rollback_directory="$(mktemp -d "$BACKUP_DIR/production-migration-XXXXXX")"
    chmod 700 "$rollback_directory"
    if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" ]]; then
        CATALOG_ROLLBACK_BACKUP="$rollback_directory/catalog-before.db"
        sqlite3 instance/catalog.db ".backup '$CATALOG_ROLLBACK_BACKUP'"
        chmod 600 "$CATALOG_ROLLBACK_BACKUP"
        sqlite3 "$CATALOG_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        CATALOG_MIGRATION_STARTED=1
    fi
    if [[ "$DOMAIN_MIGRATION_REQUIRED" == "1" ]]; then
        AUTH_ROLLBACK_BACKUP="$rollback_directory/auth-before.db"
        ORDERS_ROLLBACK_BACKUP="$rollback_directory/orders-before.db"
        sqlite3 instance/auth.db ".backup '$AUTH_ROLLBACK_BACKUP'"
        sqlite3 instance/orders.db ".backup '$ORDERS_ROLLBACK_BACKUP'"
        if [[ -f instance/tasks.db ]]; then
            TASKS_DATABASE_EXISTED=1
            TASKS_ROLLBACK_BACKUP="$rollback_directory/tasks-before.db"
            sqlite3 instance/tasks.db ".backup '$TASKS_ROLLBACK_BACKUP'"
            chmod 600 "$TASKS_ROLLBACK_BACKUP"
            sqlite3 "$TASKS_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        fi
        chmod 600 "$AUTH_ROLLBACK_BACKUP" "$ORDERS_ROLLBACK_BACKUP"
        sqlite3 "$AUTH_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        sqlite3 "$ORDERS_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        DOMAIN_MIGRATION_STARTED=1
    fi
    if [[ "$PURCHASES_MIGRATION_REQUIRED" == "1" ]]; then
        if [[ -f instance/purchases.db ]]; then
            PURCHASES_DATABASE_EXISTED=1
            PURCHASES_ROLLBACK_BACKUP="$rollback_directory/purchases-before.db"
            sqlite3 instance/purchases.db ".backup '$PURCHASES_ROLLBACK_BACKUP'"
            chmod 600 "$PURCHASES_ROLLBACK_BACKUP"
            sqlite3 "$PURCHASES_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        fi
        PURCHASES_MIGRATION_STARTED=1
    fi
    if [[ "$CUSTOMERS_MIGRATION_REQUIRED" == "1" ]]; then
        CUSTOMERS_ROLLBACK_BACKUP="$rollback_directory/customers-before.db"
        sqlite3 instance/customers.db ".backup '$CUSTOMERS_ROLLBACK_BACKUP'"
        chmod 600 "$CUSTOMERS_ROLLBACK_BACKUP"
        sqlite3 "$CUSTOMERS_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        CUSTOMERS_MIGRATION_STARTED=1
    fi
    if [[ "$SMS_MIGRATION_REQUIRED" == "1" ]]; then
        if [[ -f instance/sms.db ]]; then
            SMS_DATABASE_EXISTED=1
            SMS_ROLLBACK_BACKUP="$rollback_directory/sms-before.db"
            sqlite3 instance/sms.db ".backup '$SMS_ROLLBACK_BACKUP'"
            chmod 600 "$SMS_ROLLBACK_BACKUP"
            sqlite3 "$SMS_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        fi
        SMS_MIGRATION_STARTED=1
    fi
    if [[ "$SERVICES_MIGRATION_REQUIRED" == "1" ]]; then
        if [[ -f instance/services.db ]]; then
            SERVICES_DATABASE_EXISTED=1
            SERVICES_ROLLBACK_BACKUP="$rollback_directory/services-before.db"
            sqlite3 instance/services.db ".backup '$SERVICES_ROLLBACK_BACKUP'"
            chmod 600 "$SERVICES_ROLLBACK_BACKUP"
            sqlite3 "$SERVICES_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        fi
        SERVICES_MIGRATION_STARTED=1
    fi
    if [[ "$MAIL_MIGRATION_REQUIRED" == "1" ]]; then
        if [[ -f instance/mail.db ]]; then
            MAIL_DATABASE_EXISTED=1
            MAIL_ROLLBACK_BACKUP="$rollback_directory/mail-before.db"
            sqlite3 instance/mail.db ".backup '$MAIL_ROLLBACK_BACKUP'"
            chmod 600 "$MAIL_ROLLBACK_BACKUP"
            sqlite3 "$MAIL_ROLLBACK_BACKUP" "PRAGMA quick_check;" | grep -qx "ok"
        fi
        MAIL_MIGRATION_STARTED=1
    fi
    if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" ]]; then
        "$PYTHON_BIN" scripts/migration_preflight.py apply \
            --database instance/catalog.db \
            --source-root "$PROJECT_DIR" \
            --app-commit "$CURRENT_COMMIT" \
            --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
            --service-stopped \
            --report "$rollback_directory/catalog-apply-report.json"
    fi
    if [[ "$DOMAIN_MIGRATION_REQUIRED" == "1" ]]; then
        "$PYTHON_BIN" scripts/domain_migration_preflight.py apply \
            --auth-database instance/auth.db \
            --orders-database instance/orders.db \
            --tasks-database instance/tasks.db \
            --app-commit "$CURRENT_COMMIT" \
            --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
            --service-stopped \
            --report "$rollback_directory/domain-apply-report.json"
    fi
    if [[ "$PURCHASES_MIGRATION_REQUIRED" == "1" ]]; then
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_purchases.py apply \
            --database instance/purchases.db
    fi
    if [[ "$CUSTOMERS_MIGRATION_REQUIRED" == "1" ]]; then
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/customer_registry_schema.py apply --database instance/customers.db
        if [[ "$CUSTOMERS_REBUILD_REQUIRED" == "1" ]]; then
            PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/backfill_customers.py --apply --rebuild --backup-dir "$rollback_directory"
        else
            PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/backfill_customers.py --apply --backup-dir "$rollback_directory"
        fi
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/customer_registry_schema.py verify --database instance/customers.db
    fi
    if [[ "$SMS_MIGRATION_REQUIRED" == "1" ]]; then
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_sms.py apply \
            --database instance/sms.db
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_sms.py verify \
            --database instance/sms.db
    fi
    if [[ "$SERVICES_MIGRATION_REQUIRED" == "1" ]]; then
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_services_vault.py apply --database instance/services.db
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_services_vault.py verify --database instance/services.db
    fi
    if [[ "$MAIL_MIGRATION_REQUIRED" == "1" ]]; then
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_mail.py apply --database instance/mail.db
        PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_mail.py verify --database instance/mail.db
    fi
    DATA_SNAPSHOT_AFTER="$(stable_data_snapshot)"
    if [[ "$DATA_SNAPSHOT_BEFORE" != "$DATA_SNAPSHOT_AFTER" ]]; then
        printf 'POST-DEPLOY DATA SAFETY: business aggregate mismatch\n' >&2
        false
    fi
    printf 'DATA_SAFETY_OK=%s\n' "$DATA_SNAPSHOT_AFTER"
fi

printf 'SERVICE START: controlled full restart\n'
FAILURE_STAGE="SERVICE START"
if [[ "$SERVICE_STOPPED" == "1" ]]; then
    systemctl start "$SERVICE_NAME"
    SERVICE_STOPPED=0
else
    systemctl restart "$SERVICE_NAME"
fi
systemctl is-active --quiet "$SERVICE_NAME"
"$PYTHON_BIN" - <<'PYTHON_SERVICE_KEY'
import os
import subprocess

details = subprocess.check_output(
    ["systemctl", "show", "clock-erp", "-p", "MainPID"]
).decode("ascii").strip()
pid = int(details.split("=", 1)[1] or "0")
if not pid:
    raise SystemExit("SERVICE_VAULT_PROCESS_KEY_FAILED: service MainPID is missing")
process_key = None
process_wb_token = None
with open("/proc/{}/environ".format(pid), "rb") as environment:
    for item in environment.read().split(b"\0"):
        if item.startswith(b"SERVICE_VAULT_KEY="):
            process_key = item.split(b"=", 1)[1].decode("ascii")
        if item.startswith(b"WB_API_TOKEN="):
            process_wb_token = item.split(b"=", 1)[1].decode("utf-8")
if process_key != os.environ.get("SERVICE_VAULT_KEY"):
    raise SystemExit("SERVICE_VAULT_PROCESS_KEY_FAILED: systemd process key does not match preflight")
if process_wb_token != os.environ.get("WB_API_TOKEN"):
    raise SystemExit("WB_SECRET_PROCESS_FAILED: systemd process secret does not match preflight")
print("SERVICE_VAULT_PROCESS_KEY_OK")
print("WB_SECRET_PROCESS_OK")
PYTHON_SERVICE_KEY

printf 'HEALTH CHECK: public routes and startup log\n'
FAILURE_STAGE="HEALTH CHECK"
for healthcheck_url in "${HEALTHCHECK_URLS[@]}"; do
    http_status="000"
    for attempt in {1..10}; do
        http_status="$(
            curl --location --silent --show-error --max-time 10 \
                --output /dev/null --write-out '%{http_code}' "$healthcheck_url" \
                || printf '000'
        )"
        [[ "$http_status" == "200" ]] && break
        sleep 1
    done
    [[ "$http_status" == "200" ]] \
        || { printf 'HTTP health check failed: %s=%s\n' "$healthcheck_url" "$http_status" >&2; false; }
    printf 'HTTP_200=%s\n' "$healthcheck_url"
done
root_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 http://127.0.0.1:5000/)"
[[ "$root_status" == "302" ]]

printf 'POST-DEPLOY INTEGRITY: schema, ledger, data and service\n'
FAILURE_STAGE="POST-DEPLOY INTEGRITY"
if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]; then
    "$PYTHON_BIN" scripts/migration_preflight.py verify \
        --database instance/catalog.db \
        --source-root "$PROJECT_DIR" \
        --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
        --report "${CATALOG_ROLLBACK_BACKUP%.db}-post-deploy.json"
fi
if [[ "$DOMAIN_MIGRATION_REQUIRED" == "1" ]]; then
    "$PYTHON_BIN" scripts/domain_migration_preflight.py verify \
        --auth-database instance/auth.db \
        --orders-database instance/orders.db \
        --tasks-database instance/tasks.db \
        --expected-sqlite-version "$PRODUCTION_SQLITE_VERSION" \
        --report "${AUTH_ROLLBACK_BACKUP%.db}-post-deploy.json"
fi
if [[ "$PURCHASES_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_purchases.py verify \
        --database instance/purchases.db
fi
if [[ "$CUSTOMERS_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/customer_registry_schema.py verify --database instance/customers.db
fi
if [[ "$SMS_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_sms.py verify \
        --database instance/sms.db
fi
if [[ "$SERVICES_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_services_vault.py verify --database instance/services.db
fi
PYTHONPATH="$PROJECT_DIR" ERP_PRODUCTION_SERVICES_SMOKE=confirmed \
    LC_ALL=en_US.utf8 LANG=en_US.utf8 "$PYTHON_BIN" \
    scripts/services_production_smoke.py
PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/wb_readonly_smoke.py \
    --production --catalog instance/catalog.db \
    --matching-report "$DEPLOY_SAFETY_DIR/wb-matching.json"
PYTHONPATH="$PROJECT_DIR" ERP_PRODUCTION_JOURNAL_SMOKE=confirmed \
    LC_ALL=en_US.utf8 LANG=en_US.utf8 "$PYTHON_BIN" \
    scripts/journal_production_smoke.py
DATA_SNAPSHOT_AFTER="$(stable_data_snapshot)"
if [[ "$DATA_SNAPSHOT_BEFORE" != "$DATA_SNAPSHOT_AFTER" ]]; then
    printf 'POST-SMOKE DATA SAFETY: stable business aggregate mismatch\n' >&2
    false
fi
printf 'DATA_AFTER=%s\n' "$DATA_SNAPSHOT_AFTER"
if [[ "$MAIL_MIGRATION_REQUIRED" == "1" ]]; then
    PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" scripts/migrate_mail.py verify --database instance/mail.db
fi
systemctl is-active --quiet "$SERVICE_NAME"
if journalctl -u "$SERVICE_NAME" --since "-2 minutes" \
    --priority=err --no-pager --quiet | grep -q .; then
    printf 'Service reported errors after deployment\n' >&2
    false
fi
[[ -z "$(git status --porcelain --untracked-files=normal | awk 'substr($0, 4, 9) != "instance/" { print }')" ]]

cleanup_release
trap - ERR
printf 'DEPLOY_COMMIT=%s\n' "$CURRENT_COMMIT"
printf 'DEPLOY_OK\n'
REMOTE_SCRIPT
