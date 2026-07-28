#!/usr/bin/env bash

set -Eeuo pipefail

readonly CONFIG_PATH="/etc/nginx/conf.d/clock-erp.conf"
readonly BACKUP_DIR="/opt/clock-erp-backups"
readonly PUBLIC_ORIGIN="https://sklad.tictactoy.ru"

fail() {
    printf 'NGINX_AUTH_ERROR: %s\n' "$*" >&2
    exit 1
}

[[ "$(id -u)" == "0" ]] \
    || fail "Скрипт должен выполняться от root"
[[ -f "$CONFIG_PATH" ]] \
    || fail "Конфигурация Nginx не найдена: $CONFIG_PATH"

basic_directive_count="$(
    grep -Ec '^[[:space:]]*auth_basic "Clock ERP";[[:space:]]*$' \
        "$CONFIG_PATH" || true
)"
password_file_directive_count="$(
    grep -Ec \
        '^[[:space:]]*auth_basic_user_file /etc/nginx/\.htpasswd-clock-erp;[[:space:]]*$' \
        "$CONFIG_PATH" || true
)"

if [[ "$basic_directive_count" == "0" \
    && "$password_file_directive_count" == "0" ]]; then
    printf '%s\n' 'NGINX_AUTH_ALREADY_CONFIGURED'
    exit 0
fi

[[ "$basic_directive_count" == "1" ]] \
    || fail "Найдено неожиданное число директив auth_basic"
[[ "$password_file_directive_count" == "1" ]] \
    || fail "Найдено неожиданное число директив auth_basic_user_file"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

backup_path="$BACKUP_DIR/nginx-clock-erp-$(date +%Y%m%d-%H%M%S).conf"
cp --preserve=mode,ownership,timestamps "$CONFIG_PATH" "$backup_path"
chmod 600 "$backup_path"

temporary_config="$(mktemp /tmp/clock-erp-nginx.XXXXXX)"
cleanup() {
    if [[ -f "$temporary_config" ]]; then
        rm "$temporary_config"
    fi
}
trap cleanup EXIT

sed \
    -e '/^[[:space:]]*auth_basic "Clock ERP";[[:space:]]*$/d' \
    -e '/^[[:space:]]*auth_basic_user_file \/etc\/nginx\/\.htpasswd-clock-erp;[[:space:]]*$/d' \
    "$CONFIG_PATH" > "$temporary_config"

restore_configuration() {
    install -o root -g root -m 0644 "$backup_path" "$CONFIG_PATH"
    nginx -t
    systemctl reload nginx
}

install -o root -g root -m 0644 "$temporary_config" "$CONFIG_PATH"

if ! nginx -t; then
    restore_configuration
    fail "Новая конфигурация Nginx не прошла проверку"
fi

if ! systemctl reload nginx; then
    restore_configuration
    fail "Не удалось применить новую конфигурацию Nginx"
fi

register_status="$(
    curl --silent --show-error --max-time 15 \
        --output /dev/null --write-out '%{http_code}' \
        "$PUBLIC_ORIGIN/register"
)"
login_status="$(
    curl --silent --show-error --max-time 15 \
        --output /dev/null --write-out '%{http_code}' \
        "$PUBLIC_ORIGIN/login"
)"
root_headers="$(
    curl --silent --show-error --max-time 15 --head \
        "$PUBLIC_ORIGIN/" |
        tr -d '\r'
)"

if [[ "$register_status" != "200" || "$login_status" != "200" ]] \
    || ! printf '%s\n' "$root_headers" | grep -Eq '^HTTP/[^ ]+ 302' \
    || printf '%s\n' "$root_headers" | grep -Eiq '^www-authenticate:'; then
    restore_configuration
    fail "Внешняя smoke-проверка не прошла, конфигурация восстановлена"
fi

printf 'NGINX_BACKUP_PATH=%s\n' "$backup_path"
printf '%s\n' 'NGINX_APP_AUTH_OK'
