import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import click
from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.security import check_password_hash, generate_password_hash


auth = Blueprint("auth", __name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")
PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.register",
    "auth.verify_email",
    "auth.resend_verification",
    "auth.forgot_password",
    "auth.reset_password",
    "auth.accept_invitation",
    "auth.registration_success",
    "static",
}
ALLOWED_ROLES = {
    "employee": "Сотрудник",
    "superadmin": "Суперадминистратор",
}
LEGACY_SUPERADMIN_ROLE = "admin"
SUPERADMIN_ROLES = {"superadmin", LEGACY_SUPERADMIN_ROLE}
ROLE_PERMISSIONS = {
    "employee": frozenset({"erp.use"}),
    "superadmin": frozenset({"erp.use", "access.manage", "security.audit"}),
}
COMMON_PASSWORDS = {
    "12345678",
    "password",
    "password1",
    "qwerty123",
    "admin123",
    "tictactoy",
}
PASSWORD_HASH_METHOD = "pbkdf2:sha256:600000"
TOKEN_EMAIL_VERIFICATION = "email_verification"
TOKEN_PASSWORD_RESET = "password_reset"
LOGIN_PUBLIC_ERROR = "Неверный email или пароль."
AUTH_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
LOGIN_EMAIL_RATE_LIMIT = 8
LOGIN_IP_RATE_LIMIT = 60
LOGIN_GLOBAL_RATE_LIMIT = 500
AUTH_IDLE_TIMEOUT_SECONDS = 12 * 60 * 60
AUTH_ABSOLUTE_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
SESSION_LAST_SEEN_THROTTLE_SECONDS = 60
INVITATION_PUBLIC_ERROR = "Приглашение недействительно или недоступно."
REGISTRATION_CLOSED_MESSAGE = (
    "Регистрация доступна только сотрудникам TicTacToy по приглашению администратора."
)
SENSITIVE_CONFIRMATION_VALUE = "ПОДТВЕРЖДАЮ"
SECURITY_SECRET_FIELDS = {
    "password", "password_hash", "token", "token_hash", "session",
    "session_token", "invitation", "invitation_token", "temporary_password",
}
LOGGER = logging.getLogger(__name__)


class RegistrationError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def normalize_email(value):
    return str(value or "").strip().casefold()


def invitation_digest(token):
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


def token_digest(token):
    return invitation_digest(token)


def safe_next_url(value, default="/"):
    candidate = str(value or "").strip()
    if (
        not candidate
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return default

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return default

    return candidate


def _load_or_create_secret(path):
    configured = os.getenv("ERP_SECRET_KEY", "").strip()
    if configured:
        return configured

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            str(path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        descriptor = None

    if descriptor is not None:
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(secrets.token_hex(32))

    for _attempt in range(20):
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError:
            secret = ""
        if len(secret) >= 32:
            return secret
        time.sleep(0.05)
    raise RuntimeError("Некорректный ключ сессий ERP")


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def connect(self):
        connection = sqlite3.connect(
            str(self.path),
            timeout=15,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _ensure_schema(self):
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS roles (
                    code TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    is_privileged INTEGER NOT NULL DEFAULT 0
                );

                INSERT OR IGNORE INTO roles (code, label, is_privileged)
                VALUES ('employee', 'Сотрудник', 0);
                INSERT OR IGNORE INTO roles (code, label, is_privileged)
                VALUES ('superadmin', 'Суперадминистратор', 1);

                CREATE TABLE IF NOT EXISTS permissions (
                    code TEXT PRIMARY KEY,
                    description TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_code TEXT NOT NULL,
                    permission_code TEXT NOT NULL,
                    PRIMARY KEY (role_code, permission_code),
                    FOREIGN KEY (role_code) REFERENCES roles(code),
                    FOREIGN KEY (permission_code) REFERENCES permissions(code)
                );
                INSERT OR IGNORE INTO permissions (code, description)
                VALUES ('erp.use', 'Работа в ERP');
                INSERT OR IGNORE INTO permissions (code, description)
                VALUES ('access.manage', 'Управление сотрудниками и доступом');
                INSERT OR IGNORE INTO permissions (code, description)
                VALUES ('security.audit', 'Просмотр журнала безопасности');
                INSERT OR IGNORE INTO role_permissions (role_code, permission_code)
                VALUES ('employee', 'erp.use');
                INSERT OR IGNORE INTO role_permissions (role_code, permission_code)
                VALUES ('superadmin', 'erp.use');
                INSERT OR IGNORE INTO role_permissions (role_code, permission_code)
                VALUES ('superadmin', 'access.manage');
                INSERT OR IGNORE INTO role_permissions (role_code, permission_code)
                VALUES ('superadmin', 'security.audit');

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    email_normalized TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'employee',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invitations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    email TEXT,
                    email_normalized TEXT,
                    role TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                        CHECK (state IN ('active', 'used', 'revoked')),
                    created_by INTEGER,
                    created_at INTEGER NOT NULL,
                    used_at INTEGER,
                    used_by INTEGER,
                    FOREIGN KEY (created_by) REFERENCES users(id),
                    FOREIGN KEY (used_by) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS invitations_state_expires
                    ON invitations(state, expires_at);

                CREATE TABLE IF NOT EXISTS auth_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS auth_attempts_bucket_time
                    ON auth_attempts(bucket, attempted_at);

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    token_type TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS auth_tokens_user_type
                    ON auth_tokens(user_id, token_type, created_at);

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER,
                    data TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS auth_sessions_expiry
                    ON auth_sessions(expires_at);

                CREATE INDEX IF NOT EXISTS auth_sessions_user
                    ON auth_sessions(user_id);

                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    result TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (actor_user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS security_events_time
                    ON security_events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS security_events_actor
                    ON security_events(actor_user_id, occurred_at DESC);
                """
            )
            users_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()[0]
            if "CHECK (role IN ('employee', 'admin'))" in users_sql:
                legacy_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)")
                }
                optional_user_values = {
                    "email_verified_at": "email_verified_at" if "email_verified_at" in legacy_columns else "created_at",
                    "updated_at": "updated_at" if "updated_at" in legacy_columns else "created_at",
                    "session_version": "session_version" if "session_version" in legacy_columns else "1",
                    "last_login_at": "last_login_at" if "last_login_at" in legacy_columns else "NULL",
                    "force_password_change": "force_password_change" if "force_password_change" in legacy_columns else "0",
                    "archived_at": "archived_at" if "archived_at" in legacy_columns else "NULL",
                    "two_factor_enrolled_at": "two_factor_enrolled_at" if "two_factor_enrolled_at" in legacy_columns else "NULL",
                }
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        """
                        CREATE TABLE users_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            first_name TEXT NOT NULL,
                            last_name TEXT NOT NULL,
                            email TEXT NOT NULL,
                            email_normalized TEXT NOT NULL UNIQUE,
                            password_hash TEXT NOT NULL,
                            role TEXT NOT NULL DEFAULT 'employee',
                            active INTEGER NOT NULL DEFAULT 1,
                            created_at INTEGER NOT NULL,
                            email_verified_at INTEGER,
                            updated_at INTEGER,
                            session_version INTEGER NOT NULL DEFAULT 1,
                            last_login_at INTEGER,
                            force_password_change INTEGER NOT NULL DEFAULT 0,
                            archived_at INTEGER,
                            two_factor_enrolled_at INTEGER
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO users_new (
                            id, first_name, last_name, email, email_normalized,
                            password_hash, role, active, created_at,
                            email_verified_at, updated_at, session_version,
                            last_login_at, force_password_change, archived_at,
                            two_factor_enrolled_at
                        )
                        SELECT id, first_name, last_name, email, email_normalized,
                               password_hash,
                               CASE WHEN role = 'admin' THEN 'superadmin' ELSE role END,
                               active, created_at,
                               {email_verified_at}, {updated_at}, {session_version},
                               {last_login_at}, {force_password_change}, {archived_at},
                               {two_factor_enrolled_at}
                        FROM users
                        """.format(**optional_user_values)
                    )
                    connection.execute("DROP TABLE users")
                    connection.execute("ALTER TABLE users_new RENAME TO users")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
            else:
                connection.execute(
                    "UPDATE users SET role = 'superadmin' WHERE role = 'admin'"
                )
            invitations_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'invitations'"
            ).fetchone()
            if invitations_sql_row and "CHECK (role IN ('employee', 'admin'))" in invitations_sql_row[0]:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        """
                        CREATE TABLE invitations_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            token_hash TEXT NOT NULL UNIQUE,
                            email TEXT,
                            email_normalized TEXT,
                            role TEXT NOT NULL,
                            expires_at INTEGER NOT NULL,
                            state TEXT NOT NULL DEFAULT 'active'
                                CHECK (state IN ('active', 'used', 'revoked')),
                            created_by INTEGER,
                            created_at INTEGER NOT NULL,
                            used_at INTEGER,
                            used_by INTEGER,
                            FOREIGN KEY (created_by) REFERENCES users(id),
                            FOREIGN KEY (used_by) REFERENCES users(id)
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO invitations_new (
                            id, token_hash, email, email_normalized, role,
                            expires_at, state, created_by, created_at, used_at, used_by
                        )
                        SELECT id, token_hash, email, email_normalized,
                               CASE WHEN role = 'admin' THEN 'superadmin' ELSE role END,
                               expires_at, state, created_by, created_at, used_at, used_by
                        FROM invitations
                        """
                    )
                    connection.execute("DROP TABLE invitations")
                    connection.execute("ALTER TABLE invitations_new RENAME TO invitations")
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS invitations_state_expires ON invitations(state, expires_at)"
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            email_verification_added = "email_verified_at" not in columns
            additions = (
                ("email_verified_at", "INTEGER"),
                ("updated_at", "INTEGER"),
                ("session_version", "INTEGER NOT NULL DEFAULT 1"),
                ("last_login_at", "INTEGER"),
                ("force_password_change", "INTEGER NOT NULL DEFAULT 0"),
                ("archived_at", "INTEGER"),
                ("two_factor_enrolled_at", "INTEGER"),
            )
            for name, definition in additions:
                if name not in columns:
                    connection.execute(
                        "ALTER TABLE users ADD COLUMN {} {}".format(
                            name,
                            definition,
                        )
                    )
            if email_verification_added:
                connection.execute(
                    "UPDATE users SET email_verified_at = created_at"
                )
            connection.execute(
                "UPDATE users SET updated_at = COALESCE(updated_at, created_at)"
            )
            session_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)")
            }
            for name, definition in (
                ("last_seen_at", "INTEGER"),
                ("ip_address", "TEXT"),
                ("user_agent", "TEXT"),
                ("revoked_at", "INTEGER"),
                ("ended_at", "INTEGER"),
                ("revoked_by", "INTEGER"),
            ):
                if name not in session_columns:
                    connection.execute(
                        "ALTER TABLE auth_sessions ADD COLUMN {} {}".format(name, definition)
                    )
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = COALESCE(last_seen_at, updated_at)"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _row_dict(row):
        return dict(row) if row is not None else None

    def get_user(self, user_id):
        if not user_id:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, first_name, last_name, email, role, active,
                       created_at, email_verified_at, updated_at,
                       session_version, last_login_at, force_password_change,
                       archived_at, two_factor_enrolled_at
                FROM users
                WHERE id = ? AND active = 1
                """,
                (user_id,),
            ).fetchone()
        return self._row_dict(row)

    def authenticate(self, email, password):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM users
                WHERE email_normalized = ? AND active = 1
                """,
                (normalize_email(email),),
            ).fetchone()

        if (
            row is None
            or row["email_verified_at"] is None
            or not check_password_hash(
                row["password_hash"],
                str(password or ""),
            )
        ):
            return None
        now = int(time.time())
        with self.connect() as connection:
            if not str(row["password_hash"]).startswith(PASSWORD_HASH_METHOD + "$"):
                connection.execute(
                    """
                    UPDATE users SET password_hash = ?, last_login_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        generate_password_hash(str(password or ""), method=PASSWORD_HASH_METHOD),
                        now,
                        now,
                        row["id"],
                    ),
                )
            else:
                connection.execute(
                    "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
        return self.get_user(row["id"])

    def get_user_by_email(self, email):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ? AND active = 1",
                (normalize_email(email),),
            ).fetchone()
        return self._row_dict(row)

    def verify_password(self, user_id, password):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ? AND active = 1",
                (user_id,),
            ).fetchone()
        return bool(row and check_password_hash(row["password_hash"], str(password or "")))

    def create_initial_admin(
        self,
        first_name,
        last_name,
        email,
        password,
    ):
        normalized = normalize_email(email)
        now = int(time.time())
        try:
            with self.connect() as connection:
                connection.execute(
                    "BEGIN IMMEDIATE"
                )
                if connection.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0]:
                    raise RegistrationError(
                        "bootstrap",
                        "Начальная настройка уже завершена: в ERP есть пользователи.",
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        first_name, last_name, email, email_normalized,
                        password_hash, role, created_at, email_verified_at,
                        updated_at, session_version
                    )
                    VALUES (?, ?, ?, ?, ?, 'superadmin', ?, ?, ?, 1)
                    """,
                    (
                        first_name.strip(),
                        last_name.strip(),
                        email.strip(),
                        normalized,
                        generate_password_hash(
                            password,
                            method=PASSWORD_HASH_METHOD,
                        ),
                        now,
                        now,
                        now,
                    ),
                )
                connection.commit()
                return cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise RegistrationError(
                "email",
                "Пользователь с таким email уже существует.",
            ) from error

    def create_invitation(self, created_by, email, role, lifetime_hours):
        if role not in ALLOWED_ROLES:
            role = "employee"

        token = secrets.token_urlsafe(32)
        token_hash = invitation_digest(token)
        email_value = str(email or "").strip() or None
        email_normalized = (
            normalize_email(email_value) if email_value else None
        )
        now = int(time.time())
        expires_at = now + int(lifetime_hours) * 3600

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO invitations (
                    token_hash, email, email_normalized, role, expires_at,
                    created_by, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    email_value,
                    email_normalized,
                    role,
                    expires_at,
                    created_by,
                    now,
                ),
            )
        return token

    def create_bootstrap_invitation(self, lifetime_hours):
        token = secrets.token_urlsafe(32)
        token_hash = invitation_digest(token)
        now = int(time.time())
        expires_at = now + int(lifetime_hours) * 3600
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]:
                connection.rollback()
                raise RegistrationError(
                    "bootstrap",
                    "Начальная настройка уже завершена: в ERP есть пользователи.",
                )
            if connection.execute(
                """
                SELECT 1 FROM invitations
                WHERE created_by IS NULL AND role = 'superadmin'
                  AND state = 'active' AND expires_at > ?
                LIMIT 1
                """,
                (now,),
            ).fetchone():
                connection.rollback()
                raise RegistrationError(
                    "bootstrap",
                    "Активная начальная ссылка уже существует.",
                )
            connection.execute(
                """
                INSERT INTO invitations (
                    token_hash, email, email_normalized, role, expires_at,
                    created_by, created_at
                ) VALUES (?, NULL, NULL, 'superadmin', ?, NULL, ?)
                """,
                (token_hash, expires_at, now),
            )
            connection.commit()
        return token

    def user_count(self):
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

    def has_active_bootstrap_invitation(self):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT 1
                FROM invitations
                WHERE created_by IS NULL
                  AND role = 'superadmin'
                  AND state = 'active'
                  AND expires_at > ?
                LIMIT 1
                """,
                (int(time.time()),),
            ).fetchone() is not None

    def get_invitation(self, token_hash):
        if not token_hash:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, email_normalized, role, expires_at, state,
                       created_at, used_at
                FROM invitations
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        invitation = self._row_dict(row)
        if invitation:
            invitation["status"] = self.invitation_status(invitation)
        return invitation

    @staticmethod
    def invitation_status(invitation):
        if invitation["state"] == "used":
            return "used"
        if invitation["state"] == "revoked":
            return "revoked"
        if invitation["expires_at"] <= int(time.time()):
            return "expired"
        return "active"

    def list_invitations(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT invitations.id, invitations.email, invitations.role,
                       invitations.expires_at, invitations.state,
                       invitations.created_at, invitations.used_at,
                       users.email AS created_by_email
                FROM invitations
                LEFT JOIN users ON users.id = invitations.created_by
                ORDER BY invitations.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["status"] = self.invitation_status(item)
            item["expires_at_text"] = datetime.fromtimestamp(
                item["expires_at"]
            ).strftime("%d.%m.%Y %H:%M")
            result.append(item)
        return result

    def revoke_invitation(self, invitation_id):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE invitations
                SET state = 'revoked'
                WHERE id = ? AND state = 'active' AND expires_at > ?
                """,
                (invitation_id, int(time.time())),
            )
        return cursor.rowcount == 1

    def register_user(
        self,
        token_hash,
        first_name,
        last_name,
        email,
        password,
    ):
        normalized = normalize_email(email)
        now = int(time.time())

        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                invitation = connection.execute(
                    """
                    SELECT *
                    FROM invitations
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()

                if (
                    invitation is None
                    or invitation["state"] != "active"
                    or invitation["expires_at"] <= now
                ):
                    raise RegistrationError(
                        "invitation",
                        INVITATION_PUBLIC_ERROR,
                    )

                if (
                    invitation["email_normalized"]
                    and invitation["email_normalized"] != normalized
                ):
                    raise RegistrationError(
                        "invitation",
                        INVITATION_PUBLIC_ERROR,
                    )

                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        first_name, last_name, email, email_normalized,
                        password_hash, role, created_at, email_verified_at,
                        updated_at, session_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        first_name,
                        last_name,
                        email.strip(),
                        normalized,
                        generate_password_hash(
                            password,
                            method=PASSWORD_HASH_METHOD,
                        ),
                        invitation["role"],
                        now,
                        now,
                        now,
                    ),
                )
                user_id = cursor.lastrowid

                claimed = connection.execute(
                    """
                    UPDATE invitations
                    SET state = 'used', used_at = ?, used_by = ?
                    WHERE id = ? AND state = 'active' AND expires_at > ?
                    """,
                    (now, user_id, invitation["id"], now),
                )
                if claimed.rowcount != 1:
                    raise RegistrationError(
                        "invitation",
                        INVITATION_PUBLIC_ERROR,
                    )

                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise RegistrationError(
                    "email",
                    "Не удалось создать аккаунт с указанными данными.",
                ) from error
            except RegistrationError:
                connection.rollback()
                raise

        return self.get_user(user_id)

    def create_token(self, user_id, token_type, lifetime_seconds):
        token = secrets.token_urlsafe(32)
        digest = token_digest(token)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE auth_tokens
                SET used_at = ?
                WHERE user_id = ? AND token_type = ? AND used_at IS NULL
                """,
                (now, user_id, token_type),
            )
            connection.execute(
                """
                INSERT INTO auth_tokens (
                    user_id, token_hash, token_type, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, digest, token_type, now + lifetime_seconds, now),
            )
            connection.commit()
        return token

    def token_record(self, token, token_type):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT auth_tokens.*, users.email
                FROM auth_tokens
                JOIN users ON users.id = auth_tokens.user_id
                WHERE token_hash = ? AND token_type = ?
                """,
                (token_digest(token), token_type),
            ).fetchone()
        return self._row_dict(row)

    def verify_email(self, token):
        digest = token_digest(token)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM auth_tokens
                WHERE token_hash = ? AND token_type = ?
                """,
                (digest, TOKEN_EMAIL_VERIFICATION),
            ).fetchone()
            if (
                row is None
                or row["used_at"] is not None
                or row["expires_at"] <= now
            ):
                connection.rollback()
                return False
            claimed = connection.execute(
                """
                UPDATE auth_tokens SET used_at = ?
                WHERE id = ? AND used_at IS NULL AND expires_at > ?
                """,
                (now, row["id"], now),
            )
            if claimed.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE users
                SET email_verified_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["user_id"]),
            )
            connection.commit()
        return True

    def reset_password(self, token, password):
        digest = token_digest(token)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM auth_tokens
                WHERE token_hash = ? AND token_type = ?
                """,
                (digest, TOKEN_PASSWORD_RESET),
            ).fetchone()
            if (
                row is None
                or row["used_at"] is not None
                or row["expires_at"] <= now
            ):
                connection.rollback()
                return False
            claimed = connection.execute(
                """
                UPDATE auth_tokens SET used_at = ?
                WHERE id = ? AND used_at IS NULL AND expires_at > ?
                """,
                (now, row["id"], now),
            )
            if claimed.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, session_version = session_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    generate_password_hash(
                        password,
                        method=PASSWORD_HASH_METHOD,
                    ),
                    now,
                    row["user_id"],
                ),
            )
            connection.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
                WHERE user_id = ? AND revoked_at IS NULL AND ended_at IS NULL
                """,
                (now, row["user_id"], row["user_id"]),
            )
            connection.commit()
        return True

    @staticmethod
    def _safe_metadata(metadata):
        clean = {}
        for key, value in (metadata or {}).items():
            key_text = str(key)[:80]
            if key_text.casefold() in SECURITY_SECRET_FIELDS:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                clean[key_text] = value if not isinstance(value, str) else value[:500]
        return clean

    def record_security_event(
        self,
        action,
        result,
        actor_user_id=None,
        target_type=None,
        target_id=None,
        ip_address=None,
        user_agent=None,
        metadata=None,
    ):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO security_events (
                    occurred_at, actor_user_id, action, target_type, target_id,
                    result, ip_address, user_agent, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()), actor_user_id, str(action)[:100],
                    str(target_type)[:80] if target_type else None,
                    str(target_id)[:120] if target_id is not None else None,
                    str(result)[:40], str(ip_address or "")[:64] or None,
                    str(user_agent or "")[:500] or None,
                    json.dumps(self._safe_metadata(metadata), ensure_ascii=False),
                ),
            )

    def list_security_events(self, limit=250):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.occurred_at, e.action, e.target_type, e.target_id,
                       e.result, e.ip_address, e.user_agent, e.metadata_json,
                       u.first_name AS actor_first_name,
                       u.last_name AS actor_last_name, u.email AS actor_email,
                       target.first_name AS target_first_name,
                       target.last_name AS target_last_name,
                       target.email AS target_email
                FROM security_events e
                LEFT JOIN users u ON u.id = e.actor_user_id
                LEFT JOIN users target
                  ON e.target_type = 'user' AND CAST(target.id AS TEXT) = e.target_id
                ORDER BY e.id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users(self):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT u.id, u.first_name, u.last_name, u.email, u.role,
                       u.active, u.created_at, u.last_login_at,
                       u.force_password_change, u.archived_at,
                       MAX(s.last_seen_at) AS last_seen_at,
                       SUM(CASE WHEN s.revoked_at IS NULL AND s.ended_at IS NULL
                                     AND s.expires_at > ? THEN 1 ELSE 0 END)
                           AS active_session_count
                FROM users u
                LEFT JOIN auth_sessions s ON s.user_id = u.id
                GROUP BY u.id ORDER BY u.active DESC, u.last_name, u.first_name, u.email
                """,
                (int(time.time()),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user_for_management(self, user_id):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, first_name, last_name, email, role, active,
                       force_password_change, archived_at, last_login_at
                FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._row_dict(row)

    def active_superadmin_count(self, connection=None):
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            return connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE active = 1 AND archived_at IS NULL AND role = 'superadmin'
                """
            ).fetchone()[0]
        finally:
            if owns_connection:
                connection.close()

    def update_user(self, actor_id, user_id, *, first_name=None, last_name=None,
                    role=None, active=None, force_password_change=None):
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                connection.rollback()
                raise ValueError("Сотрудник не найден.")
            next_role = target["role"] if role is None else role
            next_active = bool(target["active"]) if active is None else bool(active)
            if next_role not in ALLOWED_ROLES:
                connection.rollback()
                raise ValueError("Недопустимая роль.")
            if actor_id == user_id and (not next_active or next_role != "superadmin"):
                connection.rollback()
                raise ValueError("Нельзя заблокировать или понизить собственную роль.")
            removes_superadmin = (
                target["role"] == "superadmin" and bool(target["active"])
                and (next_role != "superadmin" or not next_active)
            )
            if removes_superadmin and self.active_superadmin_count(connection) <= 1:
                connection.rollback()
                raise ValueError("Нельзя оставить ERP без активного суперадминистратора.")
            connection.execute(
                """
                UPDATE users SET first_name = ?, last_name = ?, role = ?, active = ?,
                    force_password_change = ?, updated_at = ?,
                    session_version = session_version + ? WHERE id = ?
                """,
                (
                    target["first_name"] if first_name is None else str(first_name).strip()[:100],
                    target["last_name"] if last_name is None else str(last_name).strip()[:100],
                    next_role, 1 if next_active else 0,
                    target["force_password_change"] if force_password_change is None else (1 if force_password_change else 0),
                    now, 1 if (not next_active or next_role != target["role"]) else 0, user_id,
                ),
            )
            if not next_active or next_role != target["role"]:
                connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
                    WHERE user_id = ? AND revoked_at IS NULL AND ended_at IS NULL
                    """,
                    (now, actor_id, user_id),
                )
            connection.commit()
        return self.get_user_for_management(user_id)

    def issue_temporary_password(self, actor_id, user_id):
        temporary_password = secrets.token_urlsafe(18)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                connection.rollback()
                raise ValueError("Сотрудник не найден.")
            connection.execute(
                """
                UPDATE users SET password_hash = ?, force_password_change = 1,
                    session_version = session_version + 1, updated_at = ? WHERE id = ?
                """,
                (generate_password_hash(temporary_password, method=PASSWORD_HASH_METHOD), now, user_id),
            )
            connection.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
                WHERE user_id = ? AND revoked_at IS NULL AND ended_at IS NULL
                """,
                (now, actor_id, user_id),
            )
            connection.commit()
        return temporary_password

    def change_password(self, user_id, password, current_session_hash=None):
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE users SET password_hash = ?, force_password_change = 0,
                    session_version = session_version + 1, updated_at = ? WHERE id = ?
                """,
                (generate_password_hash(password, method=PASSWORD_HASH_METHOD), now, user_id),
            )
            query = """
                UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
                WHERE user_id = ? AND revoked_at IS NULL AND ended_at IS NULL
            """
            params = [now, user_id, user_id]
            if current_session_hash:
                query += " AND session_hash != ?"
                params.append(current_session_hash)
            connection.execute(query, params)
            version = connection.execute(
                "SELECT session_version FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]
            connection.commit()
        return version

    def list_sessions(self, user_id=None, limit=500):
        where = "WHERE s.user_id = ?" if user_id is not None else ""
        params = [user_id] if user_id is not None else []
        params.append(max(1, min(int(limit), 1000)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.user_id, s.created_at, s.last_seen_at, s.expires_at,
                       s.ip_address, s.user_agent, s.revoked_at, s.ended_at,
                       u.first_name, u.last_name, u.email
                FROM auth_sessions s JOIN users u ON u.id = s.user_id
                {} ORDER BY s.id DESC LIMIT ?
                """.format(where), params,
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_session(self, actor_id, session_id):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
                WHERE id = ? AND revoked_at IS NULL AND ended_at IS NULL
                """,
                (int(time.time()), actor_id, session_id),
            )
        return cursor.rowcount == 1

    def revoke_user_sessions(self, actor_id, user_id, exclude_hash=None):
        query = """
            UPDATE auth_sessions SET revoked_at = ?, revoked_by = ?
            WHERE user_id = ? AND revoked_at IS NULL AND ended_at IS NULL
        """
        params = [int(time.time()), actor_id, user_id]
        if exclude_hash:
            query += " AND session_hash != ?"
            params.append(exclude_hash)
        with self.connect() as connection:
            cursor = connection.execute(query, params)
        return cursor.rowcount

    def check_rate_limit(self, bucket, limit, window_seconds):
        now = int(time.time())
        cutoff = now - int(window_seconds)
        bucket_hash = hashlib.sha256(bucket.encode("utf-8")).hexdigest()

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM auth_attempts WHERE attempted_at < ?",
                (cutoff,),
            )
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM auth_attempts
                WHERE bucket = ? AND attempted_at >= ?
                """,
                (bucket_hash, cutoff),
            ).fetchone()[0]
            if count >= limit:
                connection.commit()
                return False
            connection.execute(
                """
                INSERT INTO auth_attempts (bucket, attempted_at)
                VALUES (?, ?)
                """,
                (bucket_hash, now),
            )
            connection.commit()
        return True

    def check_rate_limits(self, limits, window_seconds):
        now = int(time.time())
        cutoff = now - int(window_seconds)
        hashed_limits = [
            (
                hashlib.sha256(bucket.encode("utf-8")).hexdigest(),
                int(limit),
            )
            for bucket, limit in limits
        ]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM auth_attempts WHERE attempted_at < ?",
                (cutoff,),
            )
            allowed = True
            for bucket_hash, limit in hashed_limits:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM auth_attempts
                    WHERE bucket = ? AND attempted_at >= ?
                    """,
                    (bucket_hash, cutoff),
                ).fetchone()[0]
                if count >= limit:
                    allowed = False
            for bucket_hash, _limit in hashed_limits:
                connection.execute(
                    "INSERT INTO auth_attempts (bucket, attempted_at) VALUES (?, ?)",
                    (bucket_hash, now),
                )
            connection.commit()
        return allowed


class ServerSideSession(dict, SessionMixin):
    def __init__(self, initial=None, sid=None, new=False):
        dict.__init__(self, initial or {})
        self.sid = sid or secrets.token_urlsafe(32)
        self.new = new
        self.modified = False


class SQLiteSessionInterface(SessionInterface):
    session_class = ServerSideSession

    def __init__(self, initialized_path=None):
        self._initialized_paths = set()
        if initialized_path:
            self._initialized_paths.add(str(initialized_path))

    @staticmethod
    def _hash(sid):
        return hashlib.sha256(sid.encode("utf-8")).hexdigest()

    def _connect(self, app=None):
        application = app or current_app
        connection = sqlite3.connect(
            application.config["AUTH_DATABASE"],
            timeout=15,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def open_session(self, app, request):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        sid = request.cookies.get(cookie_name, "")
        if not sid or len(sid) > 128:
            return self.session_class(new=True)
        now = int(time.time())
        try:
            database_path = str(app.config["AUTH_DATABASE"])
            if database_path not in self._initialized_paths:
                AuthStore(database_path)
                self._initialized_paths.add(database_path)
            with self._connect(app) as connection:
                row = connection.execute(
                    """
                    SELECT data, created_at, expires_at, revoked_at, ended_at
                    FROM auth_sessions WHERE session_hash = ?
                    """,
                    (self._hash(sid),),
                ).fetchone()
                absolute_lifetime = int(
                    app.config.get(
                        "AUTH_ABSOLUTE_SESSION_LIFETIME",
                        AUTH_ABSOLUTE_TIMEOUT_SECONDS,
                    )
                )
                if row is not None and (
                    row["revoked_at"] is not None
                    or row["ended_at"] is not None
                    or
                    row["expires_at"] <= now
                    or row["created_at"] + absolute_lifetime <= now
                ):
                    if row["revoked_at"] is None and row["ended_at"] is None:
                        connection.execute(
                            "UPDATE auth_sessions SET ended_at = ? WHERE session_hash = ?",
                            (now, self._hash(sid)),
                        )
                    row = None
        except sqlite3.Error:
            LOGGER.exception("Не удалось открыть серверную сессию ERP")
            return self.session_class(new=True)
        if row is None:
            return self.session_class(new=True)
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            data = {}
        return self.session_class(data, sid=sid)

    def save_session(self, app, session_object, response):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        domain = app.config.get("SESSION_COOKIE_DOMAIN")
        path = app.config.get("SESSION_COOKIE_PATH") or "/"
        session_hash = self._hash(session_object.sid)
        if not session_object:
            with self._connect(app) as connection:
                connection.execute(
                    "UPDATE auth_sessions SET ended_at = COALESCE(ended_at, ?) WHERE session_hash = ?",
                    (int(time.time()), session_hash),
                )
            response.delete_cookie(cookie_name, domain=domain, path=path)
            return

        now = int(time.time())
        idle_lifetime = int(app.permanent_session_lifetime.total_seconds())
        absolute_lifetime = int(
            app.config.get(
                "AUTH_ABSOLUTE_SESSION_LIFETIME",
                AUTH_ABSOLUTE_TIMEOUT_SECONDS,
            )
        )
        payload = json.dumps(dict(session_object), ensure_ascii=False)
        user_id = session_object.get("user_id")
        with self._connect(app) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE auth_sessions SET ended_at = COALESCE(ended_at, ?)
                WHERE expires_at <= ? AND revoked_at IS NULL
                """,
                (now, now),
            )
            existing = connection.execute(
                "SELECT created_at, last_seen_at, revoked_at, ended_at FROM auth_sessions WHERE session_hash = ?",
                (session_hash,),
            ).fetchone()
            if existing and (existing["revoked_at"] is not None or existing["ended_at"] is not None):
                connection.commit()
                response.delete_cookie(cookie_name, domain=domain, path=path)
                return
            created_at = existing["created_at"] if existing else now
            absolute_expires_at = created_at + absolute_lifetime
            if absolute_expires_at <= now:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE session_hash = ?",
                    (session_hash,),
                )
                connection.commit()
                response.delete_cookie(cookie_name, domain=domain, path=path)
                return
            expires_at = min(now + idle_lifetime, absolute_expires_at)
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    session_hash, user_id, data, expires_at, created_at,
                    updated_at, last_seen_at, ip_address, user_agent
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(session_hash) DO UPDATE SET
                    user_id = excluded.user_id,
                    data = excluded.data,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at,
                    last_seen_at = CASE
                        WHEN auth_sessions.last_seen_at IS NULL
                          OR auth_sessions.last_seen_at <= excluded.last_seen_at - ?
                        THEN excluded.last_seen_at ELSE auth_sessions.last_seen_at END,
                    ip_address = COALESCE(auth_sessions.ip_address, excluded.ip_address),
                    user_agent = COALESCE(auth_sessions.user_agent, excluded.user_agent)
                """,
                (
                    session_hash,
                    user_id,
                    payload,
                    expires_at,
                    created_at,
                    now,
                    now,
                    str(request.remote_addr or "")[:64] or None,
                    str(request.headers.get("User-Agent") or "")[:500] or None,
                    int(app.config.get(
                        "SESSION_LAST_SEEN_THROTTLE_SECONDS",
                        SESSION_LAST_SEEN_THROTTLE_SECONDS,
                    )),
                ),
            )
            connection.commit()
        response.set_cookie(
            cookie_name,
            session_object.sid,
            expires=datetime.utcfromtimestamp(expires_at),
            httponly=True,
            secure=app.config.get("SESSION_COOKIE_SECURE", False),
            samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
            domain=domain,
            path=path,
        )

    def destroy(self, sid):
        if not sid:
            return
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE auth_sessions SET ended_at = COALESCE(ended_at, ?)
                WHERE session_hash = ?
                """,
                (int(time.time()), self._hash(sid)),
            )


def regenerate_session():
    old_sid = getattr(session, "sid", None)
    current_app.session_interface.destroy(old_sid)
    session.clear()
    session.sid = secrets.token_urlsafe(32)
    session.modified = True


def get_auth_store():
    return AuthStore(current_app.config["AUTH_DATABASE"])


def auth_is_enabled():
    return not (
        current_app.config.get("TESTING")
        and not current_app.config.get("AUTH_TESTING")
    )


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def csrf_is_valid():
    expected = session.get("_csrf_token", "")
    provided = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token", "")
    )
    return bool(expected and hmac.compare_digest(expected, provided))


def require_csrf():
    if not csrf_is_valid():
        abort(400, description="Не удалось подтвердить форму. Обновите страницу.")


def require_csrf_when_authenticated():
    if auth_is_enabled():
        require_csrf()


def current_auth_user():
    return getattr(g, "current_user", None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_auth_user()
        if not user or user["role"] not in SUPERADMIN_ROLES:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_auth_user()
            role = user.get("role") if user else None
            effective_role = "superadmin" if role in SUPERADMIN_ROLES else role
            if not user or permission not in ROLE_PERMISSIONS.get(effective_role, frozenset()):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _request_audit_context():
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent"),
    }


def audit_security(action, result, *, actor_user_id=None, target_type=None,
                   target_id=None, metadata=None):
    user = current_auth_user()
    get_auth_store().record_security_event(
        action,
        result,
        actor_user_id=actor_user_id if actor_user_id is not None else (user["id"] if user else None),
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
        **_request_audit_context(),
    )


def _require_sensitive_confirmation(target):
    actor = current_auth_user()
    password = request.form.get("current_password")
    confirmation = request.form.get("confirmation")
    if confirmation != SENSITIVE_CONFIRMATION_VALUE:
        raise ValueError("Подтвердите опасное действие.")
    if not actor or not get_auth_store().verify_password(actor["id"], password):
        audit_security(
            "sensitive_reauthentication", "failure", target_type="user",
            target_id=target.get("id") if target else None,
        )
        raise ValueError("Текущий пароль неверен.")
    audit_security(
        "sensitive_reauthentication", "success", target_type="user",
        target_id=target.get("id") if target else None,
    )


def _rate_allowed(scope, subject, subject_limit, ip_limit=None, global_limit=None):
    remote = request.remote_addr or "unknown"
    limits = [
        (f"{scope}:subject:{subject}", subject_limit),
        (
            f"{scope}:ip:{remote}",
            ip_limit
            or current_app.config.get("AUTH_IP_RATE_LIMIT", 100),
        ),
        (
            f"{scope}:global",
            global_limit
            or current_app.config.get("AUTH_GLOBAL_RATE_LIMIT", 1000),
        ),
    ]
    return get_auth_store().check_rate_limits(
        limits,
        current_app.config.get(
            "AUTH_RATE_LIMIT_WINDOW_SECONDS",
            AUTH_RATE_LIMIT_WINDOW_SECONDS,
        ),
    )


def _validate_password(form):
    password = str(form.get("password") or "")
    password_confirmation = str(form.get("password_confirmation") or "")
    errors = {}
    if len(password) < 8:
        errors["password"] = "Пароль должен содержать не менее 8 символов."
    elif len(password) > 128:
        errors["password"] = "Пароль не должен быть длиннее 128 символов."
    elif password.casefold() in COMMON_PASSWORDS:
        errors["password"] = "Выберите менее простой пароль."

    if password != password_confirmation:
        errors["password_confirmation"] = "Пароли не совпадают."
    return password, errors


def _validate_registration(form):
    email = str(form.get("email") or "").strip()
    password, errors = _validate_password(form)
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        errors["email"] = "Введите корректный email."
    return {"email": email}, password, errors


def _auth_link(path):
    public_url = current_app.config.get("APP_PUBLIC_URL", "").rstrip("/")
    if public_url:
        return public_url + path
    return request.url_root.rstrip("/") + path


def _send_auth_email(recipient, subject, body, development_link):
    outbox = current_app.config.get("AUTH_EMAIL_OUTBOX")
    if outbox is not None:
        outbox.append({
            "to": recipient,
            "subject": subject,
            "body": body,
            "link": development_link,
        })
        return True

    host = current_app.config.get("SMTP_HOST", "")
    sender = current_app.config.get("SMTP_FROM", "")
    if not host or not sender:
        if current_app.debug and current_app.config.get("AUTH_DEV_EMAIL_LOG"):
            LOGGER.warning(
                "DEV auth email suppressed: SMTP is not configured"
            )
            return True
        LOGGER.error("Письмо авторизации не отправлено: SMTP не настроен")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    try:
        smtp = smtplib.SMTP(
            host,
            current_app.config.get("SMTP_PORT", 587),
            timeout=15,
        )
        try:
            if current_app.config.get("SMTP_USE_TLS", True):
                smtp.starttls()
            username = current_app.config.get("SMTP_USERNAME", "")
            password = current_app.config.get("SMTP_PASSWORD", "")
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        finally:
            smtp.quit()
    except Exception as error:
        LOGGER.error(
            "Ошибка отправки письма авторизации через SMTP: %s",
            type(error).__name__,
        )
        return False
    return True


def _send_verification(user, token):
    link = _auth_link("/verify-email/" + token)
    return _send_auth_email(
        user["email"],
        "Подтверждение email — TicTacToy ERP",
        "Подтвердите email, открыв одноразовую ссылку (действует 24 часа):\n\n"
        + link,
        link,
    )


def _send_password_reset(user, token):
    link = _auth_link("/reset-password/" + token)
    return _send_auth_email(
        user["email"],
        "Восстановление пароля — TicTacToy ERP",
        "Установите новый пароль по одноразовой ссылке (действует 30 минут):\n\n"
        + link,
        link,
    )


@auth.route("/register", methods=["GET", "POST"])
def register():
    if current_auth_user():
        return redirect("/")

    store = get_auth_store()
    token_hash = session.get("_pending_invitation")
    invitation = store.get_invitation(token_hash)
    if not invitation or invitation["status"] != "active":
        session.pop("_pending_invitation", None)
        invitation = None

    values = {
        "email": invitation["email"] if invitation and invitation["email"] else "",
    }
    errors = {}
    invitation_error = session.pop("_register_error", "")
    if request.method == "POST":
        if invitation is None:
            return render_template(
                "register.html",
                errors={"form": "Для регистрации требуется действующее приглашение."},
                values={},
                invitation=None,
                invitation_error=invitation_error,
                rate_limited=False,
            ), 403
        normalized = normalize_email(request.form.get("email"))
        if not _rate_allowed(
            "registration",
            normalized or "invalid",
            current_app.config.get("REGISTRATION_EMAIL_RATE_LIMIT", 8),
            current_app.config.get("REGISTRATION_IP_RATE_LIMIT", 30),
            current_app.config.get("REGISTRATION_GLOBAL_RATE_LIMIT", 200),
        ):
            return render_template(
                "register.html",
                errors={"form": "Слишком много попыток. Попробуйте позже."},
                values=request.form,
                invitation=invitation,
                invitation_error=invitation_error,
                rate_limited=True,
            ), 429
        values, password, errors = _validate_registration(request.form)

        if not errors:
            try:
                user = store.register_user(
                    token_hash,
                    "",
                    "",
                    values["email"],
                    password,
                )
            except RegistrationError as error:
                audit_security("registration", "failure", target_type="invitation")
                errors[error.field] = (
                    "Не удалось создать аккаунт с указанными данными."
                    if error.field == "email"
                    else error.message
                )
            else:
                audit_security(
                    "invitation_used", "success", actor_user_id=user["id"],
                    target_type="user", target_id=user["id"],
                    metadata={"role": user["role"]},
                )
                session.pop("_pending_invitation", None)
                regenerate_session()
                session["user_id"] = user["id"]
                session["session_version"] = user["session_version"]
                session.permanent = True
                csrf_token()
                return redirect(
                    url_for(
                        "auth.registration_success",
                        next=safe_next_url(request.values.get("next"), "/"),
                    )
                )

    return render_template(
        "register.html",
        errors=errors,
        values=values,
        invitation=invitation,
        invitation_error=invitation_error,
        rate_limited=False,
    )


@auth.post("/register/invitation")
def accept_invitation():
    require_csrf()
    next_url = safe_next_url(request.form.get("next"), "/")
    session["_auth_next"] = next_url

    if not _rate_allowed(
        "invitation-check",
        invitation_digest(request.form.get("invitation_token")),
        current_app.config.get("INVITATION_CHECK_RATE_LIMIT", 20),
        current_app.config.get("INVITATION_CHECK_IP_RATE_LIMIT", 60),
        current_app.config.get("INVITATION_CHECK_GLOBAL_RATE_LIMIT", 500),
    ):
        session["_register_error"] = (
            "Слишком много попыток. Попробуйте позже."
        )
        audit_security("invitation_validation", "rate_limited", target_type="invitation")
        return redirect(url_for("auth.register", next=next_url))

    token_hash = invitation_digest(request.form.get("invitation_token"))
    invitation = get_auth_store().get_invitation(token_hash)
    if invitation and invitation["status"] == "active":
        session["_pending_invitation"] = token_hash
        session.pop("_register_error", None)
    else:
        session.pop("_pending_invitation", None)
        session["_register_error"] = (
            INVITATION_PUBLIC_ERROR
        )
        audit_security("invitation_validation", "failure", target_type="invitation")
    return redirect(url_for("auth.register", next=next_url))


@auth.route("/register/success")
def registration_success():
    if not current_auth_user():
        return redirect(url_for("auth.login"))
    return render_template(
        "registration_success.html",
        target=safe_next_url(request.args.get("next"), "/"),
    )


@auth.route("/verify-email/<token>")
def verify_email(token):
    verified = get_auth_store().verify_email(token)
    return render_template(
        "auth_message.html",
        title="Email подтверждён" if verified else "Ссылка недействительна",
        message=(
            "Теперь вы можете войти в TicTacToy ERP."
            if verified
            else "Ссылка уже использована или срок её действия истёк."
        ),
        action_url=url_for("auth.login"),
        action_label="Войти",
    ), 200 if verified else 400


@auth.post("/resend-verification")
def resend_verification():
    email = normalize_email(request.form.get("email"))
    if _rate_allowed(
        "verification-resend",
        email or "invalid",
        current_app.config.get("PASSWORD_RESET_RATE_LIMIT", 5),
        current_app.config.get("AUTH_IP_RATE_LIMIT", 100),
        current_app.config.get("AUTH_GLOBAL_RATE_LIMIT", 1000),
    ):
        user = get_auth_store().get_user_by_email(email)
        if user and user["email_verified_at"] is None:
            token = get_auth_store().create_token(
                user["id"], TOKEN_EMAIL_VERIFICATION, 24 * 3600
            )
            _send_verification(user, token)
    return redirect(url_for("auth.login", notice="verification-sent"))


@auth.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        if _rate_allowed(
            "password-reset",
            email or "invalid",
            current_app.config.get("PASSWORD_RESET_RATE_LIMIT", 5),
            current_app.config.get("AUTH_IP_RATE_LIMIT", 100),
            current_app.config.get("AUTH_GLOBAL_RATE_LIMIT", 1000),
        ):
            user = get_auth_store().get_user_by_email(email)
            if user and user["email_verified_at"] is not None:
                token = get_auth_store().create_token(
                    user["id"], TOKEN_PASSWORD_RESET, 30 * 60
                )
                _send_password_reset(user, token)
        sent = True
        audit_security("password_reset_requested", "accepted", target_type="credential")
    return render_template("forgot_password.html", sent=sent)


@auth.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    record = get_auth_store().token_record(token, TOKEN_PASSWORD_RESET)
    valid = bool(
        record
        and record["used_at"] is None
        and record["expires_at"] > int(time.time())
    )
    errors = {}
    if request.method == "POST" and valid:
        password, errors = _validate_password(request.form)
        if not errors:
            if get_auth_store().reset_password(token, password):
                audit_security(
                    "password_reset", "success", actor_user_id=record["user_id"],
                    target_type="user", target_id=record["user_id"],
                )
                regenerate_session()
                return redirect(url_for("auth.login", notice="password-changed"))
            valid = False
    return render_template(
        "reset_password.html",
        token=token,
        valid=valid,
        errors=errors,
    ), 200 if valid else 400


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_auth_user():
        return redirect("/")

    next_url = safe_next_url(request.values.get("next"), "/")
    error = ""
    email = ""
    notice = request.args.get("notice", "")

    if request.method == "POST":
        email = str(request.form.get("email") or "").strip()
        if not _rate_allowed(
            "login",
            normalize_email(email) or "invalid",
            current_app.config.get(
                "LOGIN_EMAIL_RATE_LIMIT",
                LOGIN_EMAIL_RATE_LIMIT,
            ),
            current_app.config.get("LOGIN_IP_RATE_LIMIT", LOGIN_IP_RATE_LIMIT),
            current_app.config.get(
                "LOGIN_GLOBAL_RATE_LIMIT",
                LOGIN_GLOBAL_RATE_LIMIT,
            ),
        ):
            audit_security("login", "rate_limited", target_type="credential")
            return render_template(
                "login.html",
                error="Слишком много попыток. Попробуйте позже.",
                email=email,
                next_url=next_url,
            ), 429

        store = get_auth_store()
        user = store.authenticate(
            email,
            request.form.get("password"),
        )
        if user is None:
            error = LOGIN_PUBLIC_ERROR
            audit_security(
                "login", "failure", target_type="credential",
                target_id=hashlib.sha256(normalize_email(email).encode()).hexdigest(),
            )
        else:
            regenerate_session()
            session["user_id"] = user["id"]
            session["session_version"] = user["session_version"]
            session.permanent = True
            csrf_token()
            audit_security("login", "success", actor_user_id=user["id"], target_type="user", target_id=user["id"])
            audit_security("session_created", "success", actor_user_id=user["id"], target_type="user", target_id=user["id"])
            if user.get("force_password_change"):
                return redirect(url_for("auth.change_password_required"))
            return redirect(next_url)

    return render_template(
        "login.html",
        error=error,
        email=email,
        next_url=next_url,
        notice=notice,
    )


@auth.post("/logout")
def logout():
    audit_security("logout", "success", target_type="session")
    regenerate_session()
    session.clear()
    return redirect(url_for("auth.login"))


@auth.post("/settings/invitations")
@admin_required
def create_invitation():
    require_csrf()
    try:
        _require_sensitive_confirmation({"id": None})
    except ValueError as error:
        return _access_redirect("error", str(error))
    email = str(request.form.get("email") or "").strip()
    role = str(request.form.get("role") or "employee")
    try:
        lifetime_hours = int(request.form.get("lifetime_hours") or 24)
    except ValueError:
        lifetime_hours = 24
    lifetime_hours = max(1, min(lifetime_hours, 720))

    if email and (
        len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)
    ):
        return redirect(
            url_for(
                "auth.access_page",
                notice="error",
                message="Введите корректный email для приглашения.",
            )
        )
    if role not in ALLOWED_ROLES:
        role = "employee"

    token = get_auth_store().create_invitation(
        current_auth_user()["id"],
        email,
        role,
        lifetime_hours,
    )
    audit_security(
        "invitation_created", "success", target_type="invitation",
        metadata={"role": role, "lifetime_hours": lifetime_hours, "email_bound": bool(email)},
    )
    link = f"{url_for('auth.register')}#invite={token}"
    response = current_app.make_response(render_template(
        "invitation_created.html",
        invitation_link=link,
        email=email,
        role_label=ALLOWED_ROLES[role],
        lifetime_hours=lifetime_hours,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@auth.post("/settings/invitations/<int:invitation_id>/revoke")
@admin_required
def revoke_invitation(invitation_id):
    require_csrf()
    revoked = get_auth_store().revoke_invitation(invitation_id)
    audit_security(
        "invitation_revoked", "success" if revoked else "failure",
        target_type="invitation", target_id=invitation_id,
    )
    notice = "success" if revoked else "error"
    message = (
        "Приглашение отозвано."
        if revoked
        else "Активное приглашение не найдено."
    )
    return _access_redirect(notice, message)


def _access_redirect(notice, message):
    return redirect(url_for("auth.access_page", notice=notice, message=message))


def _presence(last_seen_at, active=True):
    if not active:
        return "blocked", "Заблокирован"
    age = int(time.time()) - int(last_seen_at or 0)
    if last_seen_at and age < 120:
        return "online", "Онлайн"
    if last_seen_at and age <= 900:
        return "away", "Отошёл"
    return "offline", "Офлайн"


def _format_local_time(timestamp):
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(int(timestamp)).strftime("%d.%m.%Y %H:%M")


@auth.get("/app/access")
@admin_required
def access_page():
    store = get_auth_store()
    users = store.list_users()
    for user in users:
        user["presence_code"], user["presence_label"] = _presence(
            user.get("last_seen_at"), bool(user.get("active"))
        )
        user["last_seen_text"] = _format_local_time(user.get("last_seen_at"))
        user["last_login_text"] = _format_local_time(user.get("last_login_at"))
    sessions = store.list_sessions()
    now = int(time.time())
    for item in sessions:
        if item.get("revoked_at"):
            item["status_label"] = "Отозвана"
        elif item.get("ended_at") or item.get("expires_at", 0) <= now:
            item["status_label"] = "Завершена"
        else:
            item["status_label"] = _presence(item.get("last_seen_at"))[1]
        item["created_text"] = _format_local_time(item.get("created_at"))
        item["last_seen_text"] = _format_local_time(item.get("last_seen_at"))
        item["ended_text"] = _format_local_time(item.get("revoked_at") or item.get("ended_at"))
    events = store.list_security_events()
    for event in events:
        event["occurred_text"] = _format_local_time(event.get("occurred_at"))
    return render_template(
        "access_control.html",
        users=users,
        invitations=store.list_invitations(),
        sessions=sessions,
        security_events=events,
        invitation_roles=ALLOWED_ROLES,
        role_labels=ALLOWED_ROLES,
        confirmation_value=SENSITIVE_CONFIRMATION_VALUE,
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
    )


@auth.post("/app/access/users/<int:user_id>/name")
@admin_required
def update_employee_name(user_id):
    store = get_auth_store()
    try:
        store.update_user(
            current_auth_user()["id"], user_id,
            first_name=request.form.get("first_name"),
            last_name=request.form.get("last_name"),
        )
    except ValueError as error:
        audit_security("employee_name_changed", "failure", target_type="user", target_id=user_id)
        return _access_redirect("error", str(error))
    audit_security("employee_name_changed", "success", target_type="user", target_id=user_id)
    return _access_redirect("success", "Имя сотрудника обновлено.")


@auth.post("/app/access/users/<int:user_id>/access")
@admin_required
def update_employee_access(user_id):
    store = get_auth_store()
    target = store.get_user_for_management(user_id)
    if not target:
        abort(404)
    try:
        _require_sensitive_confirmation(target)
        role = request.form.get("role") or target["role"]
        active = request.form.get("active") == "1"
        force_change = request.form.get("force_password_change") == "1"
        store.update_user(
            current_auth_user()["id"], user_id, role=role, active=active,
            force_password_change=force_change,
        )
    except ValueError as error:
        audit_security("employee_access_changed", "failure", target_type="user", target_id=user_id)
        return _access_redirect("error", str(error))
    audit_security(
        "employee_access_changed", "success", target_type="user", target_id=user_id,
        metadata={"role": role, "active": active, "force_password_change": force_change},
    )
    if role != target["role"]:
        audit_security(
            "role_changed", "success", target_type="user", target_id=user_id,
            metadata={"from_role": target["role"], "to_role": role},
        )
    if active != bool(target["active"]):
        audit_security(
            "account_unblocked" if active else "account_blocked", "success",
            target_type="user", target_id=user_id,
        )
        if not active:
            audit_security("user_sessions_revoked", "success", target_type="user", target_id=user_id)
    if force_change != bool(target["force_password_change"]):
        audit_security(
            "password_change_requirement_updated", "success",
            target_type="user", target_id=user_id,
            metadata={"required": force_change},
        )
    return _access_redirect("success", "Доступ сотрудника обновлён.")


@auth.post("/app/access/users/<int:user_id>/temporary-password")
@admin_required
def issue_employee_temporary_password(user_id):
    store = get_auth_store()
    target = store.get_user_for_management(user_id)
    if not target:
        abort(404)
    try:
        _require_sensitive_confirmation(target)
        temporary_password = store.issue_temporary_password(current_auth_user()["id"], user_id)
    except ValueError as error:
        audit_security("temporary_access_issued", "failure", target_type="user", target_id=user_id)
        return _access_redirect("error", str(error))
    audit_security("temporary_access_issued", "success", target_type="user", target_id=user_id)
    response = current_app.make_response(render_template(
        "temporary_password.html", employee=target,
        temporary_password=temporary_password,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@auth.post("/app/access/sessions/<int:session_id>/revoke")
@admin_required
def revoke_employee_session(session_id):
    try:
        _require_sensitive_confirmation({"id": request.form.get("user_id")})
    except ValueError as error:
        return _access_redirect("error", str(error))
    revoked = get_auth_store().revoke_session(current_auth_user()["id"], session_id)
    audit_security(
        "session_revoked", "success" if revoked else "failure",
        target_type="session", target_id=session_id,
    )
    return _access_redirect(
        "success" if revoked else "error",
        "Сессия отозвана." if revoked else "Активная сессия не найдена.",
    )


@auth.post("/app/access/users/<int:user_id>/sessions/revoke")
@admin_required
def revoke_employee_sessions(user_id):
    target = get_auth_store().get_user_for_management(user_id)
    if not target:
        abort(404)
    try:
        _require_sensitive_confirmation(target)
    except ValueError as error:
        return _access_redirect("error", str(error))
    count = get_auth_store().revoke_user_sessions(current_auth_user()["id"], user_id)
    audit_security(
        "user_sessions_revoked", "success", target_type="user", target_id=user_id,
        metadata={"session_count": count},
    )
    return _access_redirect("success", "Активные сесии сотрудника отозваны.")


@auth.route("/change-password", methods=["GET", "POST"])
def change_password_required():
    user = current_auth_user()
    if not user:
        return redirect(url_for("auth.login"))
    errors = {}
    if request.method == "POST":
        password, errors = _validate_password(request.form)
        if not errors:
            sid = getattr(session, "sid", None)
            current_hash = SQLiteSessionInterface._hash(sid) if sid else None
            version = get_auth_store().change_password(user["id"], password, current_hash)
            session["session_version"] = version
            audit_security("password_changed", "success", target_type="user", target_id=user["id"])
            return redirect("/")
    return render_template("change_password.html", errors=errors)


def settings_invitation_context():
    user = current_auth_user()
    can_manage = bool(user and user.get("role") in SUPERADMIN_ROLES)
    return {
        "can_manage_invitations": can_manage,
        "invitations": (
            get_auth_store().list_invitations() if can_manage else []
        ),
        "invitation_roles": ALLOWED_ROLES,
    }


def configure_auth(app, project_root):
    project_root = Path(project_root)
    app.secret_key = _load_or_create_secret(
        project_root / "instance" / ".auth_session_key"
    )
    app.config.setdefault(
        "AUTH_DATABASE",
        os.getenv("ERP_AUTH_DATABASE", "").strip()
        or str(project_root / "instance" / "auth.db"),
    )
    app.config["SMTP_HOST"] = os.getenv("SMTP_HOST", "").strip()
    try:
        app.config["SMTP_PORT"] = int(os.getenv("SMTP_PORT", "587") or 587)
    except ValueError:
        app.config["SMTP_PORT"] = 587
    app.config["SMTP_USERNAME"] = os.getenv("SMTP_USERNAME", "").strip()
    app.config["SMTP_PASSWORD"] = os.getenv("SMTP_PASSWORD", "")
    app.config["SMTP_FROM"] = os.getenv("SMTP_FROM", "").strip()
    app.config["SMTP_USE_TLS"] = (
        os.getenv("SMTP_USE_TLS", "true").strip().lower()
        in ("1", "true", "yes", "on")
    )
    app.config["APP_PUBLIC_URL"] = os.getenv("APP_PUBLIC_URL", "").strip()
    app.config["AUTH_DEV_EMAIL_LOG"] = (
        os.getenv("AUTH_DEV_EMAIL_LOG", "false").strip().lower()
        in ("1", "true", "yes", "on")
    )
    app.config.setdefault(
        "AUTH_RATE_LIMIT_WINDOW_SECONDS",
        AUTH_RATE_LIMIT_WINDOW_SECONDS,
    )
    app.config.setdefault("LOGIN_EMAIL_RATE_LIMIT", LOGIN_EMAIL_RATE_LIMIT)
    app.config.setdefault("LOGIN_IP_RATE_LIMIT", LOGIN_IP_RATE_LIMIT)
    app.config.setdefault("LOGIN_GLOBAL_RATE_LIMIT", LOGIN_GLOBAL_RATE_LIMIT)
    app.config.setdefault("AUTH_IP_RATE_LIMIT", 100)
    app.config.setdefault("AUTH_GLOBAL_RATE_LIMIT", 1000)
    app.config.setdefault(
        "AUTH_ABSOLUTE_SESSION_LIFETIME",
        AUTH_ABSOLUTE_TIMEOUT_SECONDS,
    )
    app.config.setdefault(
        "SESSION_LAST_SEEN_THROTTLE_SECONDS",
        SESSION_LAST_SEEN_THROTTLE_SECONDS,
    )
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        seconds=AUTH_IDLE_TIMEOUT_SECONDS
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.getenv("ERP_SESSION_COOKIE_SECURE", "1").strip() != "0"
    )
    AuthStore(app.config["AUTH_DATABASE"])
    app.session_interface = SQLiteSessionInterface(app.config["AUTH_DATABASE"])
    app.register_blueprint(auth)

    @app.before_request
    def load_user_and_protect_erp():
        g.current_user = get_auth_store().get_user(session.get("user_id"))
        if (
            current_app.config.get("AUTH_TESTING")
            and g.current_user
            and session.get("session_version") is None
        ):
            session["session_version"] = g.current_user["session_version"]
        if (
            session.get("user_id")
            and (
                not g.current_user
                or session.get("session_version")
                != g.current_user.get("session_version")
            )
        ):
            session.clear()
            g.current_user = None

        if not auth_is_enabled():
            return None
        if (
            g.current_user
            and g.current_user.get("force_password_change")
            and request.endpoint not in {
                "auth.change_password_required", "auth.logout", "static"
            }
        ):
            if request.path.startswith("/api/"):
                return jsonify({
                    "code": "PASSWORD_CHANGE_REQUIRED",
                    "message": "Необходимо сменить временный пароль.",
                }), 403
            return redirect(url_for("auth.change_password_required"))
        is_public = request.endpoint in PUBLIC_ENDPOINTS
        if not is_public and not g.current_user:
            if request.path.startswith("/api/"):
                return jsonify({
                    "code": "AUTH_REQUIRED",
                    "message": "Требуется авторизация.",
                }), 401
            return redirect(
                url_for(
                    "auth.login",
                    next=safe_next_url(request.full_path.rstrip("?"), "/"),
                )
            )
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            require_csrf()
        return None

    @app.context_processor
    def inject_auth_context():
        return {
            "current_user": current_auth_user(),
            "csrf_token": csrf_token,
        }

    @app.cli.command("auth-create-admin")
    @click.option("--email", prompt=True)
    @click.option("--first-name", prompt="Имя")
    @click.option("--last-name", prompt="Фамилия")
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    )
    def create_admin_command(email, first_name, last_name, password):
        if not EMAIL_PATTERN.fullmatch(email.strip()):
            raise click.ClickException("Некорректный email")
        if len(password) < 8 or password.casefold() in COMMON_PASSWORDS:
            raise click.ClickException("Пароль слишком простой")
        try:
            get_auth_store().create_initial_admin(
                first_name,
                last_name,
                email,
                password,
            )
        except RegistrationError as error:
            raise click.ClickException(error.message) from error
        click.echo("Администратор создан.")

    @app.cli.command("auth-bootstrap-admin-link")
    @click.option(
        "--lifetime-hours",
        type=click.IntRange(1, 72),
        default=24,
        show_default=True,
    )
    def bootstrap_admin_link_command(lifetime_hours):
        store = get_auth_store()
        try:
            token = store.create_bootstrap_invitation(lifetime_hours)
        except RegistrationError as error:
            raise click.ClickException(error.message) from error
        click.echo(f"/register#invite={token}")
