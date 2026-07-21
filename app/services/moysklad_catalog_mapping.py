from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.clients.bitrix_catalog import normalize_key


XML_ATTRIBUTE_NAMES = {"xml_id", "xml id", "xmlid", "xml-id"}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_moysklad_products(client, limit=1000, max_pages=100):
    products = []
    offset = 0
    for _page in range(max_pages):
        response = client.get(
            "/entity/product",
            params={"limit": limit, "offset": offset, "expand": "attributes"},
        ) or {}
        if not isinstance(response, dict):
            raise ValueError("МойСклад вернул неверный ответ каталога")
        rows = response.get("rows") or []
        if not isinstance(rows, list):
            raise ValueError("МойСклад вернул неверный список товаров")
        products.extend(row for row in rows if isinstance(row, dict))
        meta = response.get("meta") or {}
        total = meta.get("size")
        if len(rows) < limit or (isinstance(total, int) and len(products) >= total):
            return products
        offset += len(rows)
    raise ValueError("Каталог МойСклад не загружен полностью: превышен предел страниц")


def load_product_attribute_definitions(client):
    response = client.get(
        "/entity/product/metadata/attributes", params={"limit": 1000, "offset": 0}
    )
    if isinstance(response, dict):
        rows = response.get("rows") or []
    elif isinstance(response, list):
        rows = response
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _attribute_identity(attribute):
    meta = attribute.get("meta") or {}
    href = str(meta.get("href") or "")
    return str(attribute.get("id") or href.rsplit("/", 1)[-1] or "")


def xml_attribute_ids(definitions):
    result = set()
    for attribute in definitions:
        name = normalize_key(attribute.get("name")).replace("-", "_")
        if name in XML_ATTRIBUTE_NAMES:
            identity = _attribute_identity(attribute)
            if identity:
                result.add(identity)
    return result


def attribute_value(product, attribute_ids):
    for attribute in product.get("attributes") or []:
        if _attribute_identity(attribute) in attribute_ids:
            value = attribute.get("value")
            if value not in (None, ""):
                return str(value).strip()
    return ""


def candidate_view(product, attribute_ids=None):
    attribute_ids = attribute_ids or set()
    return {
        "id": str(product.get("id") or ""),
        "name": str(product.get("name") or "").strip(),
        "external_code": str(product.get("externalCode") or "").strip(),
        "code": str(product.get("code") or "").strip(),
        "article": str(product.get("article") or "").strip(),
        "xml_id": attribute_value(product, attribute_ids),
        "archived": bool(product.get("archived")),
    }


def _unique_index(rows, getter):
    index = defaultdict(list)
    for row in rows:
        value = normalize_key(getter(row))
        if value:
            index[value].append(row)
    return index


class MoySkladCatalogMatcher:
    def __init__(self, database=None, moysklad_products=None, attribute_definitions=None):
        self.database = database or CatalogDatabase()
        discovered_attributes = list(attribute_definitions or [])
        for product in moysklad_products or []:
            discovered_attributes.extend(
                attribute for attribute in product.get("attributes") or []
                if isinstance(attribute, dict) and attribute.get("name")
            )
        self.attribute_ids = xml_attribute_ids(discovered_attributes)
        self.moysklad_products = [
            candidate_view(product, self.attribute_ids)
            for product in moysklad_products or []
        ]
        self.by_id = {row["id"]: row for row in self.moysklad_products if row["id"]}
        self.indexes = {
            "xml_attribute": _unique_index(self.moysklad_products, lambda row: row["xml_id"]),
            "external_code": _unique_index(self.moysklad_products, lambda row: row["external_code"]),
            "article": _unique_index(self.moysklad_products, lambda row: row["article"]),
            "name": _unique_index(self.moysklad_products, lambda row: row["name"]),
        }

    def preview(self, status="all", product_id=None, page=1, per_page=50):
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100))
        with self.database.connect() as connection:
            all_products = [dict(row) for row in connection.execute(
                "SELECT p.*, m.moysklad_product_id, m.confirmed, m.match_method AS saved_match_method "
                "FROM catalog_products p LEFT JOIN catalog_moysklad_mappings m ON m.product_id = p.id "
                "ORDER BY p.name, p.id"
            ).fetchall()]
        source_counts = {
            "xml": Counter(normalize_key(row.get("external_xml_id")) for row in all_products if normalize_key(row.get("external_xml_id"))),
            "article": Counter(normalize_key(row.get("article")) for row in all_products if normalize_key(row.get("article"))),
            "name": Counter(normalize_key(row.get("name")) for row in all_products if normalize_key(row.get("name"))),
        }
        products = (
            [row for row in all_products if row["id"] == int(product_id)]
            if product_id else all_products
        )
        items = [self._match(row, source_counts) for row in products]
        summary = Counter(item["status"] for item in items)
        if status != "all":
            items = [item for item in items if item["status"] == status]
        total = len(items)
        start = (page - 1) * per_page
        return {
            "items": items[start:start + per_page],
            "summary": dict(summary),
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
            "moysklad_products": len(self.moysklad_products),
            "xml_attribute_present": bool(self.attribute_ids),
        }

    def _match(self, product, source_counts):
        confirmed_id = str(product.get("moysklad_product_id") or "")
        if product.get("confirmed") and confirmed_id:
            candidate = self.by_id.get(confirmed_id) or {
                "id": confirmed_id, "name": "Подтверждённый товар недоступен",
                "external_code": "", "code": "", "article": "", "xml_id": "",
                "archived": False,
            }
            return self._result(product, "confirmed", "manual", [candidate])

        xml_id = normalize_key(product.get("external_xml_id"))
        if self.attribute_ids and xml_id and source_counts["xml"][xml_id] == 1:
            candidates = self.indexes["xml_attribute"].get(xml_id, [])
            if candidates:
                return self._candidate_result(product, "xml_id", candidates, probable=False)

        if xml_id and source_counts["xml"][xml_id] == 1:
            candidates = self.indexes["external_code"].get(xml_id, [])
            if candidates:
                return self._candidate_result(product, "external_code", candidates, probable=False)

        article = normalize_key(product.get("article"))
        if article and source_counts["article"][article] == 1:
            candidates = self.indexes["article"].get(article, [])
            if candidates:
                return self._candidate_result(product, "article", candidates, probable=False)

        name = normalize_key(product.get("name"))
        if name and source_counts["name"][name] == 1:
            candidates = self.indexes["name"].get(name, [])
            if candidates:
                return self._candidate_result(product, "unique_name", candidates, probable=True)
        return self._result(product, "not_found", "none", [])

    def _candidate_result(self, product, method, candidates, probable):
        if len(candidates) > 1:
            status = "multiple_candidates"
        else:
            status = "probable" if probable else "matched"
        return self._result(product, status, method, candidates)

    @staticmethod
    def _result(product, status, method, candidates):
        return {
            "product": {
                "id": product["id"], "name": product["name"],
                "external_product_id": product["external_product_id"],
                "external_xml_id": product.get("external_xml_id") or "",
                "article": product.get("article") or "", "brand": product.get("brand") or "",
                "active": bool(product.get("active")),
            },
            "status": status,
            "method": method,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }

    def confirm(self, product_id, moysklad_product_id):
        candidate_id = str(moysklad_product_id or "").strip()
        if candidate_id not in self.by_id:
            raise ValueError("MoySklad candidate is not present in the read-only snapshot")
        now = utc_now()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT id FROM catalog_products WHERE id = ?", (int(product_id),)
            ).fetchone()
            if not product:
                raise ValueError("Catalog product does not exist")
            occupied = connection.execute(
                "SELECT product_id FROM catalog_moysklad_mappings "
                "WHERE moysklad_product_id = ? AND product_id <> ?",
                (candidate_id, int(product_id)),
            ).fetchone()
            if occupied:
                raise ValueError("MoySklad product is already confirmed for another card")
            existing = connection.execute(
                "SELECT id FROM catalog_moysklad_mappings WHERE product_id = ?",
                (int(product_id),),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE catalog_moysklad_mappings SET moysklad_product_id=?, "
                    "match_status=?, match_method=?, candidate_count=1, confirmed=1, "
                    "confirmed_at=?, updated_at=? WHERE product_id=?",
                    (candidate_id, "confirmed", "manual", now, now, int(product_id)),
                )
            else:
                connection.execute(
                    "INSERT INTO catalog_moysklad_mappings "
                    "(product_id,moysklad_product_id,match_status,match_method,candidate_count,"
                    "confirmed,confirmed_at,created_at,updated_at) VALUES (?,?,?,?,1,1,?,?,?)",
                    (int(product_id), candidate_id, "confirmed", "manual", now, now, now),
                )
        return self.by_id[candidate_id]
