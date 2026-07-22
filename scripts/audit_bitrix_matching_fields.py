#!/usr/bin/env python3
"""Audit Bitrix property values and Excel articles without writing source data."""

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.product_reconciliation import (  # noqa: E402
    classify_article,
    extract_model_codes,
    property_is_model_identifier,
    text,
)
from scripts.reconcile_bitrix_excel_catalog import read_excel_rows  # noqa: E402


VARIANT_PROPERTY_CODES = {
    "ACCESSORY_COLOR", "ACCESSORY_SIZE", "CHOSE_COLOR", "CHOSE_SIZE",
    "DIAMETR", "FILTER_COLOR", "PROP_COLOR_OF_FRAME", "PROP_LINSES_COLOR",
    "PROP_SIZE", "SIZE_OF_RING", "STRAP_COLOR", "STRAP_WIDTH",
}


def _connect_read_only(path):
    uri = "file:{}?mode=ro&immutable=1".format(Path(path).resolve().as_posix())
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        connection.close()
        raise ValueError("catalog database quick_check failed")
    return connection


def _flatten_display_values(value):
    if value in (None, "", False):
        return []
    if isinstance(value, (str, int, float)):
        return [text(value)]
    if isinstance(value, list):
        return [item for value_item in value for item in _flatten_display_values(value_item)]
    if isinstance(value, dict):
        for key in ("name", "filename", "id", "url"):
            if text(value.get(key)):
                return [text(value.get(key))]
    return []


def _decode_value(row):
    raw = row["display_value_json"] or row["value_json"]
    if raw in (None, ""):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _property_suitability(prop):
    code = text(prop["code"]).upper()
    if property_is_model_identifier(prop):
        return (
            "primary_identifier",
            "Название/код свойства однозначно обозначает модель или SKU.",
        )
    if code == "BRAND_MODEL":
        return (
            "brand_validation",
            "Фактические значения являются брендами, а не кодами моделей.",
        )
    if code in VARIANT_PROPERTY_CODES:
        return (
            "variant_validation",
            "Поле пригодно только для выявления конфликта цвета/размера.",
        )
    return (
        "not_suitable",
        "Поле не является идентификатором товара; похожие на коды значения смешаны с характеристиками или служебными данными.",
    )


def audit_properties(connection):
    properties = [
        dict(row) for row in connection.execute(
            """
            SELECT id, external_property_id, code, name, property_type, multiple
            FROM catalog_properties
            ORDER BY CAST(external_property_id AS INTEGER), external_property_id
            """
        )
    ]
    values_by_property = defaultdict(dict)
    for row in connection.execute(
        """
        SELECT product_id, property_id, value_json, display_value_json
        FROM catalog_product_property_values
        ORDER BY property_id, product_id
        """
    ):
        values = _flatten_display_values(_decode_value(row))
        if values:
            values_by_property[row["property_id"]][row["product_id"]] = values

    audit = []
    for prop in properties:
        product_values = values_by_property.get(prop["id"], {})
        unique_values = sorted({
            value for values in product_values.values() for value in values if value
        })
        model_like = [
            value for value in unique_values
            if extract_model_codes(
                value, "property:{}".format(text(prop["code"] or prop["external_property_id"]))
            )
        ]
        suitability, reason = _property_suitability(prop)
        audit.append({
            "property_id": text(prop["external_property_id"]),
            "property_code": text(prop["code"]),
            "name": text(prop["name"]),
            "type": text(prop["property_type"]),
            "multiple": bool(prop["multiple"]),
            "filled_products": len(product_values),
            "unique_values": len(unique_values),
            "examples": unique_values[:5],
            "model_like_unique_values": len(model_like),
            "model_like_examples": model_like[:5],
            "suitability": suitability,
            "suitability_reason": reason,
        })
    return audit


def audit_product_fields(connection):
    result = {}
    for field in ("external_product_id", "external_xml_id", "article"):
        row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN COALESCE({field}, '') <> '' THEN 1 ELSE 0 END),
                COUNT(DISTINCT CASE WHEN COALESCE({field}, '') <> '' THEN {field} END)
            FROM catalog_products
            """.format(field=field)
        ).fetchone()
        result[field] = {"filled_products": int(row[0] or 0), "unique_values": int(row[1] or 0)}
    return result


def audit_excel(path):
    rows, sheets = read_excel_rows(path)
    categories = Counter(classify_article(row["excel_article"]) for row in rows)
    examples = defaultdict(list)
    extracted_rows = 0
    for row in rows:
        classification = classify_article(row["excel_article"])
        article = text(row["excel_article"])
        codes = extract_model_codes(article, "excel_article")
        if codes:
            extracted_rows += 1
        if article and len(examples[classification]) < 10:
            examples[classification].append({
                "excel_row": row["excel_row"],
                "value": article,
                "model_codes": [item["normalized"] for item in codes],
            })
    return {
        "rows_total": len(rows),
        "sheets": sheets,
        "filled_articles": sum(bool(text(row["excel_article"])) for row in rows),
        "empty_articles": categories["empty"],
        "safe_model_code_articles": categories["model_code"],
        "text_articles": categories["text"],
        "comment_articles": categories["comment"],
        "ambiguous_articles": categories["ambiguous"],
        "rows_with_extracted_article_model_codes": extracted_rows,
        "examples": dict(examples),
    }


def _short(value, limit=80):
    value = " ".join(text(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def write_outputs(output_dir, payload):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "matching_field_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    columns = [
        "property_id", "property_code", "name", "type", "multiple",
        "filled_products", "unique_values", "examples",
        "model_like_unique_values", "model_like_examples", "suitability",
        "suitability_reason",
    ]
    with (output_dir / "bitrix_property_audit.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        for item in payload["properties"]:
            row = dict(item)
            row["examples"] = " | ".join(_short(value) for value in row["examples"])
            row["model_like_examples"] = " | ".join(
                _short(value) for value in row["model_like_examples"]
            )
            writer.writerow(row)

    lines = [
        "# Аудит полей Bitrix для сопоставления Excel",
        "",
        "Снимок проанализирован только для чтения; внешние записи не выполнялись.",
        "",
        "| ID | Код | Название | Тип | Заполнено | Уникальных | Примеры | Пригодность |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for item in payload["properties"]:
        examples = "; ".join(_short(value, 45) for value in item["examples"])
        row = dict(item)
        row["examples"] = examples.replace("|", "/")
        lines.append(
            "| {property_id} | {property_code} | {name} | {type} | {filled_products} | "
            "{unique_values} | {examples} | {suitability} |".format(
                **row
            )
        )
    (output_dir / "bitrix_property_audit.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )


def build_payload(catalog_path, excel_path):
    connection = _connect_read_only(catalog_path)
    try:
        properties = audit_properties(connection)
        product_fields = audit_product_fields(connection)
        products_total = connection.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
    finally:
        connection.close()
    return {
        "products_total": int(products_total),
        "properties_total": len(properties),
        "trusted_model_identifier_properties": sum(
            item["suitability"] == "primary_identifier" for item in properties
        ),
        "product_fields": product_fields,
        "properties": properties,
        "excel": audit_excel(excel_path),
        "writes_performed": 0,
        "bitrix_writes": 0,
        "moysklad_writes": 0,
        "production_changes": 0,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-db", required=True, type=Path)
    parser.add_argument("--excel", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    payload = build_payload(args.catalog_db, args.excel)
    write_outputs(args.output_dir, payload)
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "properties_total": payload["properties_total"],
        "trusted_model_identifier_properties": payload["trusted_model_identifier_properties"],
        "excel": payload["excel"],
        "writes_performed": 0,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
