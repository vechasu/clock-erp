"""SMS domain service: validation, idempotency, history and provider sync."""

from __future__ import print_function

import json
import logging
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.clients.smsbliss import (
    SmsBlissError, SmsBlissInvalidResponse, SmsBlissNotConfigured,
    SmsBlissSecurityError, SmsBlissUnknownDelivery, SmsBlissUnavailable,
)
from app.sms_migrations import migrate_database, verify_database


LOGGER = logging.getLogger(__name__)


PAGE_SIZES = (20, 50, 100, 200)
MAX_TEXT_LENGTH = 2000
TEMPLATE_VARIABLES = {
    "client_name", "order_number", "order_status", "amount", "repair_number",
}
TERMINAL_STATUSES = {"delivered", "failed", "cancelled"}
STATUS_LABELS = {
    "created": "Создано", "sending": "Отправляется",
    "accepted": "Принято SmsBliss", "unknown": "Статус неизвестен",
    "queued": "В очереди", "smsc_submit": "Передано оператору",
    "delivered": "Доставлено", "failed": "Ошибка доставки",
    "cancelled": "Отменено",
}
PROVIDER_STATUS_LABELS = {
    "accepted": "Принято SmsBliss", "queued": "В очереди",
    "smsc submit": "Передано оператору", "delivered": "Доставлено",
    "delivery error": "Ошибка доставки", "smsc reject": "Отклонено оператором",
    "incorrect id": "Сообщение не найдено", "not enough balance": "Недостаточно средств",
    "invalid mobile phone": "Некорректный номер",
    "sender address invalid": "Недоступная подпись отправителя",
}
FAILED_PROVIDER_STATUSES = {
    "delivery error", "smsc reject", "incorrect id", "not enough balance",
    "invalid mobile phone", "sender address invalid",
}
GSM_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./"
    "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENDED = "^{}\\[~]|€"
PHONE_CHARS = re.compile(r"^[+\d\s().\- ]+$")
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,72}$")
TEMPLATE_PATTERN = re.compile(r"\{([a-z_]+)\}")


class SmsValidationError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_phone(value):
    raw = str(value or "").strip()
    if (not raw or not PHONE_CHARS.fullmatch(raw) or raw.count("+") > 1
            or ("+" in raw and not raw.startswith("+"))):
        raise SmsValidationError("Укажите корректный номер телефона")
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        normalized = "+7" + digits[1:]
    elif len(digits) == 10 and digits.startswith("9") and not raw.startswith("+"):
        normalized = "+7" + digits
    elif raw.startswith("+") and 8 <= len(digits) <= 15:
        normalized = "+" + digits
    else:
        raise SmsValidationError("Номер должен быть в формате E.164")
    if len(normalized) > 16 or normalized[1] == "0":
        raise SmsValidationError("Номер должен быть в формате E.164")
    return normalized


def mask_phone(value):
    try:
        normalized = normalize_phone(value)
    except SmsValidationError:
        return "***"
    return normalized[:2] + "***" + normalized[-4:]


def sms_segments(text):
    text = str(text or "")
    gsm_units = 0
    gsm = True
    for character in text:
        if character in GSM_BASIC:
            gsm_units += 1
        elif character in GSM_EXTENDED:
            gsm_units += 2
        else:
            gsm = False
            break
    units = gsm_units if gsm else len(text)
    single, multipart = (160, 153) if gsm else (70, 67)
    segments = 0 if not units else 1 if units <= single else int(math.ceil(units / float(multipart)))
    return {"encoding": "GSM-7" if gsm else "Unicode", "units": units, "segments": segments}


def render_template_text(body, values):
    body = str(body or "")
    variables = set(TEMPLATE_PATTERN.findall(body))
    unknown = variables - TEMPLATE_VARIABLES
    if unknown:
        raise SmsValidationError("Неизвестная переменная шаблона: {}".format(sorted(unknown)[0]))
    clean = {key: str((values or {}).get(key) or "") for key in TEMPLATE_VARIABLES}
    return TEMPLATE_PATTERN.sub(lambda match: clean.get(match.group(1), ""), body)


def normalized_provider_status(value):
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def internal_status(provider_status):
    value = normalized_provider_status(provider_status)
    if value in FAILED_PROVIDER_STATUSES:
        return "failed"
    if value == "smsc submit":
        return "smsc_submit"
    if value in {"accepted", "queued", "delivered"}:
        return value
    return "unknown"


def status_label(status, provider_status=""):
    provider = normalized_provider_status(provider_status)
    return PROVIDER_STATUS_LABELS.get(provider) or STATUS_LABELS.get(status, "Статус неизвестен")


class SmsStore:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        migrate_database(self.path)

    def verify(self):
        verify_database(self.path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def row(row):
        return dict(row) if row is not None else None

    def get(self, message_id=None, client_message_id=None):
        if message_id is None and not client_message_id:
            return None
        query = "SELECT * FROM sms_messages WHERE {}=?".format(
            "id" if message_id is not None else "client_message_id"
        )
        value = int(message_id) if message_id is not None else client_message_id
        with self.connect() as connection:
            row = connection.execute(query, (value,)).fetchone()
        return self.row(row)

    def history(self, message_id):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sms_status_history WHERE message_id=? ORDER BY changed_at,id",
                (int(message_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_once(self, payload, actor):
        client_id = str(payload.get("client_message_id") or "").strip()
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise SmsValidationError("Некорректный идентификатор отправки")
        phone = normalize_phone(payload.get("phone"))
        body = str(payload.get("text") or "").strip()
        if not body:
            raise SmsValidationError("Введите текст сообщения")
        if len(body) > MAX_TEXT_LENGTH:
            raise SmsValidationError("Текст SMS не должен превышать {} символов".format(MAX_TEXT_LENGTH))
        sender = str(payload.get("sender") or "").strip()
        if len(sender) > 32:
            raise SmsValidationError("Некорректная подпись отправителя")
        schedule = str(payload.get("scheduled_at") or "").strip() or None
        if schedule:
            try:
                normalized_schedule = schedule.rstrip("Z")
                if "." in normalized_schedule:
                    normalized_schedule = normalized_schedule.split(".", 1)[0]
                parsed = datetime.strptime(normalized_schedule[:19], "%Y-%m-%dT%H:%M:%S")
                schedule = parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                raise SmsValidationError("Некорректное время отправки")
        now = utc_now()
        template_id = payload.get("template_id") or None
        try:
            template_id = int(template_id) if template_id else None
        except (TypeError, ValueError):
            raise SmsValidationError("Некорректный шаблон")
        values = (
            client_id, payload.get("customer_id") or None,
            str(payload.get("customer_name") or "")[:240],
            str(payload.get("order_id") or "") or None,
            str(payload.get("order_number") or "") or None,
            str(payload.get("repair_id") or "") or None,
            str(payload.get("repair_number") or "") or None,
            str(payload.get("phone") or "")[:40], phone, body, sender or None,
            template_id, now, schedule, str(actor["id"]), actor["name"], now,
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM sms_messages WHERE client_message_id=?", (client_id,)
            ).fetchone()
            if existing:
                return dict(existing), False
            cursor = connection.execute(
                "INSERT INTO sms_messages (client_message_id,customer_id,customer_name,"
                "order_id,order_number,repair_id,repair_number,source_phone,normalized_phone,"
                "message_text,sender,template_id,status,created_at,scheduled_at,created_by_id,"
                "created_by_name,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'created',?,?,?,?,?)",
                values,
            )
            message_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO sms_status_history(message_id,status,description,changed_at,"
                "changed_by_id,changed_by_name) VALUES(?,'created','Сообщение создано',?,?,?)",
                (message_id, now, str(actor["id"]), actor["name"]),
            )
            row = connection.execute("SELECT * FROM sms_messages WHERE id=?", (message_id,)).fetchone()
        return dict(row), True

    def claim_for_send(self, message_id, actor):
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sms_messages WHERE id=?", (int(message_id),)).fetchone()
            if row is None:
                return None, False
            if row["status"] != "created":
                return dict(row), False
            connection.execute(
                "UPDATE sms_messages SET status='sending',sent_by_id=?,sent_by_name=?,updated_at=? WHERE id=?",
                (str(actor["id"]), actor["name"], now, int(message_id)),
            )
            connection.execute(
                "INSERT INTO sms_status_history(message_id,previous_status,status,description,changed_at,changed_by_id,changed_by_name) "
                "VALUES(?,'created','sending','Передано серверу отправки',?,?,?)",
                (int(message_id), now, str(actor["id"]), actor["name"]),
            )
            result = connection.execute("SELECT * FROM sms_messages WHERE id=?", (int(message_id),)).fetchone()
        return dict(result), True

    def update_status(self, message_id, status, provider_status="", smsc_id=None,
                      segments=None, cost=None, currency=None, description="",
                      actor=None):
        actor = actor or {"id": "system", "name": "Система"}
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM sms_messages WHERE id=?", (int(message_id),)).fetchone()
            if current is None:
                return None
            delivered_at = now if status == "delivered" else current["delivered_at"]
            sent_at = now if status in {"accepted", "queued", "smsc_submit", "delivered"} and not current["sent_at"] else current["sent_at"]
            connection.execute(
                "UPDATE sms_messages SET status=?,provider_status=?,smsc_id=COALESCE(?,smsc_id),"
                "segments=COALESCE(?,segments),cost=COALESCE(?,cost),currency=COALESCE(?,currency),"
                "error_description=?,sent_at=?,delivered_at=?,updated_at=? WHERE id=?",
                (status, provider_status or None, smsc_id, segments,
                 None if cost is None else str(cost), currency,
                 str(description or "")[:500] or None, sent_at, delivered_at, now, int(message_id)),
            )
            if current["status"] != status or normalized_provider_status(current["provider_status"]) != normalized_provider_status(provider_status):
                connection.execute(
                    "INSERT INTO sms_status_history(message_id,previous_status,status,provider_status,description,changed_at,changed_by_id,changed_by_name) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (int(message_id), current["status"], status, provider_status or None,
                     str(description or "")[:500] or None, now, str(actor["id"]), actor["name"]),
                )
            row = connection.execute("SELECT * FROM sms_messages WHERE id=?", (int(message_id),)).fetchone()
        return dict(row)

    def list(self, filters=None):
        filters = filters or {}
        conditions, parameters = [], []
        query = str(filters.get("q") or "").strip().casefold()
        if query:
            conditions.append("lower(COALESCE(customer_name,'') || ' ' || normalized_phone || ' ' || "
                              "COALESCE(order_number,'') || ' ' || COALESCE(repair_number,'') || ' ' || message_text) LIKE ?")
            parameters.append("%" + query + "%")
        status = str(filters.get("status") or "").strip()
        if status in STATUS_LABELS:
            conditions.append("status=?")
            parameters.append(status)
        actor = str(filters.get("actor") or "").strip()
        if actor:
            conditions.append("created_by_id=?")
            parameters.append(actor)
        for key, column in (("customer_id", "customer_id"), ("order_id", "order_id"), ("repair_id", "repair_id")):
            value = str(filters.get(key) or "").strip()
            if value:
                conditions.append(column + "=?")
                parameters.append(value)
        relation = str(filters.get("relation") or "").strip()
        if relation == "customer": conditions.append("customer_id IS NOT NULL")
        elif relation == "order": conditions.append("order_id IS NOT NULL")
        elif relation == "repair": conditions.append("repair_id IS NOT NULL")
        elif relation == "none": conditions.append("customer_id IS NULL AND order_id IS NULL AND repair_id IS NULL")
        for field, column, suffix in (("date_from", "created_at", "T00:00:00"), ("date_to", "created_at", "T23:59:59")):
            value = str(filters.get(field) or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                conditions.append(column + (">=?" if field == "date_from" else "<=?"))
                parameters.append(value + suffix)
        try: page = max(1, int(filters.get("page") or 1))
        except (TypeError, ValueError): page = 1
        try: per_page = int(filters.get("per_page") or 20)
        except (TypeError, ValueError): per_page = 20
        if per_page not in PAGE_SIZES: per_page = 20
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM sms_messages" + where, parameters).fetchone()[0])
            page_count = max(1, int(math.ceil(total / float(per_page))))
            page = min(page, page_count)
            rows = connection.execute(
                "SELECT * FROM sms_messages" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [dict(row) for row in rows], "total": total, "page": page,
                "per_page": per_page, "page_count": page_count}

    def pending(self, limit=200):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sms_messages WHERE smsc_id IS NOT NULL AND status NOT IN "
                "('delivered','failed','cancelled') ORDER BY updated_at,id LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def actors(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT created_by_id, MAX(created_by_name) AS created_by_name "
                "FROM sms_messages GROUP BY created_by_id ORDER BY created_by_name"
            ).fetchall()
        return [dict(row) for row in rows]

    def templates(self, active_only=False):
        sql = "SELECT t.*, (SELECT COUNT(*) FROM sms_messages m WHERE m.template_id=t.id) AS use_count FROM sms_templates t"
        if active_only: sql += " WHERE t.active=1"
        sql += " ORDER BY t.active DESC,t.name"
        with self.connect() as connection:
            rows = connection.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def save_template(self, template_id, name, body, active, actor):
        name, body = str(name or "").strip(), str(body or "").strip()
        if not name or len(name) > 120: raise SmsValidationError("Укажите название шаблона")
        if not body or len(body) > MAX_TEXT_LENGTH: raise SmsValidationError("Укажите корректный текст шаблона")
        render_template_text(body, {})
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if template_id:
                connection.execute(
                    "UPDATE sms_templates SET name=?,message_text=?,active=?,updated_by_id=?,updated_by_name=?,updated_at=? WHERE id=?",
                    (name, body, int(bool(active)), str(actor["id"]), actor["name"], now, int(template_id)),
                )
                result_id = int(template_id)
            else:
                cursor = connection.execute(
                    "INSERT INTO sms_templates(name,message_text,active,created_by_id,created_by_name,created_at,updated_by_id,updated_by_name,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (name, body, int(bool(active)), str(actor["id"]), actor["name"], now,
                     str(actor["id"]), actor["name"], now),
                )
                result_id = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM sms_templates WHERE id=?", (result_id,)).fetchone()
        return dict(row)

    def delete_template(self, template_id):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = connection.execute("SELECT COUNT(*) FROM sms_messages WHERE template_id=?", (int(template_id),)).fetchone()[0]
            if used:
                connection.execute("UPDATE sms_templates SET active=0 WHERE id=?", (int(template_id),))
                return False
            connection.execute("DELETE FROM sms_templates WHERE id=?", (int(template_id),))
        return True

    def cache_set(self, key, value, success=True):
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO sms_integration_cache(key,value_json,updated_at,success) VALUES(?,?,?,?)",
                (str(key), json.dumps(value, ensure_ascii=False), now, int(bool(success))),
            )

    def cache_get(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sms_integration_cache WHERE key=?", (str(key),)).fetchone()
        if not row: return None
        result = dict(row)
        try: result["value"] = json.loads(result.pop("value_json"))
        except (TypeError, ValueError): result["value"] = None
        return result

    def summary(self):
        today = datetime.now().date().isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS sent, SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS delivered, "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed FROM sms_messages WHERE created_at>=?",
                (today + "T00:00:00",),
            ).fetchone()
        return {"sent": int(row["sent"] or 0), "delivered": int(row["delivered"] or 0), "failed": int(row["failed"] or 0)}


class SmsService:
    def __init__(self, store, client, audit=None):
        self.store, self.client, self.audit = store, client, audit

    def _audit(self, message, action, actor):
        if self.audit is None: return
        try:
            self.audit.record(
                "sms", str(message["id"]), action,
                "SMS {}".format(message["client_message_id"][:12]),
                object_secondary=mask_phone(message["normalized_phone"]),
                metadata={"client_message_id": message["client_message_id"], "number": message.get("order_number") or message.get("repair_number") or ""},
                actor_id=str(actor["id"]), actor_name=actor["name"], actor_type="user",
                status=message["status"], source="SmsBliss",
            )
        except Exception:
            # SMS delivery must never depend on the auxiliary shared journal.
            LOGGER.exception("SMS audit write failed message_id=%s", message.get("id"))

    def send(self, payload, actor):
        message, created = self.store.create_once(payload, actor)
        if not created:
            return message, False
        message, claimed = self.store.claim_for_send(message["id"], actor)
        if not claimed:
            return message, False
        try:
            response = self.client.send(
                message["client_message_id"], message["normalized_phone"],
                message["message_text"], message.get("sender") or "",
                message.get("scheduled_at") or "",
            )
            rows = response.get("messages")
            if response.get("status") != "ok" or not isinstance(rows, list) or not rows:
                raise SmsBlissUnknownDelivery("SmsBliss acceptance is uncertain")
            result = rows[0] if isinstance(rows[0], dict) else {}
            provider_status = normalized_provider_status(result.get("status"))
            status = internal_status(provider_status)
            if status in {"accepted", "queued", "smsc_submit", "delivered"} and not result.get("smscId"):
                status = "unknown"
            description = "" if status != "failed" else status_label(status, provider_status)
            currency = "RUB" if result.get("msgCost") is not None else None
            message = self.store.update_status(
                message["id"], status, provider_status,
                smsc_id=result.get("smscId"), segments=result.get("smsCount"),
                cost=result.get("msgCost"), currency=currency,
                description=description, actor=actor,
            )
        except SmsBlissNotConfigured:
            message = self.store.update_status(message["id"], "failed", "not configured",
                                               description="SmsBliss не настроен", actor=actor)
        except SmsBlissSecurityError:
            message = self.store.update_status(message["id"], "failed", "insecure endpoint",
                                               description="Небезопасный адрес SmsBliss", actor=actor)
        except (SmsBlissUnknownDelivery, SmsBlissInvalidResponse, SmsBlissUnavailable):
            message = self.store.update_status(message["id"], "unknown", "unknown",
                                               description="Ответ SmsBliss не получен; повторная отправка заблокирована", actor=actor)
        self._audit(message, "created", actor)
        return message, True

    def sync_statuses(self, actor=None):
        actor = actor or {"id": "system", "name": "Система"}
        pending = self.store.pending(200)
        if not pending: return {"checked": 0, "updated": 0}
        response = self.client.statuses([
            {"smscId": row["smsc_id"], "clientId": row["client_message_id"]}
            for row in pending
        ])
        results = response.get("messages") if isinstance(response, dict) else None
        if not isinstance(results, list): raise SmsBlissInvalidResponse("Missing status list")
        by_smsc = {str(row["smsc_id"]): row for row in pending if row.get("smsc_id")}
        by_client = {str(row["client_message_id"]): row for row in pending}
        updated = 0
        for result in results:
            if not isinstance(result, dict): continue
            message = by_smsc.get(str(result.get("smscId") or "")) or by_client.get(str(result.get("clientId") or ""))
            if not message: continue
            provider_status = normalized_provider_status(result.get("status"))
            status = internal_status(provider_status)
            changed = self.store.update_status(
                message["id"], status, provider_status,
                description="" if status != "failed" else status_label(status, provider_status),
                actor=actor,
            )
            updated += int(bool(changed and changed["status"] != message["status"]))
        return {"checked": len(pending), "updated": updated}


def new_client_message_id():
    return uuid.uuid4().hex
