#!/usr/bin/env python3
"""Import the TicTacToy Bitrix location tree for the ERP sales form."""

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = (
    PROJECT_ROOT / "app" / "data" / "tictactoy_locations.json"
)
SOURCE_URL = (
    "https://www.tictactoy.ru/bitrix/components/bitrix/"
    "sale.location.selector.steps/get.php"
)
COUNTRY_IDS = {
    "Россия": "24",
    "Беларусь": "1",
    "Казахстан": "8",
}
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")


def fetch_location_bundle(parent_ids):
    fields = [
        ("select[VALUE]", "ID"),
        ("select[DISPLAY]", "NAME.NAME"),
        ("select[1]", "TYPE_ID"),
        ("select[2]", "CODE"),
        ("select[3]", "PARENT_ID"),
        ("select[10]", "IS_PARENT"),
        ("filter[=NAME.LANGUAGE_ID]", "ru"),
        ("version", "2"),
    ]
    fields.extend(
        ("filter[PARENT_ID][]", parent_id)
        for parent_id in parent_ids
    )
    request = Request(
        SOURCE_URL,
        data=urlencode(fields).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": (
                "application/x-www-form-urlencoded; charset=UTF-8"
            ),
            "User-Agent": "VechasuERP location importer",
        },
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        raw_payload = response.read().decode("utf-8")

    normalized_payload = re.sub(
        r"\bnull\b",
        "None",
        re.sub(
            r"\bfalse\b",
            "False",
            re.sub(r"\btrue\b", "True", raw_payload),
        ),
    )
    payload = ast.literal_eval(normalized_payload)

    if not payload.get("result"):
        raise RuntimeError(
            ", ".join(payload.get("errors") or ["Location import failed"])
        )

    return payload.get("data", {}).get("ITEMS") or []


def fetch_location_items():
    items = []
    expanded_parent_ids = set()
    pending_parent_ids = list(COUNTRY_IDS.values())

    while pending_parent_ids:
        items.extend(fetch_location_bundle(pending_parent_ids))
        expanded_parent_ids.update(pending_parent_ids)
        pending_parent_ids = sorted({
            str(item.get("VALUE") or "").strip()
            for item in items
            if (
                item.get("IS_PARENT")
                and str(item.get("VALUE") or "").strip()
                    not in expanded_parent_ids
            )
        })

    return items


def prefer_russian_name(current, candidate):
    if not current:
        return candidate

    if (
        CYRILLIC_PATTERN.search(candidate)
        and not CYRILLIC_PATTERN.search(current)
    ):
        return candidate

    return current


def build_location_catalog(items):
    nodes = {}

    for item in items:
        node_id = str(item.get("VALUE") or "").strip()
        display = str(item.get("DISPLAY") or "").strip()

        if not node_id or not display:
            continue

        existing = nodes.get(node_id)

        if existing is None:
            nodes[node_id] = {
                "id": node_id,
                "name": display,
                "parent_id": str(
                    item.get("PARENT_ID") or ""
                ).strip(),
                "type_id": str(item.get("TYPE_ID") or "").strip(),
            }
        else:
            existing["name"] = prefer_russian_name(
                existing["name"],
                display,
            )

    children = defaultdict(list)

    for node in nodes.values():
        children[node["parent_id"]].append(node["id"])

    def descendant_city_names(root_id):
        city_names = set()
        pending = list(children.get(root_id, []))
        visited = set()

        while pending:
            node_id = pending.pop()

            if node_id in visited:
                continue

            visited.add(node_id)
            node = nodes[node_id]

            if node["type_id"] == "3":
                city_names.add(node["name"])

            pending.extend(children.get(node_id, []))

        return sorted(city_names, key=str.casefold)

    countries = {}
    locations_by_id = {}

    country_names_by_id = {
        country_id: country_name
        for country_name, country_id in COUNTRY_IDS.items()
    }

    def location_path(node_id):
        path = []
        visited = set()

        while node_id and node_id not in visited:
            visited.add(node_id)
            if node_id in country_names_by_id:
                return country_names_by_id[node_id], list(reversed(path))
            node = nodes.get(node_id)
            if node is None:
                break
            path.append(node)
            node_id = node["parent_id"]

        return "", []

    for country_name, country_id in COUNTRY_IDS.items():
        regions = {}
        top_level_nodes = [
            nodes[node_id]
            for node_id in children.get(country_id, [])
            if nodes[node_id]["type_id"] in {"2", "3"}
        ]

        for region_node in sorted(
            top_level_nodes,
            key=lambda node: node["name"].casefold(),
        ):
            if region_node["type_id"] == "3":
                cities = [region_node["name"]]
            else:
                cities = descendant_city_names(region_node["id"])

            regions[region_node["name"]] = cities

        countries[country_name] = regions

    for node_id, node in nodes.items():
        if node["type_id"] not in {"2", "3"}:
            continue
        country, path = location_path(node_id)
        if not country:
            continue

        region_node = next(
            (item for item in path if item["type_id"] == "2"),
            None,
        )
        city_node = next(
            (item for item in reversed(path) if item["type_id"] == "3"),
            None,
        )
        region = region_node["name"] if region_node else ""
        city = city_node["name"] if city_node else ""
        if city and not region:
            # Federal cities are direct children of the country in Bitrix.
            region = city

        locations_by_id[node_id] = "\t".join((country, region, city))

    return {
        "source": SOURCE_URL,
        "countries": countries,
        "locations_by_id": locations_by_id,
    }


def main():
    catalog = build_location_catalog(fetch_location_items())
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            catalog,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    for country, regions in catalog["countries"].items():
        city_count = sum(len(cities) for cities in regions.values())
        print(
            f"{country}: {len(regions)} regions, "
            f"{city_count} cities"
        )


if __name__ == "__main__":
    main()
