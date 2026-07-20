import html
import json
from html.parser import HTMLParser

from app.catalog_db import CatalogDatabase


ALLOWED_DESCRIPTION_TAGS = {
    "a", "b", "br", "em", "h2", "h3", "h4", "i", "li", "ol", "p",
    "strong", "ul",
}


class _DescriptionSanitizer(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_DESCRIPTION_TAGS:
            return
        rendered_attrs = []
        if tag == "a":
            attributes = dict(attrs)
            href = str(attributes.get("href") or "").strip()
            if href.startswith(("http://", "https://", "/")):
                rendered_attrs.append('href="{}"'.format(html.escape(href, quote=True)))
                rendered_attrs.append('rel="noopener noreferrer"')
        suffix = " " + " ".join(rendered_attrs) if rendered_attrs else ""
        self.parts.append("<{}{}>".format(tag, suffix))

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ALLOWED_DESCRIPTION_TAGS and tag != "br":
            self.parts.append("</{}>".format(tag))

    def handle_data(self, data):
        self.parts.append(html.escape(data))


def sanitize_catalog_html(value):
    sanitizer = _DescriptionSanitizer()
    try:
        sanitizer.feed(str(value or ""))
        sanitizer.close()
    except (TypeError, ValueError):
        return html.escape(str(value or ""))
    return "".join(sanitizer.parts)


def _json_value(value, fallback=None):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class CatalogReader:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def list_products(self, query="", activity="all", category_id="", page=1, per_page=50):
        if not self.database.exists():
            return {
                "items": [], "categories": [], "total": 0, "page": 1,
                "per_page": per_page, "pages": 0,
            }
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100))
        where = []
        parameters = []
        if query:
            pattern = "%{}%".format(query.strip())
            where.append("(p.name LIKE ? OR p.article LIKE ? OR p.brand LIKE ? OR p.external_product_id LIKE ?)")
            parameters.extend([pattern, pattern, pattern, pattern])
        if activity == "active":
            where.append("p.active = 1")
        elif activity == "inactive":
            where.append("p.active = 0")
        if category_id:
            where.append(
                "EXISTS (SELECT 1 FROM catalog_product_categories pc "
                "JOIN catalog_categories c ON c.id = pc.category_id "
                "WHERE pc.product_id = p.id AND c.external_category_id = ?)"
            )
            parameters.append(str(category_id))
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        select_sql = """
            SELECT p.*,
                (SELECT original_url FROM catalog_images i WHERE i.product_id = p.id
                 ORDER BY i.is_primary DESC, i.sort, i.id LIMIT 1) AS image_url,
                (SELECT c.name FROM catalog_categories c WHERE c.id = p.primary_category_id) AS category_name,
                (SELECT amount FROM catalog_prices pr WHERE pr.product_id = p.id
                 ORDER BY pr.is_base DESC, pr.id LIMIT 1) AS price_amount,
                (SELECT currency FROM catalog_prices pr WHERE pr.product_id = p.id
                 ORDER BY pr.is_base DESC, pr.id LIMIT 1) AS price_currency,
                (SELECT COUNT(*) FROM catalog_product_property_values pv WHERE pv.product_id = p.id) AS property_count,
                (SELECT COUNT(*) FROM catalog_images i WHERE i.product_id = p.id) AS image_count,
                (SELECT COUNT(*) FROM catalog_offers o WHERE o.product_id = p.id) AS offer_count,
                (SELECT match_status FROM catalog_moysklad_mappings m WHERE m.product_id = p.id) AS mapping_status
            FROM catalog_products p
        """
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM catalog_products p" + where_sql,
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                select_sql + where_sql + " ORDER BY p.name, p.id LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
            categories = connection.execute(
                "SELECT external_category_id, name FROM catalog_categories ORDER BY path_json, sort, name"
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "categories": [dict(row) for row in categories],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    def get_product(self, product_id):
        if not self.database.exists():
            return None
        with self.database.connect() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_products WHERE id = ?", (int(product_id),)
            ).fetchone()
            if product is None:
                return None
            result = dict(product)
            result["categories"] = [dict(row) for row in connection.execute(
                "SELECT c.*, pc.is_primary FROM catalog_categories c "
                "JOIN catalog_product_categories pc ON pc.category_id = c.id "
                "WHERE pc.product_id = ? ORDER BY pc.is_primary DESC, pc.sort",
                (product_id,),
            ).fetchall()]
            properties = []
            for row in connection.execute(
                "SELECT pr.*, pv.value_json, pv.display_value_json, pv.enum_id_json "
                "FROM catalog_properties pr JOIN catalog_product_property_values pv "
                "ON pv.property_id = pr.id WHERE pv.product_id = ? ORDER BY pr.sort, pr.name",
                (product_id,),
            ).fetchall():
                item = dict(row)
                item["value"] = _json_value(item.pop("value_json"), "")
                item["display_value"] = _json_value(item.pop("display_value_json"), item["value"])
                item["enum_id"] = _json_value(item.pop("enum_id_json"), None)
                properties.append(item)
            result["properties"] = properties
            result["images"] = [dict(row) for row in connection.execute(
                "SELECT * FROM catalog_images WHERE product_id = ? ORDER BY is_primary DESC, sort, id",
                (product_id,),
            ).fetchall()]
            result["prices"] = [dict(row) for row in connection.execute(
                "SELECT * FROM catalog_prices WHERE product_id = ? ORDER BY is_base DESC, id",
                (product_id,),
            ).fetchall()]
            result["mapping"] = connection.execute(
                "SELECT * FROM catalog_moysklad_mappings WHERE product_id = ?", (product_id,)
            ).fetchone()
            if result["mapping"] is not None:
                result["mapping"] = dict(result["mapping"])
            result["offers"] = self._load_offers(connection, product_id)
            result["sync_history"] = self._load_sync_history(
                connection, result["external_product_id"]
            )
        result["preview_html"] = sanitize_catalog_html(result.get("preview_text"))
        result["detail_html"] = sanitize_catalog_html(result.get("detail_text"))
        return result

    @staticmethod
    def _load_offers(connection, product_id):
        offers = []
        rows = connection.execute(
            "SELECT * FROM catalog_offers WHERE product_id = ? ORDER BY name, id", (product_id,)
        ).fetchall()
        for row in rows:
            offer = dict(row)
            offer_id = offer["id"]
            offer["properties"] = [dict(value) for value in connection.execute(
                "SELECT pr.name, pr.code, pv.display_value_json FROM catalog_properties pr "
                "JOIN catalog_offer_property_values pv ON pv.property_id = pr.id "
                "WHERE pv.offer_id = ? ORDER BY pr.sort, pr.name", (offer_id,),
            ).fetchall()]
            for prop in offer["properties"]:
                prop["display_value"] = _json_value(prop.pop("display_value_json"), "")
            offer["images"] = [dict(value) for value in connection.execute(
                "SELECT * FROM catalog_images WHERE offer_id = ? ORDER BY is_primary DESC, sort, id",
                (offer_id,),
            ).fetchall()]
            offer["prices"] = [dict(value) for value in connection.execute(
                "SELECT * FROM catalog_prices WHERE offer_id = ? ORDER BY is_base DESC, id",
                (offer_id,),
            ).fetchall()]
            offers.append(offer)
        return offers

    @staticmethod
    def _load_sync_history(connection, external_product_id):
        history = []
        rows = connection.execute(
            "SELECT * FROM catalog_sync_runs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        for row in rows:
            details = _json_value(row["details_json"], [])
            if not isinstance(details, list):
                continue
            item = next((entry for entry in details if str(entry.get("external_product_id")) == str(external_product_id)), None)
            if item:
                history.append({
                    "id": row["id"], "mode": row["mode"], "status": row["status"],
                    "started_at": row["started_at"], "finished_at": row["finished_at"],
                    "item_status": item.get("status"), "match_method": item.get("match_method"),
                })
            if len(history) >= 20:
                break
        return history
