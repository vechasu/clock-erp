"""Shared mailbox storage, MIME safety, IMAP sync and SMTP delivery."""

from __future__ import print_function

import base64
import email
import hashlib
import hmac
import imaplib
import json
import os
import re
import secrets
import smtplib
import socket
import sqlite3
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as email_policy
from email.utils import getaddresses, parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from app.mail_migrations import validate_database


MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_INITIAL_MESSAGES = 2000
STATUSES = {"new", "in_progress", "waiting_customer", "answered", "closed"}
ENTITY_TYPES = {"customer", "order", "repair", "purchase", "task"}
EMAIL_RE = re.compile(r"^[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+$")
MAIL_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class MailError(RuntimeError):
    code = "MAIL_ERROR"


class MailValidationError(MailError):
    code = "MAIL_VALIDATION_FAILED"


class MailConnectionError(MailError):
    code = "MAIL_CONNECTION_FAILED"


class MailSecretError(MailError):
    code = "MAIL_SECRET_KEY_MISSING"


class MailAmbiguousDelivery(MailError):
    code = "MAIL_DELIVERY_UNKNOWN"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_email(value):
    return str(value or "").strip().casefold()


def decode_text(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return str(value)[:1000]


def safe_error(error):
    if isinstance(error, MailSecretError):
        return str(error)
    if isinstance(error, imaplib.IMAP4.error):
        return "Почтовый сервер отклонил логин или пароль приложения."
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP-сервер отклонил логин или пароль приложения."
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "Почтовый сервер не ответил вовремя."
    if isinstance(error, (ssl.SSLError, socket.gaierror, ConnectionError, OSError)):
        return "Не удалось установить защищённое соединение с почтовым сервером."
    if isinstance(error, MailConnectionError):
        return str(error)
    return "Почтовая операция завершилась ошибкой."


class SecretBox:
    """Encrypt-then-MAC envelope using independent HMAC-derived keys.

    HMAC-SHA256 is used as a PRF-generated stream with a fresh 256-bit nonce;
    ciphertext and context are authenticated before decryption. The master key
    is supplied only through ERP_MAIL_SECRET_KEY and is never stored in SQLite.
    """

    def __init__(self, encoded_key=None):
        encoded_key = encoded_key if encoded_key is not None else os.getenv("ERP_MAIL_SECRET_KEY", "")
        try:
            key = base64.urlsafe_b64decode(str(encoded_key).encode("ascii"))
        except Exception:
            key = b""
        if len(key) < 32:
            raise MailSecretError(
                "Задайте ERP_MAIL_SECRET_KEY: URL-safe Base64 ключ длиной не менее 32 байт."
            )
        self.enc_key = hmac.new(key, b"vechasu-mail-encryption-v1", hashlib.sha256).digest()
        self.mac_key = hmac.new(key, b"vechasu-mail-authentication-v1", hashlib.sha256).digest()

    def _stream(self, nonce, length):
        result = bytearray()
        counter = 0
        while len(result) < length:
            result.extend(hmac.new(
                self.enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
            ).digest())
            counter += 1
        return bytes(result[:length])

    def encrypt(self, value):
        raw = str(value or "").encode("utf-8")
        nonce = secrets.token_bytes(32)
        encrypted = bytes(a ^ b for a, b in zip(raw, self._stream(nonce, len(raw))))
        payload = b"v1" + nonce + encrypted
        tag = hmac.new(self.mac_key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + tag).decode("ascii")

    def decrypt(self, value):
        try:
            payload = base64.urlsafe_b64decode(str(value).encode("ascii"))
        except Exception:
            raise MailSecretError("Сохранённый пароль приложения повреждён.")
        if len(payload) < 66 or payload[:2] != b"v1":
            raise MailSecretError("Сохранённый пароль приложения повреждён.")
        body, tag = payload[:-32], payload[-32:]
        expected = hmac.new(self.mac_key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise MailSecretError("Не удалось расшифровать пароль приложения текущим ключом.")
        nonce, encrypted = body[2:34], body[34:]
        raw = bytes(a ^ b for a, b in zip(encrypted, self._stream(nonce, len(encrypted))))
        return raw.decode("utf-8")


class _SafeHTML(HTMLParser):
    TAGS = {"a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "hr", "i", "li", "ol", "p", "pre", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "u", "ul", "img"}
    VOID = {"br", "hr", "img"}

    def __init__(self, show_images=False):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self.show_images = show_images
        self.external_images = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in self.TAGS:
            return
        safe = []
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag == "a":
            href = values.get("href", "").strip()
            if href.startswith(("http://", "https://", "mailto:")):
                safe.extend((("href", href), ("rel", "noopener noreferrer"), ("target", "_blank")))
        if tag == "img":
            src = (values.get("src") or values.get("data-external-src") or "").strip()
            if src.startswith("cid:"):
                safe.append(("data-cid", src[4:]))
            elif src.startswith(("http://", "https://")):
                self.external_images = True
                if self.show_images:
                    safe.extend((("src", src), ("referrerpolicy", "no-referrer")))
                else:
                    safe.extend((("data-external-image", "blocked"), ("data-external-src", src)))
            safe.append(("alt", values.get("alt", "Изображение")[:300]))
        rendered = "".join(" {}=\"{}\"".format(key, escape(value, quote=True)) for key, value in safe)
        self.parts.append("<{}{}>".format(tag, rendered))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.TAGS and tag not in self.VOID:
            self.parts.append("</{}>".format(tag))

    def handle_data(self, data):
        self.parts.append(escape(data))


def sanitize_html(value, show_images=False):
    parser = _SafeHTML(show_images=show_images)
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (ValueError, UnicodeError):
        return escape(str(value or "")), False
    return "".join(parser.parts), parser.external_images


def _part_text(part):
    try:
        return part.get_content()
    except (LookupError, UnicodeError, AttributeError):
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, "replace")


def parse_message(raw):
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MailValidationError("Письмо превышает допустимый размер 25 МБ.")
    message = email.message_from_bytes(raw, policy=email_policy)
    plain, html, attachments = "", "", []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = decode_text(part.get_filename() or "")
        if disposition == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            if len(payload) > MAX_ATTACHMENT_BYTES:
                continue
            attachments.append({
                "name": filename or "attachment", "content_type": content_type,
                "content_id": str(part.get("Content-ID") or "").strip("<>"), "data": payload,
            })
        elif content_type == "text/plain" and not plain:
            plain = _part_text(part)
        elif content_type == "text/html" and not html:
            html = _part_text(part)
    safe_html, external_images = sanitize_html(html)
    try:
        sent_at = parsedate_to_datetime(message.get("Date"))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        sent_at = sent_at.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OverflowError):
        sent_at = utc_now()
    recipients = {}
    for kind, header in (("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")):
        recipients[kind] = [
            {"name": decode_text(name), "email": address.strip(), "normalized": normalize_email(address)}
            for name, address in getaddresses(message.get_all(header, [])) if address.strip()
        ]
    message_id = str(message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = "<erp-import-{}@local>".format(hashlib.sha256(raw).hexdigest())
    references = re.findall(r"<[^<>]+>", str(message.get("References") or ""))
    subject = decode_text(message.get("Subject") or "(без темы)")[:1000]
    display_text = re.sub(r"\s+", " ", plain or re.sub(r"<[^>]+>", " ", safe_html)).strip()
    return {
        "message_id": message_id[:1000], "in_reply_to": str(message.get("In-Reply-To") or "").strip()[:1000],
        "references": references[-100:], "subject": subject,
        "sent_at": sent_at, "text_body": plain[:1000000], "html_body": safe_html[:2000000],
        "snippet": display_text[:500], "external_images": external_images,
        "recipients": recipients, "attachments": attachments,
    }


def parse_addresses(value):
    addresses = []
    for name, address in getaddresses([str(value or "")]):
        normalized = normalize_email(address)
        if not EMAIL_RE.match(normalized):
            raise MailValidationError("Укажите корректный адрес электронной почты.")
        if "\r" in address or "\n" in address or "\r" in name or "\n" in name:
            raise MailValidationError("Адрес содержит недопустимые символы.")
        addresses.append({"name": decode_text(name)[:240], "email": address.strip()[:320], "normalized": normalized})
    if not addresses:
        raise MailValidationError("Добавьте хотя бы одного получателя.")
    return addresses


def validated_connection_settings(payload, existing=None):
    """Return a normalized, TLS-only account payload without a password."""
    existing = dict(existing or {})

    def value(name, fallback=""):
        raw = payload.get(name)
        if raw is None or str(raw).strip() == "":
            raw = existing.get(name, fallback)
        return str(raw or "").strip()

    legacy_security = value("security", existing.get("security", "ssl"))
    clean = {
        "mailbox_name": value("mailbox_name"),
        "sender_name": value("sender_name")[:240],
        "email": value("email"),
        "imap_host": value("imap_host"),
        "imap_port": value("imap_port", "993"),
        "imap_security": value(
            "imap_security", existing.get("imap_security", legacy_security)
        ),
        "smtp_host": value("smtp_host"),
        "smtp_port": value("smtp_port", "465"),
        "smtp_security": value(
            "smtp_security", existing.get("smtp_security", legacy_security)
        ),
        "login": value("login"),
    }
    required = (
        "mailbox_name", "email", "imap_host", "imap_port",
        "smtp_host", "smtp_port", "login",
    )
    if any(not clean[name] for name in required):
        raise MailValidationError("Заполните обязательные параметры подключения.")
    if any("\r" in item or "\n" in item for item in clean.values()):
        raise MailValidationError("Параметры подключения содержат недопустимые символы.")
    if not EMAIL_RE.match(normalize_email(clean["email"])):
        raise MailValidationError("Укажите корректный email рабочего ящика.")
    for field in ("imap_host", "smtp_host"):
        host = clean[field]
        if (
            not MAIL_HOST_RE.match(host)
            or ".." in host
            or "." not in host
        ):
            raise MailValidationError("Укажите корректный адрес почтового сервера.")
    for field in ("imap_security", "smtp_security"):
        if clean[field] not in {"ssl", "starttls"}:
            raise MailValidationError(
                "Для IMAP и SMTP обязательно выберите SSL/TLS или STARTTLS."
            )
    for field in ("imap_port", "smtp_port"):
        try:
            port = int(clean[field])
        except (TypeError, ValueError):
            raise MailValidationError("Порты IMAP и SMTP должны быть целыми числами.")
        if port < 1 or port > 65535:
            raise MailValidationError("Порты IMAP и SMTP должны быть от 1 до 65535.")
        clean[field] = port
    clean["email"] = clean["email"][:320]
    clean["login"] = clean["login"][:500]
    clean["imap_host"] = clean["imap_host"][:253]
    clean["smtp_host"] = clean["smtp_host"][:253]
    return clean


class MailStore:
    def __init__(self, path=None, attachment_root=None):
        self.path = Path(path or os.getenv("ERP_MAIL_DATABASE", "") or "instance/mail.db")
        self.attachment_root = Path(attachment_root or os.getenv("ERP_MAIL_ATTACHMENT_ROOT", "") or "instance/mail-attachments")
        self._validated = False

    def initialize(self):
        if not self._validated:
            validate_database(self.path)
            self._validated = True
        return self

    def connect(self):
        self.initialize()
        connection = sqlite3.connect(str(self.path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def account(self, include_disabled=True):
        with self.connect() as connection:
            sql = "SELECT * FROM mail_accounts"
            if not include_disabled:
                sql += " WHERE enabled=1"
            row = connection.execute(sql + " ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None

    def save_account(self, payload, actor_id, secret_box):
        password = str(payload.get("password") or "")
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute("SELECT * FROM mail_accounts ORDER BY id LIMIT 1").fetchone()
            clean = validated_connection_settings(payload, existing)
            encrypted = secret_box.encrypt(password) if password else (existing["encrypted_password"] if existing else "")
            if not encrypted:
                raise MailValidationError("Введите пароль приложения.")
            values = (clean["mailbox_name"][:240], clean["sender_name"], clean["email"][:320], normalize_email(clean["email"]),
                      clean["imap_host"][:500], clean["imap_port"], clean["smtp_host"][:500], clean["smtp_port"],
                      clean["smtp_security"], clean["imap_security"], clean["smtp_security"],
                      clean["login"][:500], encrypted, now)
            if existing:
                connection.execute(
                    "UPDATE mail_accounts SET mailbox_name=?,sender_name=?,email=?,email_normalized=?,imap_host=?,imap_port=?,smtp_host=?,smtp_port=?,security=?,imap_security=?,smtp_security=?,login=?,encrypted_password=?,enabled=1,initial_sync_complete=0,last_sync_status='pending',last_sync_error='',updated_at=? WHERE id=?",
                    values + (existing["id"],),
                )
                account_id = existing["id"]
            else:
                cursor = connection.execute(
                    "INSERT INTO mail_accounts(mailbox_name,sender_name,email,email_normalized,imap_host,imap_port,smtp_host,smtp_port,security,imap_security,smtp_security,login,encrypted_password,created_by,created_at,updated_at,last_sync_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
                    values[:-1] + (int(actor_id), now, now),
                )
                account_id = cursor.lastrowid
                for role, folder in (("inbox", "INBOX"), ("sent", "Sent")):
                    connection.execute("INSERT INTO mail_sync_state(account_id,folder_role,folder_name,updated_at) VALUES(?,?,?,?)", (account_id, role, folder, now))
            self._history(connection, account_id, None, actor_id, "account_connected", {})
            connection.commit()
        return self.account()

    @staticmethod
    def _history(connection, account_id, thread_id, actor_id, action, details):
        connection.execute(
            "INSERT INTO mail_history(thread_id,account_id,actor_id,action,details_json,created_at) VALUES(?,?,?,?,?,?)",
            (thread_id, account_id, actor_id or None, action, json.dumps(details or {}, ensure_ascii=False, sort_keys=True), utc_now()),
        )

    def disable(self, actor_id):
        account = self.account()
        if not account:
            return
        with self.connect() as connection:
            connection.execute("UPDATE mail_accounts SET enabled=0,updated_at=? WHERE id=?", (utc_now(), account["id"]))
            self._history(connection, account["id"], None, actor_id, "account_disconnected", {})
            connection.commit()

    def unread_count(self):
        try:
            with self.connect() as connection:
                return int(connection.execute(
                    "SELECT COALESCE(SUM(unread_count),0) FROM mail_threads WHERE archived=0 AND status NOT IN ('answered','closed')"
                ).fetchone()[0])
        except (sqlite3.Error, RuntimeError):
            return 0

    @staticmethod
    def _customer_matches(address):
        normalized = normalize_email(address)
        path = Path(os.getenv("CUSTOMERS_DATABASE_PATH", "") or "instance/customers.db")
        if not normalized or not path.is_file():
            return []
        try:
            connection = sqlite3.connect("file:{}?mode=ro".format(path.resolve()), uri=True)
            rows = connection.execute(
                "SELECT DISTINCT customer_id FROM customer_contacts WHERE kind='email' AND normalized_value=? ORDER BY customer_id",
                (normalized,),
            ).fetchall()
            connection.close()
            return [int(row[0]) for row in rows]
        except sqlite3.Error:
            return []

    def threads_for_entity(self, entity_type, entity_id, limit=100):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT t.* FROM mail_threads t JOIN mail_links l ON l.thread_id=t.id WHERE l.entity_type=? AND l.entity_id=? ORDER BY t.last_message_at DESC,t.id DESC LIMIT ?",
                (entity_type, str(entity_id), max(1, min(int(limit), 200))),
            )]

    def list_threads(self, filters, user_id):
        page = max(1, min(int(filters.get("page") or 1), 1000000))
        per_page = max(10, min(int(filters.get("per_page") or 30), 100))
        view = str(filters.get("view") or "all")
        conditions, parameters = [], []
        if view == "inbox":
            conditions.append("EXISTS (SELECT 1 FROM mail_messages m WHERE m.thread_id=t.id AND m.folder_role='inbox')")
        elif view == "needs_reply":
            conditions.append("t.status IN ('new','in_progress')")
        elif view == "mine":
            conditions.append("t.assignee_id=?")
            parameters.append(int(user_id or 0))
        elif view == "sent":
            conditions.append("EXISTS (SELECT 1 FROM mail_messages m WHERE m.thread_id=t.id AND m.folder_role='sent')")
        elif view == "archive":
            conditions.append("t.archived=1")
        elif view == "drafts":
            conditions.append("EXISTS (SELECT 1 FROM mail_outbox o WHERE o.thread_id=t.id AND o.state='draft')")
        else:
            conditions.append("t.archived=0")
        query = str(filters.get("q") or "").strip().casefold()
        if query:
            conditions.append("(t.subject_fold LIKE ? OR lower(t.last_snippet) LIKE ? OR EXISTS (SELECT 1 FROM mail_recipients r JOIN mail_messages m ON m.id=r.message_id WHERE m.thread_id=t.id AND r.email_normalized LIKE ?))")
            term = "%{}%".format(query)
            parameters.extend((term, term, term))
        if filters.get("status") in STATUSES:
            conditions.append("t.status=?")
            parameters.append(filters["status"])
        if filters.get("assignee_id"):
            conditions.append("t.assignee_id=?")
            parameters.append(int(filters["assignee_id"]))
        if filters.get("date_from"):
            conditions.append("t.last_message_at>=?")
            parameters.append(str(filters["date_from"])[:10])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM mail_threads t" + where, parameters).fetchone()[0])
            rows = connection.execute(
                "SELECT t.*,(SELECT r.display_name FROM mail_recipients r JOIN mail_messages m ON m.id=r.message_id WHERE m.thread_id=t.id AND r.kind='from' ORDER BY m.sent_at DESC,m.id DESC LIMIT 1) sender_name,(SELECT r.email FROM mail_recipients r JOIN mail_messages m ON m.id=r.message_id WHERE m.thread_id=t.id AND r.kind='from' ORDER BY m.sent_at DESC,m.id DESC LIMIT 1) sender_email FROM mail_threads t" + where + " ORDER BY t.last_message_at DESC,t.id DESC LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["links"] = [dict(link) for link in connection.execute("SELECT entity_type,entity_id,label FROM mail_links WHERE thread_id=? ORDER BY id", (row["id"],))]
                result.append(item)
        return {"rows": result, "page": page, "per_page": per_page, "total": total, "pages": max(1, (total + per_page - 1) // per_page)}

    def get_thread(self, thread_id, mark_read_by=None, show_images=False):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM mail_threads WHERE id=?", (int(thread_id),)).fetchone()
            if not row:
                raise MailValidationError("Переписка не найдена.")
            messages = []
            for message in connection.execute("SELECT * FROM mail_messages WHERE thread_id=? ORDER BY sent_at,id", (int(thread_id),)):
                item = dict(message)
                if show_images and item["html_body"]:
                    item["html_body"], unused = sanitize_html(item["html_body"], show_images=True)
                item["recipients"] = [dict(value) for value in connection.execute("SELECT kind,display_name,email FROM mail_recipients WHERE message_id=? ORDER BY kind,position", (message["id"],))]
                item["attachments"] = [dict(value) for value in connection.execute("SELECT id,original_name,content_type,size_bytes FROM mail_attachments WHERE message_id=? ORDER BY id", (message["id"],))]
                messages.append(item)
            if mark_read_by and row["unread_count"]:
                connection.execute("UPDATE mail_messages SET is_read=1 WHERE thread_id=?", (int(thread_id),))
                connection.execute("UPDATE mail_threads SET unread_count=0,updated_at=? WHERE id=?", (utc_now(), int(thread_id)))
                self._history(connection, row["account_id"], int(thread_id), mark_read_by, "opened", {})
                connection.commit()
            result = dict(row)
            result["messages"] = messages
            result["links"] = [dict(value) for value in connection.execute("SELECT entity_type,entity_id,label FROM mail_links WHERE thread_id=? ORDER BY id", (int(thread_id),))]
            result["history"] = [dict(value) for value in connection.execute("SELECT actor_id,action,details_json,created_at FROM mail_history WHERE thread_id=? ORDER BY id DESC LIMIT 100", (int(thread_id),))]
        return result

    def update_thread(self, thread_id, payload, actor_id):
        allowed, values, details = [], [], {}
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise MailValidationError("Недопустимый статус переписки.")
            allowed.append("status=?"); values.append(payload["status"]); details["status"] = payload["status"]
        if "assignee_id" in payload:
            assignee = int(payload["assignee_id"]) if payload["assignee_id"] else None
            allowed.append("assignee_id=?"); values.append(assignee); details["assignee_id"] = assignee
        if "due_at" in payload:
            due = str(payload["due_at"] or "")[:32] or None
            allowed.append("due_at=?"); values.append(due); details["due_at"] = due
        if "archived" in payload:
            archived = 1 if payload["archived"] else 0
            allowed.append("archived=?"); values.append(archived); details["archived"] = bool(archived)
        if not allowed:
            raise MailValidationError("Нет изменений для сохранения.")
        with self.connect() as connection:
            row = connection.execute("SELECT account_id FROM mail_threads WHERE id=?", (int(thread_id),)).fetchone()
            if not row:
                raise MailValidationError("Переписка не найдена.")
            values.extend((utc_now(), int(thread_id)))
            connection.execute("UPDATE mail_threads SET " + ",".join(allowed) + ",updated_at=? WHERE id=?", values)
            self._history(connection, row["account_id"], int(thread_id), actor_id, "thread_updated", details)
            connection.commit()
        return self.get_thread(thread_id)

    def replace_link(self, thread_id, entity_type, entity_id, label, actor_id, remove=False):
        if entity_type not in ENTITY_TYPES or not str(entity_id or "").strip():
            raise MailValidationError("Некорректная связь ERP.")
        with self.connect() as connection:
            row = connection.execute("SELECT account_id FROM mail_threads WHERE id=?", (int(thread_id),)).fetchone()
            if not row:
                raise MailValidationError("Переписка не найдена.")
            if remove:
                connection.execute("DELETE FROM mail_links WHERE thread_id=? AND entity_type=? AND entity_id=?", (int(thread_id), entity_type, str(entity_id)))
                action = "link_removed"
            else:
                connection.execute("INSERT OR IGNORE INTO mail_links(thread_id,entity_type,entity_id,label,created_by,created_at) VALUES(?,?,?,?,?,?)", (int(thread_id), entity_type, str(entity_id), str(label or "")[:500], int(actor_id), utc_now()))
                action = "link_added"
            self._history(connection, row["account_id"], int(thread_id), actor_id, action, {"entity_type": entity_type, "entity_id": str(entity_id)})
            connection.commit()

    def request_sync(self, actor_id):
        account = self.account(include_disabled=False)
        if not account:
            raise MailValidationError("Почта не подключена.")
        with self.connect() as connection:
            recent = connection.execute("SELECT requested_at FROM mail_sync_requests WHERE account_id=? ORDER BY id DESC LIMIT 1", (account["id"],)).fetchone()
            if recent:
                try:
                    parsed = datetime.strptime(str(recent[0])[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    if parsed > datetime.now(timezone.utc) - timedelta(seconds=30):
                        raise MailValidationError("Повторную синхронизацию можно запросить через 30 секунд.")
                except ValueError:
                    pass
            connection.execute("INSERT INTO mail_sync_requests(account_id,requested_by,requested_at) VALUES(?,?,?)", (account["id"], int(actor_id), utc_now()))
            connection.commit()

    def queue_outbox(self, payload, actor_id, idempotency_key, draft=False):
        account = self.account(include_disabled=False)
        if not account:
            raise MailValidationError("Почта не подключена.")
        recipients = parse_addresses(payload.get("to"))
        cc = parse_addresses(payload.get("cc")) if str(payload.get("cc") or "").strip() else []
        bcc = parse_addresses(payload.get("bcc")) if str(payload.get("bcc") or "").strip() else []
        subject = str(payload.get("subject") or "").strip()[:1000]
        body = str(payload.get("text_body") or "")[:1000000]
        if not subject or not body:
            raise MailValidationError("Тема и текст письма обязательны.")
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise MailValidationError("Не удалось подтвердить уникальность отправки.")
        now = utc_now()
        attachment_payloads = payload.get("attachments") or []
        if not isinstance(attachment_payloads, list) or len(attachment_payloads) > 20:
            raise MailValidationError("Передан некорректный список вложений.")
        prepared_attachments, total_size = [], 0
        for item in attachment_payloads:
            try:
                raw = base64.b64decode(str(item.get("data") or ""), validate=True)
            except Exception:
                raise MailValidationError("Не удалось прочитать вложение.")
            if len(raw) > MAX_ATTACHMENT_BYTES:
                raise MailValidationError("Одно вложение не должно превышать 15 МБ.")
            total_size += len(raw)
            if total_size > MAX_MESSAGE_BYTES:
                raise MailValidationError("Общий размер письма не должен превышать 25 МБ.")
            prepared_attachments.append({
                "data": raw, "name": Path(str(item.get("name") or "attachment")).name.replace("\x00", "")[:500],
                "content_type": str(item.get("content_type") or "application/octet-stream")[:240],
            })
        with self.connect() as connection:
            existing = connection.execute("SELECT * FROM mail_outbox WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return dict(existing), False
            thread_id = int(payload["thread_id"]) if payload.get("thread_id") else None
            if thread_id is None:
                thread_cursor = connection.execute(
                    "INSERT INTO mail_threads(account_id,subject,subject_fold,status,last_message_at,last_snippet,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (account["id"], subject, subject.casefold(), "in_progress", now, body.replace("\n", " ")[:500], now, now),
                )
                thread_id = thread_cursor.lastrowid
            if payload.get("customer_id"):
                customer_id = int(payload["customer_id"])
                connection.execute("UPDATE mail_threads SET customer_id=? WHERE id=?", (customer_id, thread_id))
                connection.execute("INSERT OR IGNORE INTO mail_links(thread_id,entity_type,entity_id,label,created_by,created_at) VALUES(?,'customer',?,?,?,?)", (thread_id, str(customer_id), "Клиент №{}".format(customer_id), int(actor_id), now))
            cursor = connection.execute(
                "INSERT INTO mail_outbox(account_id,thread_id,idempotency_key,state,to_json,cc_json,bcc_json,subject,text_body,in_reply_to,references_json,author_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (account["id"], thread_id, key, "draft" if draft else "queued",
                 json.dumps(recipients, ensure_ascii=False), json.dumps(cc, ensure_ascii=False), json.dumps(bcc, ensure_ascii=False),
                 subject, body, str(payload.get("in_reply_to") or "")[:1000], json.dumps(payload.get("references") or []), int(actor_id), now, now),
            )
            self.attachment_root.mkdir(parents=True, exist_ok=True)
            os.chmod(str(self.attachment_root), 0o700)
            for attachment in prepared_attachments:
                digest = hashlib.sha256(attachment["data"]).hexdigest()
                storage_key = "outbox-{}-{}".format(uuid.uuid4().hex, digest[:16])
                target = self.attachment_root / storage_key
                with target.open("xb") as handle:
                    handle.write(attachment["data"])
                os.chmod(str(target), 0o600)
                connection.execute("INSERT INTO mail_outbox_attachments(outbox_id,storage_key,original_name,content_type,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?)", (cursor.lastrowid, storage_key, attachment["name"], attachment["content_type"], len(attachment["data"]), digest, now))
            self._history(connection, account["id"], thread_id, actor_id, "draft_saved" if draft else "send_queued", {"outbox_id": cursor.lastrowid})
            connection.commit()
            row = connection.execute("SELECT * FROM mail_outbox WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row), True

    def attachment(self, attachment_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM mail_attachments WHERE id=?", (int(attachment_id),)).fetchone()
        if not row:
            raise MailValidationError("Вложение не найдено.")
        path = self.attachment_root / row["storage_key"]
        if not path.is_file() or path.parent.resolve() != self.attachment_root.resolve():
            raise MailValidationError("Файл вложения недоступен.")
        return dict(row), path

    def _store_attachment(self, connection, message_id, item, now):
        payload = item["data"]
        digest = hashlib.sha256(payload).hexdigest()
        storage_key = "{}-{}".format(uuid.uuid4().hex, digest[:16])
        self.attachment_root.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self.attachment_root), 0o700)
        target = self.attachment_root / storage_key
        with target.open("xb") as handle:
            handle.write(payload)
        os.chmod(str(target), 0o600)
        safe_name = Path(str(item.get("name") or "attachment")).name.replace("\x00", "")[:500]
        connection.execute("INSERT INTO mail_attachments(message_id,storage_key,original_name,content_type,size_bytes,content_id,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)", (message_id, storage_key, safe_name, str(item.get("content_type") or "application/octet-stream")[:240], len(payload), str(item.get("content_id") or "")[:500], digest, now))

    def ingest(self, account_id, folder_role, folder_name, uidvalidity, uid, parsed, erp_author_id=None, delivery_status="received", forced_thread_id=None):
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute("SELECT id,thread_id FROM mail_messages WHERE account_id=? AND (message_id=? OR (remote_folder=? AND uidvalidity=? AND remote_uid=?)) LIMIT 1", (account_id, parsed["message_id"], folder_name, str(uidvalidity), int(uid))).fetchone()
            if existing:
                return existing["thread_id"], False
            parent_ids = list(parsed.get("references") or []) + ([parsed.get("in_reply_to")] if parsed.get("in_reply_to") else [])
            thread = connection.execute("SELECT * FROM mail_threads WHERE id=? AND account_id=?", (int(forced_thread_id), account_id)).fetchone() if forced_thread_id else None
            for parent in reversed(parent_ids):
                thread = connection.execute("SELECT t.* FROM mail_threads t JOIN mail_messages m ON m.thread_id=t.id WHERE m.account_id=? AND m.message_id=? LIMIT 1", (account_id, parent)).fetchone()
                if thread:
                    break
            if not thread:
                cursor = connection.execute("INSERT INTO mail_threads(account_id,subject,subject_fold,last_message_at,last_snippet,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (account_id, parsed["subject"], parsed["subject"].casefold(), parsed["sent_at"], parsed["snippet"], now, now))
                thread_id = cursor.lastrowid
            else:
                thread_id = thread["id"]
            sender = (parsed["recipients"].get("from") or [{}])[0]
            cursor = connection.execute(
                "INSERT INTO mail_messages(account_id,thread_id,folder_role,remote_folder,remote_uid,uidvalidity,message_id,in_reply_to,references_json,subject,sender_name,sender_email,sent_at,received_at,text_body,html_body,snippet,is_read,external_images,erp_author_id,delivery_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (account_id, thread_id, folder_role, folder_name, int(uid), str(uidvalidity), parsed["message_id"], parsed.get("in_reply_to", ""), json.dumps(parsed.get("references") or []), parsed["subject"], sender.get("name", "")[:240], sender.get("email", "")[:320], parsed["sent_at"], now, parsed["text_body"], parsed["html_body"], parsed["snippet"], 0 if folder_role == "inbox" else 1, 1 if parsed["external_images"] else 0, erp_author_id, delivery_status, now),
            )
            message_id = cursor.lastrowid
            for kind in ("from", "to", "cc", "bcc"):
                for position, recipient in enumerate(parsed["recipients"].get(kind) or []):
                    connection.execute("INSERT INTO mail_recipients(message_id,kind,display_name,email,email_normalized,position) VALUES(?,?,?,?,?,?)", (message_id, kind, recipient["name"][:240], recipient["email"][:320], recipient["normalized"][:320], position))
            for item in parsed["attachments"]:
                self._store_attachment(connection, message_id, item, now)
            incoming = 1 if folder_role == "inbox" else 0
            connection.execute("UPDATE mail_threads SET message_count=message_count+1,unread_count=unread_count+?,has_attachments=CASE WHEN ? THEN 1 ELSE has_attachments END,last_message_at=?,last_snippet=?,subject=?,subject_fold=?,status=CASE WHEN ? THEN 'new' ELSE 'answered' END,updated_at=? WHERE id=?", (incoming, bool(parsed["attachments"]), parsed["sent_at"], parsed["snippet"], parsed["subject"], parsed["subject"].casefold(), incoming, now, thread_id))
            if incoming and sender.get("email"):
                matches = self._customer_matches(sender["email"])
                if len(matches) == 1:
                    customer_id = matches[0]
                    connection.execute("UPDATE mail_threads SET customer_id=? WHERE id=? AND customer_id IS NULL", (customer_id, thread_id))
                    connection.execute("INSERT OR IGNORE INTO mail_links(thread_id,entity_type,entity_id,label,created_by,created_at) VALUES(?,'customer',?,'Клиент №',0,?)", (thread_id, str(customer_id), now))
                elif len(matches) > 1:
                    self._history(connection, account_id, thread_id, None, "customer_match_ambiguous", {"candidate_ids": matches[:20]})
            connection.commit()
        return thread_id, True


class MailTransport:
    def __init__(self, account, password, timeout=15):
        self.account, self.password, self.timeout = account, password, timeout

    def imap(self):
        security = self.account.get("imap_security") or self.account.get("security")
        if security == "ssl":
            try:
                client = imaplib.IMAP4_SSL(self.account["imap_host"], int(self.account["imap_port"]), timeout=self.timeout)
            except TypeError:
                client = imaplib.IMAP4_SSL(self.account["imap_host"], int(self.account["imap_port"]))
        else:
            try:
                client = imaplib.IMAP4(self.account["imap_host"], int(self.account["imap_port"]), timeout=self.timeout)
            except TypeError:
                client = imaplib.IMAP4(self.account["imap_host"], int(self.account["imap_port"]))
            client.starttls(ssl_context=ssl.create_default_context())
        client.login(self.account["login"], self.password)
        return client

    def smtp(self):
        security = self.account.get("smtp_security") or self.account.get("security")
        if security == "ssl":
            client = smtplib.SMTP_SSL(self.account["smtp_host"], int(self.account["smtp_port"]), timeout=self.timeout, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(self.account["smtp_host"], int(self.account["smtp_port"]), timeout=self.timeout)
            client.ehlo(); client.starttls(context=ssl.create_default_context()); client.ehlo()
        client.login(self.account["login"], self.password)
        return client

    def check(self):
        """Authenticate both protocols without sending a message."""
        results = {
            "imap": {"connected": False, "message": "Не проверено"},
            "smtp": {"connected": False, "message": "Не проверено"},
            "tls": {"active": True, "message": "Защищённое соединение активно"},
        }
        try:
            imap = self.imap()
            try:
                status, unused = imap.select("INBOX", readonly=True)
                del unused
                if status != "OK":
                    raise MailConnectionError(
                        "IMAP подключён, но папка входящих недоступна."
                    )
                results["imap"] = {
                    "connected": True, "message": "Подключено"
                }
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as error:
            results["imap"]["message"] = safe_error(error)
        try:
            smtp = self.smtp()
            try:
                code, unused = smtp.noop()
                del unused
                if int(code or 0) >= 400:
                    raise MailConnectionError("SMTP-сервер недоступен.")
                results["smtp"] = {
                    "connected": True, "message": "Подключено"
                }
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
        except Exception as error:
            results["smtp"]["message"] = safe_error(error)
        results["connected"] = bool(
            results["imap"]["connected"] and results["smtp"]["connected"]
        )
        return results

    def test(self):
        results = self.check()
        if not results["connected"]:
            raise MailConnectionError("Не удалось проверить IMAP и SMTP.")
        return results


class MailSynchronizer:
    def __init__(self, store, secret_box, transport_factory=MailTransport):
        self.store, self.secret_box, self.transport_factory = store, secret_box, transport_factory

    @staticmethod
    def _uidvalidity(response):
        text = b" ".join(item for item in (response or []) if isinstance(item, bytes)).decode("ascii", "ignore")
        match = re.search(r"UIDVALIDITY\s+(\d+)", text)
        if match:
            return match.group(1)
        digits = re.search(r"\b(\d+)\b", text)
        return digits.group(1) if digits else "unknown"

    @staticmethod
    def _sent_folder(client):
        try:
            status, rows = client.list()
        except Exception:
            return "Sent"
        if status != "OK":
            return "Sent"
        fallback = ""
        for row in rows or []:
            text = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
            quoted = re.findall(r'"([^"]+)"', text)
            name = quoted[-1] if quoted else text.rsplit(" ", 1)[-1].strip('"')
            folded = name.casefold()
            if "\\sent" in text.casefold():
                return name
            if folded in {"sent", "sent items", "отправленные", "отправленные письма"}:
                fallback = name
        return fallback or "Sent"

    def sync(self, force=False):
        account = self.store.account(include_disabled=False)
        if not account:
            return {"accounts": 0, "messages": 0, "threads": 0}
        password = self.secret_box.decrypt(account["encrypted_password"])
        now = utc_now()
        token = uuid.uuid4().hex
        imported, threads = 0, set()
        with self.store.connect() as connection:
            locked = connection.execute("SELECT lock_expires_at FROM mail_sync_state WHERE account_id=? AND lock_token<>'' LIMIT 1", (account["id"],)).fetchone()
            if locked and locked[0] and locked[0] > now:
                return {"accounts": 1, "messages": 0, "threads": 0, "locked": True}
            expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0).isoformat()
            connection.execute("UPDATE mail_sync_state SET lock_token=?,lock_expires_at=? WHERE account_id=?", (token, expires, account["id"]))
            connection.commit()
        try:
            client = self.transport_factory(account, password).imap()
            try:
                with self.store.connect() as connection:
                    states = [dict(row) for row in connection.execute("SELECT * FROM mail_sync_state WHERE account_id=? ORDER BY id", (account["id"],))]
                for state in states:
                    status, unused = client.select(state["folder_name"], readonly=True)
                    if status != "OK":
                        if state["folder_role"] == "sent":
                            folder = self._sent_folder(client)
                            status, unused = client.select(folder, readonly=True)
                            if status != "OK":
                                continue
                            state["folder_name"] = folder
                            with self.store.connect() as connection:
                                connection.execute("UPDATE mail_sync_state SET folder_name=?,updated_at=? WHERE id=?", (folder, utc_now(), state["id"]))
                                connection.commit()
                        else:
                            raise MailConnectionError("Не удалось открыть папку входящих.")
                    validity = self._uidvalidity(client.response("UIDVALIDITY")[1])
                    last_uid = int(state["last_uid"] or 0) if state["uidvalidity"] == validity else 0
                    if last_uid:
                        criterion = "UID {}:*".format(last_uid + 1)
                    else:
                        since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%d-%b-%Y")
                        criterion = "SINCE {}".format(since)
                    status, data = client.uid("search", None, criterion)
                    uids = (data[0].split() if status == "OK" and data else [])[:MAX_INITIAL_MESSAGES]
                    highest = last_uid
                    for raw_uid in uids:
                        uid = int(raw_uid)
                        status, fetched = client.uid("fetch", raw_uid, "(RFC822)")
                        raw = next((part[1] for part in fetched or [] if isinstance(part, tuple)), None)
                        if status != "OK" or not raw:
                            continue
                        parsed = parse_message(raw)
                        thread_id, created = self.store.ingest(account["id"], state["folder_role"], state["folder_name"], validity, uid, parsed)
                        if created:
                            imported += 1; threads.add(thread_id)
                        highest = max(highest, uid)
                    with self.store.connect() as connection:
                        connection.execute("UPDATE mail_sync_state SET uidvalidity=?,last_uid=?,updated_at=? WHERE id=?", (validity, highest, utc_now(), state["id"]))
                        connection.commit()
            finally:
                try: client.logout()
                except Exception: pass
            with self.store.connect() as connection:
                connection.execute("UPDATE mail_accounts SET last_sync_at=?,last_sync_status='ok',last_sync_error='',initial_sync_complete=1,updated_at=? WHERE id=?", (utc_now(), utc_now(), account["id"]))
                connection.execute("UPDATE mail_sync_requests SET state='done',finished_at=? WHERE account_id=? AND state='pending'", (utc_now(), account["id"]))
                connection.commit()
            return {"accounts": 1, "messages": imported, "threads": len(threads)}
        except Exception as error:
            with self.store.connect() as connection:
                connection.execute("UPDATE mail_accounts SET last_sync_status='error',last_sync_error=?,updated_at=? WHERE id=?", (safe_error(error), utc_now(), account["id"]))
                connection.commit()
            raise
        finally:
            with self.store.connect() as connection:
                connection.execute("UPDATE mail_sync_state SET lock_token='',lock_expires_at=NULL WHERE account_id=? AND lock_token=?", (account["id"], token))
                connection.commit()

    def deliver(self):
        account = self.store.account(include_disabled=False)
        if not account:
            return {"sent": 0, "failed": 0, "unknown": 0}
        password = self.secret_box.decrypt(account["encrypted_password"])
        with self.store.connect() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM mail_outbox WHERE state='queued' ORDER BY id LIMIT 20")]
        result = {"sent": 0, "failed": 0, "unknown": 0}
        for row in rows:
            with self.store.connect() as connection:
                claimed = connection.execute("UPDATE mail_outbox SET state='sending',updated_at=? WHERE id=? AND state='queued'", (utc_now(), row["id"])).rowcount
                connection.commit()
            if not claimed:
                continue
            message = EmailMessage()
            message["From"] = email.utils.formataddr((account["sender_name"], account["email"]))
            recipients = json.loads(row["to_json"]); cc = json.loads(row["cc_json"]); bcc = json.loads(row["bcc_json"])
            message["To"] = ", ".join(email.utils.formataddr((item["name"], item["email"])) for item in recipients)
            if cc: message["Cc"] = ", ".join(email.utils.formataddr((item["name"], item["email"])) for item in cc)
            message["Subject"] = row["subject"]
            message["Message-ID"] = "<erp-{}@{}>".format(uuid.uuid4().hex, account["email"].split("@")[-1])
            if row["in_reply_to"]: message["In-Reply-To"] = row["in_reply_to"]
            references = json.loads(row["references_json"] or "[]")
            if references: message["References"] = " ".join(references[-100:])
            message.set_content(row["text_body"])
            with self.store.connect() as connection:
                attachments = [dict(item) for item in connection.execute("SELECT * FROM mail_outbox_attachments WHERE outbox_id=? ORDER BY id", (row["id"],))]
            for attachment in attachments:
                target = self.store.attachment_root / attachment["storage_key"]
                if not target.is_file() or target.parent.resolve() != self.store.attachment_root.resolve():
                    continue
                content_type = attachment["content_type"].split("/", 1)
                maintype, subtype = content_type if len(content_type) == 2 else ("application", "octet-stream")
                message.add_attachment(target.read_bytes(), maintype=maintype, subtype=subtype, filename=attachment["original_name"])
            all_recipients = [item["email"] for item in recipients + cc + bcc]
            try:
                smtp = self.transport_factory(account, password).smtp()
                try:
                    smtp.send_message(message, to_addrs=all_recipients)
                finally:
                    try: smtp.quit()
                    except Exception: pass
            except (socket.timeout, TimeoutError, smtplib.SMTPServerDisconnected):
                state, code = "unknown", "SMTP_AMBIGUOUS_TIMEOUT"
                result["unknown"] += 1
            except Exception as error:
                state, code = "failed", safe_error(error)
                result["failed"] += 1
            else:
                state, code = "sent", ""
                result["sent"] += 1
                parsed = parse_message(message.as_bytes())
                self.store.ingest(account["id"], "sent", "ERP Sent", "local", row["id"], parsed, erp_author_id=row["author_id"], delivery_status="sent", forced_thread_id=row["thread_id"])
                try:
                    imap = self.transport_factory(account, password).imap()
                    with self.store.connect() as connection:
                        sent_state = connection.execute("SELECT folder_name FROM mail_sync_state WHERE account_id=? AND folder_role='sent'", (account["id"],)).fetchone()
                    sent_folder = sent_state[0] if sent_state else "Sent"
                    try: imap.append(sent_folder, "\\Seen", imaplib.Time2Internaldate(datetime.now()), message.as_bytes())
                    finally:
                        try: imap.logout()
                        except Exception: pass
                except Exception:
                    pass
            with self.store.connect() as connection:
                connection.execute("UPDATE mail_outbox SET state=?,error_code=?,sent_at=?,updated_at=? WHERE id=?", (state, code, utc_now() if state == "sent" else None, utc_now(), row["id"]))
                self.store._history(connection, account["id"], row["thread_id"], row["author_id"], "sent" if state == "sent" else "send_" + state, {"outbox_id": row["id"]})
                connection.commit()
        return result
