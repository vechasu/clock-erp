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
readonly HEALTHCHECK_URLS=(
    "http://127.0.0.1:5000/"
    "http://127.0.0.1:5000/analytics?period=all"
)

PREVIOUS_COMMIT=""
DEPLOY_UPDATED=0

rollback() {
    local exit_code=$?
    trap - ERR
    set +e

    printf 'DEPLOY_ERROR: deployment failed with exit code %s\n' "$exit_code" >&2

    if [[ "$DEPLOY_UPDATED" == "1" && -n "$PREVIOUS_COMMIT" ]]; then
        local rollback_status
        rollback_status="$(git status --porcelain --untracked-files=normal 2>/dev/null)"

        if [[ -n "$rollback_status" ]]; then
            printf '%s\n' \
                'ROLLBACK_BLOCKED: server repository became dirty; no files were removed or reset' >&2
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

server_status="$(git status --porcelain --untracked-files=normal)"
if [[ -n "$server_status" ]]; then
    printf '%s\n' \
        'Server repository is dirty; deployment stopped without changes' >&2
    false
fi

PREVIOUS_COMMIT="$(git rev-parse HEAD)"

git fetch "$REMOTE_NAME" "$EXPECTED_BRANCH"
FETCHED_COMMIT="$(git rev-parse FETCH_HEAD)"
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

systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME"

for healthcheck_url in "${HEALTHCHECK_URLS[@]}"; do
    http_ok=0

    for attempt in {1..10}; do
        if curl --fail --silent --show-error \
            --max-time 10 \
            --output /dev/null \
            "$healthcheck_url"; then
            http_ok=1
            break
        fi

        sleep 1
    done

    if [[ "$http_ok" != "1" ]]; then
        printf 'HTTP health check failed: %s\n' "$healthcheck_url" >&2
        false
    fi
done

trap - ERR
printf 'DEPLOY_COMMIT=%s\n' "$CURRENT_COMMIT"
printf '%s\n' 'DEPLOY_OK'
REMOTE_SCRIPT
