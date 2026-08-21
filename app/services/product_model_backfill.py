"""Conservative, auditable model detection for ERP product cards."""

import hashlib
import json
import re
import sqlite3
import subprocess
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.inventory_lock import assert_no_active_inventory


EXPLICIT_MODEL_CODES = {
    "model", "model_name", "product_model", "watch_model", "модель",
    "модель_товара", "модель_часов",
}
TEXT_FAMILY_BRANDS = {
    "aark", "contempus", "divided by zero", "humism", "millner",
    "solar lab", "wewood", "ziiiro", "zinvo",
}
COLOR_WORDS = {
    "all", "beige", "black", "blue", "brown", "chrome", "colored",
    "copper", "crimson", "gold", "gray", "green", "grey", "gunmetal",
    "indigo", "magenta", "navy", "ocean", "orange", "pink", "red",
    "rose", "silver", "smoke", "steel", "tan", "transparent", "white",
    "yellow", "черный", "чёрный", "белый", "синий", "красный",
}
PROTECTED_TABLES = (
    "catalog_images", "catalog_prices", "catalog_stock_movements",
    "erp_brands", "erp_categories", "erp_inventory_sessions",
    "erp_inventory_items", "erp_sales", "erp_sale_items", "erp_receipts",
    "erp_receipt_items",
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_model(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", value).strip(" \t\r\n,;/")


def normalize_model_key(value):
    return re.sub(r"\s+", "", clean_model(value)).casefold()


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _property_model(row):
    for prop in _json_list(row.get("bitrix_properties_json")):
        if not isinstance(prop, dict):
            continue
        code = clean_model(prop.get("code")).casefold()
        name = clean_model(prop.get("name")).casefold().replace(" ", "_")
        # BRAND_MODEL / «Марка часов» is the brand property, not a model.
        if code == "brand_model" or "марка" in name or "brand" in code:
            continue
        if code not in EXPLICIT_MODEL_CODES and name not in EXPLICIT_MODEL_CODES:
            continue
        value = prop.get("value")
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else ""
        value = clean_model(value)
        if value:
            return value
    return ""


def _source_texts(row):
    url_path = urlsplit(str(row.get("bitrix_source_url") or "")).path
    slug = url_path.rstrip("/").rsplit("/", 1)[-1]
    return {
        "article_or_sku": clean_model(row.get("excel_article")),
        "name": clean_model(row.get("bitrix_name") or row.get("excel_name_raw")),
        "url_slug": clean_model(slug),
    }


def _match(pattern, *values):
    for value in values:
        result = re.search(pattern, value or "", re.IGNORECASE)
        if result:
            return clean_model(result.group(1)).upper()
    return ""


def _brand_candidate(row):
    brand = clean_model(row.get("excel_brand") or row.get("bitrix_brand"))
    brand_key = brand.casefold()
    sources = _source_texts(row)
    article = sources["article_or_sku"]
    name = sources["name"]
    slug = sources["url_slug"]
    category = clean_model(row.get("excel_category") or row.get("bitrix_category")).casefold()
    source_path = urlsplit(str(row.get("bitrix_source_url") or "")).path.casefold()
    if (
        "/straps/" in source_path
        or "/accessories/" in source_path
        or "ремеш" in category
        or "аксесс" in category
        or "ремеш" in name.casefold()
        or re.search(r"\bstrap\b", name, re.IGNORECASE)
    ):
        return "", "non_model_accessory_category", []

    candidate = ""
    rule = ""
    if brand_key == "braun":
        candidate = _match(r"\b((?:BNC\d{3}|BN\d{4}|BC\s*\d{2,3}))", article, name, slug)
        rule = "braun_reference_prefix"
    elif brand_key == "void":
        candidate = _match(r"\b((?:V\d{2}[A-Z]|PKG\d{2}))", article, name, slug)
        rule = "void_reference_family"
    elif brand_key == "klokers":
        candidate = _match(r"\b(KLOK[- ]?\d{2})\b", article, name, slug)
        rule = "klokers_klok_series"
    elif brand_key == "daniel wellington":
        candidate = _match(r"\b(\d{4}DW)\b", article, name, slug)
        rule = "daniel_wellington_reference"
    elif brand_key == "komono":
        candidate = _match(r"\b(KOM-[A-Z]\d{3,5})\b", article, name, slug)
        rule = "komono_reference"
    elif brand_key == "olivia burton":
        candidate = _match(r"\b(OB\d{2}[A-Z]+\d+)\b", article, name, slug)
        rule = "olivia_burton_reference"
    elif brand_key == "d1 milano":
        candidate = _match(r"\b([A-Z]{2,6}\d{2,4})\b", article, name, slug)
        rule = "d1_milano_reference"
    elif brand_key == "luch":
        candidate = _match(r"\b(\d{8})\b", article, name, slug)
        rule = "luch_reference"
    elif brand_key == "ciga design":
        candidate = _match(r"\b([A-Z]\d{3})[- ]", article, name, slug)
        rule = "ciga_series_prefix"
    elif brand_key == "benlydesign":
        candidate = _match(r"\b([A-Z]{1,3}\d{3,4})\b", article, name, slug)
        rule = "benlydesign_reference_family"
    elif brand_key == "tid":
        result = re.search(r"\bNO\.?\s*(\d+)\b", name, re.IGNORECASE)
        candidate = "No.{}".format(result.group(1)) if result else ""
        rule = "tid_numbered_family"
    elif brand_key == "eone":
        result = re.match(r"\s*(Bradley(?:\s+Compass)?)\b", name, re.IGNORECASE)
        candidate = clean_model(result.group(1)).title() if result else ""
        rule = "eone_named_family"
    elif brand_key == "zinvo":
        # Some Bitrix names repeat the brand before the actual family
        # ("ZINVO APEX GOLD").  The family is APEX, not ZINVO.
        result = re.match(
            r"\s*(?:ZINVO\s+)?([^\W\d_][\wÀ-žĜ-ž&'-]*)\b",
            name,
            re.IGNORECASE | re.UNICODE,
        )
        candidate = clean_model(result.group(1)).title() if result else ""
        if candidate.casefold() in {"zinvo"} | COLOR_WORDS:
            candidate = ""
        rule = "zinvo_named_family"
    elif brand_key in TEXT_FAMILY_BRANDS and "strap" not in category and "рем" not in category:
        first = re.match(r"\s*([^\W\d_][\wÀ-žĜ-ž&'-]*)", name, re.UNICODE)
        candidate = clean_model(first.group(1)).title() if first else ""
        if candidate.casefold() in COLOR_WORDS:
            candidate = ""
        rule = "{}_named_family".format(re.sub(r"\W+", "_", brand_key).strip("_"))

    if candidate:
        compact = re.sub(r"[^0-9a-zа-я]+", "", candidate.casefold())
        evidence = [key for key, value in sources.items()
                    if compact and compact in re.sub(r"[^0-9a-zа-я]+", "", value.casefold())]
        return candidate, rule, evidence

    # Conservative fallback: an exact reference containing both letters and
    # digits is accepted only when independently present in name or URL.
    if (article and len(article) <= 32 and re.search(r"[A-Za-z]", article)
            and re.search(r"\d", article) and " " not in article):
        article_key = re.sub(r"[^0-9a-z]+", "", article.casefold())
        corroborated = [key for key in ("name", "url_slug") if article_key and article_key in re.sub(
            r"[^0-9a-z]+", "", sources[key].casefold())]
        if corroborated:
            return article.upper(), "corroborated_exact_reference", ["article_or_sku"] + corroborated
    return "", "no_safe_brand_rule", []


class ProductModelBackfill:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def _rows(self, connection):
        return [dict(row) for row in connection.execute(
            "SELECT p.*, b.name AS canonical_brand_name, c.name AS canonical_category_name "
            "FROM catalog_excel_products p "
            "LEFT JOIN erp_brands b ON b.id = p.brand_id "
            "LEFT JOIN erp_categories c ON c.id = p.category_id "
            "WHERE p.active = 1 ORDER BY p.excel_brand COLLATE NOCASE, p.id"
        ).fetchall()]

    def _protected_digest(self, connection):
        columns = [row["name"] for row in connection.execute(
            "PRAGMA table_info(catalog_excel_products)"
        ).fetchall() if row["name"] not in {"model", "model_id", "updated_at"}]
        snapshots = {
            "catalog_excel_products": [list(row) for row in connection.execute(
                "SELECT {} FROM catalog_excel_products ORDER BY id".format(
                    ", ".join(columns)
                )
            ).fetchall()]
        }
        for table in PROTECTED_TABLES:
            snapshots[table] = [list(row) for row in connection.execute(
                "SELECT * FROM {} ORDER BY rowid".format(table)
            ).fetchall()]
        payload = json.dumps(snapshots, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build(self, connection):
        rows = self._rows(connection)
        existing_models = {(int(row["brand_id"]), row["normalized_name"]): dict(row)
                           for row in connection.execute("SELECT * FROM erp_models")}
        preliminary = []
        family_counts = Counter()
        for row in rows:
            explicit = _property_model(row)
            candidate, rule, evidence = _brand_candidate(row)
            if explicit:
                candidate, rule, evidence = explicit, "bitrix_explicit_model_property", ["bitrix_property"]
            candidate = clean_model(candidate)
            if candidate and rule.endswith("named_family"):
                family_counts[(clean_model(row.get("excel_brand")).casefold(), normalize_model_key(candidate))] += 1
            preliminary.append((row, candidate, rule, evidence))

        items = []
        conflicts = []
        for row, candidate, rule, evidence in preliminary:
            existing = clean_model(row.get("model"))
            brand = clean_model(row.get("canonical_brand_name") or row.get("excel_brand") or row.get("bitrix_brand"))
            confidence = "low"
            reason = "Недостаточно независимых данных для безопасного определения."
            source = "none"
            if candidate:
                source = "+".join(evidence) or "brand_rule"
                if rule == "bitrix_explicit_model_property":
                    confidence = "high"
                    reason = "Использовано явное свойство модели Bitrix."
                elif rule.endswith("named_family"):
                    repeated = family_counts[(brand.casefold(), normalize_model_key(candidate))]
                    confidence = (
                        "high"
                        if repeated >= 2 and "name" in evidence and len(evidence) >= 2
                        else "medium"
                    )
                    reason = ("Семейство повторяется у {} вариантов бренда и подтверждено названием/URL."
                              .format(repeated))
                elif rule == "corroborated_exact_reference":
                    confidence = "medium"
                    reason = "Код подтверждён, но для бренда нет отдельного правила группировки вариантов."
                elif "name" in evidence and len(set(evidence)) >= 2:
                    confidence = "high"
                    reason = "Brand-specific правило подтверждено минимум двумя источниками."
                else:
                    confidence = "medium"
                    reason = "Кандидат найден только в одном источнике."

            conflict = bool(existing and candidate and normalize_model_key(existing) != normalize_model_key(candidate))
            if conflict:
                conflicts.append({"product_id": row["id"], "existing_model": existing,
                                  "proposed_model": candidate, "rule": rule})
            brand_id = row.get("brand_id")
            target_model = existing or candidate
            model_row = existing_models.get((int(brand_id), normalize_model_key(target_model))) if brand_id and target_model else None
            action = "requires_review"
            if existing:
                if not brand_id:
                    action = "preserve_existing"
                elif row.get("model_id"):
                    action = "conflict" if conflict else "preserve_existing"
                else:
                    action = "link_existing" if model_row else "create_and_link_existing"
            elif confidence == "high" and brand_id:
                action = "assign_existing" if model_row else "create_and_assign"
            item = {
                "product_id": row["id"], "brand": brand,
                "category": clean_model(row.get("canonical_category_name") or row.get("excel_category") or row.get("bitrix_category")),
                "product": clean_model(row.get("excel_name_raw")),
                "source_name": clean_model(row.get("bitrix_name") or row.get("excel_name_raw")),
                "sku_or_article": clean_model(row.get("excel_article")),
                "existing_model": existing, "proposed_model": candidate,
                "source": source, "confidence": confidence, "reason": reason,
                "rule": rule, "action": action, "conflict": conflict,
                "new_model": bool(action in {"create_and_assign", "create_and_link_existing"}),
                "target_model": target_model,
            }
            items.append(item)

        summary = {
            "total_products": len(items),
            "already_have_model": sum(bool(item["existing_model"]) for item in items),
            "high_confidence": sum(item["confidence"] == "high" and not item["existing_model"] for item in items),
            "ambiguous": sum(item["confidence"] in {"medium", "low"} and not item["existing_model"] for item in items),
            "conflicts": len(conflicts),
            "new_models": len({(item["brand"].casefold(), normalize_model_key(item["proposed_model"]))
                               for item in items if item["new_model"]}),
        }
        examples = []
        example_brands = set()
        for item in sorted(items, key=lambda value: (value["confidence"] != "high", value["brand"].casefold(), value["product_id"])):
            key = item["brand"].casefold()
            if not key or key in example_brands:
                continue
            examples.append(item)
            example_brands.add(key)
            if len(examples) == 10:
                break
        return {"mode": "dry_run", "writes_performed": 0, "summary": summary,
                "items": items, "manual_review": [item for item in items if item["action"] == "requires_review" or (item["confidence"] != "high" and not item["existing_model"])],
                "conflicts": conflicts, "examples": examples,
                "plan_digest": hashlib.sha256(json.dumps(
                    items, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")).hexdigest()}

    def dry_run(self):
        self.database.initialize()
        with self.database.connect() as connection:
            return self._build(connection)

    def backup(self, backup_dir=None):
        source = Path(self.database.path)
        target_dir = Path(backup_dir) if backup_dir else source.parent / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = target_dir / "{}-models-{}.bak".format(source.name, stamp)
        with sqlite3.connect(str(source)) as source_connection:
            if hasattr(source_connection, "backup"):
                with sqlite3.connect(str(target)) as target_connection:
                    source_connection.backup(target_connection)
            else:  # Python 3.6 compatibility on production.
                escaped_target = str(target).replace("'", "''")
                subprocess.run(
                    ["sqlite3", str(source)],
                    input=".timeout 10000\n.backup '{}'\n".format(escaped_target),
                    universal_newlines=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                )
        with sqlite3.connect(str(target)) as backup_connection:
            if backup_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
        return str(target)

    def apply(self, backup_dir=None):
        self.database.initialize()
        with self.database.connect() as connection:
            assert_no_active_inventory(connection)
            preview = self._build(connection)
        backup_path = self.backup(backup_dir)
        applied = []
        try:
            with self.database.transaction() as connection:
                assert_no_active_inventory(connection)
                plan = self._build(connection)
                if plan["plan_digest"] != preview["plan_digest"]:
                    raise RuntimeError(
                        "Catalog changed after backup; model backfill was rolled back"
                    )
                protected_before = self._protected_digest(connection)
                for item in plan["items"]:
                    if item["action"] not in {"assign_existing", "create_and_assign", "link_existing", "create_and_link_existing"}:
                        continue
                    row = connection.execute(
                        "SELECT id, brand_id, excel_name_raw, model FROM catalog_excel_products WHERE id = ? AND active = 1",
                        (item["product_id"],),
                    ).fetchone()
                    if row is None:
                        continue
                    now = utc_now()
                    target_model = clean_model(row["model"]) or item["proposed_model"]
                    if not target_model:
                        continue
                    key = normalize_model_key(target_model)
                    model = connection.execute(
                        "SELECT id, name FROM erp_models WHERE brand_id = ? AND normalized_name = ?",
                        (row["brand_id"], key),
                    ).fetchone()
                    if model is None:
                        model_id = connection.execute(
                            "INSERT INTO erp_models (brand_id, name, normalized_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                            (row["brand_id"], target_model, key, now, now),
                        ).lastrowid
                    else:
                        model_id = model["id"]
                    connection.execute(
                        "UPDATE catalog_excel_products SET model = CASE WHEN trim(COALESCE(model, '')) = '' THEN ? ELSE model END, model_id = ?, updated_at = ? WHERE id = ? AND model_id IS NULL",
                        (target_model, model_id, now, row["id"]),
                    )
                    AuditJournal(self.database).record(
                        "product", row["id"], "updated", row["excel_name_raw"],
                        before={"model": row["model"]}, after={"model": target_model},
                        metadata={"source": item["source"], "rule": item["rule"], "confidence": "high"},
                        actor_type="system", actor_name="Model backfill", occurred_at=now,
                        source="model_backfill", connection=connection,
                    )
                    applied.append({"product_id": row["id"], "old_model": row["model"],
                                    "new_model": target_model, "source": item["source"],
                                    "rule": item["rule"], "changed_at": now})
                if self._protected_digest(connection) != protected_before:
                    raise RuntimeError(
                        "Protected catalog, stock, sales, receipt or inventory data changed"
                    )
        except Exception:
            # The transaction has already rolled back. The backup remains for a
            # full-file restore and is intentionally never deleted here.
            raise
        result = dict(preview)
        result.update({"mode": "apply", "writes_performed": len(applied),
                       "backup_path": backup_path, "applied": applied})
        return result
