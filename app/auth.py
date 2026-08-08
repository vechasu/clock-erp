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
    "admin": "Администратор",
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


def allowed_emails(value):
    return {
        normalize_email(item)
        for item in str(value or "").split(",")
        if normalize_email(item)
    }


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
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    email_normalized TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('employee', 'admin')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invitations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    email TEXT,
                    email_normalized TEXT,
                    role TEXT NOT NULL CHECK (role IN ('employee', 'admin')),
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
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            email_verification_added = "email_verified_at" not in columns
            additions = (
                ("email_verified_at", "INTEGER"),
                ("updated_at", "INTEGER"),
                ("session_version", "INTEGER NOT NULL DEFAULT 1"),
                ("last_login_at", "INTEGER"),
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
                       session_version, last_login_at
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
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        first_name, last_name, email, email_normalized,
                        password_hash, role, created_at, email_verified_at,
                        updated_at, session_version
                    )
                    VALUES (?, ?, ?, ?, ?, 'admin', ?, ?, ?, 1)
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
                  AND role = 'admin'
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
                        "Приглашение недействительно или срок его действия истёк.",
                    )

                if (
                    invitation["email_normalized"]
                    and invitation["email_normalized"] != normalized
                ):
                    raise RegistrationError(
                        "email",
                        "Используйте email, указанный в приглашении.",
                    )

                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        first_name, last_name, email, email_normalized,
                        password_hash, role, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
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
                        "Приглашение недействительно или срок его действия истёк.",
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

    def create_allowed_user(self, email, password):
        normalized = normalize_email(email)
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        digest = token_digest(token)
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                user_count = connection.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0]
                role = "admin" if user_count == 0 else "employee"
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        first_name, last_name, email, email_normalized,
                        password_hash, role, active, created_at, updated_at,
                        session_version
                    )
                    VALUES ('', '', ?, ?, ?, ?, 1, ?, ?, 1)
                    """,
                    (
                        str(email).strip(),
                        normalized,
                        generate_password_hash(
                            password,
                            method=PASSWORD_HASH_METHOD,
                        ),
                        role,
                        now,
                        now,
                    ),
                )
                user_id = cursor.lastrowid
                connection.execute(
                    """
                    INSERT INTO auth_tokens (
                        user_id, token_hash, token_type, expires_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        digest,
                        TOKEN_EMAIL_VERIFICATION,
                        now + 24 * 3600,
                        now,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise RegistrationError(
                "email",
                "Аккаунт с таким email уже существует.",
            ) from error
        return self.get_user_by_email(normalized), token

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
                "DELETE FROM auth_sessions WHERE user_id = ?",
                (row["user_id"],),
            )
            connection.commit()
        return True

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
                    SELECT data FROM auth_sessions
                    WHERE session_hash = ? AND expires_at > ?
                    """,
                    (self._hash(sid), now),
                ).fetchone()
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
                    "DELETE FROM auth_sessions WHERE session_hash = ?",
                    (session_hash,),
                )
            response.delete_cookie(cookie_name, domain=domain, path=path)
            return

        now = int(time.time())
        lifetime = int(app.permanent_session_lifetime.total_seconds())
        expires_at = now + lifetime
        payload = json.dumps(dict(session_object), ensure_ascii=False)
        user_id = session_object.get("user_id")
        with self._connect(app) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?",
                (now,),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO auth_sessions (
                    session_hash, user_id, data, expires_at, created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?,
                    COALESCE((
                        SELECT created_at FROM auth_sessions
                        WHERE session_hash = ?
                    ), ?), ?
                )
                """,
                (
                    session_hash,
                    user_id,
                    payload,
                    expires_at,
                    session_hash,
                    now,
                    now,
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
                "DELETE FROM auth_sessions WHERE session_hash = ?",
                (self._hash(sid),),
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
        if not user or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _client_rate_key(scope):
    session_key = session.get("_rate_key")
    if not session_key:
        session_key = secrets.token_urlsafe(18)
        session["_rate_key"] = session_key
    remote = request.remote_addr or "unknown"
    return f"{scope}:{remote}:{session_key}"


def _rate_allowed(scope, session_limit, window=900):
    store = get_auth_store()
    remote = request.remote_addr or "unknown"
    session_allowed = store.check_rate_limit(
        _client_rate_key(scope),
        session_limit,
        window,
    )
    office_allowed = store.check_rate_limit(
        f"{scope}:office:{remote}",
        current_app.config.get("AUTH_OFFICE_RATE_LIMIT", 1000),
        window,
    )
    return session_allowed and office_allowed


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
    normalized = normalize_email(email)
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        errors["email"] = "Введите корректный email."
    elif normalized not in current_app.config["TICTACTOY_ALLOWED_EMAILS"]:
        errors["email"] = "Регистрация для этого email недоступна."
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
            LOGGER.warning("DEV auth link: %s", development_link)
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

    values = {}
    errors = {}
    if request.method == "POST":
        if not _rate_allowed(
            "registration",
            current_app.config.get("REGISTRATION_RATE_LIMIT", 8),
        ):
            return render_template(
                "register.html",
                errors={"form": "Слишком много попыток. Попробуйте позже."},
                values=request.form,
                rate_limited=True,
            ), 429
        values, password, errors = _validate_registration(request.form)

        if not errors:
            try:
                user, token = get_auth_store().create_allowed_user(
                    values["email"], password
                )
            except RegistrationError as error:
                errors[error.field] = error.message
            else:
                _send_verification(user, token)
                return render_template(
                    "auth_message.html",
                    title="Подтвердите email",
                    message="Мы отправили ссылку подтверждения на указанный email.",
                    action_url=url_for("auth.login"),
                    action_label="Перейти ко входу",
                )

    return render_template(
        "register.html",
        errors=errors,
        values=values,
        rate_limited=False,
    )


@auth.post("/register/invitation")
def accept_invitation():
    require_csrf()
    next_url = safe_next_url(request.form.get("next"), "/")
    session["_auth_next"] = next_url

    if not _rate_allowed(
        "invitation-check",
        current_app.config.get("INVITATION_CHECK_RATE_LIMIT", 20),
    ):
        session["_register_error"] = (
            "Слишком много попыток. Попробуйте позже."
        )
        return redirect(url_for("auth.register", next=next_url))

    token_hash = invitation_digest(request.form.get("invitation_token"))
    invitation = get_auth_store().get_invitation(token_hash)
    if invitation and invitation["status"] == "active":
        session["_pending_invitation"] = token_hash
        session.pop("_register_error", None)
    else:
        session.pop("_pending_invitation", None)
        session["_register_error"] = (
            "Приглашение недействительно или срок его действия истёк."
        )
    return redirect(url_for("auth.register", next=next_url))


@auth.route("/register/success")
def registration_success():
    return redirect(url_for("auth.login"))


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
    if _rate_allowed(
        "verification-resend",
        current_app.config.get("PASSWORD_RESET_RATE_LIMIT", 5),
    ):
        user = get_auth_store().get_user_by_email(request.form.get("email"))
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
        if _rate_allowed(
            "password-reset",
            current_app.config.get("PASSWORD_RESET_RATE_LIMIT", 5),
        ):
            user = get_auth_store().get_user_by_email(request.form.get("email"))
            if user and user["email_verified_at"] is not None:
                token = get_auth_store().create_token(
                    user["id"], TOKEN_PASSWORD_RESET, 30 * 60
                )
                _send_password_reset(user, token)
        sent = True
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
            current_app.config.get("LOGIN_RATE_LIMIT", 10),
        ):
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
            candidate = store.get_user_by_email(email)
            if (
                candidate
                and candidate["email_verified_at"] is None
                and check_password_hash(
                    candidate["password_hash"],
                    str(request.form.get("password") or ""),
                )
            ):
                error = "Сначала подтвердите email по ссылке из письма."
            else:
                error = "Неверный email или пароль."
        else:
            regenerate_session()
            session["user_id"] = user["id"]
            session["session_version"] = user["session_version"]
            session.permanent = True
            csrf_token()
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
    regenerate_session()
    session.clear()
    return redirect(url_for("auth.login"))


@auth.post("/settings/invitations")
@admin_required
def create_invitation():
    require_csrf()
    email = str(request.form.get("email") or "").strip()
    role = str(request.form.get("role") or "employee")
    try:
        lifetime_hours = int(request.form.get("lifetime_hours") or 72)
    except ValueError:
        lifetime_hours = 72
    lifetime_hours = max(1, min(lifetime_hours, 720))

    if email and (
        len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)
    ):
        return redirect(
            url_for(
                "settings_page",
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
    link = f"{url_for('auth.register')}#invite={token}"
    return render_template(
        "invitation_created.html",
        invitation_link=link,
        email=email,
        role_label=ALLOWED_ROLES[role],
        lifetime_hours=lifetime_hours,
    )


@auth.post("/settings/invitations/<int:invitation_id>/revoke")
@admin_required
def revoke_invitation(invitation_id):
    require_csrf()
    revoked = get_auth_store().revoke_invitation(invitation_id)
    notice = "success" if revoked else "error"
    message = (
        "Приглашение отозвано."
        if revoked
        else "Активное приглашение не найдено."
    )
    return redirect(f"/app/settings?notice={notice}&message={message}")


def settings_invitation_context():
    return {
        "can_manage_invitations": False,
        "invitations": [],
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
    app.config["TICTACTOY_ALLOWED_EMAILS"] = allowed_emails(
        os.getenv("TICTACTOY_ALLOWED_EMAILS", "")
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
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
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
        if store.user_count():
            raise click.ClickException(
                "Начальная настройка уже завершена: в ERP есть пользователи."
            )
        if store.has_active_bootstrap_invitation():
            raise click.ClickException(
                "Активная начальная ссылка уже существует."
            )
        token = store.create_invitation(
            None,
            None,
            "admin",
            lifetime_hours,
        )
        click.echo(f"/register#invite={token}")
