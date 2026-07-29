import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
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
from werkzeug.security import check_password_hash, generate_password_hash


auth = Blueprint("auth", __name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")
PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.register",
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


class RegistrationError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def normalize_email(value):
    return str(value or "").strip().casefold()


def invitation_digest(token):
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


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
                """
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
                SELECT id, first_name, last_name, email, role, active, created_at
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

        if row is None or not check_password_hash(
            row["password_hash"],
            str(password or ""),
        ):
            return None
        return self.get_user(row["id"])

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
                        password_hash, role, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'admin', ?)
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


def _validate_registration(form, invitation):
    values = {
        "first_name": str(form.get("first_name") or "").strip(),
        "last_name": str(form.get("last_name") or "").strip(),
        "email": str(form.get("email") or "").strip(),
    }
    password = str(form.get("password") or "")
    password_confirmation = str(form.get("password_confirmation") or "")
    errors = {}

    if not values["first_name"]:
        errors["first_name"] = "Укажите имя."
    elif len(values["first_name"]) > 80:
        errors["first_name"] = "Имя не должно быть длиннее 80 символов."

    if not values["last_name"]:
        errors["last_name"] = "Укажите фамилию."
    elif len(values["last_name"]) > 80:
        errors["last_name"] = "Фамилия не должна быть длиннее 80 символов."

    if len(values["email"]) > 254 or not EMAIL_PATTERN.fullmatch(
        values["email"]
    ):
        errors["email"] = "Введите корректный email."
    elif (
        invitation
        and invitation["email_normalized"]
        and invitation["email_normalized"] != normalize_email(values["email"])
    ):
        errors["email"] = "Используйте email, указанный в приглашении."

    if len(password) < 8:
        errors["password"] = "Пароль должен содержать не менее 8 символов."
    elif len(password) > 128:
        errors["password"] = "Пароль не должен быть длиннее 128 символов."
    elif password.casefold() in COMMON_PASSWORDS:
        errors["password"] = "Выберите менее простой пароль."

    if password != password_confirmation:
        errors["password_confirmation"] = "Пароли не совпадают."

    if form.get("terms") != "1":
        errors["terms"] = "Подтвердите согласие с правилами и обработкой данных."

    return values, password, errors


@auth.route("/register", methods=["GET", "POST"])
def register():
    if current_auth_user():
        return redirect("/")

    store = get_auth_store()
    next_url = safe_next_url(
        request.values.get("next"),
        session.get("_auth_next", "/"),
    )
    session["_auth_next"] = next_url

    pending_hash = session.get("_pending_invitation")
    invitation = store.get_invitation(pending_hash)
    if invitation and invitation["status"] != "active":
        invitation = None
        session.pop("_pending_invitation", None)

    values = {}
    if invitation and invitation["email"]:
        values["email"] = invitation["email"]

    errors = {}
    register_error = session.pop("_register_error", None)
    if register_error:
        errors["invitation"] = register_error

    if request.method == "POST":
        require_csrf()
        if not _rate_allowed(
            "registration",
            current_app.config.get("REGISTRATION_RATE_LIMIT", 8),
        ):
            return render_template(
                "register.html",
                errors={"invitation": "Слишком много попыток. Попробуйте позже."},
                values=request.form,
                invitation=invitation,
                rate_limited=True,
            ), 429

        submitted_code = str(request.form.get("invitation_code") or "").strip()
        if not invitation and submitted_code:
            pending_hash = invitation_digest(submitted_code)
            invitation = store.get_invitation(pending_hash)
            if invitation and invitation["status"] == "active":
                session["_pending_invitation"] = pending_hash
            else:
                invitation = None

        values, password, errors = _validate_registration(
            request.form,
            invitation,
        )
        if not invitation:
            errors["invitation"] = (
                "Приглашение недействительно или срок его действия истёк."
            )

        if not errors:
            try:
                user = store.register_user(
                    pending_hash,
                    values["first_name"],
                    values["last_name"],
                    values["email"],
                    password,
                )
            except RegistrationError as error:
                errors[error.field] = error.message
            else:
                target = safe_next_url(session.get("_auth_next"), "/")
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = True
                session["_registration_target"] = target
                csrf_token()
                return redirect(url_for("auth.registration_success"))

    return render_template(
        "register.html",
        errors=errors,
        values=values,
        invitation=invitation,
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
    if not current_auth_user():
        return redirect(url_for("auth.login"))
    return render_template(
        "registration_success.html",
        target=safe_next_url(session.pop("_registration_target", "/"), "/"),
    )


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_auth_user():
        return redirect("/")

    next_url = safe_next_url(request.values.get("next"), "/")
    error = ""
    email = ""

    if request.method == "POST":
        require_csrf()
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

        user = get_auth_store().authenticate(
            email,
            request.form.get("password"),
        )
        if user is None:
            error = "Неверный email или пароль."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            csrf_token()
            return redirect(next_url)

    return render_template(
        "login.html",
        error=error,
        email=email,
        next_url=next_url,
    )


@auth.post("/logout")
def logout():
    require_csrf()
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
    return redirect(
        url_for(
            "settings_page",
            notice="success" if revoked else "error",
            message=(
                "Приглашение отозвано."
                if revoked
                else "Активное приглашение не найдено."
            ),
        )
    )


def settings_invitation_context():
    user = current_auth_user()
    if not user or user["role"] != "admin":
        return {
            "can_manage_invitations": False,
            "invitations": [],
            "invitation_roles": ALLOWED_ROLES,
        }
    return {
        "can_manage_invitations": True,
        "invitations": get_auth_store().list_invitations(),
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
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.getenv("ERP_SESSION_COOKIE_SECURE", "1").strip() != "0"
    )
    app.register_blueprint(auth)

    @app.before_request
    def load_user_and_protect_erp():
        g.current_user = get_auth_store().get_user(session.get("user_id"))
        if session.get("user_id") and not g.current_user:
            session.clear()

        if not auth_is_enabled():
            return None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if g.current_user:
            return None
        if request.path.startswith("/api/"):
            return jsonify({
                "code": "AUTH_REQUIRED",
                "message": "Требуется авторизация.",
            }), 401
        if request.path == "/":
            return redirect(url_for("auth.register"))
        return redirect(
            url_for(
                "auth.login",
                next=safe_next_url(request.full_path.rstrip("?"), "/"),
            )
        )

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
