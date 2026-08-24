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

local_status="$(git status --porcelain --untracked-files=normal)"
[[ -z "$local_status" ]] \
    || fail "Локальный репозиторий содержит незакоммиченные изменения"

printf 'Pushing %s/%s...\n' "$REMOTE_NAME" "$EXPECTED_BRANCH"
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
readonly RETENTION_TOOL="/usr/local/sbin/clock-erp-backup-retention"
readonly RETENTION_CRON="/etc/cron.d/clock-erp-backup-retention"
readonly RETENTION_LOGROTATE="/etc/logrotate.d/clock-erp-backup-retention"
readonly MAX_BACKUP_DISK_USAGE=85
readonly BITRIX_ENDPOINT_SOURCE="$PROJECT_DIR/bitrix/catalog-export.php"
readonly BITRIX_ENDPOINT_TARGET="/var/www/admin/data/www/tictactoy.ru/api/catalog-export.php"
readonly BITRIX_COMMENT_ENDPOINT_SOURCE="$PROJECT_DIR/bitrix/order-comments.php"
readonly BITRIX_COMMENT_ENDPOINT_TARGET="/var/www/admin/data/www/tictactoy.ru/api/order-comments.php"
SERVICE_STOPPED=0
DATABASE_MIGRATION_REQUIRED=0
readonly HEALTHCHECK_URLS=(
    "http://127.0.0.1:5000/register"
    "http://127.0.0.1:5000/login"
)

PREVIOUS_COMMIT=""
DEPLOY_UPDATED=0
BITRIX_ENDPOINT_BACKUP=""
BITRIX_ENDPOINT_UPDATED=0
BITRIX_COMMENT_ENDPOINT_BACKUP=""
BITRIX_COMMENT_ENDPOINT_UPDATED=0

rollback() {
    local exit_code=$?
    trap - ERR
    set +e

    printf 'DEPLOY_ERROR: deployment failed with exit code %s\n' "$exit_code" >&2

    if [[ "$BITRIX_ENDPOINT_UPDATED" == "1" && -f "$BITRIX_ENDPOINT_BACKUP" ]]; then
        install -o admin -g admin -m 0640 \
            "$BITRIX_ENDPOINT_BACKUP" "$BITRIX_ENDPOINT_TARGET"
        printf 'ROLLBACK_OK: restored Bitrix catalog endpoint\n' >&2
    fi

    if [[ "$BITRIX_COMMENT_ENDPOINT_UPDATED" == "1" ]]; then
        if [[ -f "$BITRIX_COMMENT_ENDPOINT_BACKUP" ]]; then
            install -o admin -g admin -m 0640 \
                "$BITRIX_COMMENT_ENDPOINT_BACKUP" "$BITRIX_COMMENT_ENDPOINT_TARGET"
        else
            rm -f -- "$BITRIX_COMMENT_ENDPOINT_TARGET"
        fi
        printf 'ROLLBACK_OK: restored Bitrix order-comment endpoint\n' >&2
    fi

    if [[ "$DEPLOY_UPDATED" == "1" && -n "$PREVIOUS_COMMIT" ]]; then
        local rollback_status
        rollback_status="$(git status --porcelain --untracked-files=normal 2>/dev/null)"

        if [[ -n "$rollback_status" ]]; then
            printf '%s\n' \
                'ROLLBACK_BLOCKED: server repository became dirty; no files were removed or reset' >&2
            if [[ "$SERVICE_STOPPED" == "1" ]]; then
                systemctl start "$SERVICE_NAME"
                SERVICE_STOPPED=0
            fi
        else
            printf 'Rolling back to %s...\n' "$PREVIOUS_COMMIT" >&2
            git reset --hard "$PREVIOUS_COMMIT"
            systemctl restart "$SERVICE_NAME"

            if systemctl is-active --quiet "$SERVICE_NAME"; then
                printf 'ROLLBACK_OK: restored %s and restarted %s\n' \
                    "$PREVIOUS_COMMIT" "$SERVICE_NAME" >&2
            else
                printf 'ROLLBACK_ERROR: %s is not active after rollback\n' \
                    "$SERVICE_NAME" >&2
            fi
        fi
    elif [[ "$SERVICE_STOPPED" == "1" ]]; then
        systemctl start "$SERVICE_NAME"
    fi

    exit "$exit_code"
}

trap rollback ERR

cd "$PROJECT_DIR"

git rev-parse --is-inside-work-tree >/dev/null 2>&1

server_branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ "$server_branch" != "$EXPECTED_BRANCH" ]]; then
    printf 'Server branch must be main, current branch: %s\n' \
        "${server_branch:-detached HEAD}" >&2
    false
fi

server_source_status="$(
    git status --porcelain --untracked-files=normal |
        awk 'substr($0, 4, 9) != "instance/" { print }'
)"
if [[ -n "$server_source_status" ]]; then
    printf '%s\n' \
        'Server source tree is dirty; deployment stopped without changes' >&2
    false
fi

PREVIOUS_COMMIT="$(git rev-parse HEAD)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if [[ -x "$RETENTION_TOOL" ]]; then
    "$RETENTION_TOOL" \
        --backup-root "$BACKUP_DIR" \
        --apply
elif [[ -f scripts/retain_erp_backups.py ]]; then
    python3 scripts/retain_erp_backups.py \
        --backup-root "$BACKUP_DIR" \
        --apply
else
    printf 'BACKUP_ERROR: retention tool is not installed\n' >&2
    false
fi

check_backup_disk_usage() {
    local backup_disk_usage
    backup_disk_usage="$(
        df -P "$BACKUP_DIR" |
            awk 'NR == 2 { gsub(/%/, "", $5); print $5 }'
    )"
    if [[ ! "$backup_disk_usage" =~ ^[0-9]+$ ]]; then
        printf 'BACKUP_ERROR: cannot determine disk usage for %s\n' \
            "$BACKUP_DIR" >&2
        return 1
    fi
    if (( backup_disk_usage >= MAX_BACKUP_DISK_USAGE )); then
        printf 'BACKUP_ERROR: disk usage is %s%% (limit %s%%); deployment stopped before backup\n' \
            "$backup_disk_usage" "$MAX_BACKUP_DISK_USAGE" >&2
        return 1
    fi
}

check_backup_disk_usage
if [[ -x "$RETENTION_TOOL" ]]; then
    "$RETENTION_TOOL" \
        --backup-root "$BACKUP_DIR" \
        --project-root "$PROJECT_DIR" \
        --create-daily \
        --apply
else
    python3 scripts/retain_erp_backups.py \
        --backup-root "$BACKUP_DIR" \
        --project-root "$PROJECT_DIR" \
        --create-daily \
        --apply
fi

git fetch "$REMOTE_NAME"
FETCHED_COMMIT="$(git rev-parse "$REMOTE_NAME/$EXPECTED_BRANCH")"
git merge --ff-only "$FETCHED_COMMIT"

CURRENT_COMMIT="$(git rev-parse HEAD)"
if [[ "$CURRENT_COMMIT" != "$FETCHED_COMMIT" ]]; then
    printf 'Updated commit %s does not match fetched commit %s\n' \
        "$CURRENT_COMMIT" "$FETCHED_COMMIT" >&2
    false
fi

if [[ "$CURRENT_COMMIT" != "$PREVIOUS_COMMIT" ]]; then
    DEPLOY_UPDATED=1
fi

install -o root -g root -m 0755 scripts/retain_erp_backups.py "$RETENTION_TOOL"
install -o root -g root -m 0644 ops/clock-erp-backup-retention.cron "$RETENTION_CRON"
install -o root -g root -m 0644 \
    ops/clock-erp-backup-retention.logrotate "$RETENTION_LOGROTATE"
"$RETENTION_TOOL" \
    --backup-root "$BACKUP_DIR" \
    --project-root "$PROJECT_DIR" \
    --archive-runtime-backups \
    --apply

if git diff --name-only "$PREVIOUS_COMMIT" "$CURRENT_COMMIT" |
    grep -Eq '^(app/catalog_db\.py|scripts/(migrate_auth_mvp|migrate_inventory_scopes)\.py)$'; then
    DATABASE_MIGRATION_REQUIRED=1
fi

if [[ -x venv/bin/python ]]; then
    PYTHON_BIN="venv/bin/python"
else
    PYTHON_BIN="python3"
fi

"$PYTHON_BIN" - <<'PYTHON_CHECK'
import ast
from pathlib import Path

project_root = Path.cwd()
python_files = sorted((project_root / "app").rglob("*.py"))

for path in python_files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

from jinja2 import Environment

environment = Environment()
template_files = sorted((project_root / "app" / "templates").glob("*.html"))

for path in template_files:
    environment.parse(path.read_text(encoding="utf-8"))

print(
    f"CHECKS_OK: {len(python_files)} Python files, "
    f"{len(template_files)} templates"
)
PYTHON_CHECK

if [[ -f "$BITRIX_ENDPOINT_SOURCE" && -f "$BITRIX_ENDPOINT_TARGET" ]]; then
    /opt/php81/bin/php -l "$BITRIX_ENDPOINT_SOURCE" >/dev/null
    BITRIX_ENDPOINT_BACKUP="$BACKUP_DIR/bitrix-catalog-export-$(date +%Y%m%d-%H%M%S).php"
    cp -p "$BITRIX_ENDPOINT_TARGET" "$BITRIX_ENDPOINT_BACKUP"
    chmod 600 "$BITRIX_ENDPOINT_BACKUP"
    install -o admin -g admin -m 0640 \
        "$BITRIX_ENDPOINT_SOURCE" "$BITRIX_ENDPOINT_TARGET"
    BITRIX_ENDPOINT_UPDATED=1
    printf 'BITRIX_BACKUP_PATH=%s\n' "$BITRIX_ENDPOINT_BACKUP"
fi


if [[ -f "$BITRIX_COMMENT_ENDPOINT_SOURCE" ]]; then
    /opt/php81/bin/php -l "$BITRIX_COMMENT_ENDPOINT_SOURCE" >/dev/null
    if [[ -f "$BITRIX_COMMENT_ENDPOINT_TARGET" ]]; then
        BITRIX_COMMENT_ENDPOINT_BACKUP="$BACKUP_DIR/bitrix-order-comments-$(date +%Y%m%d-%H%M%S).php"
        cp -p "$BITRIX_COMMENT_ENDPOINT_TARGET" "$BITRIX_COMMENT_ENDPOINT_BACKUP"
        chmod 600 "$BITRIX_COMMENT_ENDPOINT_BACKUP"
    fi
    install -o admin -g admin -m 0640 \
        "$BITRIX_COMMENT_ENDPOINT_SOURCE" "$BITRIX_COMMENT_ENDPOINT_TARGET"
    BITRIX_COMMENT_ENDPOINT_UPDATED=1
    printf 'BITRIX_COMMENT_BACKUP_PATH=%s\n' "${BITRIX_COMMENT_ENDPOINT_BACKUP:-new-file}"
fi

if [[ -f instance/repair_cases.json ]]; then
    mkdir -p "$BACKUP_DIR/temporary/repair-data"
    chmod 700 "$BACKUP_DIR/temporary" "$BACKUP_DIR/temporary/repair-data"
    "$PYTHON_BIN" scripts/migrate_repair_cases.py \
        --path instance/repair_cases.json \
        --backup-dir "$BACKUP_DIR/temporary/repair-data" \
        --apply
fi

if [[ "$DATABASE_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]; then
    active_inventory_count="$(
        sqlite3 instance/catalog.db \
            "SELECT COUNT(*) FROM erp_inventory_sessions WHERE status = 'active';" \
            2>/dev/null || printf '0'
    )"
    if [[ "$active_inventory_count" != "0" ]]; then
        active_inventory_details="$(
            sqlite3 -separator ' | ' instance/catalog.db \
                "SELECT s.id, b.name, s.status, s.started_at, "\
"COALESCE(s.started_by, 'system'), COUNT(i.id), "\
"SUM(CASE WHEN i.status IN ('confirmed','adjusted','added','missing') "\
"THEN 1 ELSE 0 END), "\
"SUM(CASE WHEN i.status IN ('pending','conflict','error') "\
"THEN 1 ELSE 0 END) "\
"FROM erp_inventory_sessions s "\
"JOIN erp_brands b ON b.id=s.brand_id "\
"LEFT JOIN erp_inventory_items i ON i.session_id=s.id "\
"WHERE s.status='active' GROUP BY s.id ORDER BY s.started_at;"
        )"
        printf 'DEPLOY_BLOCKED: %s active inventory session(s) require uninterrupted access\n' \
            "$active_inventory_count" >&2
        printf 'DEPLOY_BLOCKED_DETAILS: id | brand | status | started_at | actor | positions | checked | remaining\n%s\n' \
            "$active_inventory_details" >&2
        false
    fi
fi

if [[ "$DATABASE_MIGRATION_REQUIRED" == "1" && -f instance/auth.db ]]; then
    check_backup_disk_usage
    systemctl stop "$SERVICE_NAME"
    SERVICE_STOPPED=1
    "$PYTHON_BIN" scripts/migrate_auth_mvp.py \
        --database instance/auth.db \
        --backup-dir "$BACKUP_DIR/auth-migrations" \
        --apply
fi

if [[ "$DATABASE_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]; then
    check_backup_disk_usage
    if [[ "$SERVICE_STOPPED" != "1" ]]; then
        systemctl stop "$SERVICE_NAME"
        SERVICE_STOPPED=1
    fi
    "$PYTHON_BIN" scripts/migrate_inventory_scopes.py \
        --database instance/catalog.db \
        --backup-dir "$BACKUP_DIR/temporary/inventory-scope-migrations" \
        --apply

    sqlite3 instance/catalog.db "PRAGMA quick_check;" | grep -qx "ok"
fi

if [[ "$SERVICE_STOPPED" == "1" ]]; then
    systemctl start "$SERVICE_NAME"
    SERVICE_STOPPED=0
else
    systemctl kill --kill-who=main --signal=HUP "$SERVICE_NAME"
fi
systemctl is-active --quiet "$SERVICE_NAME"

for healthcheck_url in "${HEALTHCHECK_URLS[@]}"; do
    http_status=""

    for attempt in {1..10}; do
        if ! http_status="$(
            curl --location --silent --show-error \
                --max-time 10 \
                --output /dev/null \
                --write-out '%{http_code}' \
                "$healthcheck_url"
        )"; then
            http_status="000"
        fi

        if [[ "$http_status" == "200" ]]; then
            break
        fi

        sleep 1
    done

    if [[ "$http_status" != "200" ]]; then
        printf 'HTTP health check failed: %s returned %s\n' \
            "$healthcheck_url" "$http_status" >&2
        false
    fi

    printf 'HTTP_200=%s\n' "$healthcheck_url"
done

root_headers="$(
    curl --silent --show-error --max-time 10 --head \
        http://127.0.0.1:5000/ |
        tr -d '\r'
)"
printf '%s\n' "$root_headers" | grep -Eq '^HTTP/[^ ]+ 302'
printf '%s\n' "$root_headers" |
    grep -Eiq '^location: (https?://[^/]+)?/(register|login)(\?[^[:space:]]*)?$'

if journalctl -u "$SERVICE_NAME" --since "-2 minutes" \
    --priority=err --no-pager --quiet | grep -q .; then
    printf '%s\n' 'Service reported errors after restart' >&2
    false
fi

trap - ERR
printf 'DEPLOY_COMMIT=%s\n' "$CURRENT_COMMIT"
printf '%s\n' 'DEPLOY_OK'
REMOTE_SCRIPT
