import json
import re
import statistics
from collections import Counter
from urllib.parse import urlsplit

from app.catalog_db import CatalogDatabase


VALID_CURRENCIES = {"RUB", "USD", "EUR"}


def parse_json_value(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def is_empty_value(value):
    return value is None or value == "" or value == [] or value == {}


def is_empty_property_row(value_json, display_value_json):
    return (
        is_empty_value(parse_json_value(value_json))
        and is_empty_value(parse_json_value(display_value_json))
    )


def value_kind(value):
    if value is None:
        return "null"
    if value == "":
        return "empty_string"
    if value == []:
        return "empty_array"
    if value == {}:
        return "empty_object"
    if value is False:
        return "false"
    if value == 0 and not isinstance(value, bool):
        return "zero"
    if value == "0":
        return "string_zero"
    return "filled"


def text_content(value):
    without_tags = re.sub(r"<[^>]*>", " ", value or "")
    return re.sub(r"\s+", " ", without_tags).strip()


def property_audit(connection):
    rows = connection.execute(
        "SELECT pv.id, pv.product_id, pv.property_id, pv.value_json, "
        "pv.display_value_json, pr.code, pr.name "
        "FROM catalog_product_property_values pv "
        "JOIN catalog_properties pr ON pr.id = pv.property_id"
    ).fetchall()
    product_counts = Counter()
    property_filled = Counter()
    property_total = Counter()
    kinds = Counter()
    empty_ids = []
    sql_null_rows = 0
    for row in rows:
        value = parse_json_value(row[3])
        display = parse_json_value(row[4])
        kinds[(value_kind(value), value_kind(display))] += 1
        property_total[row[2]] += 1
        if row[3] is None or row[4] is None:
            sql_null_rows += 1
        if is_empty_value(value) and is_empty_value(display):
            empty_ids.append(row[0])
        else:
            product_counts[row[1]] += 1
            property_filled[row[2]] += 1

    product_ids = [row[0] for row in connection.execute("SELECT id FROM catalog_products")]
    counts = [product_counts[product_id] for product_id in product_ids]
    property_rows = {
        row[0]: row for row in connection.execute(
            "SELECT id, code, name FROM catalog_properties"
        ).fetchall()
    }
    top_filled = sorted(
        property_filled,
        key=lambda property_id: (-property_filled[property_id], property_rows[property_id][1]),
    )[:20]
    top_sparse = sorted(
        property_total,
        key=lambda property_id: (property_filled[property_id], property_rows[property_id][1]),
    )[:20]
    anomalies = sorted(
        product_counts.items(), key=lambda item: (-item[1], item[0])
    )[:20]
    return {
        "total_rows": len(rows),
        "filled_rows": len(rows) - len(empty_ids),
        "empty_rows": len(empty_ids),
        "sql_null_rows": sql_null_rows,
        "mean_filled_per_product": round(statistics.mean(counts), 4) if counts else 0,
        "median_filled_per_product": statistics.median(counts) if counts else 0,
        "min_filled_per_product": min(counts) if counts else 0,
        "max_filled_per_product": max(counts) if counts else 0,
        "value_kinds": [
            {"value": pair[0], "display": pair[1], "rows": count}
            for pair, count in sorted(kinds.items(), key=lambda item: (-item[1], item[0]))
        ],
        "top_filled_properties": [
            {
                "property_id": property_id,
                "code": property_rows[property_id][1],
                "name": property_rows[property_id][2],
                "filled_products": property_filled[property_id],
                "total_products": property_total[property_id],
            }
            for property_id in top_filled
        ],
        "top_sparse_properties": [
            {
                "property_id": property_id,
                "code": property_rows[property_id][1],
                "name": property_rows[property_id][2],
                "filled_products": property_filled[property_id],
                "total_products": property_total[property_id],
            }
            for property_id in top_sparse
        ],
        "anomalous_products": [
            {"catalog_product_id": product_id, "filled_properties": count}
            for product_id, count in anomalies
        ],
        "duplicate_product_property_pairs": connection.execute(
            "SELECT COUNT(*) FROM (SELECT product_id, property_id "
            "FROM catalog_product_property_values GROUP BY product_id, property_id "
            "HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "empty_row_ids": empty_ids,
    }


def card_audit(connection):
    descriptions = connection.execute(
        "SELECT preview_text, detail_text FROM catalog_products"
    ).fetchall()
    image_rows = connection.execute("SELECT original_url FROM catalog_images").fetchall()
    price_rows = connection.execute("SELECT amount, currency FROM catalog_prices").fetchall()

    def price_amount(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    currencies = Counter((row[1] or "").upper() for row in price_rows)
    duplicate_name_sizes = [
        row[0] for row in connection.execute(
            "SELECT COUNT(*) AS count FROM catalog_products "
            "GROUP BY lower(trim(name)) HAVING COUNT(*) > 1 "
            "ORDER BY count DESC LIMIT 20"
        ).fetchall()
    ]
    duplicate_xml_sizes = [
        row[0] for row in connection.execute(
            "SELECT COUNT(*) AS count FROM catalog_products "
            "WHERE trim(coalesce(external_xml_id, '')) <> '' "
            "GROUP BY external_xml_id HAVING COUNT(*) > 1 "
            "ORDER BY count DESC LIMIT 20"
        ).fetchall()
    ]
    return {
        "empty_name": connection.execute(
            "SELECT COUNT(*) FROM catalog_products WHERE trim(name) = ''"
        ).fetchone()[0],
        "without_brand": connection.execute(
            "SELECT COUNT(*) FROM catalog_products WHERE brand IS NULL OR trim(brand) = ''"
        ).fetchone()[0],
        "without_description": sum(
            not text_content(row[0]) and not text_content(row[1]) for row in descriptions
        ),
        "duplicate_name_groups": connection.execute(
            "SELECT COUNT(*) FROM (SELECT lower(trim(name)) AS key "
            "FROM catalog_products GROUP BY key HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "products_in_duplicate_name_groups": connection.execute(
            "SELECT coalesce(sum(count), 0) FROM (SELECT COUNT(*) AS count "
            "FROM catalog_products GROUP BY lower(trim(name)) HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "duplicate_name_group_sizes": duplicate_name_sizes,
        "duplicate_xml_groups": connection.execute(
            "SELECT COUNT(*) FROM (SELECT external_xml_id FROM catalog_products "
            "WHERE trim(coalesce(external_xml_id, '')) <> '' "
            "GROUP BY external_xml_id HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "products_in_duplicate_xml_groups": connection.execute(
            "SELECT coalesce(sum(count), 0) FROM (SELECT COUNT(*) AS count "
            "FROM catalog_products WHERE trim(coalesce(external_xml_id, '')) <> '' "
            "GROUP BY external_xml_id HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "duplicate_xml_group_sizes": duplicate_xml_sizes,
        "duplicate_bitrix_id_groups": connection.execute(
            "SELECT COUNT(*) FROM (SELECT external_source, external_product_id "
            "FROM catalog_products GROUP BY external_source, external_product_id "
            "HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "multiple_primary_image_products": connection.execute(
            "SELECT COUNT(*) FROM (SELECT product_id FROM catalog_images "
            "WHERE product_id IS NOT NULL AND is_primary = 1 "
            "GROUP BY product_id HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "duplicate_image_url_within_product_groups": connection.execute(
            "SELECT COUNT(*) FROM (SELECT product_id, original_url FROM catalog_images "
            "WHERE product_id IS NOT NULL GROUP BY product_id, original_url "
            "HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "global_reused_image_url_groups": connection.execute(
            "SELECT COUNT(*) FROM (SELECT original_url FROM catalog_images "
            "GROUP BY original_url HAVING COUNT(DISTINCT product_id) > 1)"
        ).fetchone()[0],
        "http_image_urls": sum(
            urlsplit(row[0]).scheme.lower() == "http" for row in image_rows
        ),
        "invalid_image_urls": sum(
            urlsplit(row[0]).scheme.lower() not in {"http", "https"}
            or not urlsplit(row[0]).netloc
            for row in image_rows
        ),
        "nonpositive_prices": sum(price_amount(row[0]) <= 0 for row in price_rows),
        "currencies": dict(currencies),
        "unknown_currency_prices": sum(
            count for currency, count in currencies.items() if currency not in VALID_CURRENCIES
        ),
        "multiple_base_price_products": connection.execute(
            "SELECT COUNT(*) FROM (SELECT product_id FROM catalog_prices "
            "WHERE product_id IS NOT NULL AND is_base = 1 "
            "GROUP BY product_id HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "active_products_in_inactive_categories": connection.execute(
            "SELECT COUNT(DISTINCT p.id) FROM catalog_products p "
            "JOIN catalog_product_categories pc ON pc.product_id = p.id "
            "JOIN catalog_categories c ON c.id = pc.category_id "
            "WHERE p.active = 1 AND c.active = 0"
        ).fetchone()[0],
        "categories_without_products": connection.execute(
            "SELECT COUNT(*) FROM catalog_categories c WHERE NOT EXISTS "
            "(SELECT 1 FROM catalog_product_categories pc WHERE pc.category_id = c.id)"
        ).fetchone()[0],
        "primary_category_not_linked": connection.execute(
            "SELECT COUNT(*) FROM catalog_products p "
            "WHERE p.primary_category_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM catalog_product_categories pc "
            "WHERE pc.product_id = p.id AND pc.category_id = p.primary_category_id)"
        ).fetchone()[0],
        "products_without_primary_category": connection.execute(
            "SELECT COUNT(*) FROM catalog_products WHERE primary_category_id IS NULL"
        ).fetchone()[0],
    }


def build_quality_report(database=None, include_empty_ids=False):
    database = database or CatalogDatabase()
    with database.connect() as connection:
        properties = property_audit(connection)
        cards = card_audit(connection)
    if not include_empty_ids:
        properties.pop("empty_row_ids", None)
    return {"properties": properties, "cards": cards}
