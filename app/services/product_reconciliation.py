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
MODEL_PROPERTY_CODES = {
    "ARTICLE", "EXTERNAL_CODE", "MANUFACTURER_CODE", "MODEL", "MODEL_CODE",
    "SKU", "VENDOR_CODE", "XML_ID",
}
MODEL_PROPERTY_NAMES = {
    "артикул", "внешний код", "код модели", "код производителя", "модель",
    "manufacturer code", "model", "model code", "sku", "vendor code",
}
NON_MODEL_PROPERTY_CODES = {"BRAND_MODEL", "RETARGETING"}
DISPLAY_MARKERS = {
    "display", "sample", "витрина", "витринный", "образец",
}
SET_MARKERS = {"gift set", "set", "kit", "комплект", "набор"}
ARTICLE_NOTE_MARKERS = {
    "брак", "витрин", "замен", "комментар", "образец", "переучет",
    "провер", "ремонт", "служеб", "уцен",
}
COLOR_EQUIVALENTS = {
    "rose gold": "rosegold",
    "rose-gold": "rosegold",
    "gun metal": "gunmetal",
    "gun-metal": "gunmetal",
    "белая": "белый", "белые": "белый",
    "бирюзовая": "бирюзовый", "бирюзовые": "бирюзовый",
    "бронзовая": "бронзовый", "бронзовые": "бронзовый",
    "голубая": "голубой", "голубые": "голубой",
    "желтая": "желтый", "желтые": "желтый",
    "зеленая": "зеленый", "зеленые": "зеленый",
    "золотая": "золотой", "золотые": "золотой",
    "коричневая": "коричневый", "коричневые": "коричневый",
    "красная": "красный", "красные": "красный",
    "оранжевая": "оранжевый", "оранжевые": "оранжевый",
    "розовая": "розовый", "розовые": "розовый",
    "серебряная": "серебряный", "серебряные": "серебряный",
    "серая": "серый", "серые": "серый",
    "синяя": "синий", "синие": "синий",
    "фиолетовая": "фиолетовый", "фиолетовые": "фиолетовый",
    "черная": "черный", "черные": "черный",
}
COLOR_MARKERS = {
    "beige", "black", "blue", "bronze", "brown", "copper", "cream",
    "chrome", "gold", "gray", "green", "grey", "gunmetal", "navy", "orange",
    "pink", "purple", "red", "rosegold", "silver", "steel", "tan",
    "turquoise", "white", "yellow",
    "бежевый", "белый", "бирюзовый", "бордовый", "бронзовый", "голубой",
    "желтый", "зеленый", "золотой", "коричневый", "красный", "оранжевый",
    "розовый", "серебряный", "серый", "синий", "фиолетовый", "черный",
}
GENERIC_MODEL_LIKE_TOKENS = {"2d", "3d", "24h"}


def text(value):
    return str(value or "").strip()


def normalize_text(value):
    value = unicodedata.normalize("NFKC", text(value)).casefold().replace("ё", "е")
    value = value.replace("&", " ").replace("/", " ")
    value = re.sub(r"[‐‑‒–—―−]+", "-", value)
    value = re.sub(r"[^\w-]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"[-_]+", " ", value)
    return " ".join(value.split())


def normalize_safe_equivalents(value):
    normalized = normalize_text(value)
    for source, replacement in COLOR_EQUIVALENTS.items():
        normalized = normalized.replace(source, replacement)
    return " ".join(normalized.split())


def canonical_name(name, brand):
    name_tokens = normalize_text(name).split()
    brand_tokens = normalize_text(brand).split()
    if brand_tokens and name_tokens[:len(brand_tokens)] == brand_tokens:
        name_tokens = name_tokens[len(brand_tokens):]
    return " ".join(name_tokens)


def normalize_model_code(value, allow_structured=False):
    raw = unicodedata.normalize("NFKC", text(value))
    raw = re.sub(r"[‐‑‒–—―−]+", "-", raw)
    normalized = re.sub(r"[^A-Za-z0-9]", "", raw).casefold()
    if not (3 <= len(normalized) <= 40):
        return ""
    has_alpha_and_digit = bool(
        re.search(r"[a-z]", normalized) and re.search(r"\d", normalized)
    )
    structured_alpha = bool(
        allow_structured
        and re.fullmatch(r"[A-Z]{2,}(?:[./-][A-Z]{2,})+", raw)
    )
    structured_numeric = bool(re.fullmatch(r"\d{4,}(?:[./-]\d{4,})+", raw))
    long_article_dimension_shape = bool(
        allow_structured and re.fullmatch(r"\d{5,}MM", raw.upper())
    )
    if not (
        has_alpha_and_digit or structured_alpha or structured_numeric
        or long_article_dimension_shape
    ):
        return ""
    if normalized in GENERIC_MODEL_LIKE_TOKENS:
        return ""
    if (
        re.fullmatch(r"\d+(?:mm|cm|atm|m)", normalized)
        and not long_article_dimension_shape
    ):
        return ""
    simple_number = re.fullmatch(r"(pg)0+([1-9]\d*)", normalized)
    if simple_number:
        normalized = "{}{}".format(simple_number.group(1), simple_number.group(2))
    return normalized


def extract_model_codes(value, source="text"):
    """Return conservative model-code candidates with original and canonical forms."""
    raw = unicodedata.normalize("NFKC", text(value))
    if not raw or re.search(r"(?i)https?://|www\.", raw):
        return []
    raw = re.sub(r"[‐‑‒–—―−]+", "-", raw)
    allow_structured = "article" in source or source.startswith("property:")
    found = []

    def add(original, normalized=None):
        normalized = normalized or normalize_model_code(
            original, allow_structured=allow_structured,
        )
        if not normalized:
            return
        item = {"original": text(original), "normalized": normalized, "source": source}
        if not any(existing["normalized"] == normalized for existing in found):
            found.append(item)

    token_pattern = re.compile(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*)(?![A-Za-z0-9])"
    )
    for match in token_pattern.finditer(raw):
        token = match.group(1)
        normalized = normalize_model_code(token, allow_structured=allow_structured)
        if not normalized:
            continue
        parts = re.split(r"[._/-]+", token)
        if len(parts) > 1 and parts[0].isalpha() and len(parts[0]) > 4:
            component_codes = [
                part for part in parts[1:]
                if normalize_model_code(part, allow_structured=allow_structured)
            ]
            if component_codes:
                for component in component_codes:
                    add(component)
                continue
        add(token, normalized)

    spaced_pattern = re.compile(
        r"(?<![A-Za-z0-9])([A-Z]{1,4})\s+"
        r"([A-Z0-9]+(?:[._/-][A-Z0-9]+)*)(?![A-Za-z0-9])"
    )
    for match in spaced_pattern.finditer(raw):
        suffix = normalize_model_code(match.group(2))
        if suffix:
            add(match.group(0), normalize_model_code(match.group(1) + match.group(2)))
    return found


def model_tokens(value):
    return {item["normalized"] for item in extract_model_codes(value)}


def classify_article(value):
    raw = text(value)
    if not raw:
        return "empty"
    normalized = normalize_text(raw)
    if any(marker in normalized for marker in ARTICLE_NOTE_MARKERS):
        return "comment"
    codes = extract_model_codes(raw, "article")
    if codes and len(raw) <= 64 and len(raw.split()) <= 8:
        return "model_code"
    if re.search(r"\d", raw):
        return "ambiguous"
    return "text"


def reliable_article(value):
    return classify_article(value) == "model_code"


def article_quality(value):
    return classify_article(value)


def property_is_model_identifier(prop):
    code = text(prop.get("code")).upper()
    name = normalize_text(prop.get("name"))
    if code in NON_MODEL_PROPERTY_CODES:
        return False
    return code in MODEL_PROPERTY_CODES or name in MODEL_PROPERTY_NAMES


def _flatten_property_scalars(value):
    if value in (None, "", False):
        return []
    if isinstance(value, (str, int, float)):
        return [text(value)]
    if isinstance(value, list):
        return [scalar for item in value for scalar in _flatten_property_scalars(item)]
    return []


def extract_property_model_codes(properties):
    found = []
    for prop in properties or []:
        if not property_is_model_identifier(prop):
            continue
        value = prop.get("display_value")
        if value in (None, "", [], False):
            value = prop.get("value")
        for scalar in _flatten_property_scalars(value):
            if classify_article(scalar) == "comment":
                continue
            for item in extract_model_codes(
                scalar, "property:{}".format(text(prop.get("code") or prop.get("id")))
            ):
                if not any(existing["normalized"] == item["normalized"] for existing in found):
                    found.append(item)
    return found


def variant_markers(value):
    normalized = normalize_safe_equivalents(value)
    tokens = set(normalized.split())
    found = {marker for marker in DISPLAY_MARKERS if marker in tokens}
    found.update(
        marker for marker in SET_MARKERS
        if (" " in marker and marker in normalized) or marker in tokens
    )
    return found


def color_markers(value):
    tokens = set(normalize_safe_equivalents(value).split())
    return tokens & COLOR_MARKERS


def size_markers(value):
    normalized = normalize_safe_equivalents(value)
    return {
        "{}mm".format(match.replace(",", "."))
        for match in re.findall(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:mm|мм)\b", normalized)
    }


def significant_name_tokens(name, brand):
    canonical = normalize_safe_equivalents(canonical_name(name, brand))
    return tuple(sorted(canonical.split()))


def variant_conflicts(excel_name, bitrix_name):
    conflicts = []
    excel_context = variant_markers(excel_name)
    bitrix_context = variant_markers(bitrix_name)
    if excel_context != bitrix_context:
        conflicts.append("display_or_set")
    excel_colors = color_markers(excel_name)
    bitrix_colors = color_markers(bitrix_name)
    if excel_colors and bitrix_colors and excel_colors.isdisjoint(bitrix_colors):
        conflicts.append("color")
    excel_sizes = size_markers(excel_name)
    bitrix_sizes = size_markers(bitrix_name)
    if excel_sizes and bitrix_sizes and excel_sizes.isdisjoint(bitrix_sizes):
        conflicts.append("size")
    return conflicts


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


def _candidate_view(product, score=0, evidence="", matched_model=None):
    product_model_codes = product.get("_model_codes") or []
    matched_model = matched_model or {}
    return {
        "product_id": product.get("id"),
        "bitrix_product_id": text(product.get("external_product_id")),
        "bitrix_xml_id": text(product.get("external_xml_id")),
        "name": text(product.get("name")),
        "brand": text(product.get("brand")),
        "score": round(float(score), 4),
        "evidence": evidence,
        "model_code": matched_model.get("normalized") or "",
        "model_source": matched_model.get("source") or "",
        "model_original": matched_model.get("original") or "",
        "product_model_codes": [item["normalized"] for item in product_model_codes],
    }


class ProductReconciler:
    """Build a deterministic preview without calling external services or writing data."""

    def __init__(self, products):
        self.products = [dict(product) for product in products]
        self.by_bitrix_id = defaultdict(list)
        self.by_xml_id = defaultdict(list)
        self.by_brand_name = defaultdict(list)
        self.by_brand_canonical = defaultdict(list)
        self.by_brand_tokens = defaultdict(list)
        self.by_name = defaultdict(list)
        self.by_brand = defaultdict(list)
        self.by_property_model = defaultdict(list)
        self.by_name_model = defaultdict(list)
        for product in self.products:
            brand = normalize_text(product.get("brand"))
            name = normalize_text(product.get("name"))
            canonical = canonical_name(product.get("name"), product.get("brand"))
            property_codes = extract_property_model_codes(product.get("properties"))
            name_codes = extract_model_codes(product.get("name"), "bitrix_name")
            article_codes = (
                extract_model_codes(product.get("article"), "bitrix_article")
                if reliable_article(product.get("article")) else []
            )
            product["_property_model_codes"] = property_codes
            product["_name_model_codes"] = name_codes
            model_codes = []
            for item in property_codes + name_codes + article_codes:
                if item["normalized"] not in {
                    existing["normalized"] for existing in model_codes
                }:
                    model_codes.append(item)
            product["_model_codes"] = model_codes
            if text(product.get("external_product_id")):
                self.by_bitrix_id[text(product.get("external_product_id"))].append(product)
            if text(product.get("external_xml_id")):
                self.by_xml_id[normalize_text(product.get("external_xml_id"))].append(product)
            if brand and name:
                self.by_brand_name[(brand, name)].append(product)
            if brand and canonical:
                self.by_brand_canonical[(brand, canonical)].append(product)
            tokens = significant_name_tokens(product.get("name"), product.get("brand"))
            if brand and len(tokens) >= 2:
                self.by_brand_tokens[(brand, tokens)].append(product)
            if name:
                self.by_name[name].append(product)
            if brand:
                self.by_brand[brand].append(product)
            for item in property_codes:
                self.by_property_model[item["normalized"]].append(product)
            for item in name_codes + article_codes:
                self.by_name_model[item["normalized"]].append(product)

    def reconcile(self, rows):
        normalized_rows = [self._prepare_row(row) for row in rows]
        results = [self._match(row) for row in normalized_rows]
        self._annotate_bitrix_cardinality(results)
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
        prepared["article_model_codes"] = (
            extract_model_codes(prepared["excel_article"], "excel_article")
            if prepared["article_quality"] == "model_code" else []
        )
        prepared["name_model_codes"] = extract_model_codes(
            prepared["excel_name"], "excel_name"
        )
        prepared["preferred_model_codes"] = (
            prepared["article_model_codes"] or prepared["name_model_codes"]
        )
        try:
            prepared["stock"] = float(row.get("stock") or 0)
            prepared["stock_valid"] = (
                math.isfinite(prepared["stock"]) and prepared["stock"] >= 0
            )
        except (TypeError, ValueError):
            prepared["stock"] = row.get("stock")
            prepared["stock_valid"] = False
        return prepared

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
            ("xml_id", normalize_text(row.get("xml_id")), self.by_xml_id, 1.0),
            ("bitrix_id", row.get("bitrix_id"), self.by_bitrix_id, 1.0),
        )
        for method, value, index, confidence in reliable_keys:
            if not value:
                continue
            candidates = index.get(value, [])
            if candidates:
                return self._resolve_exact(row, candidates, method, confidence)

        for method, index, confidence in (
            ("model_property", self.by_property_model, 0.995),
            ("model_name", self.by_name_model, 0.99),
        ):
            model_match = self._resolve_model_index(row, index, method, confidence)
            if model_match is not None:
                return model_match

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

        tokens = significant_name_tokens(row["excel_name"], row["excel_brand"])
        candidates = self.by_brand_tokens.get((brand, tokens), [])
        if candidates:
            return self._resolve_exact(
                row, candidates, "brand_significant_tokens", 0.93,
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

    def _resolve_model_index(self, row, index, method, confidence):
        codes = row.get("preferred_model_codes") or []
        if not codes:
            return None
        candidates_by_id = {}
        matched_codes = defaultdict(list)
        for item in codes:
            for product in index.get(item["normalized"], []):
                candidates_by_id[product.get("id")] = product
                matched_codes[product.get("id")].append(item)
        if not candidates_by_id:
            return None

        excel_brand = normalize_text(row.get("excel_brand"))
        compatible = {
            product_id: product for product_id, product in candidates_by_id.items()
            if not normalize_text(product.get("brand"))
            or normalize_text(product.get("brand")) == excel_brand
        }
        if not compatible:
            return None
        narrowed_by_brand = len(compatible) == 1 and len(candidates_by_id) > 1
        if len(compatible) != 1:
            return None

        product_id, product = next(iter(compatible.items()))
        matched = sorted(
            matched_codes[product_id],
            key=lambda item: (-len(item["normalized"]), item["normalized"]),
        )[0]
        method_name = "brand_{}".format(method) if narrowed_by_brand else method
        return self._resolve_model(
            row, product, method_name, confidence, matched,
        )

    def _resolve_model(self, row, product, method, confidence, matched_model):
        base = self._result_base(row)
        product_model = next(
            (
                item for item in product.get("_model_codes") or []
                if item["normalized"] == matched_model["normalized"]
            ),
            {"normalized": matched_model["normalized"], "source": method, "original": ""},
        )
        candidate = _candidate_view(
            product, confidence, method, matched_model=product_model,
        )
        self._attach_candidate(base, candidate)
        base["alternatives"] = [candidate]
        if (
            normalize_text(row["excel_brand"])
            and normalize_text(product.get("brand"))
            and normalize_text(row["excel_brand"]) != normalize_text(product.get("brand"))
        ):
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Код модели совпал, но бренды Excel и Bitrix различаются.",
            })
            return base
        conflicts = variant_conflicts(row["excel_name"], product.get("name"))
        if conflicts:
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Код модели совпал, но найден конфликт варианта: {}.".format(
                    ", ".join(conflicts)
                ),
            })
            return base
        base.update({
            "match_status": "high_confidence",
            "match_method": method,
            "confidence": confidence,
            "reason": (
                "Уникальный точный код модели {} совпал; бренд и признаки варианта "
                "не противоречат."
            ).format(matched_model["normalized"]),
            "excel_model_code": matched_model["normalized"],
            "excel_model_source": matched_model["source"],
            "bitrix_model_code": matched_model["normalized"],
            "bitrix_model_source": product_model.get("source") or method,
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
        excel_codes = {
            item["normalized"] for item in row.get("article_model_codes") or []
        }
        product_codes = {
            item["normalized"]
            for item in product.get("_property_model_codes") or []
        }
        if excel_codes and product_codes and excel_codes.isdisjoint(product_codes):
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Точное название найдено, но безопасные коды модели конфликтуют.",
            })
            return base
        conflicts = variant_conflicts(row["excel_name"], product.get("name"))
        if conflicts:
            base.update({
                "match_status": "ambiguous", "match_method": method,
                "confidence": 0,
                "reason": "Признаки варианта различаются ({}); нужна ручная проверка.".format(
                    ", ".join(conflicts)
                ),
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
        excel_models = {
            item["normalized"]
            for item in (
                (row.get("name_model_codes") or [])
                + (row.get("article_model_codes") or [])
            )
        }
        scored = []
        for product in self.by_brand.get(brand, []):
            product_name = canonical_name(product.get("name"), product.get("brand"))
            product_tokens = set(product_name.split())
            union = name_tokens | product_tokens
            jaccard = len(name_tokens & product_tokens) / float(len(union) or 1)
            sequence = SequenceMatcher(None, name, product_name).ratio()
            product_models = {
                item["normalized"] for item in product.get("_model_codes") or []
            }
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
            "excel_model_codes": [
                dict(item) for item in row.get("preferred_model_codes") or []
            ],
            "excel_model_code": "",
            "excel_model_source": "",
            "stock": row["stock"],
            "stock_valid": row["stock_valid"],
            "cell": row["cell"],
            "category": row["category"],
            "product_id": None,
            "bitrix_product_id": "",
            "bitrix_xml_id": "",
            "bitrix_name": "",
            "bitrix_brand": "",
            "bitrix_model_codes": [],
            "bitrix_model_code": "",
            "bitrix_model_source": "",
            "match_status": "not_found",
            "match_method": "none",
            "confidence": 0,
            "bitrix_link_cardinality": "unlinked",
            "shared_bitrix_row_count": 0,
            "alternatives": [],
            "reason": "",
            "previous_match_status": "",
            "previous_match_method": "",
            "previous_product_id": None,
            "candidate_changed": False,
            "comparison_status": "not_compared",
        }

    @staticmethod
    def _attach_candidate(result, candidate):
        result.update({
            "product_id": candidate.get("product_id"),
            "bitrix_product_id": candidate.get("bitrix_product_id") or "",
            "bitrix_xml_id": candidate.get("bitrix_xml_id") or "",
            "bitrix_name": candidate.get("name") or "",
            "bitrix_brand": candidate.get("brand") or "",
            "bitrix_model_codes": candidate.get("product_model_codes") or [],
            "bitrix_model_code": candidate.get("model_code") or "",
            "bitrix_model_source": candidate.get("model_source") or "",
        })

    @staticmethod
    def _annotate_bitrix_cardinality(results):
        linked_by_product = defaultdict(list)
        claims_by_product = defaultdict(list)
        for result in results:
            product_id = result.get("product_id")
            if product_id is None:
                continue
            if result["match_status"] in AUTOMATIC_STATUSES:
                linked_by_product[product_id].append(result)
                claims_by_product[product_id].append(result)
            elif result["match_status"] == "ambiguous":
                claims_by_product[product_id].append(result)

        for rows in linked_by_product.values():
            explicit_colors = {
                tuple(sorted(color_markers(result.get("excel_name"))))
                for result in rows if color_markers(result.get("excel_name"))
            }
            if len(explicit_colors) > 1:
                for result in rows:
                    result.update({
                        "match_status": "ambiguous",
                        "match_method": "many_to_one_variant_conflict",
                        "confidence": 0,
                        "bitrix_link_cardinality": "many_to_one_candidate",
                        "shared_bitrix_row_count": len(rows),
                        "reason": (
                            "Несколько Excel-карточек разных цветов претендуют на одну "
                            "карточку Bitrix; автоматическая связь снята."
                        ),
                    })
                continue
            cardinality = "many_to_one" if len(rows) > 1 else "one_to_one"
            for result in rows:
                result["bitrix_link_cardinality"] = cardinality
                result["shared_bitrix_row_count"] = len(rows)
                if cardinality == "many_to_one":
                    result["reason"] += (
                        " Контент одной карточки Bitrix безопасно используется несколькими "
                        "отдельными Excel-карточками; их остатки не объединяются."
                    )

        for rows in claims_by_product.values():
            if len(rows) < 2:
                continue
            for result in rows:
                if result["match_status"] != "ambiguous":
                    continue
                result["bitrix_link_cardinality"] = "many_to_one_candidate"
                result["shared_bitrix_row_count"] = len(rows)


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
    repeated_excel_keys = Counter()
    excel_identity_keys = {}
    for result in results:
        article = result.get("excel_article")
        if reliable_article(article):
            identity_key = ("article", normalize_text(article))
            repeated_excel_keys[identity_key] += 1
            excel_identity_keys[result["excel_row"]] = identity_key
            continue
        brand = normalize_text(result.get("excel_brand"))
        name = canonical_name(result.get("excel_name"), result.get("excel_brand"))
        if brand and name:
            identity_key = ("brand_name", brand, name)
            repeated_excel_keys[identity_key] += 1
            excel_identity_keys[result["excel_row"]] = identity_key
    repeated_excel_groups = [count for count in repeated_excel_keys.values() if count > 1]
    unblocked_excel_rows = {
        result["excel_row"] for result in results
        if (
            repeated_excel_keys[excel_identity_keys.get(result["excel_row"])] > 1
            or result.get("bitrix_link_cardinality") == "many_to_one"
        )
    }
    many_to_one_links = Counter(
        result["product_id"] for result in results
        if result.get("bitrix_link_cardinality") == "many_to_one"
        and result.get("product_id") is not None
    )
    many_to_one_candidates = Counter(
        result["product_id"] for result in results
        if result.get("bitrix_link_cardinality") == "many_to_one_candidate"
        and result.get("product_id") is not None
    )
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
        if result["match_status"] != "invalid"
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
        "duplicate_excel": 0,
        "duplicate_excel_groups": 0,
        "excel_rows_unblocked_by_row_identity": len(unblocked_excel_rows),
        "repeated_excel_identity_groups": len(repeated_excel_groups),
        "many_to_one_link_groups": len(many_to_one_links),
        "many_to_one_link_rows": sum(many_to_one_links.values()),
        "many_to_one_candidate_groups": len(many_to_one_candidates),
        "many_to_one_candidate_rows": sum(many_to_one_candidates.values()),
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
        "duplicates_blocking": 0,
        "batch_blocked": bool(statuses["invalid"]),
        "empty_names": sum(not text(result.get("excel_name")) for result in results),
        "empty_brands": sum(not text(result.get("excel_brand")) for result in results),
        "empty_categories": sum(not text(result.get("category")) for result in results),
        "empty_cells": sum(not text(result.get("cell")) for result in results),
        "invalid_stocks": sum(not result.get("stock_valid") for result in results),
        "filled_articles": sum(bool(text(result.get("excel_article"))) for result in results),
        "safe_model_code_articles": sum(
            result.get("article_quality") == "model_code" for result in results
        ),
        "code_like_articles": sum(
            result.get("article_quality") == "model_code" for result in results
        ),
        "article_text_values": sum(
            result.get("article_quality") == "text" for result in results
        ),
        "article_comment_values": sum(
            result.get("article_quality") == "comment" for result in results
        ),
        "article_ambiguous_values": sum(
            result.get("article_quality") == "ambiguous" for result in results
        ),
        "articles_needing_review": sum(
            result.get("article_quality") in {"comment", "ambiguous"} for result in results
        ),
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
        "stock_operation_rows_blocked_now": 0,
        "receipt_documents_if_applied": 0,
        "stock_operation_rows_if_applied": (
            0 if statuses["invalid"] else len(positive)
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


def compare_with_baseline(results, baseline_rows):
    baseline_by_row = {
        int(item.get("excel_row") or 0): item for item in baseline_rows or []
    }
    metrics = Counter()
    for result in results:
        previous = baseline_by_row.get(result["excel_row"])
        if previous is None:
            result["comparison_status"] = "missing_baseline"
            metrics["missing_baseline"] += 1
            continue
        previous_status = text(previous.get("match_status"))
        previous_method = text(previous.get("match_method"))
        previous_product_id = previous.get("product_id")
        result.update({
            "previous_match_status": previous_status,
            "previous_match_method": previous_method,
            "previous_product_id": previous_product_id,
            "candidate_changed": previous_product_id != result.get("product_id"),
        })
        if result["candidate_changed"]:
            metrics["candidate_changed_total"] += 1
        previous_automatic = previous_status in AUTOMATIC_STATUSES
        current_automatic = result["match_status"] in AUTOMATIC_STATUSES
        if current_automatic and not previous_automatic:
            status = "new_automatic"
            metrics["new_automatic"] += 1
            if "model" in result.get("match_method", ""):
                metrics["new_automatic_by_model"] += 1
        elif previous_automatic and not current_automatic:
            status = "downgraded_conflict"
            metrics["downgraded_conflict"] += 1
        elif result["candidate_changed"]:
            status = "candidate_changed"
            metrics["candidate_changed_nonautomatic"] += 1
        elif previous_status != result["match_status"]:
            status = "status_changed"
            metrics["status_changed"] += 1
        else:
            status = "unchanged"
            metrics["unchanged"] += 1
        result["comparison_status"] = status
        if previous_automatic and (
            not current_automatic or previous_product_id != result.get("product_id")
        ):
            metrics["old_automatic_matches_changed"] += 1
    return {
        "baseline_rows_compared": len(results) - metrics["missing_baseline"],
        "new_automatic_matches": metrics["new_automatic"],
        "new_matches_by_model": metrics["new_automatic_by_model"],
        "candidate_changed_rows": metrics["candidate_changed_total"],
        "status_changed_rows": metrics["status_changed"],
        "old_automatic_matches_changed": metrics["old_automatic_matches_changed"],
        "matches_downgraded_for_conflict": metrics["downgraded_conflict"],
        "missing_baseline_rows": metrics["missing_baseline"],
    }


def alternatives_json(result):
    return json.dumps(result.get("alternatives") or [], ensure_ascii=False, sort_keys=True)
