"""Canonical ERP order-comment history with safe Bitrix field synchronization."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.clients.bitrix_order_comments import BitrixOrderCommentError


MAX_COMMENT_LENGTH = 2000
BITRIX_FIELD_REFERENCE = "COMMENTS"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def text_hash(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def normalize_text(text):
    value = str(text or "").strip()
    if not value:
        raise ValueError("Введите комментарий")
    if len(value) > MAX_COMMENT_LENGTH:
        raise ValueError(
            "Комментарий не должен превышать {} символов".format(
                MAX_COMMENT_LENGTH
            )
        )
    return value


class OrderCommentsService:
    def __init__(self, database, client_factory=None, logger=None):
        self.database = database
        self.client_factory = client_factory
        self.logger = logger or logging.getLogger(__name__)

    def _initialize(self):
        self.database.initialize()

    def list(self, order_id):
        self._initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, order_id, text, author_name, author_user_id, "
                "created_at, COALESCE(updated_at, created_at) AS updated_at, "
                "external_system, external_id, external_updated_at, sync_status, "
                "sync_hash, source, sync_attempts, last_sync_error "
                "FROM erp_order_comments WHERE order_id = ? "
                "ORDER BY created_at DESC, id DESC",
                (str(order_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, order_id, comment_id, connection=None):
        own_connection = connection is None
        connection = connection or self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM erp_order_comments WHERE order_id = ? AND id = ?",
                (str(order_id), int(comment_id)),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            if own_connection:
                connection.close()

    def create(self, order_id, text, author_name, author_user_id="",
               external_order_id=None):
        text = normalize_text(text)
        author_name = str(author_name or "").strip() or "Сотрудник ERP"
        author_user_id = str(author_user_id or "").strip() or None
        order_id = str(order_id)
        now = utc_now()
        created_at = now.isoformat()
        duplicate_after = (now - timedelta(seconds=30)).isoformat()
        sync_status = "pending" if external_order_id else "not_applicable"
        self._initialize()
        with self.database.transaction() as connection:
            duplicate = connection.execute(
                "SELECT id FROM erp_order_comments WHERE order_id = ? "
                "AND text = ? AND author_name = ? "
                "AND COALESCE(author_user_id, '') = COALESCE(?, '') "
                "AND source = 'erp' AND created_at >= ? "
                "ORDER BY id DESC LIMIT 1",
                (order_id, text, author_name, author_user_id, duplicate_after),
            ).fetchone()
            if duplicate is not None:
                return self.get(order_id, duplicate["id"], connection)
            cursor = connection.execute(
                "INSERT INTO erp_order_comments "
                "(order_id, text, author_name, author_user_id, created_at, "
                "updated_at, sync_status, source, next_retry_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'erp', ?)",
                (
                    order_id, text, author_name, author_user_id, created_at,
                    created_at, sync_status, created_at if external_order_id else None,
                ),
            )
            comment_id = cursor.lastrowid
            if external_order_id:
                connection.execute(
                    "INSERT INTO erp_order_comment_sync_state "
                    "(order_id, external_order_id, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET external_order_id=excluded.external_order_id, "
                    "updated_at=excluded.updated_at",
                    (order_id, str(external_order_id), created_at),
                )
            return self.get(order_id, comment_id, connection)

    def edit(self, order_id, comment_id, text, actor_user_id, actor_is_admin=False):
        text = normalize_text(text)
        actor_user_id = str(actor_user_id or "").strip()
        self._initialize()
        now = utc_now().isoformat()
        with self.database.transaction() as connection:
            comment = self.get(order_id, comment_id, connection)
            if comment is None:
                raise LookupError("Комментарий не найден")
            if comment.get("source") != "erp":
                raise PermissionError("Импортированный комментарий нельзя редактировать")
            owner = str(comment.get("author_user_id") or "").strip()
            if not actor_is_admin and (not actor_user_id or actor_user_id != owner):
                raise PermissionError("Недостаточно прав для редактирования")
            if text == comment["text"]:
                return comment
            state = connection.execute(
                "SELECT external_order_id, last_outbound_comment_id "
                "FROM erp_order_comment_sync_state WHERE order_id = ?",
                (str(order_id),),
            ).fetchone()
            can_sync = bool(
                state is not None
                and state["last_outbound_comment_id"] == int(comment_id)
            )
            connection.execute(
                "UPDATE erp_order_comments SET text = ?, updated_at = ?, "
                "sync_status = ?, next_retry_at = ?, last_sync_error = NULL "
                "WHERE order_id = ? AND id = ?",
                (
                    text, now, "pending" if can_sync else "not_applicable",
                    now if can_sync else None, str(order_id), int(comment_id),
                ),
            )
            return self.get(order_id, comment_id, connection)

    def _state(self, order_id, connection):
        return connection.execute(
            "SELECT * FROM erp_order_comment_sync_state WHERE order_id = ?",
            (str(order_id),),
        ).fetchone()

    def _store_external_snapshot(self, order_id, external_order_id, snapshot,
                                 connection, conflict=False):
        text = str((snapshot or {}).get("text") or "").strip()
        digest = str((snapshot or {}).get("hash") or text_hash(text))
        updated_at = (snapshot or {}).get("updated_at")
        now = utc_now().isoformat()
        known = connection.execute(
            "SELECT id FROM erp_order_comments WHERE order_id = ? AND sync_hash = ? "
            "ORDER BY id DESC LIMIT 1",
            (str(order_id), digest),
        ).fetchone()
        if text and known is None:
            external_id = "order:{}:{}:{}".format(
                external_order_id, BITRIX_FIELD_REFERENCE, digest
            )
            connection.execute(
                "INSERT OR IGNORE INTO erp_order_comments "
                "(order_id, text, author_name, created_at, updated_at, "
                "external_system, external_id, external_updated_at, sync_status, "
                "sync_hash, source) VALUES (?, ?, 'Bitrix', ?, ?, 'bitrix', ?, ?, "
                "'conflict' , ?, ?)",
                (
                    str(order_id), text, now, now,
                    external_id, updated_at, digest,
                    "bitrix_conflict" if conflict else "bitrix_legacy",
                ),
            )
            if not conflict:
                connection.execute(
                    "UPDATE erp_order_comments SET sync_status = 'synced' "
                    "WHERE external_system = 'bitrix' AND external_id = ?",
                    (external_id,),
                )
        connection.execute(
            "INSERT INTO erp_order_comment_sync_state "
            "(order_id, external_order_id, last_external_hash, "
            "last_external_updated_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET "
            "external_order_id=excluded.external_order_id, "
            "last_external_hash=excluded.last_external_hash, "
            "last_external_updated_at=excluded.last_external_updated_at, "
            "updated_at=excluded.updated_at",
            (str(order_id), str(external_order_id), digest, updated_at, now),
        )
        return bool(text and known is None), None

    def pull(self, order_id, external_order_id):
        client = self.client_factory()
        snapshot = client.get(external_order_id)
        self._initialize()
        with self.database.transaction() as connection:
            imported, _ = self._store_external_snapshot(
                order_id, external_order_id, snapshot, connection
            )
        self.logger.info(
            "Order comment sync direction=bitrix_to_erp order_id=%s "
            "bitrix_order_id=%s external_ref=%s success=1 imported=%s",
            order_id, external_order_id, BITRIX_FIELD_REFERENCE, int(imported),
        )
        return {"imported": imported, "snapshot": snapshot}

    def push(self, comment_id):
        self._initialize()
        with self.database.connect() as connection:
            comment = connection.execute(
                "SELECT * FROM erp_order_comments WHERE id = ?",
                (int(comment_id),),
            ).fetchone()
            if comment is None:
                raise LookupError("Комментарий не найден")
            state = self._state(comment["order_id"], connection)
        if state is None:
            return {"status": "not_applicable"}

        client = self.client_factory()
        if state["last_external_hash"] is None:
            self.pull(comment["order_id"], state["external_order_id"])
            with self.database.connect() as connection:
                state = self._state(comment["order_id"], connection)
        try:
            snapshot = client.update(
                state["external_order_id"], comment["text"],
                state["last_external_hash"],
            )
        except BitrixOrderCommentError as error:
            if error.code == "conflict" and isinstance(error.current, dict):
                with self.database.transaction() as connection:
                    self._store_external_snapshot(
                        comment["order_id"], state["external_order_id"],
                        error.current, connection, conflict=True,
                    )
                    connection.execute(
                        "UPDATE erp_order_comments SET sync_status='conflict', "
                        "last_sync_error='conflict', next_retry_at=NULL "
                        "WHERE id = ?",
                        (int(comment_id),),
                    )
                self.logger.warning(
                    "Order comment sync direction=erp_to_bitrix order_id=%s "
                    "bitrix_order_id=%s comment_id=%s external_ref=%s conflict=1",
                    comment["order_id"], state["external_order_id"], comment_id,
                    BITRIX_FIELD_REFERENCE,
                )
                return {"status": "conflict"}
            self._mark_error(comment, error)
            raise

        digest = str(snapshot.get("hash") or text_hash(comment["text"]))
        now = utc_now().isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_order_comments SET external_system='bitrix', "
                "external_id=NULL, external_updated_at=?, sync_status='synced', "
                "sync_hash=?, sync_attempts=0, next_retry_at=NULL, "
                "last_sync_error=NULL WHERE id = ?",
                (
                    snapshot.get("updated_at"), digest, int(comment_id),
                ),
            )
            connection.execute(
                "UPDATE erp_order_comment_sync_state SET last_external_hash=?, "
                "last_external_updated_at=?, last_outbound_comment_id=?, updated_at=? "
                "WHERE order_id=?",
                (
                    digest, snapshot.get("updated_at"), int(comment_id), now,
                    comment["order_id"],
                ),
            )
        self.logger.info(
            "Order comment sync direction=erp_to_bitrix order_id=%s "
            "bitrix_order_id=%s comment_id=%s external_ref=%s success=1",
            comment["order_id"], state["external_order_id"], comment_id,
            BITRIX_FIELD_REFERENCE,
        )
        return {"status": "synced", "snapshot": snapshot}

    def _mark_error(self, comment, error):
        attempts = int(comment["sync_attempts"] or 0) + 1
        next_retry = (
            utc_now() + timedelta(minutes=min(2 ** min(attempts, 6), 60))
        ).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_order_comments SET sync_status='error', "
                "sync_attempts=?, next_retry_at=?, last_sync_error=? WHERE id=?",
                (attempts, next_retry, str(getattr(error, "code", "error")), comment["id"]),
            )
        self.logger.warning(
            "Order comment sync direction=erp_to_bitrix order_id=%s "
            "comment_id=%s success=0 retry=1 error=%s",
            comment["order_id"], comment["id"], type(error).__name__,
        )

    def retry_pending(self, limit=10):
        self._initialize()
        now = utc_now().isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM erp_order_comments WHERE source='erp' "
                "AND sync_status IN ('pending','error') "
                "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
                "ORDER BY id LIMIT ?",
                (now, max(1, min(int(limit), 50))),
            ).fetchall()
        result = {"attempted": 0, "synced": 0, "errors": 0, "conflicts": 0}
        for row in rows:
            result["attempted"] += 1
            try:
                outcome = self.push(row["id"])
                key = "conflicts" if outcome["status"] == "conflict" else "synced"
                result[key] += 1
            except (BitrixOrderCommentError, LookupError):
                result["errors"] += 1
        return result
