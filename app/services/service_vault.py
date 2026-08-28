"""Encrypted catalogue of company services and per-user access rules."""

import base64
import binascii
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken


SERVICE_CATEGORIES = {"sites", "sales", "delivery", "infrastructure"}
BUILTIN_ICONS = {"globe", "cart", "truck", "server", "cloud", "lock"}
PERMISSIONS = (
    "can_view", "can_open", "can_view_login", "can_copy_login",
    "can_view_password", "can_copy_password", "can_edit",
    "can_manage_access", "can_archive",
)
MAX_ICON_BYTES = 512 * 1024
MIGRATION_ID = "2026-08-28-services-vault-v1"


class ServiceVaultError(ValueError):
    pass


class ServiceNotFoundError(ServiceVaultError):
    pass


class ServicePermissionError(ServiceVaultError):
    pass


class ServiceConflictError(ServiceVaultError):
    pass


class VaultKeyError(RuntimeError):
    pass


def validate_service_url(value):
    value = str(value or "").strip()
    if len(value) > 2048:
        raise ServiceVaultError("Ссылка слишком длинная")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ServiceVaultError("Разрешены только ссылки http:// и https://")
    if parsed.username or parsed.password:
        raise ServiceVaultError("Ссылка не должна содержать логин или пароль")
    if any(ord(character) < 32 for character in value):
        raise ServiceVaultError("Ссылка содержит недопустимые символы")
    return value


def validate_icon(content, declared_mime):
    content = bytes(content or b"")
    if not content or len(content) > MAX_ICON_BYTES:
        raise ServiceVaultError("Иконка должна быть не больше 512 КБ")
    mime = ""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        mime = "image/webp"
    if not mime or declared_mime not in {mime, "application/octet-stream", ""}:
        raise ServiceVaultError("Поддерживаются только PNG, JPEG и WEBP")
    return content, mime


class ServiceVault:
    def __init__(self, path, key=None):
        self.path = Path(path)
        raw_key = str(key if key is not None else os.getenv("SERVICE_VAULT_KEY", "")).strip()
        try:
            decoded = base64.urlsafe_b64decode(raw_key.encode("ascii"))
            if len(decoded) != 32:
                raise ValueError
            self.cipher = Fernet(raw_key.encode("ascii"))
        except (ValueError, TypeError, binascii.Error):
            raise VaultKeyError("SERVICE_VAULT_KEY отсутствует или имеет неверный формат")
        self.validate_schema()

    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def validate_schema(self):
        if not self.path.exists():
            raise ServiceVaultError("Требуется миграция базы сервисов")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT migration_id FROM service_schema_migrations"
            ).fetchone()
            if row is None or row[0] != MIGRATION_ID:
                raise ServiceVaultError("Требуется миграция базы сервисов")

    def encrypt(self, value):
        value = str(value or "")
        return self.cipher.encrypt(value.encode("utf-8")) if value else None

    def decrypt(self, value):
        if value is None:
            return ""
        try:
            return self.cipher.decrypt(bytes(value)).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError) as error:
            raise VaultKeyError("Не удалось расшифровать реквизиты") from error

    @staticmethod
    def _is_owner(user):
        return str((user or {}).get("role") or "") == "admin"

    def _permission(self, connection, service_id, user):
        if self._is_owner(user):
            return {name: True for name in PERMISSIONS}
        row = connection.execute(
            "SELECT {} FROM service_permissions WHERE service_id=? AND user_id=?".format(
                ",".join(PERMISSIONS)
            ),
            (int(service_id), int((user or {}).get("id") or 0)),
        ).fetchone()
        return {name: bool(row[name]) if row else False for name in PERMISSIONS}

    def require(self, connection, service_id, user, permission, allow_archived=False):
        service = connection.execute(
            "SELECT * FROM services WHERE id=?", (int(service_id),)
        ).fetchone()
        if service is None:
            raise ServiceNotFoundError("Сервис не найден")
        if service["archived_at"] and not allow_archived:
            raise ServiceNotFoundError("Сервис находится в архиве")
        rights = self._permission(connection, service_id, user)
        if not rights.get(permission):
            raise ServicePermissionError("Недостаточно прав")
        return service, rights

    def list_services(self, user, archived=False):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT s.*, COALESCE(p.favorite,0) favorite, COALESCE(p.sort_order,0) sort_order "
                "FROM services s LEFT JOIN service_user_preferences p "
                "ON p.service_id=s.id AND p.user_id=? WHERE "
                + ("s.archived_at IS NOT NULL " if archived else "s.archived_at IS NULL ")
                + "ORDER BY favorite DESC, sort_order, s.id",
                (int(user["id"]),),
            ).fetchall()
            result = []
            for row in rows:
                rights = self._permission(connection, row["id"], user)
                if not rights["can_view"] or (archived and not self._is_owner(user)):
                    continue
                accounts = connection.execute(
                    "SELECT id,label,login_encrypted IS NOT NULL has_login,"
                    "password_encrypted IS NOT NULL has_password FROM service_accounts "
                    "WHERE service_id=? ORDER BY position,id", (row["id"],)
                ).fetchall()
                grants = []
                if rights["can_manage_access"]:
                    grants = [dict(item) for item in connection.execute(
                        "SELECT user_id,{} FROM service_permissions WHERE service_id=? ORDER BY user_id".format(
                            ",".join(PERMISSIONS)
                        ), (row["id"],)
                    ).fetchall()]
                result.append({
                    "id": row["id"], "name": row["name"],
                    "url": row["url"] if (rights["can_open"] or rights["can_edit"]) else "",
                    "domain": urlsplit(row["url"]).netloc, "description": row["description"],
                    "category": row["category"], "icon": row["icon"],
                    "has_custom_icon": bool(row["icon_blob"]), "favorite": bool(row["favorite"]),
                    "sort_order": row["sort_order"], "version": row["version"],
                    "archived": bool(row["archived_at"]), "permissions": rights,
                    "accounts": [dict(account) for account in accounts],
                    "grants": grants,
                })
            return result

    def create(self, payload, user, icon=None):
        if not self._is_owner(user):
            raise ServicePermissionError("Недостаточно прав")
        normalized = self._validated_payload(payload)
        now = int(time.time())
        icon_blob, icon_mime = icon or (None, None)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO services(name,url,description,category,icon,icon_blob,icon_mime,"
                "created_by,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                (normalized["name"], normalized["url"], normalized["description"],
                 normalized["category"], normalized["icon"], icon_blob, icon_mime,
                 int(user["id"]), now, now),
            )
            service_id = cursor.lastrowid
            self._replace_accounts(connection, service_id, normalized["accounts"], now)
            self._replace_permissions(connection, service_id, normalized["permissions"])
            connection.execute(
                "INSERT INTO service_user_preferences(service_id,user_id,favorite,sort_order,version,updated_at) "
                "VALUES(?,?,?,?,1,?)", (service_id, int(user["id"]), int(normalized["favorite"]), service_id, now)
            )
            connection.commit()
        return service_id

    def update(self, service_id, payload, user, icon=None):
        normalized = self._validated_payload(payload)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            service, rights = self.require(connection, service_id, user, "can_edit")
            expected = int(payload.get("version") or service["version"])
            icon_sql = ""
            parameters = [normalized["name"], normalized["url"], normalized["description"], normalized["category"], normalized["icon"]]
            if icon:
                icon_sql = ",icon_blob=?,icon_mime=?"
                parameters.extend(icon)
            parameters.extend([now, int(service_id), expected])
            cursor = connection.execute(
                "UPDATE services SET name=?,url=?,description=?,category=?,icon=?{}"
                ",updated_at=?,version=version+1 WHERE id=? AND version=?".format(icon_sql), parameters
            )
            if cursor.rowcount != 1:
                raise ServiceConflictError("Сервис уже изменён другим пользователем")
            self._replace_accounts(connection, service_id, normalized["accounts"], now)
            if normalized["permissions"] is not None:
                if not rights["can_manage_access"]:
                    raise ServicePermissionError("Недостаточно прав для управления доступами")
                self._replace_permissions(connection, service_id, normalized["permissions"])
            connection.commit()

    def _validated_payload(self, payload):
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 160:
            raise ServiceVaultError("Укажите название сервиса")
        category = str(payload.get("category") or "sites")
        if category not in SERVICE_CATEGORIES:
            raise ServiceVaultError("Неизвестная категория")
        icon = str(payload.get("icon") or "globe")
        if icon not in BUILTIN_ICONS:
            icon = "globe"
        accounts = payload.get("accounts") or []
        if not isinstance(accounts, list) or len(accounts) > 20:
            raise ServiceVaultError("Некорректный список аккаунтов")
        clean_accounts = []
        for account in accounts:
            label = str(account.get("label") or "Основной аккаунт").strip()[:120]
            login = str(account.get("login") or "")
            password = str(account.get("password") or "")
            if len(login) > 1000 or len(password) > 4000:
                raise ServiceVaultError("Реквизиты слишком длинные")
            if login or password or label:
                clean_accounts.append({
                    "id": int(account.get("id") or 0),
                    "label": label or "Основной аккаунт", "login": login,
                    "password": password,
                })
        permissions = payload.get("permissions")
        if permissions is not None and not isinstance(permissions, list):
            raise ServiceVaultError("Некорректные права доступа")
        return {
            "name": name, "url": validate_service_url(payload.get("url")),
            "description": str(payload.get("description") or "").strip()[:1000],
            "category": category, "icon": icon, "accounts": clean_accounts,
            "permissions": permissions, "favorite": bool(payload.get("favorite")),
        }

    def _replace_accounts(self, connection, service_id, accounts, now):
        existing = {row["id"]: row for row in connection.execute(
            "SELECT * FROM service_accounts WHERE service_id=?", (service_id,)
        ).fetchall()}
        retained = []
        for position, account in enumerate(accounts):
            account_id = int(account.get("id") or 0)
            old = existing.get(account_id)
            login_blob = self.encrypt(account["login"]) if account["login"] else (old["login_encrypted"] if old else None)
            password_blob = self.encrypt(account["password"]) if account["password"] else (old["password_encrypted"] if old else None)
            if old:
                connection.execute(
                    "UPDATE service_accounts SET label=?,login_encrypted=?,password_encrypted=?,position=?,updated_at=? WHERE id=?",
                    (account["label"], login_blob, password_blob, position, now, account_id),
                )
                retained.append(account_id)
            else:
                cursor = connection.execute(
                    "INSERT INTO service_accounts(service_id,label,login_encrypted,password_encrypted,position,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)", (service_id, account["label"], login_blob, password_blob, position, now, now)
                )
                retained.append(cursor.lastrowid)
        if retained:
            placeholders = ",".join("?" for _ in retained)
            connection.execute(
                "DELETE FROM service_accounts WHERE service_id=? AND id NOT IN ({})".format(placeholders),
                [service_id] + retained,
            )
        else:
            connection.execute("DELETE FROM service_accounts WHERE service_id=?", (service_id,))

    def _replace_permissions(self, connection, service_id, permissions):
        if permissions is None:
            return
        connection.execute("DELETE FROM service_permissions WHERE service_id=?", (service_id,))
        for item in permissions:
            user_id = int(item.get("user_id") or 0)
            if not user_id:
                continue
            values = [int(bool(item.get(name))) for name in PERMISSIONS]
            connection.execute(
                "INSERT INTO service_permissions(service_id,user_id,{}) VALUES(?,?,{})".format(
                    ",".join(PERMISSIONS), ",".join("?" for _ in PERMISSIONS)
                ), [service_id, user_id] + values,
            )

    def credential(self, account_id, user, kind, for_copy=False):
        permission = ("can_copy_" if for_copy else "can_view_") + kind
        column = "password_encrypted" if kind == "password" else "login_encrypted"
        with self.connect() as connection:
            account = connection.execute("SELECT * FROM service_accounts WHERE id=?", (int(account_id),)).fetchone()
            if account is None:
                raise ServiceNotFoundError("Аккаунт не найден")
            service, _rights = self.require(connection, account["service_id"], user, permission)
            return self.decrypt(account[column]), dict(service), dict(account)

    def set_archived(self, service_id, archived, user):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require(connection, service_id, user, "can_archive", allow_archived=True)
            connection.execute(
                "UPDATE services SET archived_at=?,updated_at=?,version=version+1 WHERE id=?",
                (int(time.time()) if archived else None, int(time.time()), int(service_id)),
            )
            connection.commit()

    def set_favorite(self, service_id, favorite, user):
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require(connection, service_id, user, "can_view")
            updated = connection.execute(
                "UPDATE service_user_preferences SET favorite=?,version=version+1,updated_at=? "
                "WHERE service_id=? AND user_id=?",
                (int(bool(favorite)), now, int(service_id), int(user["id"])),
            ).rowcount
            if not updated:
                connection.execute(
                    "INSERT INTO service_user_preferences(service_id,user_id,favorite,sort_order,version,updated_at) "
                    "VALUES(?,?,?,?,1,?)", (service_id, user["id"], int(bool(favorite)), service_id, now)
                )
            connection.commit()

    def reorder(self, ordered_ids, user):
        ids = [int(value) for value in ordered_ids]
        if len(ids) != len(set(ids)):
            raise ServiceVaultError("Некорректный порядок")
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for position, service_id in enumerate(ids):
                self.require(connection, service_id, user, "can_view")
                updated = connection.execute(
                    "UPDATE service_user_preferences SET sort_order=?,version=version+1,updated_at=? "
                    "WHERE service_id=? AND user_id=?", (position, now, service_id, user["id"])
                ).rowcount
                if not updated:
                    connection.execute(
                        "INSERT INTO service_user_preferences(service_id,user_id,favorite,sort_order,version,updated_at) "
                        "VALUES(?,?,0,?,1,?)", (service_id, user["id"], position, now)
                    )
            connection.commit()

    def icon(self, service_id, user):
        with self.connect() as connection:
            service, _rights = self.require(connection, service_id, user, "can_view", allow_archived=True)
            if not service["icon_blob"]:
                raise ServiceNotFoundError("Иконка не найдена")
            return bytes(service["icon_blob"]), service["icon_mime"]
