"""Conservative, side-effect-free matching of warehouse rows to Bitrix cards."""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher


AUTOMATIC_STATUSES = {"exact", "high_confidence"}
COMMON_NAMES = {
    "black", "blue", "bronze", "eclipse", "gold", "green", "moon",
    "red", "silver", "white",
}
DISPLAY_MARKERS = {
    "display", "sample", "витрина", "витринный", "образец",
}
SET_MARKERS = {"gift set", "set", "kit", "комплект", "набор"}
ARTICLE_NOTE_MARKERS = {
    "витрин", "замен", "комплект", "образец", "уцен", "брак", "ремонт",
}


def text(value):
    return str(value or "").strip()


def normalize_text(value):
    value = unicodedata.normalize("NFKC", text(value)).casefold().replace("ё", "е")
    value = value.replace("&", " ").replace("/", " ")
    value = re.sub(r"[‐‑‒–—―−]+", "-", value)
    value = re.sub(r"[^\w-]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"[-_]+", " ", value)
    return " ".join(value.split())


def canonical_name(name, brand):
    name_tokens = normalize_text(name).split()
    brand_tokens = normalize_text(brand).split()
    if brand_tokens and name_tokens[:len(brand_tokens)] == brand_tokens:
        name_tokens = name_tokens[len(brand_tokens):]
    return " ".join(name_tokens)


def reliable_article(value):
    raw = text(value)
    normalized = normalize_text(raw)
    if not raw:
        return False
    if any(marker in normalized for marker in ARTICLE_NOTE_MARKERS):
        return False
    if len(raw) > 64 or re.search(r"\s", raw):
        return False
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", raw):
        return False
    return bool(re.search(r"[A-Za-z]", raw) and re.search(r"\d", raw))


def article_quality(value):
    if not text(value):
        return "empty"
    return "code_like" if reliable_article(value) else "needs_review"


def model_tokens(value):
    candidates = re.findall(
        r"(?iu)(?<!\w)(?=[a-zа-я0-9._/-]{3,}(?!\w))"
        r"(?=[a-zа-я0-9._/-]*[a-zа-я])(?=[a-zа-я0-9._/-]*\d)"
        r"[a-zа-я0-9._/-]+",
        text(value),
    )
    return {
        re.sub(r"[^a-zа-я0-9]", "", item.casefold().replace("ё", "е"))
        for item in candidates
        if re.sub(r"[^a-zа-я0-9]", "", item.casefold().replace("ё", "е"))
    }


def variant_markers(value):
    normalized = normalize_text(value)
    tokens = set(normalized.split())
    found = {marker for marker in DISPLAY_MARKERS if marker in tokens}
    found.update(
        marker for marker in SET_MARKERS
        if (" " in marker and marker in normalized) or marker in tokens
    )
    return found


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def batch_id_for(file_sha256_value):
    digest = text(file_sha256_value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("file SHA-256 must contain 64 hexadecimal characters")
    return "bitrix-excel-{}".format(digest[:20])


def ensure_batch_is_new(batch_id, applied_batch_ids):
    if batch_id in set(applied_batch_ids or []):
        raise ValueError("import batch has already been applied: {}".format(batch_id))
    return True


def _candidate_view(product, score=0, evidence=""):
    return {
        "product_id": product.get("id"),
        "bitrix_product_id": text(product.get("external_product_id")),
        "bitrix_xml_id": text(product.get("external_xml_id")),
        "name": text(product.get("name")),
        "brand": text(product.get("brand")),
        "score": round(float(score), 4),
        "evidence": evidence,
    }


class ProductReconciler:
    """Build a deterministic preview without calling external services or writing data."""

    def __init__(self, products):
        self.products = [dict(product) for product in products]
        self.by_bitrix_id = defaultdict(list)
        self.by_xml_id = defaultdict(list)
        self.by_article = defaultdict(list)
        self.by_brand_name = defaultdict(list)
        self.by_brand_canonical = defaultdict(list)
        self.by_name = defaultdict(list)
        self.by_brand = defaultdict(list)
        for product in self.products:
            brand = normalize_text(product.get("brand"))
            name = normalize_text(product.get("name"))
            canonical = canonical_name(product.get("name"), product.get("brand"))
            if text(product.get("external_product_id")):
                self.by_bitrix_id[text(product.get("external_product_id"))].append(product)
            if text(product.get("external_xml_id")):
                self.by_xml_id[normalize_text(product.get("external_xml_id"))].append(product)
            if reliable_article(product.get("article")):
                self.by_article[normalize_text(product.get("article"))].append(product)
            if brand and name:
                self.by_brand_name[(brand, name)].append(product)
            if brand and canonical:
                self.by_brand_canonical[(brand, canonical)].append(product)
            if name:
                self.by_name[name].append(product)
            if brand:
                self.by_brand[brand].append(product)

    def reconcile(self, rows):
        normalized_rows = [self._prepare_row(row) for row in rows]
        duplicate_keys = self._duplicate_keys(normalized_rows)
        results = []
        for row in normalized_rows:
            result = self._match(row)
            duplicate_key = self._duplicate_key(row)
            if duplicate_key and duplicate_keys[duplicate_key] > 1:
                result["match_status"] = "duplicate_excel"
                result["match_method"] = "duplicate_excel_key"
                result["confidence"] = 0
                result["reason"] = (
                    "В Excel несколько строк с одинаковым безопасным ключом; "
                    "суммирование не подтверждено."
                )
            results.append(result)
        self._downgrade_product_conflicts(results)
        return results

    @staticmethod
    def _prepare_row(row):
        prepared = dict(row)
        prepared["excel_row"] = int(row.get("excel_row") or 0)
        prepared["excel_name"] = text(row.get("excel_name"))
        prepared["excel_brand"] = text(row.get("excel_brand"))
        prepared["excel_article"] = text(row.get("excel_article"))
        prepared["cell"] = text(row.get("cell"))
        prepared["category"] = text(row.get("category"))
        prepared["bitrix_id"] = text(row.get("bitrix_id"))
        prepared["xml_id"] = text(row.get("xml_id"))
        prepared["article_quality"] = article_quality(prepared["excel_article"])
        try:
            prepared["stock"] = float(row.get("stock") or 0)
            prepared["stock_valid"] = (
                math.isfinite(prepared["stock"]) and prepared["stock"] >= 0
            )
        except (TypeError, ValueError):
            prepared["stock"] = row.get("stock")
            prepared["stock_valid"] = False
        return prepared

    def _duplicate_key(self, row):
        if reliable_article(row.get("excel_article")):
            return ("article", normalize_text(row.get("excel_article")))
        brand = normalize_text(row.get("excel_brand"))
        name = canonical_name(row.get("excel_name"), row.get("excel_brand"))
        return ("brand_name", brand, name) if brand and name else None

    def _duplicate_keys(self, rows):
        return Counter(filter(None, (self._duplicate_key(row) for row in rows)))

    def _match(self, row):
        base = self._result_base(row)
        if not row["excel_name"] or not row["excel_brand"] or not row["stock_valid"]:
            base.update({
                "match_status": "invalid", "match_method": "validation",
                "confidence": 0,
                "reason": "Не заполнено название/бренд или остаток некорректен.",
            })
            return base

        reliable_keys = (
            ("bitrix_id", row.get("bitrix_id"), self.by_bitrix_id, 1.0),
            ("xml_id", normalize_text(row.get("xml_id")), self.by_xml_id, 1.0),
            (
                "article", normalize_text(row.get("excel_article")),
                self.by_article, 0.99,
            ),
        )
        for method, value, index, confidence in reliable_keys:
            if not value or (method == "article" and not reliable_article(row["excel_article"])):
                continue
            candidates = index.get(value, [])
            if candidates:
                return self._resolve_exact(row, candidates, method, confidence)

        brand = normalize_text(row["excel_brand"])
        normalized_name = normalize_text(row["excel_name"])
        candidates = self.by_brand_name.get((brand, normalized_name), [])
        if candidates:
            return self._resolve_exact(row, candidates, "brand_normalized_name", 0.98)

        canonical = canonical_name(row["excel_name"], row["excel_brand"])
        candidates = self.by_brand_canonical.get((brand, canonical), [])
        if candidates:
            return self._resolve_exact(
                row, candidates, "brand_prefix_normalized", 0.95,
                high_confidence=True,
            )

        alternatives = self._manual_candidates(row)
        if alternatives:
            best = alternatives[0]
            self._attach_candidate(base, best)
            base.update({
                "match_status": "ambiguous", "match_method": "manual_candidates",
                "confidence": min(float(best["score"]), 0.89),
                "alternatives": alternatives,
                "reason": "Есть только кандидаты по слабым признакам; требуется ручная проверка.",
            })
        else:
            base.update({
                "match_status": "not_found", "match_method": "none",
                "confidence": 0,
                "reason": "Безопасный кандидат в каталоге Bitrix не найден.",
            })
        return base

    def _resolve_exact(self, row, candidates, method, confidence, high_confidence=False):
        base = self._result_base(row)
        candidate_views = [_candidate_view(product, confidence, method) for product in candidates]
        if len(candidates) != 1:
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0, "alternatives": candidate_views,
                "reason": "Надёжный ключ соответствует нескольким карточкам Bitrix.",
            })
            return base
        product = candidates[0]
        self._attach_candidate(base, _candidate_view(product, confidence, method))
        base["alternatives"] = candidate_views
        if (
            normalize_text(row["excel_brand"]) != normalize_text(product.get("brand"))
        ):
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Бренд Excel отличается от бренда карточки Bitrix.",
            })
            return base
        if canonical_name(row["excel_name"], row["excel_brand"]) in COMMON_NAMES:
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Название слишком общее для автоматического объединения.",
            })
            return base
        if variant_markers(row["excel_name"]) != variant_markers(product.get("name")):
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Признаки образца/комплекта различаются; нужна ручная проверка.",
            })
            return base
        base.update({
            "match_status": "high_confidence" if high_confidence else "exact",
            "match_method": method,
            "confidence": confidence,
            "reason": (
                "Уникальное название внутри того же бренда после удаления ведущего бренда."
                if high_confidence else
                "Уникальное точное совпадение внутри того же бренда."
            ),
        })
        return base

    def _manual_candidates(self, row):
        brand = normalize_text(row["excel_brand"])
        name = canonical_name(row["excel_name"], row["excel_brand"])
        name_tokens = set(name.split())
        excel_models = model_tokens(row["excel_name"]) | model_tokens(row["excel_article"])
        scored = []
        for product in self.by_brand.get(brand, []):
            product_name = canonical_name(product.get("name"), product.get("brand"))
            product_tokens = set(product_name.split())
            union = name_tokens | product_tokens
            jaccard = len(name_tokens & product_tokens) / float(len(union) or 1)
            sequence = SequenceMatcher(None, name, product_name).ratio()
            product_models = model_tokens(product.get("name")) | model_tokens(product.get("article"))
            model_overlap = bool(excel_models & product_models)
            score = max(jaccard, sequence * 0.92, 0.86 if model_overlap else 0)
            if score >= 0.67:
                evidence = "same_brand_model" if model_overlap else "same_brand_similar_name"
                scored.append(_candidate_view(product, score, evidence))
        exact_other_brand = self.by_name.get(normalize_text(row["excel_name"]), [])
        for product in exact_other_brand:
            if normalize_text(product.get("brand")) != brand:
                scored.append(_candidate_view(product, 0.89, "exact_name_brand_mismatch"))
        deduplicated = {}
        for candidate in scored:
            candidate_id = candidate["product_id"]
            if candidate_id not in deduplicated or candidate["score"] > deduplicated[candidate_id]["score"]:
                deduplicated[candidate_id] = candidate
        return sorted(
            deduplicated.values(),
            key=lambda item: (-item["score"], normalize_text(item["name"]), str(item["product_id"])),
        )[:5]

    @staticmethod
    def _result_base(row):
        return {
            "excel_row": row["excel_row"],
            "excel_name": row["excel_name"],
            "excel_brand": row["excel_brand"],
            "excel_article": row["excel_article"],
            "article_quality": row["article_quality"],
            "stock": row["stock"],
            "stock_valid": row["stock_valid"],
            "cell": row["cell"],
            "category": row["category"],
            "product_id": None,
            "bitrix_product_id": "",
            "bitrix_xml_id": "",
            "bitrix_name": "",
            "bitrix_brand": "",
            "match_status": "not_found",
            "match_method": "none",
            "confidence": 0,
            "alternatives": [],
            "reason": "",
        }

    @staticmethod
    def _attach_candidate(result, candidate):
        result.update({
            "product_id": candidate.get("product_id"),
            "bitrix_product_id": candidate.get("bitrix_product_id") or "",
            "bitrix_xml_id": candidate.get("bitrix_xml_id") or "",
            "bitrix_name": candidate.get("name") or "",
            "bitrix_brand": candidate.get("brand") or "",
        })

    @staticmethod
    def _downgrade_product_conflicts(results):
        by_product = defaultdict(list)
        for result in results:
            if (
                result["match_status"] in AUTOMATIC_STATUSES | {"duplicate_excel"}
                and result.get("product_id") is not None
            ):
                by_product[result["product_id"]].append(result)
        for rows in by_product.values():
            if len(rows) < 2:
                continue
            for result in rows:
                result.update({
                    "match_status": "duplicate_excel",
                    "match_method": "multiple_excel_rows_one_product",
                    "confidence": 0,
                    "reason": (
                        "Одна карточка Bitrix сопоставлена нескольким строкам Excel; "
                        "автоматическое суммирование запрещено."
                    ),
                })


def summarize_reconciliation(results, products, file_metrics=None):
    statuses = Counter(result["match_status"] for result in results)
    unknown_brands = Counter()
    known_brands = {normalize_text(product.get("brand")) for product in products if product.get("brand")}
    for result in results:
        if normalize_text(result["excel_brand"]) not in known_brands:
            unknown_brands[result["excel_brand"]] += 1
    potential_product_rows = Counter(
        result["product_id"] for result in results
        if result.get("product_id") is not None
    )
    automatic_product_rows = Counter(
        result["product_id"] for result in results
        if result["match_status"] in AUTOMATIC_STATUSES
        and result.get("product_id") is not None
    )
    duplicate_group_keys = set()
    for result in results:
        if result["match_status"] != "duplicate_excel":
            continue
        if result["match_method"] == "multiple_excel_rows_one_product":
            duplicate_group_keys.add(("product", result.get("product_id")))
        elif reliable_article(result.get("excel_article")):
            duplicate_group_keys.add(("article", normalize_text(result["excel_article"])))
        else:
            duplicate_group_keys.add((
                "brand_name",
                normalize_text(result.get("excel_brand")),
                canonical_name(result.get("excel_name"), result.get("excel_brand")),
            ))
    positive = [
        result for result in results
        if result.get("stock_valid") and float(result.get("stock") or 0) > 0
    ]
    zero = [
        result for result in results
        if result.get("stock_valid") and float(result.get("stock") or 0) == 0
    ]
    ready_rows = [
        result for result in results
        if result["match_status"] not in {"duplicate_excel", "invalid"}
    ]
    automatic_positive = [
        result for result in results
        if result["match_status"] in AUTOMATIC_STATUSES
        and result.get("stock_valid")
        and float(result.get("stock") or 0) > 0
    ]
    products_by_id = {product.get("id"): product for product in products}
    enriched_products = [
        products_by_id.get(result.get("product_id"), {})
        for result in results if result["match_status"] in AUTOMATIC_STATUSES
    ]
    photo_cards = sum(
        bool(product.get("thumbnail_url") or product.get("primary_image_url"))
        for product in enriched_products
    )
    price_cards = sum(
        product.get("price_amount") not in (None, "") for product in enriched_products
    )
    category_cards = sum(bool(text(product.get("category"))) for product in enriched_products)
    description_cards = sum(
        bool(text(product.get("preview_text")) or text(product.get("detail_text")))
        for product in enriched_products
    )
    property_cards = sum(
        int(product.get("property_count") or 0) > 0 for product in enriched_products
    )
    summary = {
        "rows_total": len(results),
        "valid_rows": len(results) - statuses["invalid"],
        "exact": statuses["exact"],
        "high_confidence": statuses["high_confidence"],
        "ambiguous": statuses["ambiguous"],
        "not_found": statuses["not_found"],
        "invalid": statuses["invalid"],
        "duplicate_excel": statuses["duplicate_excel"],
        "duplicate_excel_groups": len(duplicate_group_keys),
        "automatic_total": statuses["exact"] + statuses["high_confidence"],
        "automatic_catalog_cards": len(automatic_product_rows),
        "excel_cards_planned": len(results),
        "excel_cards_ready_before_duplicate_resolution": len(ready_rows),
        "excel_cards_after_duplicate_resolution": len(results) - statuses["invalid"],
        "ready_stock_total": sum(
            float(result.get("stock") or 0)
            for result in ready_rows if result.get("stock_valid")
        ),
        "bitrix_enriched_cards": statuses["exact"] + statuses["high_confidence"],
        "bitrix_unlinked_cards": statuses["ambiguous"] + statuses["not_found"],
        "photo_cards": photo_cards,
        "cards_without_photo": len(results) - photo_cards,
        "price_cards": price_cards,
        "bitrix_category_cards": category_cards,
        "description_cards": description_cards,
        "property_cards": property_cards,
        "ambiguous_without_automatic_link": statuses["ambiguous"],
        "not_found_without_link": statuses["not_found"],
        "duplicates_blocking": statuses["duplicate_excel"],
        "batch_blocked": bool(statuses["duplicate_excel"] or statuses["invalid"]),
        "empty_names": sum(not text(result.get("excel_name")) for result in results),
        "empty_brands": sum(not text(result.get("excel_brand")) for result in results),
        "empty_categories": sum(not text(result.get("category")) for result in results),
        "empty_cells": sum(not text(result.get("cell")) for result in results),
        "invalid_stocks": sum(not result.get("stock_valid") for result in results),
        "filled_articles": sum(bool(text(result.get("excel_article"))) for result in results),
        "code_like_articles": sum(result.get("article_quality") == "code_like" for result in results),
        "articles_needing_review": sum(result.get("article_quality") == "needs_review" for result in results),
        "positive_stock_rows": len(positive),
        "zero_stock_rows": len(zero),
        "stock_total": sum(float(result.get("stock") or 0) for result in results if result.get("stock_valid")),
        "stock_exact": sum(float(result.get("stock") or 0) for result in results if result["match_status"] == "exact"),
        "stock_high_confidence": sum(float(result.get("stock") or 0) for result in results if result["match_status"] == "high_confidence"),
        "stock_disputed": sum(float(result.get("stock") or 0) for result in results if result["match_status"] not in AUTOMATIC_STATUSES and result.get("stock_valid")),
        "automatic_positive_rows": len(automatic_positive),
        "automatic_stock_total": sum(float(result.get("stock") or 0) for result in automatic_positive),
        "internal_batches_if_unblocked": 1 if results else 0,
        "planned_stock_operation_rows": len(positive),
        "stock_operation_rows_blocked_now": (
            0 if statuses["duplicate_excel"] or statuses["invalid"] else len(positive)
        ),
        "receipt_documents_if_applied": 0,
        "stock_operation_rows_if_applied": (
            0 if statuses["duplicate_excel"] or statuses["invalid"] else len(positive)
        ),
        "cards_with_multiple_excel_rows": sum(
            1 for count in potential_product_rows.values() if count > 1
        ),
        "automatic_cards_with_multiple_excel_rows": sum(
            1 for count in automatic_product_rows.values() if count > 1
        ),
        "rows_with_multiple_candidates": sum(
            len(result.get("alternatives") or []) > 1 for result in results
        ),
        "unknown_brand_rows": sum(unknown_brands.values()),
        "unknown_brands": [
            {"brand": brand, "rows": count}
            for brand, count in sorted(unknown_brands.items(), key=lambda item: (-item[1], normalize_text(item[0])))
        ],
        "writes_performed": 0,
        "bitrix_writes": 0,
        "moysklad_writes": 0,
        "production_changes": 0,
    }
    if file_metrics:
        summary.update(file_metrics)
    return summary


def alternatives_json(result):
    return json.dumps(result.get("alternatives") or [], ensure_ascii=False, sort_keys=True)
