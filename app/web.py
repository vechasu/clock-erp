import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import json
import os
import fcntl
import uuid
import requests
from app.clients.moysklad import MoySkladClient
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

ORDERS_URL = "https://tictactoy.ru/api/orders.php"
ORDER_URL = "https://tictactoy.ru/api/order.php?id="
UPDATE_ORDER_STATUS_URL = "https://tictactoy.ru/api/update_order_status.php"

UPDATE_ORDER_STATUS_TOKEN = "clock_erp_secret_2026_change_me"

ORDERS_CACHE = {
    "items": [],
    "loaded_at": 0,
}

ORDERS_CACHE_SECONDS = 60

WAREHOUSE_CACHE = {
    "items": [],
    "loaded_at": 0,
}

WAREHOUSE_CACHE_SECONDS = 300


WAREHOUSE_ADD_REQUESTS_PATH = (
    PROJECT_ROOT / "instance" / "warehouse_add_requests.json"
)


def claim_warehouse_add_request(request_id):
    request_id = str(request_id or "").strip()

    if not request_id:
        return True

    WAREHOUSE_ADD_REQUESTS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    now = time.time()

    with WAREHOUSE_ADD_REQUESTS_PATH.open(
        "a+",
        encoding="utf-8",
    ) as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)

        file.seek(0)
        raw_data = file.read().strip()

        try:
            data = json.loads(raw_data) if raw_data else {}
        except (TypeError, ValueError):
            data = {}

        if not isinstance(data, dict):
            data = {}

        cleaned_data = {}

        for key, value in data.items():
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                continue

            if now - timestamp < 86400:
                cleaned_data[str(key)] = timestamp

        if request_id in cleaned_data:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            return False

        cleaned_data[request_id] = now

        file.seek(0)
        file.truncate()

        json.dump(
            cleaned_data,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.flush()
        os.fsync(file.fileno())
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    return True


WAREHOUSE_CREATED_AT_PATH = (
    PROJECT_ROOT / "instance" / "warehouse_created_at.json"
)


def load_warehouse_created_at():
    try:
        if not WAREHOUSE_CREATED_AT_PATH.exists():
            return {}

        data = json.loads(
            WAREHOUSE_CREATED_AT_PATH.read_text(encoding="utf-8")
        )

        return data if isinstance(data, dict) else {}
    except Exception as error:
        print("Ошибка чтения времени добавления товаров:", error)
        return {}


def save_warehouse_created_at(data):
    WAREHOUSE_CREATED_AT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    WAREHOUSE_CREATED_AT_PATH.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def record_warehouse_created_at(product_id):
    product_id = str(product_id or "").strip()

    if not product_id:
        return 0

    data = load_warehouse_created_at()
    timestamp = time.time()

    data[product_id] = timestamp
    save_warehouse_created_at(data)

    return timestamp

STATUS_NAMES = {
    "N": "Не подтвержден",
    "A": "Подтвержден",
    "T": "Не дозвонились",
    "D": "Собран",
    "C": "Отказ",
    "c": "Отказ",
}


def to_float(value):
    if value is None or value == "":
        return 0.0

    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return 0.0


def get_property(order, code):
    properties = order.get("properties") or []

    for prop in properties:
        if prop.get("code") == code:
            value = prop.get("value")
            if value is not None and value != "":
                return value

    return ""


def calculate_products_total(products):
    total = 0.0

    for product in products:
        price = to_float(product.get("price") or product.get("PRICE"))
        quantity = to_float(product.get("quantity") or product.get("QUANTITY") or 1)
        total += price * quantity

    return total


def normalize_order(order):
    if not order:
        return None

    user = order.get("user") or {}

    status = order.get("status") or order.get("STATUS_ID") or order.get("status_id") or "unknown"

    customer = (
        get_property(order, "FIO")
        or user.get("name")
        or order.get("customer")
        or order.get("client")
        or order.get("name")
        or ""
    )

    phone = (
        get_property(order, "PHONE")
        or user.get("phone")
        or order.get("phone")
        or ""
    )

    email = (
        get_property(order, "EMAIL")
        or user.get("email")
        or order.get("email")
        or ""
    )

    address = (
        get_property(order, "ADDRESS")
        or order.get("address")
        or ""
    )

    city = (
        get_property(order, "CITY")
        or order.get("city")
        or ""
    )

    paid = order.get("paid") or order.get("PAYED") or ""
    paid_name = "Оплачен" if paid == "Y" else "Не оплачен"

    products = order.get("products") or []

    order_total = to_float(
        order.get("price")
        or order.get("PRICE")
        or order.get("sum")
        or order.get("SUM")
    )

    products_total = calculate_products_total(products)
    delivery_price = order_total - products_total

    if delivery_price < 0:
        delivery_price = 0.0

    order["status"] = status
    order["status_name"] = STATUS_NAMES.get(status, status)

    order["customer"] = customer
    order["phone"] = phone
    order["email"] = email
    order["address"] = address
    order["city"] = city

    order["paid"] = paid
    order["paid_name"] = paid_name

    order["products"] = products
    order["products_count"] = len(products)

    order["order_total"] = order_total
    order["products_total"] = products_total
    order["delivery_price"] = delivery_price

    return order


def get_order(order_id):
    response = requests.get(ORDER_URL + str(order_id), timeout=1)
    response.raise_for_status()

    order = response.json().get("order")
    return normalize_order(order)


def get_orders(force=False):
    now = time.time()

    if not force and ORDERS_CACHE["items"] and now - ORDERS_CACHE["loaded_at"] < ORDERS_CACHE_SECONDS:
        return ORDERS_CACHE["items"]

    try:
        response = requests.get(ORDERS_URL, timeout=20)
        response.raise_for_status()

        short_orders = response.json().get("orders", [])

        orders = []

        for short_order in short_orders:
            normalized_order = normalize_order(short_order)
            if normalized_order:
                orders.append(normalized_order)

        ORDERS_CACHE["items"] = orders
        ORDERS_CACHE["loaded_at"] = now

        return orders

    except Exception as error:
        print(f"Ошибка загрузки списка заказов: {error}")

        if ORDERS_CACHE["items"]:
            return ORDERS_CACHE["items"]

        return []


def update_order_status(order_id, new_status):
    allowed_statuses = ["N", "A", "T", "D", "C", "c"]

    if new_status not in allowed_statuses:
        return {
            "status": "error",
            "message": "Недопустимый статус"
        }

    try:
        response = requests.post(
            UPDATE_ORDER_STATUS_URL,
            data={
                "token": UPDATE_ORDER_STATUS_TOKEN,
                "order_id": str(order_id),
                "status": new_status,
            },
            timeout=15
        )

        if not response.ok:
            return {
                "status": "error",
                "message": f"Битрикс не принял статус {new_status}. HTTP {response.status_code}: {response.text[:300]}"
            }

        return response.json()

    except Exception as error:
        return {
            "status": "error",
            "message": f"Ошибка при смене статуса: {error}"
        }


@app.route("/")
def index():
    orders = get_orders()
    selected_order = orders[0] if orders else None

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order,
        warehouse_items=get_warehouse_items(),
        product_mappings=load_product_mappings()
    )


@app.route("/order/<int:order_id>")
def order_page(order_id):
    orders = get_orders()
    selected_order = None

    for order in orders:
        if str(order.get("id")) == str(order_id) or str(order.get("ID")) == str(order_id):
            selected_order = order
            break

    try:
        full_order = get_order(order_id)
        if full_order:
            selected_order = full_order
    except Exception as error:
        print(f"Полная карточка заказа {order_id} не загрузилась быстро: {error}")

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order,
        warehouse_items=get_warehouse_items(),
        product_mappings=load_product_mappings()
    )




@app.route("/order/<int:order_id>/stock-writeoff", methods=["POST"])
def order_stock_writeoff(order_id):
    full_order = get_order(order_id)

    if not full_order:
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="Заказ не найден"
        ))

    if is_order_stock_written_off(order_id):
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="Этот заказ уже был списан со склада"
        ))

    products = full_order.get("products") or []

    if not products:
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="В заказе нет товаров для списания"
        ))

    mappings = load_product_mappings()
    warehouse_items = get_warehouse_items(force=True)

    warehouse_by_id = {
        str(item.get("id") or ""): item
        for item in warehouse_items
    }

    prepared_items = []

    for product in products:
        bitrix_product_id = str(product.get("id") or product.get("ID") or "").strip()
        bitrix_product_name = str(product.get("name") or product.get("NAME") or "Товар без названия").strip()

        try:
            quantity = float(str(product.get("quantity") or product.get("QUANTITY") or "1").replace(",", "."))
        except Exception:
            quantity = 1.0

        mapping = mappings.get(bitrix_product_id)

        if not mapping:
            return redirect(url_for(
                "order_page",
                order_id=order_id,
                notice="error",
                message=f"Товар не сопоставлен со складом: {bitrix_product_name}"
            ))

        moysklad_product_id = str(mapping.get("moysklad_product_id") or "").strip()
        warehouse_item = warehouse_by_id.get(moysklad_product_id)

        if not warehouse_item:
            return redirect(url_for(
                "order_page",
                order_id=order_id,
                notice="error",
                message=f"Товар склада не найден: {mapping.get('moysklad_product_name') or bitrix_product_name}"
            ))

        current_stock = float(warehouse_item.get("stock") or 0)

        if current_stock < quantity:
            return redirect(url_for(
                "order_page",
                order_id=order_id,
                notice="error",
                message=f"Недостаточно остатка: {warehouse_item.get('name')} — нужно {quantity:g}, есть {current_stock:g}"
            ))

        prepared_items.append({
            "bitrix_product_id": bitrix_product_id,
            "bitrix_product_name": bitrix_product_name,
            "moysklad_product_id": moysklad_product_id,
            "moysklad_product_name": warehouse_item.get("name"),
            "quantity": quantity,
            "stock_before": current_stock,
            "stock_after": current_stock - quantity,
        })

    client = MoySkladClient()
    order_number = full_order.get("number") or full_order.get("account_number") or full_order.get("ACCOUNT_NUMBER") or order_id

    try:
        for item in prepared_items:
            reason = f"ТТТ ERP: списание по заказу №{order_number}. Товар Битрикс: {item['bitrix_product_name']}"

            moysklad_document = client.create_stock_loss(
                product_id=item["moysklad_product_id"],
                quantity=item["quantity"],
                reason=reason
            )

            add_stock_operation({
                "id": str(uuid.uuid4()),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "product_id": item["moysklad_product_id"],
                "product_name": item["moysklad_product_name"],
                "type": "writeoff",
                "label": "Списание по заказу",
                "quantity": item["quantity"],
                "stock_before": item["stock_before"],
                "stock_after": item["stock_after"],
                "diff": -item["quantity"],
                "source": "Заказ Битрикс",
                "reason": f"Заказ №{order_number}",
                "order_id": str(order_id),
                "order_number": str(order_number),
                "bitrix_product_id": item["bitrix_product_id"],
                "bitrix_product_name": item["bitrix_product_name"],
                "status": "success",
                "moysklad_document_id": moysklad_document.get("id") if isinstance(moysklad_document, dict) else "",
                "moysklad_document_name": moysklad_document.get("name") if isinstance(moysklad_document, dict) else "",
                "moysklad_document_url": (
                    f"https://online.moysklad.ru/app/#loss/edit?id={moysklad_document.get('id')}"
                    if isinstance(moysklad_document, dict) and moysklad_document.get("id")
                    else ""
                ),
            })

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="success",
            message=f"Заказ №{order_number} списан со склада"
        ))

    except Exception as error:
        print(f"Ошибка списания заказа {order_id}: {error}")
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message=f"Ошибка списания заказа: {error}"
        ))


@app.route("/order/<int:order_id>/product-map", methods=["POST"])
def order_product_map(order_id):
    bitrix_product_id = (request.form.get("bitrix_product_id") or "").strip()
    bitrix_product_name = (request.form.get("bitrix_product_name") or "").strip()
    moysklad_product_id = (request.form.get("moysklad_product_id") or "").strip()

    if not bitrix_product_id:
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="Не найден ID товара Битрикс"
        ))

    if not moysklad_product_id:
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="Выбери товар склада для сопоставления"
        ))

    warehouse_items = get_warehouse_items()
    selected_item = None

    for item in warehouse_items:
        if str(item.get("id") or "") == str(moysklad_product_id):
            selected_item = item
            break

    if not selected_item:
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="error",
            message="Товар склада не найден"
        ))

    mappings = load_product_mappings()

    mappings[bitrix_product_id] = {
        "bitrix_product_id": bitrix_product_id,
        "bitrix_product_name": bitrix_product_name,
        "moysklad_product_id": selected_item.get("id"),
        "moysklad_product_name": selected_item.get("name"),
        "moysklad_product_stock": selected_item.get("stock"),
    }

    save_product_mappings(mappings)

    return redirect(url_for(
        "order_page",
        order_id=order_id,
        notice="success",
        message="Товар сопоставлен со складом"
    ))


@app.route("/order/<int:order_id>/status", methods=["POST"])
def order_status_update(order_id):
    new_status = request.form.get("status", "")

    result = update_order_status(order_id, new_status)
    get_orders(force=True)

    if result.get("status") == "ok":
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="success",
            message="Статус заказа обновлен"
        ))

    return redirect(url_for(
        "order_page",
        order_id=order_id,
        notice="error",
        message=result.get("message", "Ошибка смены статуса")
    ))



def get_product_mappings_path():
    path = Path(app.instance_path)
    path.mkdir(parents=True, exist_ok=True)
    return path / "product_mappings.json"


def load_product_mappings():
    path = get_product_mappings_path()

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def save_product_mappings(mappings):
    path = get_product_mappings_path()

    with path.open("w", encoding="utf-8") as file:
        json.dump(mappings, file, ensure_ascii=False, indent=2)


def format_stock_number(value):
    try:
        number = float(value)

        if number.is_integer():
            return str(int(number))

        return str(number).rstrip("0").rstrip(".")

    except Exception:
        return value


def get_entity_href(entity):
    if not isinstance(entity, dict):
        return ""

    meta = entity.get("meta")

    if isinstance(meta, dict):
        return meta.get("href") or ""

    return ""


def normalize_key(value):
    return str(value or "").strip().lower()


def get_stock_value(row, key):
    value = row.get(key)

    if value is None:
        return 0

    return value



def get_product_cell_from_moysklad(product):
    for attribute in product.get("attributes", []) or []:
        if not isinstance(attribute, dict):
            continue

        name = str(attribute.get("name") or "").strip().lower()

        if name == "ячейка склада":
            return str(attribute.get("value") or "").strip()

    return ""

def get_warehouse_items(limit=1000, force=False):
    now = time.time()

    if (
        not force
        and WAREHOUSE_CACHE["items"]
        and now - WAREHOUSE_CACHE["loaded_at"] < WAREHOUSE_CACHE_SECONDS
    ):
        return WAREHOUSE_CACHE["items"]

    try:
        client = MoySkladClient()

        stock_by_href = {}
        stock_by_code = {}
        stock_by_article = {}
        stock_by_name = {}

        stock_response = client.get_stock(limit=limit)
        stock_rows = stock_response if isinstance(stock_response, list) else (stock_response.get("rows", []) if stock_response else [])

        for row in stock_rows:
            assortment = row.get("assortment") or {}

            href = get_entity_href(assortment)
            code = normalize_key(row.get("code") or assortment.get("code"))
            article = normalize_key(row.get("article") or assortment.get("article"))
            name = normalize_key(row.get("name") or assortment.get("name"))

            if href:
                stock_by_href[href] = row

            if code:
                stock_by_code[code] = row

            if article:
                stock_by_article[article] = row

            if name:
                stock_by_name[name] = row

        product_response = client.get(
            "/entity/product",
            params={"limit": limit}
        )

        product_rows = product_response.get("rows", []) if product_response else []

        items = []

        for product in product_rows:
            product_href = get_entity_href(product)
            name = product.get("name") or ""
            article = product.get("article") or ""
            code = product.get("code") or ""

            stock_row = (
                stock_by_href.get(product_href)
                or stock_by_code.get(normalize_key(code))
                or stock_by_article.get(normalize_key(article))
                or stock_by_name.get(normalize_key(name))
                or {}
            )

            stock_value = get_stock_value(stock_row, "stock")
            reserve_value = get_stock_value(stock_row, "reserve")
            quantity_value = get_stock_value(stock_row, "quantity")

            category = product.get("pathName") or "Без категории"

            items.append({
                "id": product.get("id") or "",
                "cell": cells.get(product.get("id") or "", ""),
                "moysklad_url": (
                    product.get("meta", {}).get("uuidHref")
                    or f"https://online.moysklad.ru/app/#good/edit?id={product.get('id')}"
                ),
                "name": name,
                "article": article,
                "code": code,
                "category": category,
                "stock": stock_value,
                "stock_display": format_stock_number(stock_value),
                "reserve": reserve_value,
                "quantity": quantity_value,
            })

        save_warehouse_cells(product_cells)

        items.sort(key=lambda item: (
            item.get("category") or "",
            item.get("name") or ""
        ))

        WAREHOUSE_CACHE["items"] = items
        WAREHOUSE_CACHE["loaded_at"] = now

        print("WAREHOUSE ITEMS:", len(items))
        print("WAREHOUSE STOCK TOTAL:", sum(float(item.get("stock") or 0) for item in items))

        return items

    except Exception as error:
        print(f"Ошибка загрузки склада МойСклад: {error}")
        return []




def split_category_path(category):
    category = (category or "Без категории").strip() or "Без категории"
    category = category.replace("\\", "/")
    return [part.strip() for part in category.split("/") if part.strip()]


def build_category_tree(items):
    counts = {}
    tree = {}

    for item in items:
        category = item.get("category") or "Без категории"
        parts = split_category_path(category)

        current_path_parts = []

        for part in parts:
            current_path_parts.append(part)
            path = "/".join(current_path_parts)
            counts[path] = counts.get(path, 0) + 1

        node = tree

        for index, part in enumerate(parts):
            path = "/".join(parts[:index + 1])

            if part not in node:
                node[part] = {
                    "name": part,
                    "path": path,
                    "children": {}
                }

            node = node[part]["children"]

    def convert(node):
        result = []

        for name in sorted(node.keys()):
            item = node[name]
            result.append({
                "name": item["name"],
                "path": item["path"],
                "count": counts.get(item["path"], 0),
                "children": convert(item["children"])
            })

        return result

    return convert(tree)


def build_brand_groups(items):
    counts = {}

    for item in items:
        brand = item.get("brand") or "Без бренда"
        counts[brand] = counts.get(brand, 0) + 1

    return [
        {
            "name": brand,
            "count": counts[brand],
        }
        for brand in sorted(counts, key=str.lower)
    ]


def item_in_category(item, selected_category):
    if not selected_category:
        return True

    item_category = item.get("category") or "Без категории"

    return (
        item_category == selected_category
        or item_category.startswith(selected_category + "/")
    )


@app.route("/warehouse")
def warehouse_page():
    query = request.args.get("q", "").strip()
    selected_category = request.args.get("category", "").strip()
    selected_brand = request.args.get("brand", "").strip()
    selected_cell = request.args.get("cell", "").strip()
    hide_zero = request.args.get("hide_zero", "").strip() == "1"
    sort_by = request.args.get("sort_by", "name").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip()

    allowed_sort_fields = {
        "name",
        "article",
        "brand",
        "category",
        "stock",
        "created_at",
        "cell",
    }

    if sort_by not in allowed_sort_fields:
        sort_by = "name"

    if sort_dir not in {"asc", "desc"}:
        sort_dir = "asc"

    all_items = get_warehouse_items(force=request.args.get("refresh") == "1")
    category_tree = build_category_tree(all_items)
    brand_groups = build_brand_groups(all_items)
    cell_groups = build_cell_groups(all_items)

    items = all_items

    if selected_brand:
        items = [
            item for item in items
            if (item.get("brand") or "Без бренда") == selected_brand
        ]

    if selected_category:
        items = [
            item for item in items
            if item_in_category(item, selected_category)
        ]

    if selected_cell:
        if selected_cell == "Без ячейки":
            items = [
                item for item in items
                if not (item.get("cell") or "").strip()
            ]
        else:
            items = [
                item for item in items
                if (item.get("cell") or "").strip() == selected_cell
            ]

    if query:
        query_lower = query.lower()

        items = [
            item for item in items
            if query_lower in (item.get("name") or "").lower()
            or query_lower in (item.get("article") or "").lower()
        ]

    if sort_by == "created_at":
        items_with_time = [
            item
            for item in items
            if float(item.get("created_at") or 0) > 0
        ]

        items_without_time = [
            item
            for item in items
            if float(item.get("created_at") or 0) <= 0
        ]

        items_with_time.sort(
            key=lambda item: float(item.get("created_at") or 0),
            reverse=sort_dir == "desc",
        )

        items = items_with_time + items_without_time
    else:
        sort_functions = {
            "name": lambda item: str(
                item.get("name") or ""
            ).casefold(),
            "article": lambda item: str(
                item.get("article") or ""
            ).casefold(),
            "brand": lambda item: str(
                item.get("brand") or ""
            ).casefold(),
            "category": lambda item: str(
                item.get("category") or ""
            ).casefold(),
            "stock": lambda item: float(
                item.get("stock") or 0
            ),
            "cell": lambda item: str(
                item.get("cell") or ""
            ).casefold(),
        }

        items = sorted(
            items,
            key=sort_functions[sort_by],
            reverse=sort_dir == "desc",
        )

    visible_positions = sum(
        1
        for item in items
        if not hide_zero or float(item.get("stock") or 0) > 0
    )

    total_stock = sum(float(item.get("stock") or 0) for item in items)
    total_reserve = sum(float(item.get("reserve") or 0) for item in items)
    total_available = sum(float(item.get("quantity") or 0) for item in items)

    print("CATEGORY TREE:", category_tree)

    return render_template(
        "warehouse.html",
        items=items,
        query=query,
        selected_category=selected_category,
        selected_brand=selected_brand,
        selected_cell=selected_cell,
        hide_zero=hide_zero,
        sort_by=sort_by,
        sort_dir=sort_dir,
        add_request_id=uuid.uuid4().hex,
        visible_positions=visible_positions,
        category_tree=category_tree,
        brand_groups=brand_groups,
        cell_groups=cell_groups,
        total_stock=total_stock,
        total_stock_display=format_stock_number(total_stock),
        total_reserve=total_reserve,
        total_available=total_available,
        stock_operations=load_stock_operations(),
    )




@app.route("/warehouse/category-cell", methods=["POST"])
def warehouse_update_category_cell():
    category = request.form.get("category", "").strip()
    cell = request.form.get("cell", "").strip()

    if not category:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не выбран раздел"
        ))

    try:
        set_warehouse_category_cell(category, cell)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="success",
            message="Ячейка раздела сохранена"
        ))

    except Exception as error:
        print(f"Ошибка сохранения ячейки раздела: {error}")
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка сохранения ячейки раздела"
        ))



@app.route("/warehouse/cell", methods=["POST"])
def warehouse_update_cell():
    product_id = request.form.get("product_id", "").strip()
    cell = request.form.get("cell", "").strip()

    if not product_id:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    try:
        # 1. Сохраняем ячейку внутри Vechasu ERP
        set_warehouse_cell(product_id, cell)

        # 2. Отправляем эту же ячейку в МойСклад
        client = MoySkladClient()
        client.update_product_cell_attribute(product_id, cell)

        # 3. Очищаем кэш склада
        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="success",
            message="Ячейка сохранена в ERP и МойСклад"
        ))

    except Exception as error:
        print(f"Ошибка синхронизации ячейки с МойСклад: {error}")

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="error",
            message="Ячейка сохранена в ERP, но не отправлена в МойСклад"
        ))


@app.route("/warehouse/add", methods=["POST"])
def warehouse_add_product():
    import uuid

    name = request.form.get("name", "").strip()
    article = request.form.get("article", "").strip()
    brand = request.form.get("brand", "").strip()
    category = request.form.get("category", "").strip()
    cell = request.form.get("cell", "").strip()
    stock_raw = request.form.get("stock", "0").strip().replace(",", ".")
    request_id = request.form.get("request_id", "").strip()
    code = f"VECHASU-{uuid.uuid4().hex[:12].upper()}"

    if not name:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название товара обязательно"
        ))

    try:
        stock = float(stock_raw or 0)
    except ValueError:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Остаток должен быть числом"
        ))

    if stock < 0:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Начальный остаток не может быть отрицательным"
        ))

    if not claim_warehouse_add_request(request_id):
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Повторное добавление остановлено: этот запрос уже обработан"
        ))

    try:
        client = MoySkladClient()

        folder_parts = []

        if brand:
            folder_parts.append(brand)

        if category:
            folder_parts.append(category)
        elif brand:
            folder_parts.append("Без категории")

        product_folder = None

        if folder_parts:
            product_folder = client.get_or_create_product_folder(
                "/".join(folder_parts)
            )

        product = client.create_product(
            name=name,
            code=code,
            article=article or None,
            product_folder=product_folder,
        )

        if not product:
            return redirect(url_for(
                "warehouse_page",
                notice="error",
                message="МойСклад не создал товар"
            ))

        product_id = str(product.get("id") or "").strip()

        if not product_id:
            return redirect(url_for(
                "warehouse_page",
                notice="error",
                message="Товар создан, но МойСклад не вернул его ID"
            ))

        record_warehouse_created_at(product_id)

        completed_actions = []

        if brand:
            completed_actions.append(f"бренд {brand}")

        if category:
            completed_actions.append(f"категория {category}")

        if stock > 0:
            client.create_stock_enter(
                product_id=product_id,
                quantity=stock,
                reason="Начальный остаток при создании товара в Vechasu ERP",
            )
            completed_actions.append(f"остаток {format_stock_number(stock)}")

        if cell:
            set_warehouse_cell(product_id, cell)
            client.update_product_cell_attribute(product_id, cell)
            completed_actions.append(f"ячейка {cell}")

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        message = "Товар добавлен в МойСклад"

        if completed_actions:
            message += ": " + ", ".join(completed_actions)

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="success",
            message=message,
        ))

    except Exception as error:
        print(f"Ошибка добавления товара: {error}")

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="error",
            message="Товар мог быть создан, но остаток или ячейка не сохранились"
        ))


@app.route("/warehouse/edit", methods=["POST"])
def warehouse_edit_product():
    product_id = request.form.get("product_id", "").strip()
    name = request.form.get("name", "").strip()
    article = request.form.get("article", "").strip()

    if not product_id:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    if not name:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название товара обязательно"
        ))

    try:
        client = MoySkladClient()
        result = client.update_product(
            product_id=product_id,
            name=name,
            article=article
        )

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if result:
            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция обновлена в МойСклад"
            ))

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не обновил позицию"
        ))

    except Exception as error:
        print(f"Ошибка редактирования позиции: {error}")
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка редактирования позиции"
        ))




# -----------------------------
# Warehouse stock operation journal
# -----------------------------

def get_stock_operations_path():
    from pathlib import Path

    path = Path("instance")
    path.mkdir(exist_ok=True)

    return path / "stock_operations.json"


def load_stock_operations():
    import json

    path = get_stock_operations_path()

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    return data if isinstance(data, list) else []


def save_stock_operations(operations):
    import json

    path = get_stock_operations_path()
    path.write_text(
        json.dumps(operations, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def add_stock_operation(operation):
    operations = load_stock_operations()
    operations.insert(0, operation)
    save_stock_operations(operations[:1000])


def get_stock_operations_for_product(product_id, limit=10):
    product_id = str(product_id or "")

    result = [
        operation for operation in load_stock_operations()
        if str(operation.get("product_id") or "") == product_id
    ]

    return result[:limit]




def is_order_stock_written_off(order_id):
    order_id = str(order_id or "")

    for operation in load_stock_operations():
        if str(operation.get("order_id") or "") == order_id and operation.get("source") == "Заказ Битрикс":
            return True

    return False


def is_recent_duplicate_stock_operation(product_id, operation_type, quantity, stock_before, stock_after, seconds=120):
    from datetime import datetime, timedelta

    now = datetime.now()

    for operation in load_stock_operations():
        if str(operation.get("product_id") or "") != str(product_id or ""):
            continue

        if str(operation.get("type") or "") != str(operation_type or ""):
            continue

        try:
            operation_quantity = float(operation.get("quantity") or 0)
            operation_before = float(operation.get("stock_before") or 0)
            operation_after = float(operation.get("stock_after") or 0)
        except Exception:
            continue

        if abs(operation_quantity - float(quantity)) > 0.0001:
            continue

        if abs(operation_before - float(stock_before)) > 0.0001:
            continue

        if abs(operation_after - float(stock_after)) > 0.0001:
            continue

        try:
            created_at = datetime.strptime(operation.get("created_at") or "", "%Y-%m-%d %H:%M")
        except Exception:
            continue

        if now - created_at <= timedelta(seconds=seconds):
            return operation

    return None


@app.route("/warehouse/stock", methods=["POST"])
def warehouse_update_stock():
    product_id = (request.form.get("product_id") or "").strip()
    current_stock_raw = (request.form.get("current_stock") or "0").strip()
    new_stock_raw = (request.form.get("new_stock") or "0").strip()
    product_name = (request.form.get("product_name") or "").strip()
    stock_reason = (request.form.get("stock_reason") or "").strip()

    if not product_id:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    try:
        current_stock = float(str(current_stock_raw).replace(",", "."))
        new_stock = float(str(new_stock_raw).replace(",", "."))
    except Exception:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Остаток должен быть числом"
        ))

    diff = new_stock - current_stock

    if diff == 0:
        return redirect(url_for(
            "warehouse_page",
            notice="success",
            message="Остаток не изменился"
        ))

    operation_type_for_duplicate = "writeoff" if diff < 0 else "enter"
    quantity_for_duplicate = abs(diff)

    duplicate_operation = is_recent_duplicate_stock_operation(
        product_id=product_id,
        operation_type=operation_type_for_duplicate,
        quantity=quantity_for_duplicate,
        stock_before=current_stock,
        stock_after=new_stock,
    )

    if duplicate_operation:
        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="success",
            message="Похожая операция уже создана. Дубль не отправлен в МойСклад"
        ))

    reason_suffix = f" Причина: {stock_reason}" if stock_reason else ""

    client = MoySkladClient()

    try:
        moysklad_document = None

        if diff < 0:
            quantity = abs(diff)
            operation_type = "writeoff"
            operation_label = "Списание"

            moysklad_document = client.create_stock_loss(
                product_id=product_id,
                quantity=quantity,
                reason=f"ТТТ ERP: списание {quantity:g} шт. {product_name}.{reason_suffix}".strip()
            )
            message = f"Создано списание на {quantity:g} шт. в МойСклад"
        else:
            quantity = diff
            operation_type = "enter"
            operation_label = "Оприходование"

            moysklad_document = client.create_stock_enter(
                product_id=product_id,
                quantity=quantity,
                reason=f"ТТТ ERP: оприходование {quantity:g} шт. {product_name}.{reason_suffix}".strip()
            )
            message = f"Создано оприходование на {quantity:g} шт. в МойСклад"

        from datetime import datetime
        import uuid

        add_stock_operation({
            "id": str(uuid.uuid4()),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "product_id": product_id,
            "product_name": product_name,
            "type": operation_type,
            "label": operation_label,
            "quantity": quantity,
            "stock_before": current_stock,
            "stock_after": new_stock,
            "diff": diff,
            "source": "ТТТ ERP",
            "reason": stock_reason,
            "status": "success",
            "moysklad_document_id": (moysklad_document or {}).get("id"),
            "moysklad_document_name": (moysklad_document or {}).get("name"),
            "moysklad_document_url": ((moysklad_document or {}).get("meta") or {}).get("uuidHref"),
        })

        return redirect(url_for(
            "warehouse_page",
            refresh="1",
            notice="success",
            message=message
        ))

    except Exception as error:
        print("Ошибка изменения остатка:", error)

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message=f"Ошибка изменения остатка: {error}"
        ))


@app.route("/warehouse/archive", methods=["POST"])
def warehouse_archive_product():
    product_id = request.form.get("product_id", "").strip()
    is_ajax = (
        request.headers.get("X-Requested-With")
        == "XMLHttpRequest"
    )

    if not product_id:
        if is_ajax:
            return jsonify(
                ok=False,
                message="Не найден ID товара",
            ), 400

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    try:
        client = MoySkladClient()
        result = client.archive_product(product_id)

        if result:
            WAREHOUSE_CACHE["items"] = []
            WAREHOUSE_CACHE["loaded_at"] = 0

            if is_ajax:
                return jsonify(
                    ok=True,
                    message="Позиция убрана в архив МойСклад",
                )

            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция убрана в архив МойСклад"
            ))

        if is_ajax:
            return jsonify(
                ok=False,
                message="МойСклад не убрал позицию",
            ), 502

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не убрал позицию"
        ))

    except Exception as error:
        print(f"Ошибка архивации позиции: {error}")

        if is_ajax:
            return jsonify(
                ok=False,
                message="Ошибка удаления позиции",
            ), 500

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка удаления позиции"
        ))


WAREHOUSE_CELLS_FILE = PROJECT_ROOT / "instance" / "warehouse_cells.json"


def load_warehouse_cells():
    try:
        WAREHOUSE_CELLS_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not WAREHOUSE_CELLS_FILE.exists():
            return {}

        return json.loads(WAREHOUSE_CELLS_FILE.read_text(encoding="utf-8") or "{}")

    except Exception as error:
        print(f"Ошибка чтения ячеек склада: {error}")
        return {}


def save_warehouse_cells(cells):
    WAREHOUSE_CELLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WAREHOUSE_CELLS_FILE.write_text(
        json.dumps(cells, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def set_warehouse_cell(product_id, cell):
    cells = load_warehouse_cells()
    product_id = str(product_id or "").strip()
    cell = str(cell or "").strip()

    if not product_id:
        return False

    if cell:
        cells[product_id] = cell
    else:
        cells.pop(product_id, None)

    save_warehouse_cells(cells)
    return True








# === FINAL WAREHOUSE OVERRIDES START ===

WAREHOUSE_CELLS_FILE = PROJECT_ROOT / "instance" / "warehouse_cells.json"
WAREHOUSE_CATEGORY_CELLS_FILE = PROJECT_ROOT / "instance" / "warehouse_category_cells.json"


def read_json_file(path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            return {}

        return json.loads(path.read_text(encoding="utf-8") or "{}")

    except Exception as error:
        print(f"Ошибка чтения JSON {path}: {error}")
        return {}


def write_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_warehouse_cells():
    return read_json_file(WAREHOUSE_CELLS_FILE)


def save_warehouse_cells(cells):
    write_json_file(WAREHOUSE_CELLS_FILE, cells)


def set_warehouse_cell(product_id, cell):
    cells = load_warehouse_cells()
    product_id = str(product_id or "").strip()
    cell = str(cell or "").strip()

    if not product_id:
        return False

    if cell:
        cells[product_id] = cell
    else:
        cells.pop(product_id, None)

    save_warehouse_cells(cells)
    return True


def load_warehouse_category_cells():
    return read_json_file(WAREHOUSE_CATEGORY_CELLS_FILE)


def save_warehouse_category_cells(cells):
    write_json_file(WAREHOUSE_CATEGORY_CELLS_FILE, cells)


def set_warehouse_category_cell(category, cell):
    cells = load_warehouse_category_cells()
    category = str(category or "").strip()
    cell = str(cell or "").strip()

    if not category:
        return False

    if cell:
        cells[category] = cell
    else:
        cells.pop(category, None)

    save_warehouse_category_cells(cells)
    return True


def wh_key(value):
    return str(value or "").strip().lower()


def split_category_path(category):
    category = (category or "Без категории").strip() or "Без категории"
    category = category.replace("\\", "/")
    return [part.strip() for part in category.split("/") if part.strip()]


def get_category_cell(category, category_cells):
    parts = split_category_path(category)

    for end_index in range(len(parts), 0, -1):
        path = "/".join(parts[:end_index])

        if path in category_cells:
            return category_cells[path], path

    return "", ""


def format_cell_source(source):
    if source == "product":
        return "у позиции"

    if source == "category":
        return "из раздела"

    return ""


def get_warehouse_items(limit=1000, force=False):
    now = time.time()

    if (
        not force
        and WAREHOUSE_CACHE["items"]
        and now - WAREHOUSE_CACHE["loaded_at"] < WAREHOUSE_CACHE_SECONDS
    ):
        return WAREHOUSE_CACHE["items"]

    try:
        client = MoySkladClient()

        product_cells = load_warehouse_cells()
        category_cells = load_warehouse_category_cells()
        created_at_map = load_warehouse_created_at()

        product_response = client.get("/entity/product", params={"limit": limit, "expand": "attributes"})
        product_rows = product_response.get("rows", []) if product_response else []

        stock_response = client.get_stock(limit=limit)
        stock_rows = stock_response if isinstance(stock_response, list) else (stock_response.get("rows", []) if stock_response else [])

        stock_by_code = {}
        stock_by_article = {}
        stock_by_name = {}

        for row in stock_rows:
            code = wh_key(row.get("code"))
            article = wh_key(row.get("article"))
            name = wh_key(row.get("name"))

            if code:
                stock_by_code[code] = row

            if article:
                stock_by_article[article] = row

            if name:
                stock_by_name[name] = row

        items = []

        for product in product_rows:
            product_id = product.get("id") or ""
            name = product.get("name") or ""
            article = product.get("article") or ""
            code = product.get("code") or ""
            raw_category = product.get("pathName") or "Без категории"
            path_parts = split_category_path(raw_category)

            if len(path_parts) >= 2:
                brand = path_parts[0]
                category = "/".join(path_parts[1:]) or "Без категории"
            elif path_parts:
                brand = "Без бренда"
                category = path_parts[0]
            else:
                brand = "Без бренда"
                category = "Без категории"

            stock_row = (
                stock_by_code.get(wh_key(code))
                or stock_by_article.get(wh_key(article))
                or stock_by_name.get(wh_key(name))
                or {}
            )

            stock_value = stock_row.get("stock")
            if stock_value is None:
                stock_value = 0

            reserve_value = stock_row.get("reserve")
            if reserve_value is None:
                reserve_value = 0

            quantity_value = stock_row.get("quantity")
            if quantity_value is None:
                quantity_value = stock_value

            moysklad_cell = get_product_cell_from_moysklad(product)

            if moysklad_cell:
                product_cells[product_id] = moysklad_cell
                product_cell = moysklad_cell
            else:
                product_cell = product_cells.get(product_id, "")

            category_cell, category_cell_path = get_category_cell(category, category_cells)

            # Поддержка назначений, созданных до разделения бренда и категории.
            if not category_cell:
                category_cell, category_cell_path = get_category_cell(
                    raw_category,
                    category_cells,
                )

            if product_cell:
                cell = product_cell
                cell_source = "product"
                cell_source_path = ""
            elif category_cell:
                cell = category_cell
                cell_source = "category"
                cell_source_path = category_cell_path
            else:
                cell = ""
                cell_source = ""
                cell_source_path = ""

            try:
                created_at = float(
                    created_at_map.get(product_id) or 0
                )
            except (TypeError, ValueError):
                created_at = 0

            created_at_display = (
                time.strftime(
                    "%d.%m.%Y %H:%M",
                    time.localtime(created_at),
                )
                if created_at > 0
                else "до 14.07.2026"
            )

            items.append({
                "id": product_id,
                "moysklad_url": (
                    product.get("meta", {}).get("uuidHref")
                    or f"https://online.moysklad.ru/app/#good/edit?id={product_id}"
                ),
                "name": name,
                "article": article,
                "code": code,
                "brand": brand,
                "category": category,
                "raw_category": raw_category,
                "cell": cell,
                "cell_source": cell_source,
                "cell_source_label": format_cell_source(cell_source),
                "cell_source_path": cell_source_path,
                "stock": stock_value,
                "stock_display": format_stock_number(stock_value),
                "reserve": reserve_value,
                "quantity": quantity_value,
                "created_at": created_at,
                "created_at_display": created_at_display,
            })

        save_warehouse_cells(product_cells)

        items.sort(key=lambda item: (
            item.get("brand") or "",
            item.get("category") or "",
            item.get("name") or ""
        ))

        WAREHOUSE_CACHE["items"] = items
        WAREHOUSE_CACHE["loaded_at"] = now

        return items

    except Exception as error:
        print("Ошибка загрузки склада МойСклад:", error)
        return []


def build_category_tree(items):
    counts = {}
    tree = {}

    for item in items:
        category = item.get("category") or "Без категории"
        parts = split_category_path(category)

        current_path_parts = []

        for part in parts:
            current_path_parts.append(part)
            path = "/".join(current_path_parts)
            counts[path] = counts.get(path, 0) + 1

        node = tree

        for index, part in enumerate(parts):
            path = "/".join(parts[:index + 1])

            if part not in node:
                node[part] = {
                    "name": part,
                    "path": path,
                    "children": {}
                }

            node = node[part]["children"]

    def convert(node):
        result = []

        for name in sorted(node.keys()):
            item = node[name]
            result.append({
                "name": item["name"],
                "path": item["path"],
                "count": counts.get(item["path"], 0),
                "children": convert(item["children"])
            })

        return result

    return convert(tree)


def item_in_category(item, selected_category):
    if not selected_category:
        return True

    item_category = item.get("category") or "Без категории"

    return (
        item_category == selected_category
        or item_category.startswith(selected_category + "/")
    )


def build_cell_groups(items):
    groups = {}

    for item in items:
        cell = (item.get("cell") or "").strip()

        if not cell:
            cell = "Без ячейки"

        if cell not in groups:
            groups[cell] = {
                "cell": cell,
                "count": 0,
                "total_stock": 0,
                "items": []
            }

        groups[cell]["count"] += 1
        groups[cell]["total_stock"] += float(item.get("stock") or 0)

        if len(groups[cell]["items"]) < 5:
            groups[cell]["items"].append(item.get("name") or "Без названия")

    result = []

    for cell, group in groups.items():
        group["total_stock_display"] = format_stock_number(group["total_stock"])
        result.append(group)

    result.sort(key=lambda group: (
        group["cell"] == "Без ячейки",
        group["cell"]
    ))

    return result


# === FINAL WAREHOUSE OVERRIDES END ===



# -----------------------------
# Repair
# -----------------------------

def get_repair_cases_path():
    from pathlib import Path
    path = Path("instance")
    path.mkdir(exist_ok=True)
    return path / "repair_cases.json"


def load_repair_cases():
    import json

    path = get_repair_cases_path()

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    return data if isinstance(data, list) else []


def save_repair_cases(cases):
    import json

    path = get_repair_cases_path()
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")



@app.route("/stock-operations")
def stock_operations_page():
    q = (request.args.get("q") or "").strip()
    operation_type = (request.args.get("type") or "").strip()

    operations = load_stock_operations()

    if operation_type:
        operations = [
            operation for operation in operations
            if str(operation.get("type") or "") == operation_type
        ]

    if q:
        q_lower = q.lower()

        operations = [
            operation for operation in operations
            if q_lower in " ".join([
                str(operation.get("product_name") or ""),
                str(operation.get("label") or ""),
                str(operation.get("reason") or ""),
                str(operation.get("moysklad_document_name") or ""),
                str(operation.get("source") or ""),
            ]).lower()
        ]

    for operation in operations:
        operation["quantity_display"] = format_stock_number(operation.get("quantity") or 0)
        operation["stock_before_display"] = format_stock_number(operation.get("stock_before") or 0)
        operation["stock_after_display"] = format_stock_number(operation.get("stock_after") or 0)
        operation["diff_display"] = format_stock_number(operation.get("diff") or 0)

    total_operations = len(operations)
    total_writeoff = sum(1 for operation in operations if operation.get("type") == "writeoff")
    total_enter = sum(1 for operation in operations if operation.get("type") == "enter")

    return render_template(
        "stock_operations.html",
        operations=operations,
        q=q,
        operation_type=operation_type,
        total_operations=total_operations,
        total_writeoff=total_writeoff,
        total_enter=total_enter,
    )


REPAIR_STATUS_LABELS = {
    "new": "Новый",
    "diagnostics": "Диагностика",
    "waiting": "Ожидание",
    "in_progress": "В работе",
    "ready": "Готов",
    "issued": "Выдан",
}

REPAIR_TYPE_LABELS = {
    "paid": "Платный",
    "warranty": "Гарантийный",
    "service": "Сервисный",
}


@app.route("/repair")
def repair_page():
    from flask import render_template, request

    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip()
    notice = request.args.get("notice") or ""
    message = request.args.get("message") or ""

    all_cases = []

    for original_case in load_repair_cases():
        case = dict(original_case)

        if case.get("status") == "done":
            case["status"] = "ready"

        case.setdefault("repair_number", "")
        case.setdefault("accepted_at", "")
        case.setdefault("due_date", "")
        case.setdefault("client_name", "")
        case.setdefault("client_phone", "")
        case.setdefault("order_number", "")
        case.setdefault("product_name", "")
        case.setdefault("serial_number", "")
        case.setdefault("equipment", "")
        case.setdefault("problem", case.get("comment") or "")
        case.setdefault("repair_type", "paid")
        case.setdefault("master", "")
        case.setdefault("responsible", "")
        case.setdefault("estimate_cost", "")
        case.setdefault("final_cost", "")
        case.setdefault("status", "new")
        case.setdefault("communication", "")
        case.setdefault("internal_comment", "")
        case.setdefault("updated_at", case.get("created_at") or "")

        all_cases.append(case)

    stats = {
        "total": len(all_cases),
        "new": sum(
            1 for case in all_cases
            if case.get("status") == "new"
        ),
        "active": sum(
            1 for case in all_cases
            if case.get("status") in {"diagnostics", "in_progress"}
        ),
        "waiting": sum(
            1 for case in all_cases
            if case.get("status") == "waiting"
        ),
        "ready": sum(
            1 for case in all_cases
            if case.get("status") == "ready"
        ),
    }

    cases = all_cases

    if status_filter in REPAIR_STATUS_LABELS:
        cases = [
            case for case in cases
            if case.get("status") == status_filter
        ]

    if q:
        q_lower = q.lower()

        cases = [
            case for case in cases
            if q_lower in " ".join([
                str(case.get("repair_number") or ""),
                str(case.get("client_name") or ""),
                str(case.get("client_phone") or ""),
                str(case.get("order_number") or ""),
                str(case.get("product_name") or ""),
                str(case.get("serial_number") or ""),
                str(case.get("equipment") or ""),
                str(case.get("problem") or ""),
                str(case.get("master") or ""),
                str(case.get("responsible") or ""),
                str(case.get("communication") or ""),
                str(case.get("internal_comment") or ""),
            ]).lower()
        ]

    cases = sorted(
        cases,
        key=lambda item: (
            item.get("accepted_at") or "",
            item.get("created_at") or "",
        ),
        reverse=True,
    )

    return render_template(
        "repair.html",
        cases=cases,
        q=q,
        status_filter=status_filter,
        notice=notice,
        message=message,
        stats=stats,
        status_labels=REPAIR_STATUS_LABELS,
        type_labels=REPAIR_TYPE_LABELS,
    )


@app.route("/repair/add", methods=["POST"])
def repair_add():
    from datetime import datetime
    from flask import redirect, request
    import uuid

    now = datetime.now()
    cases = load_repair_cases()

    client_name = (request.form.get("client_name") or "").strip()
    client_phone = (request.form.get("client_phone") or "").strip()
    product_name = (request.form.get("product_name") or "").strip()
    problem = (request.form.get("problem") or "").strip()

    if not client_name:
        return redirect(
            "/repair?notice=error&message=Укажите имя клиента"
        )

    if not client_phone:
        return redirect(
            "/repair?notice=error&message=Укажите телефон клиента"
        )

    if not product_name:
        return redirect(
            "/repair?notice=error&message=Укажите товар"
        )

    if not problem:
        return redirect(
            "/repair?notice=error&message=Опишите неисправность"
        )

    existing_numbers = {
        str(case.get("repair_number") or "")
        for case in cases
    }

    sequence = len(cases) + 1
    repair_number = f"R-{now.year}-{sequence:04d}"

    while repair_number in existing_numbers:
        sequence += 1
        repair_number = f"R-{now.year}-{sequence:04d}"

    status = (request.form.get("status") or "new").strip()

    if status not in REPAIR_STATUS_LABELS:
        status = "new"

    repair_type = (
        request.form.get("repair_type") or "paid"
    ).strip()

    if repair_type not in REPAIR_TYPE_LABELS:
        repair_type = "paid"

    cases.append({
        "id": str(uuid.uuid4()),
        "repair_number": repair_number,
        "created_at": now.strftime("%Y-%m-%d %H:%M"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "accepted_at": (
            request.form.get("accepted_at")
            or now.strftime("%Y-%m-%d")
        ).strip(),
        "due_date": (
            request.form.get("due_date") or ""
        ).strip(),
        "client_name": client_name,
        "client_phone": client_phone,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "product_name": product_name,
        "serial_number": (
            request.form.get("serial_number") or ""
        ).strip(),
        "equipment": (
            request.form.get("equipment") or ""
        ).strip(),
        "problem": problem,
        "repair_type": repair_type,
        "master": (
            request.form.get("master") or ""
        ).strip(),
        "responsible": (
            request.form.get("responsible") or ""
        ).strip(),
        "estimate_cost": (
            request.form.get("estimate_cost") or ""
        ).strip(),
        "final_cost": (
            request.form.get("final_cost") or ""
        ).strip(),
        "status": status,
        "communication": (
            request.form.get("communication") or ""
        ).strip(),
        "internal_comment": (
            request.form.get("internal_comment") or ""
        ).strip(),
    })

    save_repair_cases(cases)

    return redirect(
        "/repair?notice=success&message=Ремонт добавлен"
    )


@app.route("/repair/update", methods=["POST"])
def repair_update():
    from datetime import datetime
    from flask import redirect, request

    case_id = (request.form.get("case_id") or "").strip()
    cases = load_repair_cases()
    updated = False

    status = (request.form.get("status") or "new").strip()

    if status not in REPAIR_STATUS_LABELS:
        status = "new"

    repair_type = (
        request.form.get("repair_type") or "paid"
    ).strip()

    if repair_type not in REPAIR_TYPE_LABELS:
        repair_type = "paid"

    for case in cases:
        if case.get("id") != case_id:
            continue

        case.update({
            "accepted_at": (
                request.form.get("accepted_at") or ""
            ).strip(),
            "due_date": (
                request.form.get("due_date") or ""
            ).strip(),
            "client_name": (
                request.form.get("client_name") or ""
            ).strip(),
            "client_phone": (
                request.form.get("client_phone") or ""
            ).strip(),
            "order_number": (
                request.form.get("order_number") or ""
            ).strip(),
            "product_name": (
                request.form.get("product_name") or ""
            ).strip(),
            "serial_number": (
                request.form.get("serial_number") or ""
            ).strip(),
            "equipment": (
                request.form.get("equipment") or ""
            ).strip(),
            "problem": (
                request.form.get("problem") or ""
            ).strip(),
            "repair_type": repair_type,
            "master": (
                request.form.get("master") or ""
            ).strip(),
            "responsible": (
                request.form.get("responsible") or ""
            ).strip(),
            "estimate_cost": (
                request.form.get("estimate_cost") or ""
            ).strip(),
            "final_cost": (
                request.form.get("final_cost") or ""
            ).strip(),
            "status": status,
            "communication": (
                request.form.get("communication") or ""
            ).strip(),
            "internal_comment": (
                request.form.get("internal_comment") or ""
            ).strip(),
            "updated_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            ),
        })

        updated = True
        break

    if not updated:
        return redirect(
            "/repair?notice=error&message=Ремонт не найден"
        )

    save_repair_cases(cases)

    return redirect(
        "/repair?notice=success&message=Ремонт обновлён"
    )


@app.route("/repair/status", methods=["POST"])
def repair_status():
    from datetime import datetime
    from flask import redirect, request

    case_id = (request.form.get("case_id") or "").strip()
    status = (request.form.get("status") or "new").strip()

    if status not in REPAIR_STATUS_LABELS:
        return redirect(
            "/repair?notice=error&message=Некорректный статус"
        )

    cases = load_repair_cases()
    updated = False

    for case in cases:
        if case.get("id") == case_id:
            case["status"] = status
            case["updated_at"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            )
            updated = True
            break

    if not updated:
        return redirect(
            "/repair?notice=error&message=Ремонт не найден"
        )

    save_repair_cases(cases)

    return redirect(
        "/repair?notice=success&message=Статус обновлён"
    )


@app.route("/repair/delete", methods=["POST"])
def repair_delete():
    from flask import redirect, request

    case_id = (request.form.get("case_id") or "").strip()
    cases = load_repair_cases()

    filtered_cases = [
        case for case in cases
        if case.get("id") != case_id
    ]

    if len(filtered_cases) == len(cases):
        return redirect(
            "/repair?notice=error&message=Ремонт не найден"
        )

    save_repair_cases(filtered_cases)

    return redirect(
        "/repair?notice=success&message=Ремонт удалён"
    )


def get_manual_sales_path():
    from pathlib import Path

    path = Path("instance/manual_sales.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_manual_sales():
    import json

    path = get_manual_sales_path()

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_manual_sales(sales):
    import json

    path = get_manual_sales_path()
    path.write_text(
        json.dumps(sales, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_automatic_sales_overrides_path():
    from pathlib import Path

    path = Path("instance/automatic_sales_overrides.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_automatic_sales_overrides():
    import json

    path = get_automatic_sales_overrides_path()

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_automatic_sales_overrides(overrides):
    import json

    path = get_automatic_sales_overrides_path()
    temporary_path = path.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(
            overrides if isinstance(overrides, dict) else {},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(path)


RUSSIAN_REGIONS = [
    "Алтайский край",
    "Амурская область",
    "Архангельская область",
    "Астраханская область",
    "Белгородская область",
    "Брянская область",
    "Владимирская область",
    "Волгоградская область",
    "Вологодская область",
    "Воронежская область",
    "Донецкая Народная Республика",
    "Еврейская автономная область",
    "Забайкальский край",
    "Запорожская область",
    "Ивановская область",
    "Иркутская область",
    "Кабардино-Балкарская Республика",
    "Калининградская область",
    "Калужская область",
    "Камчатский край",
    "Карачаево-Черкесская Республика",
    "Кемеровская область — Кузбасс",
    "Кировская область",
    "Костромская область",
    "Краснодарский край",
    "Красноярский край",
    "Курганская область",
    "Курская область",
    "Ленинградская область",
    "Липецкая область",
    "Луганская Народная Республика",
    "Магаданская область",
    "Москва",
    "Московская область",
    "Мурманская область",
    "Ненецкий автономный округ",
    "Нижегородская область",
    "Новгородская область",
    "Новосибирская область",
    "Омская область",
    "Оренбургская область",
    "Орловская область",
    "Пензенская область",
    "Пермский край",
    "Приморский край",
    "Псковская область",
    "Республика Адыгея (Адыгея)",
    "Республика Алтай",
    "Республика Башкортостан",
    "Республика Бурятия",
    "Республика Дагестан",
    "Республика Ингушетия",
    "Республика Калмыкия",
    "Республика Карелия",
    "Республика Коми",
    "Республика Крым",
    "Республика Марий Эл",
    "Республика Мордовия",
    "Республика Саха (Якутия)",
    "Республика Северная Осетия — Алания",
    "Республика Татарстан (Татарстан)",
    "Республика Тыва",
    "Республика Хакасия",
    "Ростовская область",
    "Рязанская область",
    "Самарская область",
    "Санкт-Петербург",
    "Саратовская область",
    "Сахалинская область",
    "Свердловская область",
    "Севастополь",
    "Смоленская область",
    "Ставропольский край",
    "Тамбовская область",
    "Тверская область",
    "Томская область",
    "Тульская область",
    "Тюменская область",
    "Удмуртская Республика",
    "Ульяновская область",
    "Хабаровский край",
    "Ханты-Мансийский автономный округ — Югра",
    "Херсонская область",
    "Челябинская область",
    "Чеченская Республика",
    "Чувашская Республика — Чувашия",
    "Чукотский автономный округ",
    "Ямало-Ненецкий автономный округ",
    "Ярославская область",
]


def get_russian_region_cities():
    import json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from urllib.request import Request, urlopen

    cache_path = Path("instance/russian_region_cities.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    max_cache_age = timedelta(days=30)

    def load_cache(require_fresh=False):
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(
                cache_path.read_text(encoding="utf-8")
            )

            regions = payload.get("regions")

            if not isinstance(regions, dict):
                return None

            if require_fresh:
                generated_at_value = str(
                    payload.get("generated_at") or ""
                ).strip()
                generated_at_value = generated_at_value.rstrip("Z")

                for separator in ("+", "-"):
                    separator_index = generated_at_value.find(
                        separator,
                        10,
                    )

                    if separator_index != -1:
                        generated_at_value = generated_at_value[
                            :separator_index
                        ]
                        break

                generated_at = None

                for date_format in (
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S",
                ):
                    try:
                        generated_at = datetime.strptime(
                            generated_at_value,
                            date_format,
                        )
                        break
                    except ValueError:
                        continue

                if generated_at is None:
                    return None

                if generated_at.tzinfo is None:
                    generated_at = generated_at.replace(
                        tzinfo=timezone.utc
                    )

                if (
                    datetime.now(timezone.utc) - generated_at
                    > max_cache_age
                ):
                    return None

            return regions
        except Exception:
            return None

    fresh_cache = load_cache(require_fresh=True)

    if fresh_cache is not None:
        return fresh_cache

    try:
        request_object = Request(
            "https://api.hh.ru/areas",
            headers={
                "User-Agent": "VechasuERP/1.0",
                "Accept": "application/json",
            },
        )

        with urlopen(request_object, timeout=15) as response:
            countries = json.loads(
                response.read().decode("utf-8")
            )

        russia = next(
            (
                country
                for country in countries
                if country.get("name") == "Россия"
            ),
            None,
        )

        if not russia:
            raise ValueError(
                "Россия не найдена в справочнике территорий"
            )

        region_cities = {}

        def collect_leaf_areas(nodes):
            names = []

            for node in nodes or []:
                name = str(node.get("name") or "").strip()
                children = node.get("areas") or []

                if children:
                    names.extend(collect_leaf_areas(children))
                elif name:
                    names.append(name)

            return names

        federal_cities = {
            "Москва",
            "Санкт-Петербург",
            "Севастополь",
        }

        for region in russia.get("areas") or []:
            region_name = str(
                region.get("name") or ""
            ).strip()

            if not region_name:
                continue

            cities = collect_leaf_areas(
                region.get("areas") or []
            )

            if region_name in federal_cities:
                cities.append(region_name)

            region_cities[region_name] = sorted(
                set(cities),
                key=str.casefold,
            )

        payload = {
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "regions": region_cities,
        }

        temporary_path = cache_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)

        return region_cities

    except Exception:
        old_cache = load_cache(require_fresh=False)

        if old_cache is not None:
            return old_cache

        return {
            region: []
            for region in RUSSIAN_REGIONS
        }


def parse_manual_sale_quantity(value):
    try:
        quantity = int(str(value or "").strip())
    except Exception:
        return 0

    return quantity if 1 <= quantity <= 25 else 0

# === SALES PRICE FUNCTIONS V1 ===
def parse_sale_price(value):
    from decimal import Decimal, InvalidOperation

    raw_value = str(value or "").strip()

    if not raw_value:
        return None

    normalized = (
        raw_value
        .replace("\xa0", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace(",", ".")
    )

    try:
        price = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None

    if price < 1:
        return None

    return float(price.quantize(Decimal("0.01")))


def calculate_sale_amount(unit_price, quantity):
    from decimal import Decimal, InvalidOperation

    if unit_price is None:
        return None

    try:
        price = Decimal(str(unit_price))
        quantity_value = Decimal(str(quantity or 0))
        amount = price * quantity_value
    except (InvalidOperation, ValueError, TypeError):
        return None

    return float(amount.quantize(Decimal("0.01")))


def format_sale_money(value):
    from decimal import Decimal, InvalidOperation

    if value is None:
        return ""

    try:
        amount = Decimal(str(value)).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError):
        return ""

    if amount == amount.to_integral():
        formatted = "{:,}".format(
            int(amount)
        ).replace(",", " ")
    else:
        formatted = "{:,.2f}".format(amount)
        formatted = (
            formatted
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", " ")
        )

    return f"{formatted} ₽"


# === SALES PRICE FUNCTIONS V1 END ===


def normalize_manual_sale_source(value, custom_value=""):
    source = str(value or "").strip()
    custom_source = str(custom_value or "").strip()

    if source in {"Битрикс", "Заказ Битрикс"}:
        return "Tictactoy"

    if source == "__custom__":
        return custom_source or "Свой вариант"

    return source or "Свой вариант"

# === CUSTOM DELIVERY BACKEND V1 ===
def normalize_manual_delivery_method(
    value,
    custom_value="",
):
    delivery_method = str(value or "").strip()
    custom_delivery_method = str(
        custom_value or ""
    ).strip()

    if delivery_method == "__custom__":
        return (
            custom_delivery_method
            or "Свой вариант"
        )

    return delivery_method


# === CUSTOM DELIVERY BACKEND V1 END ===


@app.route("/sales/manual/add", methods=["POST"])
def manual_sale_add():
    from datetime import date
    from uuid import uuid4
    from flask import request, redirect, url_for

    product_name = (request.form.get("product_name") or "").strip()
    quantity = parse_manual_sale_quantity(request.form.get("quantity"))

    # === MANUAL SALE ADD PRICE V1 ===
    unit_price = parse_sale_price(
        request.form.get("unit_price")
    )
    total_amount = calculate_sale_amount(
        unit_price,
        quantity,
    )
    # === MANUAL SALE ADD PRICE V1 END ===

    if not product_name:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите название товара",
            )
        )

    if quantity <= 0:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Выберите количество от 1 до 25",
            )
        )

    # === MANUAL SALE PRICE VALIDATION V1 ===
    if unit_price is None:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите цену продажи не меньше 1 ₽",
            )
        )
    # === MANUAL SALE PRICE VALIDATION V1 END ===

    sales = load_manual_sales()

    sales.append({
        "id": uuid4().hex,
        "created_at": (
            request.form.get("created_at")
            or date.today().isoformat()
        ).strip(),
        "source": normalize_manual_sale_source(
            request.form.get("source"),
            request.form.get("custom_source"),
        ),
        "product_id": (
            request.form.get("product_id") or ""
        ).strip(),
        "product_name": product_name,
        "brand": (request.form.get("brand") or "").strip(),
        "category": (
            request.form.get("category") or ""
        ).strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "track_number": (
            request.form.get("track_number") or ""
        ).strip(),
        "delivery_method": normalize_manual_delivery_method(
            request.form.get("delivery_method"),
            request.form.get(
                "custom_delivery_method"
            ),
        ),
        "region": (
            request.form.get("region") or ""
        ).strip(),
        "city": (
            request.form.get("city") or ""
        ).strip(),
        "note": (
            request.form.get("note") or ""
        ).strip(),
    })

    save_manual_sales(sales)

    return redirect(
        url_for(
            "sales_page",
            notice="success",
            message="Ручная продажа добавлена",
        )
    )


@app.route("/sales/manual/update", methods=["POST"])
def manual_sale_update():
    from flask import request, redirect, url_for

    sale_id = (request.form.get("sale_id") or "").strip()
    product_name = (request.form.get("product_name") or "").strip()
    quantity = parse_manual_sale_quantity(request.form.get("quantity"))

    # === SALES PRICE EDIT AND TABLE V2 ===
    unit_price = parse_sale_price(
        request.form.get("unit_price")
    )

    total_amount = calculate_sale_amount(
        unit_price,
        quantity,
    )
    # === SALES PRICE EDIT AND TABLE V2 END ===

    if not sale_id:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Продажа не найдена",
            )
        )

    if not product_name:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите название товара",
            )
        )

    if quantity <= 0:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Выберите количество от 1 до 25",
            )
        )

    if unit_price is None:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите цену продажи не меньше 1 ₽",
            )
        )

    sales = load_manual_sales()
    sale_found = False

    for sale in sales:
        if str(sale.get("id") or "") != sale_id:
            continue

        sale["created_at"] = (
            request.form.get("created_at") or ""
        ).strip()
        sale["source"] = normalize_manual_sale_source(
            request.form.get("source"),
            request.form.get("custom_source"),
        )
        sale["product_id"] = (
            request.form.get("product_id")
            or sale.get("product_id")
            or ""
        ).strip()
        sale["product_name"] = product_name
        sale["brand"] = (
            request.form.get("brand") or ""
        ).strip()
        sale["category"] = (
            request.form.get("category") or ""
        ).strip()
        sale["quantity"] = quantity
        sale["unit_price"] = unit_price
        sale["total_amount"] = total_amount
        sale["order_number"] = (
            request.form.get("order_number") or ""
        ).strip()
        sale["track_number"] = (
            request.form.get("track_number") or ""
        ).strip()
        sale["delivery_method"] = (
            normalize_manual_delivery_method(
                request.form.get(
                    "delivery_method"
                ),
                request.form.get(
                    "custom_delivery_method"
                ),
            )
        )
        sale["region"] = (
            request.form.get("region") or ""
        ).strip()
        sale["city"] = (
            request.form.get("city") or ""
        ).strip()
        sale["note"] = (
            request.form.get("note") or ""
        ).strip()

        sale_found = True
        break

    if not sale_found:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Продажа не найдена",
            )
        )

    save_manual_sales(sales)

    return redirect(
        url_for(
            "sales_page",
            notice="success",
            message="Ручная продажа сохранена",
        )
    )


@app.route("/sales/manual/delete", methods=["POST"])
def manual_sale_delete():
    from flask import request, redirect, url_for

    sale_id = (request.form.get("sale_id") or "").strip()

    sales = [
        sale
        for sale in load_manual_sales()
        if str(sale.get("id") or "") != sale_id
    ]

    save_manual_sales(sales)

    return redirect(
        url_for(
            "sales_page",
            notice="success",
            message="Ручная продажа удалена",
        )
    )


@app.route("/sales/automatic/update", methods=["POST"])
def automatic_sale_update():
    from flask import request, redirect, url_for

    operation_id = (
        request.form.get("operation_id") or ""
    ).strip()

    product_name = (
        request.form.get("product_name") or ""
    ).strip()

    quantity = parse_manual_sale_quantity(
        request.form.get("quantity")
    )

    unit_price = parse_sale_price(
        request.form.get("unit_price")
    )

    total_amount = calculate_sale_amount(
        unit_price,
        quantity,
    )

    if not operation_id:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Автоматическая продажа не найдена",
            )
        )

    operation_exists = any(
        str(operation.get("id") or "").strip() == operation_id
        and str(operation.get("source") or "") == "Заказ Битрикс"
        and str(operation.get("type") or "") in {"writeoff", "loss"}
        for operation in load_stock_operations()
    )

    if not operation_exists:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Исходная операция продажи не найдена",
            )
        )

    if not product_name:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите название товара",
            )
        )

    if quantity <= 0:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Выберите количество от 1 до 25",
            )
        )

    if unit_price is None:
        return redirect(
            url_for(
                "sales_page",
                notice="error",
                message="Укажите цену продажи не меньше 1 ₽",
            )
        )

    overrides = load_automatic_sales_overrides()

    overrides[operation_id] = {
        "created_at": (
            request.form.get("created_at") or ""
        ).strip(),
        "source": (
            request.form.get("source") or "Tictactoy"
        ).strip(),
        "product_name": product_name,
        "brand": (request.form.get("brand") or "").strip(),
        "category": (
            request.form.get("category") or ""
        ).strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "track_number": (
            request.form.get("track_number") or ""
        ).strip(),
        "delivery_method": normalize_manual_delivery_method(
            request.form.get("delivery_method"),
            request.form.get(
                "custom_delivery_method"
            ),
        ),
        "region": (
            request.form.get("region") or ""
        ).strip(),
        "city": (
            request.form.get("city") or ""
        ).strip(),
        "note": (
            request.form.get("note") or ""
        ).strip(),
    }

    save_automatic_sales_overrides(overrides)

    return redirect(
        url_for(
            "sales_page",
            notice="success",
            message="Автоматическая продажа сохранена",
        )
    )



# === SALES REPORTS START ===

from datetime import datetime


def build_sales_product_metadata_lookup(items):
    by_id = {}
    by_name = {}

    for item in items if isinstance(items, list) else []:
        product_id = str(item.get("id") or "").strip()
        product_name = str(item.get("name") or "").strip()
        metadata = {
            "brand": str(item.get("brand") or "").strip(),
            "category": str(item.get("category") or "").strip(),
        }

        if product_id:
            by_id[product_id] = metadata

        if product_name:
            by_name[product_name.casefold()] = metadata

    return {"by_id": by_id, "by_name": by_name}


def get_sales_product_metadata(lookup, product_id, product_name):
    product_id = str(product_id or "").strip()
    product_name = str(product_name or "").strip().casefold()

    return (
        lookup["by_id"].get(product_id)
        or lookup["by_name"].get(product_name)
        or {"brand": "", "category": ""}
    )


def build_sales_report_records():
    operations = load_stock_operations()
    stored_manual_sales = load_manual_sales()
    automatic_overrides = load_automatic_sales_overrides()
    all_warehouse_items = get_warehouse_items()
    product_metadata_lookup = build_sales_product_metadata_lookup(
        all_warehouse_items
    )

    automatic_sales = []
    manual_sales = []

    for operation in operations:
        technical_source = str(operation.get("source") or "")
        operation_type = str(operation.get("type") or "")

        if technical_source != "Заказ Битрикс":
            continue

        if operation_type not in {"writeoff", "loss"}:
            continue

        operation_id = str(operation.get("id") or "").strip()

        if not operation_id:
            continue

        override = automatic_overrides.get(operation_id) or {}

        if not isinstance(override, dict):
            override = {}

        try:
            original_quantity = float(
                operation.get("quantity") or 0
            )
        except Exception:
            original_quantity = 0

        if "quantity" in override:
            quantity_number = parse_manual_sale_quantity(
                override.get("quantity")
            )

            if quantity_number <= 0:
                quantity_number = original_quantity
        else:
            quantity_number = original_quantity

        order_id = str(operation.get("order_id") or "")
        original_order_number = str(
            operation.get("order_number") or order_id or ""
        )

        created_at = str(
            override.get(
                "created_at",
                operation.get("created_at") or "",
            )
            or ""
        )
        product_metadata = get_sales_product_metadata(
            product_metadata_lookup,
            operation.get("product_id"),
            override.get("product_name")
            or operation.get("product_name"),
        )

        automatic_sales.append({
            "id": operation_id,
            "sale_type": "automatic",
            "sale_type_label": "Автоматическая",
            "is_manual": False,
            "created_at": created_at,
            "source": str(
                override.get("source", "Tictactoy")
                or "Tictactoy"
            ),
            "order_number": str(
                override.get(
                    "order_number",
                    original_order_number,
                )
                or ""
            ),
            "product_id": str(
                operation.get("product_id") or ""
            ),
            "product_name": str(
                override.get(
                    "product_name",
                    operation.get("product_name") or "",
                )
                or ""
            ),
            "brand": str(
                override.get(
                    "brand",
                    operation.get("brand")
                    or product_metadata.get("brand")
                    or "",
                )
                or ""
            ),
            "category": str(
                override.get(
                    "category",
                    operation.get("category")
                    or product_metadata.get("category")
                    or "",
                )
                or ""
            ),
            "quantity_value": quantity_number,
            **{
                "unit_price": parse_sale_price(
                    override.get("unit_price")
                ),
                "unit_price_display": format_sale_money(
                    parse_sale_price(
                        override.get("unit_price")
                    )
                ),
                "total_amount": calculate_sale_amount(
                    parse_sale_price(
                        override.get("unit_price")
                    ),
                    quantity_number,
                ),
                "total_amount_display": format_sale_money(
                    calculate_sale_amount(
                        parse_sale_price(
                            override.get("unit_price")
                        ),
                        quantity_number,
                    )
                ),
            },
            "track_number": str(
                override.get(
                    "track_number",
                    operation.get("track_number")
                    or operation.get("shipment_number")
                    or "",
                )
                or ""
            ),
            "delivery_method": str(
                override.get(
                    "delivery_method",
                    operation.get("delivery_method") or "",
                )
                or ""
            ),
            "region": str(
                override.get(
                    "region",
                    operation.get("region") or "",
                )
                or ""
            ),
            "city": str(
                override.get(
                    "city",
                    operation.get("city")
                    or operation.get("town")
                    or "",
                )
                or ""
            ),
            "note": str(
                override.get(
                    "note",
                    operation.get("reason") or "",
                )
                or ""
            ),
        })

    for stored_sale in reversed(stored_manual_sales):
        quantity_number = parse_manual_sale_quantity(
            stored_sale.get("quantity")
        )
        product_metadata = get_sales_product_metadata(
            product_metadata_lookup,
            stored_sale.get("product_id"),
            stored_sale.get("product_name"),
        )

        manual_sales.append({
            "id": str(stored_sale.get("id") or ""),
            "sale_type": "manual",
            "sale_type_label": "Ручная",
            "is_manual": True,
            "created_at": str(
                stored_sale.get("created_at") or ""
            ),
            "source": normalize_manual_sale_source(
                stored_sale.get("source")
            ),
            "order_number": str(
                stored_sale.get("order_number") or ""
            ),
            "product_id": str(
                stored_sale.get("product_id") or ""
            ),
            "product_name": str(
                stored_sale.get("product_name") or ""
            ),
            "brand": str(
                stored_sale.get("brand")
                or product_metadata.get("brand")
                or ""
            ),
            "category": str(
                stored_sale.get("category")
                or product_metadata.get("category")
                or ""
            ),
            "quantity_value": quantity_number,
            **{
                "unit_price": parse_sale_price(
                    stored_sale.get("unit_price")
                ),
                "unit_price_display": format_sale_money(
                    parse_sale_price(
                        stored_sale.get("unit_price")
                    )
                ),
                "total_amount": calculate_sale_amount(
                    parse_sale_price(
                        stored_sale.get("unit_price")
                    ),
                    quantity_number,
                ),
                "total_amount_display": format_sale_money(
                    calculate_sale_amount(
                        parse_sale_price(
                            stored_sale.get("unit_price")
                        ),
                        quantity_number,
                    )
                ),
            },
            "track_number": str(
                stored_sale.get("track_number") or ""
            ),
            "delivery_method": str(
                stored_sale.get("delivery_method") or ""
            ),
            "region": str(
                stored_sale.get("region") or ""
            ),
            "city": str(
                stored_sale.get("city") or ""
            ),
            "note": str(
                stored_sale.get("note") or ""
            ),
        })

    sales = manual_sales + automatic_sales

    return sorted(
        sales,
        key=lambda sale: str(
            sale.get("created_at") or ""
        ),
        reverse=True,
    )


def get_sales_report_filters():
    return {
        "date_from": (
            request.args.get("date_from") or ""
        ).strip(),
        "date_to": (
            request.args.get("date_to") or ""
        ).strip(),
        "sale_type": (
            request.args.get("sale_type") or ""
        ).strip(),
        "source": (
            request.args.get("source") or ""
        ).strip(),
        "product": (
            request.args.get("product") or ""
        ).strip(),
        "delivery_method": (
            request.args.get("delivery_method") or ""
        ).strip(),
        "region": (
            request.args.get("region") or ""
        ).strip(),
        "city": (
            request.args.get("city") or ""
        ).strip(),
    }


def filter_sales_report_records(sales, filters):
    result = []

    product_query = str(
        filters.get("product") or ""
    ).casefold()

    for sale in sales:
        sale_date = str(
            sale.get("created_at") or ""
        )[:10]

        if (
            filters.get("date_from")
            and sale_date < filters["date_from"]
        ):
            continue

        if (
            filters.get("date_to")
            and sale_date > filters["date_to"]
        ):
            continue

        if (
            filters.get("sale_type")
            and sale.get("sale_type")
            != filters["sale_type"]
        ):
            continue

        if (
            filters.get("source")
            and sale.get("source")
            != filters["source"]
        ):
            continue

        if product_query:
            product_text = " ".join([
                str(sale.get("product_name") or ""),
                str(sale.get("product_id") or ""),
            ]).casefold()

            if product_query not in product_text:
                continue

        if (
            filters.get("delivery_method")
            and sale.get("delivery_method")
            != filters["delivery_method"]
        ):
            continue

        if (
            filters.get("region")
            and sale.get("region")
            != filters["region"]
        ):
            continue

        if (
            filters.get("city")
            and sale.get("city")
            != filters["city"]
        ):
            continue

        result.append(sale)

    return result


def build_sales_report_context():
    all_sales = build_sales_report_records()
    filters = get_sales_report_filters()
    sales = filter_sales_report_records(
        all_sales,
        filters,
    )

    unique_orders = {
        str(sale.get("order_number") or "").strip()
        for sale in sales
        if str(sale.get("order_number") or "").strip()
    }

    total_quantity = sum(
        float(sale.get("quantity_value") or 0)
        for sale in sales
    )

    # === SALES REPORT PRICE V1 ===
    total_revenue = sum(
        float(sale.get("total_amount") or 0)
        for sale in sales
        if sale.get("total_amount") is not None
    )
    # === SALES REPORT PRICE V1 END ===

    def unique_values(field):
        return sorted(
            {
                str(sale.get(field) or "").strip()
                for sale in all_sales
                if str(sale.get(field) or "").strip()
            },
            key=str.casefold,
        )

    return {
        "sales": sales,
        "filters": filters,
        "total_sales": len(sales),
        "total_orders": len(unique_orders),
        "total_quantity": format_stock_number(
            total_quantity
        ),
        "total_revenue": total_revenue,
        "total_revenue_display": format_sale_money(
            total_revenue
        ),
        "sources": unique_values("source"),
        "products": unique_values("product_name"),
        "delivery_methods": unique_values(
            "delivery_method"
        ),
        "regions": unique_values("region"),
        "cities": unique_values("city"),
        "generated_at": datetime.now().strftime(
            "%d.%m.%Y %H:%M"
        ),
    }


def sales_report_filename(extension):
    return "sales-report-{}.{}".format(
        datetime.now().strftime("%Y-%m-%d"),
        extension,
    )


@app.route("/sales/report")
def sales_report_page():
    context = build_sales_report_context()

    return render_template(
        "sales_report.html",
        **context
    )


@app.route("/sales/report.xlsx")
def sales_report_excel():
    from io import BytesIO
    from flask import Response
    from openpyxl import Workbook
    from openpyxl.styles import (
        Alignment,
        Border,
        Font,
        PatternFill,
        Side,
    )

    context = build_sales_report_context()
    sales = context["sales"]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Продажи"

    sheet["A1"] = "Отчёт по продажам"
    sheet["A1"].font = Font(
        bold=True,
        size=16,
    )

    sheet["A2"] = "Сформирован"
    sheet["B2"] = context["generated_at"]
    sheet["D2"] = "Продаж"
    sheet["E2"] = context["total_sales"]
    sheet["G2"] = "Заказов"
    sheet["H2"] = context["total_orders"]
    sheet["J2"] = "Единиц"
    sheet["K2"] = context["total_quantity"]

    # === SALES EXCEL PRICE V1 ===
    sheet["M2"] = "Выручка"
    sheet["N2"] = context["total_revenue"]
    sheet["N2"].number_format = '#,##0.00 "₽"'
    # === SALES EXCEL PRICE V1 END ===

    headers = [
        "Дата",
        "Тип",
        "Источник",
        "Товар",
        "Бренд",
        "Категория",
        "Количество",
        "Цена за единицу, ₽",
        "Сумма, ₽",
        "Номер заказа",
        "Трек-номер",
        "Способ доставки",
        "Регион",
        "Город",
        "Примечание",
    ]

    header_row = 4

    for column, value in enumerate(
        headers,
        start=1,
    ):
        cell = sheet.cell(
            row=header_row,
            column=column,
            value=value,
        )
        cell.font = Font(
            bold=True,
            color="FFFFFF",
        )
        cell.fill = PatternFill(
            "solid",
            fgColor="2563EB",
        )
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )

    for row_number, sale in enumerate(
        sales,
        start=header_row + 1,
    ):
        values = [
            sale.get("created_at") or "",
            sale.get("sale_type_label") or "",
            sale.get("source") or "",
            sale.get("product_name") or "",
            sale.get("brand") or "",
            sale.get("category") or "",
            sale.get("quantity_value") or 0,
            sale.get("unit_price"),
            sale.get("total_amount"),
            sale.get("order_number") or "",
            sale.get("track_number") or "",
            sale.get("delivery_method") or "",
            sale.get("region") or "",
            sale.get("city") or "",
            sale.get("note") or "",
        ]

        for column, value in enumerate(
            values,
            start=1,
        ):
            cell = sheet.cell(
                row=row_number,
                column=column,
                value=value,
            )
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )

            if (
                column in {8, 9}
                and value is not None
            ):
                cell.number_format = '#,##0.00 "₽"'

    thin_side = Side(
        style="thin",
        color="D1D5DB",
    )

    for row in sheet.iter_rows(
        min_row=header_row,
        max_row=max(header_row, sheet.max_row),
        min_col=1,
        max_col=len(headers),
    ):
        for cell in row:
            cell.border = Border(
                left=thin_side,
                right=thin_side,
                top=thin_side,
                bottom=thin_side,
            )

    widths = [
        14,
        16,
        18,
        34,
        20,
        28,
        12,
        18,
        18,
        18,
        24,
        24,
        24,
        20,
        40,
    ]

    for index, width in enumerate(
        widths,
        start=1,
    ):
        sheet.column_dimensions[
            chr(64 + index)
        ].width = width

    sheet.freeze_panes = "A5"
    sheet.auto_filter.ref = (
        "A{}:O{}".format(
            header_row,
            max(header_row, sheet.max_row),
        )
    )

    output = BytesIO()
    workbook.save(output)

    return Response(
        output.getvalue(),
        mimetype=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                'attachment; filename="{}"'
            ).format(
                sales_report_filename("xlsx")
            )
        },
    )


@app.route("/sales/report.pdf")
def sales_report_pdf():
    # === SALES PDF PRICE V1 ===
    from io import BytesIO
    from html import escape
    from flask import Response
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import (
        ParagraphStyle,
        getSampleStyleSheet,
    )
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    context = build_sales_report_context()
    sales = context["sales"]

    regular_font = (
        "/usr/share/fonts/dejavu/DejaVuSans.ttf"
    )
    bold_font = (
        "/usr/share/fonts/dejavu/"
        "DejaVuSans-Bold.ttf"
    )

    registered = pdfmetrics.getRegisteredFontNames()

    if "VechasuSans" not in registered:
        pdfmetrics.registerFont(
            TTFont(
                "VechasuSans",
                regular_font,
            )
        )

    if "VechasuSansBold" not in registered:
        pdfmetrics.registerFont(
            TTFont(
                "VechasuSansBold",
                bold_font,
            )
        )

    output = BytesIO()

    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A3),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
        title="Отчёт по продажам",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "SalesReportTitle",
        parent=styles["Title"],
        fontName="VechasuSansBold",
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=6,
    )

    info_style = ParagraphStyle(
        "SalesReportInfo",
        parent=styles["Normal"],
        fontName="VechasuSans",
        fontSize=8,
        leading=11,
    )

    header_style = ParagraphStyle(
        "SalesReportHeader",
        parent=info_style,
        fontName="VechasuSansBold",
        fontSize=5.8,
        leading=7,
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    cell_style = ParagraphStyle(
        "SalesReportCell",
        parent=info_style,
        fontSize=6.5,
        leading=8,
    )

    centered_cell_style = ParagraphStyle(
        "SalesReportCenteredCell",
        parent=cell_style,
        alignment=TA_CENTER,
    )

    story = [
        Paragraph(
            "Отчёт по продажам",
            title_style,
        ),
        Paragraph(
            (
                "Сформирован: {} &nbsp;&nbsp; "
                "Продаж: {} &nbsp;&nbsp; "
                "Заказов: {} &nbsp;&nbsp; "
                "Продано единиц: {} &nbsp;&nbsp; "
                "Выручка: {}"
            ).format(
                escape(context["generated_at"]),
                context["total_sales"],
                context["total_orders"],
                escape(str(context["total_quantity"])),
                escape(
                    context["total_revenue_display"]
                ),
            ),
            info_style,
        ),
        Spacer(1, 5 * mm),
    ]

    headers = [
        "Дата",
        "Тип",
        "Источник",
        "Товар",
        "Бренд",
        "Категория",
        "Кол-во",
        "Цена",
        "Сумма",
        "Заказ",
        "Трек-номер",
        "Доставка",
        "Регион",
        "Город",
        "Примечание",
    ]

    table_data = [[
        Paragraph(
            escape(header),
            header_style,
        )
        for header in headers
    ]]

    for sale in sales:
        values = [
            sale.get("created_at") or "",
            sale.get("sale_type_label") or "",
            sale.get("source") or "",
            sale.get("product_name") or "",
            sale.get("brand") or "",
            sale.get("category") or "",
            format_stock_number(
                sale.get("quantity_value") or 0
            ),
            sale.get("unit_price_display") or "—",
            sale.get("total_amount_display") or "—",
            sale.get("order_number") or "",
            sale.get("track_number") or "",
            sale.get("delivery_method") or "",
            sale.get("region") or "",
            sale.get("city") or "",
            sale.get("note") or "",
        ]

        row = []

        for index, value in enumerate(values):
            style = (
                centered_cell_style
                if index in {0, 1, 6, 7, 8}
                else cell_style
            )

            row.append(
                Paragraph(
                    escape(str(value)),
                    style,
                )
            )

        table_data.append(row)

    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[
            18 * mm,  # Дата
            15 * mm,  # Тип
            18 * mm,  # Источник
            28 * mm,  # Товар
            18 * mm,  # Бренд
            22 * mm,  # Категория
            11 * mm,  # Количество
            18 * mm,  # Цена
            20 * mm,  # Сумма
            18 * mm,  # Заказ
            25 * mm,  # Трек
            28 * mm,  # Доставка
            27 * mm,  # Регион
            20 * mm,  # Город
            29 * mm,  # Примечание
        ],
    )

    table.setStyle(TableStyle([
        (
            "BACKGROUND",
            (0, 0),
            (-1, 0),
            colors.HexColor("#2563EB"),
        ),
        (
            "GRID",
            (0, 0),
            (-1, -1),
            0.35,
            colors.HexColor("#CBD5E1"),
        ),
        (
            "VALIGN",
            (0, 0),
            (-1, -1),
            "TOP",
        ),
        (
            "LEFTPADDING",
            (0, 0),
            (-1, -1),
            3,
        ),
        (
            "RIGHTPADDING",
            (0, 0),
            (-1, -1),
            3,
        ),
        (
            "TOPPADDING",
            (0, 0),
            (-1, -1),
            3,
        ),
        (
            "BOTTOMPADDING",
            (0, 0),
            (-1, -1),
            3,
        ),
        (
            "ROWBACKGROUNDS",
            (0, 1),
            (-1, -1),
            [
                colors.white,
                colors.HexColor("#F8FAFC"),
            ],
        ),
    ]))

    story.append(table)
    document.build(story)

    return Response(
        output.getvalue(),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="{}"'
            ).format(
                sales_report_filename("pdf")
            )
        },
    )


# === SALES REPORTS END ===


@app.route("/sales")
def sales_page():
    from flask import request

    operations = load_stock_operations()
    stored_manual_sales = load_manual_sales()
    automatic_overrides = load_automatic_sales_overrides()
    all_warehouse_items = get_warehouse_items()
    product_metadata_lookup = build_sales_product_metadata_lookup(
        all_warehouse_items
    )

    automatic_sales = []
    manual_sales = []
    total_quantity = 0

    for operation in operations:
        technical_source = str(operation.get("source") or "")
        operation_type = str(operation.get("type") or "")

        if technical_source != "Заказ Битрикс":
            continue

        if operation_type not in ["writeoff", "loss"]:
            continue

        operation_id = str(operation.get("id") or "").strip()

        if not operation_id:
            continue

        override = automatic_overrides.get(operation_id) or {}

        if not isinstance(override, dict):
            override = {}

        try:
            original_quantity = float(
                operation.get("quantity") or 0
            )
        except Exception:
            original_quantity = 0

        if "quantity" in override:
            quantity_number = parse_manual_sale_quantity(
                override.get("quantity")
            )

            if quantity_number <= 0:
                quantity_number = original_quantity
        else:
            quantity_number = original_quantity

        total_quantity += quantity_number

        unit_price = parse_sale_price(
            override.get("unit_price")
        )

        total_amount = calculate_sale_amount(
            unit_price,
            quantity_number,
        )

        order_id = str(operation.get("order_id") or "")
        original_order_number = str(
            operation.get("order_number") or order_id or ""
        )

        created_at = str(
            override.get(
                "created_at",
                operation.get("created_at") or "",
            )
            or ""
        )
        product_metadata = get_sales_product_metadata(
            product_metadata_lookup,
            operation.get("product_id"),
            override.get("product_name")
            or operation.get("product_name"),
        )

        automatic_sales.append({
            "id": operation_id,
            "is_manual": False,
            "created_at": created_at,
            "created_at_input": created_at[:10],
            "source": str(
                override.get("source", "Tictactoy")
                or "Tictactoy"
            ),
            "order_id": order_id,
            "order_number": str(
                override.get(
                    "order_number",
                    original_order_number,
                )
                or ""
            ),
            "product_id": operation.get("product_id") or "",
            "product_name": str(
                override.get(
                    "product_name",
                    operation.get("product_name") or "",
                )
                or ""
            ),
            "brand": str(
                override.get(
                    "brand",
                    operation.get("brand")
                    or product_metadata.get("brand")
                    or "",
                )
                or ""
            ),
            "category": str(
                override.get(
                    "category",
                    operation.get("category")
                    or product_metadata.get("category")
                    or "",
                )
                or ""
            ),
            "bitrix_product_name": (
                operation.get("bitrix_product_name") or ""
            ),
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
            "unit_price": unit_price,
            "unit_price_display": format_sale_money(
                unit_price
            ),
            "total_amount": total_amount,
            "total_amount_display": format_sale_money(
                total_amount
            ),
            "track_number": str(
                override.get(
                    "track_number",
                    operation.get("track_number")
                    or operation.get("shipment_number")
                    or "",
                )
                or ""
            ),
            "delivery_method": str(
                override.get(
                    "delivery_method",
                    operation.get("delivery_method") or "",
                )
                or ""
            ),
            "region": str(
                override.get(
                    "region",
                    operation.get("region") or "",
                )
                or ""
            ),
            "city": str(
                override.get(
                    "city",
                    operation.get("city")
                    or operation.get("town")
                    or "",
                )
                or ""
            ),
            "note": str(
                override.get(
                    "note",
                    operation.get("reason") or "",
                )
                or ""
            ),
            "document_name": (
                operation.get("moysklad_document_name") or ""
            ),
            "document_url": (
                operation.get("moysklad_document_url") or ""
            ),
            "status": operation.get("status") or "",
        })

    for stored_sale in reversed(stored_manual_sales):
        quantity_number = parse_manual_sale_quantity(
            stored_sale.get("quantity")
        )
        product_metadata = get_sales_product_metadata(
            product_metadata_lookup,
            stored_sale.get("product_id"),
            stored_sale.get("product_name"),
        )

        total_quantity += quantity_number

        unit_price = parse_sale_price(
            stored_sale.get("unit_price")
        )

        total_amount = calculate_sale_amount(
            unit_price,
            quantity_number,
        )

        manual_sales.append({
            "id": str(stored_sale.get("id") or ""),
            "is_manual": True,
            "created_at": stored_sale.get("created_at") or "",
            "source": normalize_manual_sale_source(
                stored_sale.get("source")
            ),
            "order_id": "",
            "order_number": stored_sale.get("order_number") or "",
            "product_id": stored_sale.get("product_id") or "",
            "product_name": stored_sale.get("product_name") or "",
            "brand": (
                stored_sale.get("brand")
                or product_metadata.get("brand")
                or ""
            ),
            "category": (
                stored_sale.get("category")
                or product_metadata.get("category")
                or ""
            ),
            "bitrix_product_name": "",
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
            "unit_price": unit_price,
            "unit_price_display": format_sale_money(
                unit_price
            ),
            "total_amount": total_amount,
            "total_amount_display": format_sale_money(
                total_amount
            ),
            "track_number": stored_sale.get("track_number") or "",
            "delivery_method": stored_sale.get("delivery_method") or "",
            "region": stored_sale.get("region") or "",
            "city": stored_sale.get("city") or "",
            "note": stored_sale.get("note") or "",
            "document_name": "",
            "document_url": "",
            "status": "",
        })

    sales = manual_sales + automatic_sales

    unique_orders = set()

    for sale in sales:
        order_number = str(
            sale.get("order_number") or ""
        ).strip()

        if order_number:
            unique_orders.add(order_number)

    warehouse_items = [
        {
            "id": item.get("id") or "",
            "name": item.get("name") or "",
            "article": item.get("article") or "",
            "code": item.get("code") or "",
            "brand": item.get("brand") or "",
            "category": item.get("category") or "",
            "stock": item.get("stock") or 0,
            "stock_display": item.get("stock_display") or "0",
        }
        for item in all_warehouse_items
        if float(item.get("stock") or 0) > 0
    ]

    russian_region_cities = get_russian_region_cities()

    return render_template(
        "sales.html",
        sales=sales,
        warehouse_items=warehouse_items,
        russian_regions=sorted(
            russian_region_cities.keys(),
            key=str.casefold,
        ),
        russian_region_cities=russian_region_cities,
        total_sales=len(sales),
        total_orders=len(unique_orders),
        total_quantity=format_stock_number(total_quantity),
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
    )



def get_receipts_path():
    path = PROJECT_ROOT / "instance" / "receipts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_receipts():
    path = get_receipts_path()

    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    return data if isinstance(data, list) else []


def save_receipts(receipts):
    get_receipts_path().write_text(
        json.dumps(receipts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_receipt_number(value, default=0):
    try:
        return float(str(value or "").strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def generate_receipt_number(receipts):
    from datetime import datetime

    year = datetime.now().year
    prefix = f"PR-{year}-"
    numbers = []

    for receipt in receipts:
        number = str(receipt.get("number") or "")

        if not number.startswith(prefix):
            continue

        try:
            numbers.append(int(number.replace(prefix, "", 1)))
        except ValueError:
            continue

    return f"{prefix}{max(numbers, default=0) + 1:04d}"


@app.route("/receipts")
def receipts_page():
    from datetime import datetime
    from flask import request

    receipts = load_receipts()

    warehouse_items = [
        {
            "id": item.get("id") or "",
            "name": item.get("name") or "",
            "article": item.get("article") or "",
            "code": item.get("code") or "",
            # === SIMPLE RECEIPT FORM V1 ===
            "brand": (
                item.get("brand")
                or item.get("manufacturer")
                or ""
            ),
            "category": item.get("category") or "",
            # === SIMPLE RECEIPT FORM V1 END ===
            "cell": item.get("cell") or "",
            "stock": item.get("stock") or 0,
            "stock_display": item.get("stock_display") or "0",
        }
        for item in get_warehouse_items()
    ]

    total_quantity = sum(
        parse_receipt_number(receipt.get("total_quantity"))
        for receipt in receipts
    )
    total_amount = sum(
        parse_receipt_number(receipt.get("total_amount"))
        for receipt in receipts
    )

    return render_template(
        "receipts.html",
        receipts=receipts,
        warehouse_items=warehouse_items,
        today=datetime.now().strftime("%Y-%m-%d"),
        total_receipts=len(receipts),
        total_quantity=format_stock_number(total_quantity),
        total_amount=total_amount,
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
        open_receipt_modal=(
            request.args.get(
                "open_receipt_modal"
            )
            == "1"
        ),
    )


# === RECEIPTS REPORT PAGE V1 ===
@app.route("/receipts/report")
def receipts_report():
    from datetime import datetime
    from flask import request

    date_from = (
        request.args.get("date_from") or ""
    ).strip()

    date_to = (
        request.args.get("date_to") or ""
    ).strip()

    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    receipts = []

    for receipt in load_receipts():
        receipt_date = str(
            receipt.get("receipt_date")
            or receipt.get("created_at")
            or ""
        )[:10]

        if date_from and receipt_date < date_from:
            continue

        if date_to and receipt_date > date_to:
            continue

        receipts.append(receipt)

    receipts.sort(
        key=lambda receipt: (
            str(
                receipt.get("receipt_date")
                or receipt.get("created_at")
                or ""
            ),
            str(receipt.get("number") or ""),
        ),
        reverse=True,
    )

    total_quantity = sum(
        parse_receipt_number(
            receipt.get("total_quantity")
        )
        for receipt in receipts
    )

    total_amount = sum(
        parse_receipt_number(
            receipt.get("total_amount")
        )
        for receipt in receipts
    )

    return render_template(
        "receipts_report.html",
        receipts=receipts,
        date_from=date_from,
        date_to=date_to,
        total_receipts=len(receipts),
        total_quantity=format_stock_number(
            total_quantity
        ),
        total_amount=total_amount,
        generated_at=datetime.now().strftime(
            "%d.%m.%Y %H:%M"
        ),
    )
# === RECEIPTS REPORT PAGE V1 END ===


# === RECEIPTS EXCEL IMPORT PREVIEW V1 ===
@app.route(
    "/receipts/import/preview",
    methods=["POST"],
)
def receipts_import_preview():
    from flask import jsonify, request
    from io import BytesIO
    from openpyxl import load_workbook
    import re

    max_file_size = 15 * 1024 * 1024

    uploaded_file = request.files.get("file")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({
            "ok": False,
            "message": "Выберите Excel-файл",
        }), 400

    filename = str(uploaded_file.filename).strip()
    filename_lower = filename.lower()

    if not filename_lower.endswith((".xlsx", ".xlsm")):
        return jsonify({
            "ok": False,
            "message": (
                "Поддерживаются только файлы "
                ".xlsx и .xlsm"
            ),
        }), 400

    file_data = uploaded_file.read()

    if not file_data:
        return jsonify({
            "ok": False,
            "message": "Загруженный файл пуст",
        }), 400

    if len(file_data) > max_file_size:
        return jsonify({
            "ok": False,
            "message": (
                "Файл слишком большой. "
                "Максимальный размер — 15 МБ"
            ),
        }), 400

    def stringify_excel_value(value):
        if value is None:
            return ""

        if isinstance(value, float) and value.is_integer():
            return str(int(value))

        return str(value).strip()

    def normalize_excel_text(value):
        normalized = stringify_excel_value(value)
        normalized = normalized.lower().replace("ё", "е")
        normalized = re.sub(
            r"[^a-zа-я0-9]+",
            " ",
            normalized,
        )
        return " ".join(normalized.split())

    header_aliases = {
        "name": {
            "наименование",
            "название",
            "название товара",
            "товар",
            "модель",
            "product",
            "product name",
            "name",
        },
        "article": {
            "артикул",
            "арт",
            "артикул товара",
            "sku",
            "vendor code",
        },
        "code": {
            "код",
            "код товара",
            "внутренний код",
            "code",
        },
        "brand": {
            "бренд",
            "марка",
            "производитель",
            "brand",
            "manufacturer",
        },
        "category": {
            "категория",
            "тип товара",
            "группа",
            "category",
            "product category",
        },
        "collection": {
            "коллекция",
            "серия",
            "линейка",
            "collection",
            "series",
        },
        "quantity": {
            "количество",
            "кол во",
            "колво",
            "количество шт",
            "шт",
            "остаток",
            "qty",
            "quantity",
            "stock",
        },
        "purchase_price": {
            "закупочная цена",
            "цена закупки",
            "закупка",
            "закупочная стоимость",
            "себестоимость",
            "purchase price",
            "cost",
            "price",
        },
        "cell": {
            "ячейка",
            "ячейка склада",
            "место хранения",
            "cell",
            "location",
        },
    }

    normalized_aliases = {
        field: {
            normalize_excel_text(alias)
            for alias in aliases
        }
        for field, aliases in header_aliases.items()
    }

    try:
        workbook = load_workbook(
            filename=BytesIO(file_data),
            read_only=True,
            data_only=True,
        )
    except Exception as error:
        return jsonify({
            "ok": False,
            "message": (
                "Не удалось прочитать Excel-файл: "
                + str(error)
            ),
        }), 400

    requested_sheet = (
        request.form.get("sheet") or ""
    ).strip()

    if requested_sheet:
        if requested_sheet not in workbook.sheetnames:
            return jsonify({
                "ok": False,
                "message": "Указанный лист не найден",
                "sheet_names": workbook.sheetnames,
            }), 400

        worksheet = workbook[requested_sheet]
    else:
        worksheet = workbook[
            workbook.sheetnames[0]
        ]

    header_row_number = None
    column_indexes = {}
    header_values = []
    best_score = 0

    for row_number, row in enumerate(
        worksheet.iter_rows(
            min_row=1,
            max_row=min(25, worksheet.max_row),
            values_only=True,
        ),
        start=1,
    ):
        row_indexes = {}
        row_headers = [
            stringify_excel_value(value)
            for value in row
        ]

        for column_index, value in enumerate(row):
            normalized_value = normalize_excel_text(
                value
            )

            if not normalized_value:
                continue

            for field, aliases in normalized_aliases.items():
                if (
                    field not in row_indexes
                    and normalized_value in aliases
                ):
                    row_indexes[field] = column_index

        score = len(row_indexes)

        if (
            score > best_score
            and "quantity" in row_indexes
            and any(
                field in row_indexes
                for field in (
                    "name",
                    "article",
                    "code",
                )
            )
        ):
            best_score = score
            header_row_number = row_number
            column_indexes = row_indexes
            header_values = row_headers

    if not header_row_number:
        return jsonify({
            "ok": False,
            "message": (
                "Не удалось определить заголовки. "
                "В таблице должны быть количество "
                "и название, артикул или код товара"
            ),
            "sheet_names": workbook.sheetnames,
        }), 400

    catalog = get_warehouse_items(force=True)

    catalog_by_id = {}
    article_index = {}
    code_index = {}
    name_index = {}

    def add_catalog_index(index, value, product):
        key = normalize_excel_text(value)

        if not key:
            return

        index.setdefault(key, []).append(product)

    for product in catalog:
        product_id = str(
            product.get("id") or ""
        ).strip()

        if not product_id:
            continue

        catalog_by_id[product_id] = product

        add_catalog_index(
            article_index,
            product.get("article"),
            product,
        )
        add_catalog_index(
            code_index,
            product.get("code"),
            product,
        )
        add_catalog_index(
            name_index,
            product.get("name"),
            product,
        )

    def read_row_value(row, field):
        column_index = column_indexes.get(field)

        if column_index is None:
            return ""

        if column_index >= len(row):
            return ""

        return stringify_excel_value(
            row[column_index]
        )

    preview_rows = []
    aggregated_rows = {}
    duplicate_count = 0
    input_rows_count = 0
    truncated = False

    max_data_rows = 5000

    for row_offset, row in enumerate(
        worksheet.iter_rows(
            min_row=header_row_number + 1,
            values_only=True,
        ),
        start=1,
    ):
        if row_offset > max_data_rows:
            truncated = True
            break

        excel_row_number = (
            header_row_number + row_offset
        )

        name = read_row_value(row, "name")
        article = read_row_value(row, "article")
        code = read_row_value(row, "code")
        brand = read_row_value(row, "brand")
        category = read_row_value(row, "category")
        collection = read_row_value(
            row,
            "collection",
        )
        cell = read_row_value(row, "cell")

        raw_quantity = read_row_value(
            row,
            "quantity",
        )
        raw_purchase_price = read_row_value(
            row,
            "purchase_price",
        )

        identifying_values = [
            name,
            article,

            brand,
            category,
            collection,
            raw_quantity,
            raw_purchase_price,
        ]

        if not any(
            stringify_excel_value(value)
            for value in identifying_values
        ):
            continue

        input_rows_count += 1

        quantity = parse_receipt_number(
            raw_quantity,
            default=0,
        )
        purchase_price = parse_receipt_number(
            raw_purchase_price,
            default=0,
        )

        messages = []
        matched_products = {}

        lookup_values = (
            (article_index, article, "артикулу"),
            (code_index, code, "коду"),
            (name_index, name, "названию"),
        )

        for index, lookup_value, lookup_label in lookup_values:
            lookup_key = normalize_excel_text(
                lookup_value
            )

            if not lookup_key:
                continue

            products = index.get(lookup_key, [])

            if len(products) > 1:
                messages.append(
                    "В каталоге найдено несколько "
                    f"товаров по {lookup_label}"
                )

            for product in products:
                product_id = str(
                    product.get("id") or ""
                ).strip()

                if product_id:
                    matched_products[
                        product_id
                    ] = product

        status = "new"
        status_label = "Новый"
        matched_product = None

        if len(matched_products) > 1:
            status = "error"
            status_label = "Ошибка"
            messages.append(
                "Артикул, код и название указывают "
                "на разные товары"
            )
        elif len(matched_products) == 1:
            matched_product = next(
                iter(matched_products.values())
            )
            status = "found"
            status_label = "Найден"

        if matched_product:
            product_id = str(
                matched_product.get("id") or ""
            ).strip()

            name = (
                matched_product.get("name")
                or name
            )
            article = (
                matched_product.get("article")
                or article
            )
            code = (
                matched_product.get("code")
                or code
            )
            brand = (
                brand
                or matched_product.get("brand")
                or matched_product.get(
                    "manufacturer"
                )
                or ""
            )
            category = (
                category
                or matched_product.get("category")
                or collection
                or ""
            )
            cell = (
                matched_product.get("cell")
                or cell
            )
            current_stock = parse_receipt_number(
                matched_product.get("stock"),
                default=0,
            )
        else:
            product_id = ""
            current_stock = 0
            category = category or collection

        if not name and status != "found":
            status = "error"

            messages.append(
                "Не указано название нового товара"
            )

        if quantity <= 0:
            status = "error"
            status_label = "Ошибка"
            messages.append(
                "Количество должно быть больше нуля"
            )

        if purchase_price < 0:
            status = "error"
            status_label = "Ошибка"
            messages.append(
                "Закупочная цена не может быть "
                "отрицательной"
            )

        if status == "new" and not brand:
            status = "error"
            status_label = "Ошибка"
            messages.append(
                "Для нового товара не указан бренд"
            )

        if status == "new" and not category:
            status = "error"
            status_label = "Ошибка"
            messages.append(
                "Для нового товара не указана "
                "категория или коллекция"
            )

        if (
            "purchase_price"
            not in column_indexes
            or raw_purchase_price == ""
        ):
            messages.append(
                "Закупочная цена не указана — "
                "используется 0 ₽"
            )

        row_data = {
            "row_number": excel_row_number,
            "source_rows": [excel_row_number],
            "status": status,
            "status_label": status_label,
            "can_import": status != "error",
            "product_id": product_id,
            "name": stringify_excel_value(name),
            "article": stringify_excel_value(
                article
            ),
            "code": stringify_excel_value(code),
            "brand": stringify_excel_value(
                brand
            ),
            "category": stringify_excel_value(
                category
            ),
            "collection": stringify_excel_value(
                collection
            ),
            "cell": stringify_excel_value(cell),
            "quantity": quantity,
            "purchase_price": purchase_price,
            "line_total": round(
                quantity * purchase_price,
                2,
            ),
            "current_stock": current_stock,
            "stock_after": (
                current_stock + quantity
            ),
            "duplicate_count": 0,
            "messages": messages,
        }

        if status == "error":
            preview_rows.append(row_data)
            continue

        identity_key = (
            product_id
            or normalize_excel_text(article)
            or normalize_excel_text(code)
            or normalize_excel_text(name)
        )

        aggregation_key = (
            status,
            identity_key,
        )

        existing_row = aggregated_rows.get(
            aggregation_key
        )

        if existing_row:
            duplicate_count += 1

            previous_quantity = parse_receipt_number(
                existing_row.get("quantity"),
                default=0,
            )
            previous_amount = parse_receipt_number(
                existing_row.get("line_total"),
                default=0,
            )

            combined_quantity = (
                previous_quantity + quantity
            )
            combined_amount = (
                previous_amount
                + quantity * purchase_price
            )

            existing_row["quantity"] = (
                combined_quantity
            )
            existing_row["line_total"] = round(
                combined_amount,
                2,
            )
            existing_row["purchase_price"] = (
                round(
                    combined_amount
                    / combined_quantity,
                    2,
                )
                if combined_quantity
                else 0
            )
            existing_row["stock_after"] = (
                existing_row["current_stock"]
                + combined_quantity
            )
            existing_row["duplicate_count"] += 1
            existing_row["source_rows"].append(
                excel_row_number
            )

            duplicate_message = (
                "Объединено строк Excel: "
                + ", ".join(
                    str(number)
                    for number
                    in existing_row["source_rows"]
                )
            )

            existing_row["messages"] = [
                message
                for message
                in existing_row["messages"]
                if not message.startswith(
                    "Объединено строк Excel:"
                )
            ]
            existing_row["messages"].append(
                duplicate_message
            )
        else:
            aggregated_rows[
                aggregation_key
            ] = row_data
            preview_rows.append(row_data)

    importable_rows = [
        row
        for row in preview_rows
        if row.get("can_import")
    ]

    found_rows = [
        row
        for row in importable_rows
        if row.get("status") == "found"
    ]

    new_rows = [
        row
        for row in importable_rows
        if row.get("status") == "new"
    ]

    error_rows = [
        row
        for row in preview_rows
        if row.get("status") == "error"
    ]

    total_quantity = sum(
        parse_receipt_number(
            row.get("quantity"),
            default=0,
        )
        for row in importable_rows
    )

    total_amount = round(
        sum(
            parse_receipt_number(
                row.get("line_total"),
                default=0,
            )
            for row in importable_rows
        ),
        2,
    )

    columns = {
        field: (
            header_values[index]
            if index < len(header_values)
            else ""
        )
        for field, index in column_indexes.items()
    }

    return jsonify({
        "ok": True,
        "filename": filename,
        "sheet": worksheet.title,
        "sheet_names": workbook.sheetnames,
        "header_row": header_row_number,
        "columns": columns,
        "truncated": truncated,
        "summary": {
            "input_rows": input_rows_count,
            "result_rows": len(preview_rows),
            "found": len(found_rows),
            "new": len(new_rows),
            "duplicates": duplicate_count,
            "errors": len(error_rows),
            "total_quantity": total_quantity,
            "total_amount": total_amount,
        },
        "rows": preview_rows,
    })
# === RECEIPTS EXCEL IMPORT PREVIEW V1 END ===


@app.route("/receipts/create", methods=["POST"])
def receipt_create():
    from datetime import datetime
    from flask import request, redirect, url_for
    import json as receipt_json
    import uuid

    # === SIMPLE RECEIPT FORM V1 ===
    submitted_brand = (
        request.form.get("brand") or ""
    ).strip()

    submitted_category = (
        request.form.get("category") or ""
    ).strip()

    # === NEW PRODUCT IN RECEIPT BACKEND V1 ===
    new_product_name = (
        request.form.get("new_product_name") or ""
    ).strip()
    # === NEW PRODUCT IN RECEIPT BACKEND V1 END ===

    # === RECEIPT CREATE NEXT V2 ===
    submit_mode = (
        request.form.get("submit_mode")
        or "close"
    ).strip()
    # === RECEIPT CREATE NEXT V2 END ===


    # === SIMPLE RECEIPT FORM V1 END ===

    receipt_date = (
        request.form.get("receipt_date")
        or datetime.now().strftime("%Y-%m-%d")
    ).strip()
    note = (request.form.get("note") or "").strip()

    raw_import_payload = (
        request.form.get("import_payload") or ""
    ).strip()

    import_rows = []

    if raw_import_payload:
        try:
            parsed_import_payload = receipt_json.loads(
                raw_import_payload
            )
        except (TypeError, ValueError):
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message=(
                    "Не удалось прочитать данные "
                    "импорта из Excel"
                ),
                open_receipt_modal="1",
            ))

        if not isinstance(parsed_import_payload, list):
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Неверный формат импорта",
                open_receipt_modal="1",
            ))

        import_rows = [
            row
            for row in parsed_import_payload
            if isinstance(row, dict)
        ]

        if not import_rows:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message=(
                    "В импорте нет доступных "
                    "для проведения строк"
                ),
                open_receipt_modal="1",
            ))

    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    purchase_prices = request.form.getlist("purchase_price")

    catalog = {
        str(item.get("id") or ""): item
        for item in get_warehouse_items(force=True)
    }

    created_new_product = False

    # === RECEIPTS IMPORT CREATE MANY V1 ===
    imported_position_metadata = {}

    if import_rows:
        product_ids = []
        quantities = []
        purchase_prices = []

        # Общие поля ручной формы не должны
        # переопределять бренд и категорию импорта.
        submitted_brand = ""
        submitted_category = ""

        import_product_client = None

        for import_index, import_row in enumerate(
            import_rows,
            start=1,
        ):
            import_name = str(
                import_row.get("name") or ""
            ).strip()

            import_article = str(
                import_row.get("article") or ""
            ).strip()

            import_code = str(
                import_row.get("code") or ""
            ).strip()

            import_brand = str(
                import_row.get("brand") or ""
            ).strip()

            import_category = str(
                import_row.get("category")
                or import_row.get("collection")
                or ""
            ).strip()

            import_product_id = str(
                import_row.get("product_id") or ""
            ).strip()

            import_quantity = parse_receipt_number(
                import_row.get("quantity"),
                default=0,
            )

            import_purchase_price = (
                parse_receipt_number(
                    import_row.get("purchase_price"),
                    default=0,
                )
            )

            if import_quantity <= 0:
                return redirect(url_for(
                    "receipts_page",
                    notice="error",
                    message=(
                        "Строка импорта "
                        f"{import_index}: количество "
                        "должно быть больше нуля"
                    ),
                    open_receipt_modal="1",
                ))

            if import_purchase_price < 0:
                return redirect(url_for(
                    "receipts_page",
                    notice="error",
                    message=(
                        "Строка импорта "
                        f"{import_index}: закупочная "
                        "цена не может быть отрицательной"
                    ),
                    open_receipt_modal="1",
                ))

            if import_product_id:
                if import_product_id not in catalog:
                    return redirect(url_for(
                        "receipts_page",
                        notice="error",
                        message=(
                            "Один из импортируемых "
                            "товаров больше не найден "
                            "в каталоге"
                        ),
                        open_receipt_modal="1",
                    ))
            else:
                if not import_name:
                    return redirect(url_for(
                        "receipts_page",
                        notice="error",
                        message=(
                            "Для нового товара "
                            "не указано название"
                        ),
                        open_receipt_modal="1",
                    ))

                if not import_brand:
                    return redirect(url_for(
                        "receipts_page",
                        notice="error",
                        message=(
                            f"Для товара «{import_name}» "
                            "не указан бренд"
                        ),
                        open_receipt_modal="1",
                    ))

                if not import_category:
                    return redirect(url_for(
                        "receipts_page",
                        notice="error",
                        message=(
                            f"Для товара «{import_name}» "
                            "не указана категория "
                            "или коллекция"
                        ),
                        open_receipt_modal="1",
                    ))

                try:
                    if import_product_client is None:
                        import_product_client = (
                            MoySkladClient()
                        )

                    import_product_folder = (
                        import_product_client
                        .get_or_create_product_folder(
                            "/".join([
                                import_brand,
                                import_category,
                            ])
                        )
                    )

                    generated_code = (
                        import_code
                        or (
                            "VECHASU-"
                            + uuid.uuid4()
                            .hex[:12]
                            .upper()
                        )
                    )

                    created_product = (
                        import_product_client
                        .create_product(
                            name=import_name,
                            code=generated_code,
                            article=(
                                import_article or None
                            ),
                            product_folder=(
                                import_product_folder
                            ),
                        )
                    )

                    if not created_product:
                        raise ValueError(
                            "МойСклад не создал товар"
                        )

                    import_product_id = str(
                        created_product.get("id") or ""
                    ).strip()

                    if not import_product_id:
                        raise ValueError(
                            "МойСклад не вернул "
                            "ID нового товара"
                        )

                    record_warehouse_created_at(
                        import_product_id
                    )

                    catalog[import_product_id] = {
                        "id": import_product_id,
                        "name": (
                            created_product.get("name")
                            or import_name
                        ),
                        "article": (
                            created_product.get("article")
                            or import_article
                        ),
                        "code": (
                            created_product.get("code")
                            or generated_code
                        ),
                        "brand": import_brand,
                        "category": import_category,
                        "cell": str(
                            import_row.get("cell") or ""
                        ).strip(),
                        "stock": 0,
                    }

                    created_new_product = True

                except Exception as error:
                    print(
                        "Ошибка создания товара "
                        "из Excel: "
                        + str(error)
                    )

                    WAREHOUSE_CACHE["items"] = []
                    WAREHOUSE_CACHE["loaded_at"] = 0

                    return redirect(url_for(
                        "receipts_page",
                        notice="error",
                        message=(
                            "Не удалось создать товар "
                            f"«{import_name}»: "
                            + str(error)
                        ),
                        open_receipt_modal="1",
                    ))

            product_ids.append(import_product_id)
            quantities.append(import_quantity)
            purchase_prices.append(
                import_purchase_price
            )

            imported_position_metadata[
                import_product_id
            ] = {
                "brand": import_brand,
                "category": import_category,
            }
    # === RECEIPTS IMPORT CREATE MANY V1 END ===

    # === NEW PRODUCT IN RECEIPT BACKEND V1 ===
    if product_ids and product_ids[0] == "__new__":
        if not new_product_name:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Укажите название нового товара",
            ))

        if not submitted_brand:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Укажите бренд нового товара",
            ))

        if not submitted_category:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Укажите категорию нового товара",
            ))

        try:
            product_client = MoySkladClient()

            product_folder = (
                product_client
                .get_or_create_product_folder(
                    "/".join([
                        submitted_brand,
                        submitted_category,
                    ])
                )
            )

            product_code = (
                "VECHASU-"
                + uuid.uuid4().hex[:12].upper()
            )

            created_product = (
                product_client.create_product(
                    name=new_product_name,
                    code=product_code,
                    article=None,
                    product_folder=product_folder,
                )
            )

            if not created_product:
                raise ValueError(
                    "МойСклад не создал товар"
                )

            new_product_id = str(
                created_product.get("id") or ""
            ).strip()

            if not new_product_id:
                raise ValueError(
                    "МойСклад не вернул ID товара"
                )

            record_warehouse_created_at(
                new_product_id
            )

            catalog[new_product_id] = {
                "id": new_product_id,
                "name": (
                    created_product.get("name")
                    or new_product_name
                ),
                "article": (
                    created_product.get("article")
                    or ""
                ),
                "code": (
                    created_product.get("code")
                    or product_code
                ),
                "brand": submitted_brand,
                "category": submitted_category,
                "cell": "",
                "stock": 0,
            }

            product_ids = [new_product_id]
            created_new_product = True

        except Exception as error:
            print(
                "Ошибка создания товара из прихода: "
                + str(error)
            )

            WAREHOUSE_CACHE["items"] = []
            WAREHOUSE_CACHE["loaded_at"] = 0

            return redirect(url_for(
                "receipts_page",
                notice="error",
                message=(
                    "Ошибка создания нового товара: "
                    + str(error)
                ),
            ))
    # === NEW PRODUCT IN RECEIPT BACKEND V1 END ===

    positions = []

    for index, product_id in enumerate(product_ids):
        product_id = str(product_id or "").strip()

        if not product_id:
            continue

        product = catalog.get(product_id)

        if not product:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Один из товаров не найден в каталоге",
            ))

        quantity = parse_receipt_number(
            quantities[index] if index < len(quantities) else 0
        )
        purchase_price = parse_receipt_number(
            purchase_prices[index] if index < len(purchase_prices) else 0
        )

        if quantity <= 0:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message=f"Количество товара «{product.get('name')}» должно быть больше нуля",
            ))

        if purchase_price < 0:
            return redirect(url_for(
                "receipts_page",
                notice="error",
                message="Закупочная цена не может быть отрицательной",
            ))

        stock_before = parse_receipt_number(product.get("stock"))

        imported_metadata = (
            imported_position_metadata.get(
                product_id,
                {},
            )
        )

        position_brand = (
            imported_metadata.get("brand")
            or submitted_brand
            or product.get("brand")
            or product.get("manufacturer")
            or ""
        )

        position_category = (
            imported_metadata.get("category")
            or submitted_category
            or product.get("category")
            or ""
        )

        positions.append({
            "brand": str(position_brand).strip(),
            "category": str(position_category).strip(),
            "product_id": product_id,
            "product_name": product.get("name") or "",
            "article": product.get("article") or "",
            "code": product.get("code") or "",
            "cell": product.get("cell") or "",
            "quantity": quantity,
            "purchase_price": purchase_price,
            "line_total": round(quantity * purchase_price, 2),
            "stock_before": stock_before,
            "stock_after": stock_before + quantity,
        })

    if not positions:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Добавьте хотя бы один товар",
        ))

    receipts = load_receipts()
    receipt_id = str(uuid.uuid4())
    receipt_number = generate_receipt_number(receipts)

    first_position = positions[0]

    reason_parts = [
        f"Vechasu ERP: приход {receipt_number}",
        (
            "Товар: "
            f"{first_position['product_name']}"
        ),
    ]

    if first_position.get("brand"):
        reason_parts.append(
            f"Бренд: {first_position['brand']}"
        )

    if first_position.get("category"):
        reason_parts.append(
            "Категория: "
            f"{first_position['category']}"
        )

    if note:
        reason_parts.append(f"Комментарий: {note}")

    reason = ". ".join(reason_parts)

    try:
        client = MoySkladClient()
        moysklad_document = client.create_stock_enter_many(
            positions=positions,
            reason=reason,
            moment=receipt_date,
        )

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        total_quantity = sum(position["quantity"] for position in positions)
        total_amount = round(
            sum(position["line_total"] for position in positions),
            2,
        )

        receipt = {
            "id": receipt_id,
            "number": receipt_number,
            "created_at": created_at,
            "receipt_date": receipt_date,
            "brand": first_position.get("brand") or "",
            "category": (
                first_position.get("category") or ""
            ),
            "product_id": (
                first_position.get("product_id") or ""
            ),
            "product_name": (
                first_position.get("product_name") or ""
            ),
            "quantity": (
                first_position.get("quantity") or 0
            ),
            "purchase_price": (
                first_position.get("purchase_price") or 0
            ),
            # Старые ключи оставлены пустыми для совместимости.
            "supplier": "",
            "invoice_number": "",
            "note": note,
            "status": "posted",
            "status_label": "Проведён",
            "positions": positions,
            "positions_count": len(positions),
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "moysklad_document_id": (moysklad_document or {}).get("id"),
            "moysklad_document_name": (moysklad_document or {}).get("name"),
            "moysklad_document_url": (
                ((moysklad_document or {}).get("meta") or {}).get("uuidHref")
            ),
        }

        receipts.insert(0, receipt)
        save_receipts(receipts)

        for position in positions:
            add_stock_operation({
                "id": str(uuid.uuid4()),
                "created_at": created_at,
                "product_id": position["product_id"],
                "product_name": position["product_name"],
                "type": "enter",
                "label": "Приход",
                "quantity": position["quantity"],
                "stock_before": position["stock_before"],
                "stock_after": position["stock_after"],
                "diff": position["quantity"],
                "source": "Приход",
                "reason": reason,
                "status": "success",
                "receipt_id": receipt_id,
                "receipt_number": receipt_number,
                "brand": position.get("brand") or "",
                "category": position.get("category") or "",
                "supplier": "",
                "invoice_number": "",
                "purchase_price": position["purchase_price"],
                "moysklad_document_id": receipt["moysklad_document_id"],
                "moysklad_document_name": receipt["moysklad_document_name"],
                "moysklad_document_url": receipt["moysklad_document_url"],
            })

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if created_new_product:
            success_message = (
                f"Товар «{first_position['product_name']}» "
                f"создан, приход {receipt_number} проведён"
            )
        else:
            success_message = (
                f"Приход {receipt_number} проведён"
            )

        if submit_mode == "create_next":
            return redirect(url_for(
                "receipts_page",
                notice="success",
                message=success_message,
                open_receipt_modal="1",
            ))

        return redirect(url_for(
            "receipts_page",
            notice="success",
            message=success_message,
        ))

    except Exception as error:
        print(f"Ошибка проведения прихода: {error}")

        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=f"Ошибка проведения прихода: {error}",
        ))


# === RECEIPT ROW ACTIONS BACKEND V1 ===
@app.route("/receipts/update", methods=["POST"])
def receipt_update():
    from flask import request, redirect, url_for

    receipt_id = (
        request.form.get("receipt_id") or ""
    ).strip()

    receipt_date = (
        request.form.get("receipt_date") or ""
    ).strip()

    brand = (
        request.form.get("brand") or ""
    ).strip()

    category = (
        request.form.get("category") or ""
    ).strip()

    note = (
        request.form.get("note") or ""
    ).strip()

    quantity = parse_receipt_number(
        request.form.get("quantity")
    )

    purchase_price = parse_receipt_number(
        request.form.get("purchase_price")
    )

    if not receipt_id:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Приход не найден",
        ))

    if not receipt_date:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Укажите дату прихода",
        ))

    if not brand:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Укажите бренд",
        ))

    if not category:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Укажите категорию",
        ))

    if quantity <= 0:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "Количество должно быть больше нуля"
            ),
        ))

    if purchase_price < 0:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "Закупочная цена не может быть "
                "отрицательной"
            ),
        ))

    receipts = load_receipts()
    receipt = None

    for item in receipts:
        if str(item.get("id") or "") == receipt_id:
            receipt = item
            break

    if not receipt:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Приход не найден",
        ))

    positions = receipt.get("positions") or []

    if len(positions) != 1:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "Редактирование доступно только "
                "для прихода с одной позицией"
            ),
        ))

    old_position = positions[0]

    product_id = str(
        receipt.get("product_id")
        or old_position.get("product_id")
        or ""
    ).strip()

    product_name = str(
        receipt.get("product_name")
        or old_position.get("product_name")
        or ""
    ).strip()

    document_id = str(
        receipt.get("moysklad_document_id") or ""
    ).strip()

    if not product_id or not document_id:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "У прихода нет связанного товара "
                "или документа МоегоСклада"
            ),
        ))

    line_total = round(
        quantity * purchase_price,
        2,
    )

    updated_position = dict(old_position)
    updated_position.update({
        "brand": brand,
        "category": category,
        "product_id": product_id,
        "product_name": product_name,
        "quantity": quantity,
        "purchase_price": purchase_price,
        "line_total": line_total,
    })

    reason_parts = [
        "Vechasu ERP: приход "
        + str(receipt.get("number") or ""),
        "Товар: " + product_name,
        "Бренд: " + brand,
        "Категория: " + category,
    ]

    if note:
        reason_parts.append(
            "Комментарий: " + note
        )

    reason = ". ".join(reason_parts)

    try:
        client = MoySkladClient()

        result = client.update_stock_enter_many(
            document_id=document_id,
            positions=[updated_position],
            reason=reason,
            moment=receipt_date,
        )

        if not result:
            raise ValueError(
                "МойСклад не обновил приход"
            )

        receipt.update({
            "receipt_date": receipt_date,
            "brand": brand,
            "category": category,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "purchase_price": purchase_price,
            "note": note,
            "positions": [updated_position],
            "positions_count": 1,
            "total_quantity": quantity,
            "total_amount": line_total,
            "moysklad_document_name": (
                result.get("name")
                or receipt.get(
                    "moysklad_document_name"
                )
            ),
            "moysklad_document_url": (
                (
                    result.get("meta")
                    or {}
                ).get("uuidHref")
                or receipt.get(
                    "moysklad_document_url"
                )
            ),
        })

        save_receipts(receipts)

        operations = load_stock_operations()

        for operation in operations:
            if (
                str(
                    operation.get("receipt_id")
                    or ""
                )
                != receipt_id
            ):
                continue

            stock_before = parse_receipt_number(
                operation.get("stock_before")
            )

            operation.update({
                "product_id": product_id,
                "product_name": product_name,
                "brand": brand,
                "category": category,
                "quantity": quantity,
                "diff": quantity,
                "stock_after": (
                    stock_before + quantity
                ),
                "purchase_price": purchase_price,
                "reason": reason,
                "moysklad_document_name": (
                    receipt.get(
                        "moysklad_document_name"
                    )
                ),
                "moysklad_document_url": (
                    receipt.get(
                        "moysklad_document_url"
                    )
                ),
            })

        save_stock_operations(operations)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "receipts_page",
            notice="success",
            message=(
                "Приход "
                + str(receipt.get("number") or "")
                + " обновлён"
            ),
        ))

    except Exception as error:
        print(
            "Ошибка редактирования прихода: "
            + str(error)
        )

        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "Ошибка редактирования прихода: "
                + str(error)
            ),
        ))


@app.route("/receipts/delete", methods=["POST"])
def receipt_delete():
    from flask import request, redirect, url_for

    receipt_id = (
        request.form.get("receipt_id") or ""
    ).strip()

    receipts = load_receipts()

    receipt = next(
        (
            item
            for item in receipts
            if str(item.get("id") or "")
            == receipt_id
        ),
        None,
    )

    if not receipt:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Приход не найден",
        ))

    document_id = str(
        receipt.get("moysklad_document_id") or ""
    ).strip()

    if not document_id:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "У прихода нет документа "
                "МоегоСклада"
            ),
        ))

    try:
        client = MoySkladClient()

        deleted = client.delete_stock_enter(
            document_id
        )

        if not deleted:
            raise ValueError(
                "МойСклад не удалил приход"
            )

        receipts = [
            item
            for item in receipts
            if str(item.get("id") or "")
            != receipt_id
        ]

        save_receipts(receipts)

        operations = [
            operation
            for operation in load_stock_operations()
            if str(
                operation.get("receipt_id") or ""
            )
            != receipt_id
        ]

        save_stock_operations(operations)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "receipts_page",
            notice="success",
            message=(
                "Приход "

                + " удалён"
            ),
        ))

    except Exception as error:
        print(
            "Ошибка удаления прихода: "
            + str(error)
        )

        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=(
                "Ошибка удаления прихода: "
                + str(error)
            ),
        ))
# === RECEIPT ROW ACTIONS BACKEND V1 END ===


def parse_analytics_date(value):
    from datetime import datetime

    raw_value = str(value or "").strip()

    if not raw_value:
        return None

    try:
        return datetime.strptime(raw_value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def build_analytics_data(
    sales_records,
    receipts,
    warehouse_items,
    requested_period="30",
    today=None,
):
    from datetime import date, timedelta

    period_days = {
        "7": 7,
        "30": 30,
        "90": 90,
        "all": None,
    }
    period_labels = {
        "7": "7 дней",
        "30": "30 дней",
        "90": "90 дней",
        "all": "Всё время",
    }

    period = requested_period if requested_period in period_days else "30"
    days = period_days[period]
    today = today or date.today()
    start_date = today - timedelta(days=days - 1) if days else None

    def is_in_period(item_date):
        if days is None:
            return True

        return item_date is not None and start_date <= item_date <= today

    def positive_number(value):
        try:
            return max(0.0, float(str(value or 0).replace(",", ".")))
        except (TypeError, ValueError):
            return 0.0

    filtered_sales = []

    for sale in sales_records if isinstance(sales_records, list) else []:
        sale_date = parse_analytics_date(sale.get("created_at"))

        if not is_in_period(sale_date):
            continue

        filtered_sales.append({
            "product_key": str(
                sale.get("product_id")
                or sale.get("product_name")
                or "Без названия"
            ),
            "product_name": str(sale.get("product_name") or "Без названия"),
            "source": str(sale.get("source") or "Без источника"),
            "quantity": positive_number(sale.get("quantity_value")),
            "amount": positive_number(sale.get("total_amount")),
        })

    filtered_receipts = []

    for receipt in receipts if isinstance(receipts, list) else []:
        receipt_date = parse_analytics_date(
            receipt.get("receipt_date")
            or receipt.get("created_at")
        )

        if is_in_period(receipt_date):
            filtered_receipts.append(receipt)

    receipt_product_rows = []

    for receipt in filtered_receipts:
        positions = receipt.get("positions") or []

        if not isinstance(positions, list):
            positions = []

        if not positions and receipt.get("product_name"):
            positions = [receipt]

        for position in positions:
            quantity = positive_number(position.get("quantity"))
            line_total = position.get("line_total")

            if line_total is None:
                line_total = (
                    quantity
                    * positive_number(position.get("purchase_price"))
                )

            receipt_product_rows.append({
                "product_key": str(
                    position.get("product_id")
                    or position.get("product_name")
                    or "Без названия"
                ),
                "product_name": str(
                    position.get("product_name") or "Без названия"
                ),
                "quantity": quantity,
                "amount": positive_number(line_total),
            })

    def aggregate_rows(rows, key_name, label_name):
        aggregated = {}

        for row in rows:
            key = str(row.get(key_name) or "Без названия")
            label = str(row.get(label_name) or "Без названия")

            if key not in aggregated:
                aggregated[key] = {
                    "name": label,
                    "quantity": 0.0,
                    "amount": 0.0,
                    "operations": 0,
                }

            aggregated[key]["quantity"] += positive_number(
                row.get("quantity")
            )
            aggregated[key]["amount"] += positive_number(
                row.get("amount")
            )
            aggregated[key]["operations"] += 1

        result = sorted(
            aggregated.values(),
            key=lambda item: (-item["quantity"], item["name"].casefold()),
        )
        max_quantity = result[0]["quantity"] if result else 0

        for item in result:
            item["quantity_display"] = format_stock_number(item["quantity"])
            item["amount_display"] = format_sale_money(item["amount"])
            item["bar_width"] = (
                round(item["quantity"] / max_quantity * 100, 1)
                if max_quantity > 0
                else 0
            )

        return result

    sales_by_product = aggregate_rows(
        filtered_sales,
        "product_key",
        "product_name",
    )
    receipts_by_product = aggregate_rows(
        receipt_product_rows,
        "product_key",
        "product_name",
    )
    sales_by_source = aggregate_rows(
        filtered_sales,
        "source",
        "source",
    )

    products = []

    for item in warehouse_items if isinstance(warehouse_items, list) else []:
        stock = to_float(item.get("stock"))
        products.append({
            "name": str(item.get("name") or "Без названия"),
            "article": str(item.get("article") or ""),
            "category": str(item.get("category") or "Без категории"),
            "stock": stock,
            "stock_display": format_stock_number(stock),
        })

    products.sort(key=lambda item: (-item["stock"], item["name"].casefold()))

    sales_quantity = sum(row["quantity"] for row in filtered_sales)
    sales_revenue = sum(row["amount"] for row in filtered_sales)
    receipts_quantity = sum(
        positive_number(receipt.get("total_quantity"))
        for receipt in filtered_receipts
    )
    receipts_amount = sum(
        positive_number(receipt.get("total_amount"))
        for receipt in filtered_receipts
    )
    total_stock = sum(item["stock"] for item in products)

    return {
        "period": period,
        "period_label": period_labels[period],
        "sales": {
            "rows": len(filtered_sales),
            "quantity": format_stock_number(sales_quantity),
            "revenue": format_sale_money(sales_revenue),
            "products": len(sales_by_product),
            "top_products": sales_by_product[:10],
            "sources": sales_by_source[:8],
        },
        "receipts": {
            "operations": len(filtered_receipts),
            "quantity": format_stock_number(receipts_quantity),
            "amount": format_sale_money(receipts_amount),
            "products": len(receipts_by_product),
            "top_products": receipts_by_product[:10],
        },
        "products": {
            "positions": len(products),
            "in_stock": sum(1 for item in products if item["stock"] > 0),
            "out_of_stock": sum(1 for item in products if item["stock"] <= 0),
            "total_stock": format_stock_number(total_stock),
            "top_stock": products[:10],
        },
    }


@app.route("/analytics")
def analytics_page():
    analytics = build_analytics_data(
        sales_records=build_sales_report_records(),
        receipts=load_receipts(),
        warehouse_items=get_warehouse_items(),
        requested_period=(request.args.get("period") or "30").strip(),
    )

    return render_template(
        "analytics.html",
        analytics=analytics,
    )


DEFAULT_APP_SETTINGS = {
    "company_name": "Tictactoy",
    "erp_name": "Vechasu ERP",
    "low_stock_threshold": 3,
}


NAVIGATION_DEFINITIONS = [
    {
        "key": "orders",
        "label": "Заказы",
        "description": "Заказы интернет-магазина и карточки заказов.",
        "icon": "📦",
        "href": "/",
        "position": 1,
        "active_exact": ["/"],
        "active_prefixes": ["/order"],
    },
    {
        "key": "products",
        "label": "Товары",
        "description": "Каталог, остатки, ячейки и управление товарами.",
        "icon": "🏷",
        "href": "/warehouse",
        "position": 2,
        "active_exact": [],
        "active_prefixes": ["/warehouse"],
    },
    {
        "key": "sales",
        "label": "Продажи",
        "description": "Ручные продажи, источники и отчёты.",
        "icon": "💰",
        "href": "/sales",
        "position": 3,
        "active_exact": [],
        "active_prefixes": ["/sales"],
    },
    {
        "key": "receipts",
        "label": "Приход",
        "description": "Оформление и проведение поступлений товара.",
        "icon": "📥",
        "href": "/receipts",
        "position": 4,
        "active_exact": [],
        "active_prefixes": ["/receipts"],
    },
    {
        "key": "analytics",
        "label": "Аналитика",
        "description": "Продажи, приходы и текущие остатки товаров.",
        "icon": "📊",
        "href": "/analytics",
        "position": 5,
        "active_exact": [],
        "active_prefixes": ["/analytics"],
    },
    {
        "key": "stock_operations",
        "label": "Журнал операций",
        "description": "История складских движений и операций.",
        "icon": "📒",
        "href": "/stock-operations",
        "position": 6,
        "active_exact": [],
        "active_prefixes": ["/stock-operations"],
    },
    {
        "key": "repair",
        "label": "Ремонт",
        "description": "Учёт ремонтных обращений и статусов.",
        "icon": "🛠",
        "href": "/repair",
        "position": 7,
        "active_exact": [],
        "active_prefixes": ["/repair"],
    },
    {
        "key": "settings",
        "label": "Настройки",
        "description": "Управление компанией, системой и вкладками.",
        "icon": "⚙️",
        "href": "/settings",
        "position": 8,
        "active_exact": [],
        "active_prefixes": ["/settings"],
        "required": True,
    },
]


def get_navigation_settings_path():
    path = PROJECT_ROOT / "instance" / "navigation_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_default_navigation_settings():
    return {
        item["key"]: {
            "enabled": True,
            "position": item["position"],
        }
        for item in NAVIGATION_DEFINITIONS
    }


def load_navigation_settings():
    settings = get_default_navigation_settings()
    path = get_navigation_settings_path()

    if not path.exists():
        return settings

    try:
        stored_settings = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return settings

    if not isinstance(stored_settings, dict):
        return settings

    for item in NAVIGATION_DEFINITIONS:
        key = item["key"]
        stored_item = stored_settings.get(key)

        if not isinstance(stored_item, dict):
            continue

        enabled = bool(
            stored_item.get(
                "enabled",
                settings[key]["enabled"],
            )
        )

        try:
            position = int(
                stored_item.get(
                    "position",
                    settings[key]["position"],
                )
            )
        except (TypeError, ValueError):
            position = settings[key]["position"]

        settings[key] = {
            "enabled": enabled,
            "position": max(1, position),
        }

    # Настройки нельзя скрыть, иначе пользователь потеряет
    # доступ к управлению вкладками.
    settings["settings"]["enabled"] = True

    return settings


def save_navigation_settings(settings):
    path = get_navigation_settings_path()
    normalized_settings = get_default_navigation_settings()

    for item in NAVIGATION_DEFINITIONS:
        key = item["key"]
        source = settings.get(key, {})

        enabled = bool(source.get("enabled", True))

        if item.get("required"):
            enabled = True

        try:
            position = int(
                source.get("position", item["position"])
            )
        except (TypeError, ValueError):
            position = item["position"]

        normalized_settings[key] = {
            "enabled": enabled,
            "position": max(1, position),
        }

    temporary_path = path.with_suffix(".json.tmp")

    temporary_path.write_text(
        json.dumps(
            normalized_settings,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(path)


def get_navigation_items(include_disabled=False):
    navigation_settings = load_navigation_settings()
    current_path = request.path
    items = []

    for definition in NAVIGATION_DEFINITIONS:
        key = definition["key"]
        item_settings = navigation_settings.get(key, {})

        enabled = bool(item_settings.get("enabled", True))

        if definition.get("required"):
            enabled = True

        if not include_disabled and not enabled:
            continue

        item = dict(definition)
        item["enabled"] = enabled
        item["position"] = item_settings.get(
            "position",
            definition["position"],
        )

        item["active"] = (
            current_path in definition.get("active_exact", [])
            or any(
                current_path.startswith(prefix)
                for prefix in definition.get(
                    "active_prefixes",
                    [],
                )
            )
        )

        items.append(item)

    return sorted(
        items,
        key=lambda item: (
            item["position"],
            item["label"],
        ),
    )


@app.context_processor
def inject_sidebar_navigation():
    return {
        "sidebar_navigation_items": get_navigation_items(),
    }


def get_app_settings_path():
    path = PROJECT_ROOT / "instance" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_app_settings():
    settings = DEFAULT_APP_SETTINGS.copy()
    path = get_app_settings_path()

    if not path.exists():
        return settings

    try:
        stored_settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings

    if isinstance(stored_settings, dict):
        settings.update(stored_settings)

    return settings


def save_app_settings(settings):
    path = get_app_settings_path()
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    settings = load_app_settings()
    navigation_settings = load_navigation_settings()

    if request.method == "POST":
        company_name = (
            request.form.get("company_name") or ""
        ).strip()

        erp_name = (
            request.form.get("erp_name") or ""
        ).strip()

        try:
            low_stock_threshold = int(
                request.form.get("low_stock_threshold") or 0
            )
        except ValueError:
            low_stock_threshold = 0

        low_stock_threshold = max(
            0,
            min(low_stock_threshold, 999),
        )

        settings = {
            "company_name": company_name or "Tictactoy",
            "erp_name": erp_name or "Vechasu ERP",
            "low_stock_threshold": low_stock_threshold,
        }

        updated_navigation_settings = {}

        for item in NAVIGATION_DEFINITIONS:
            key = item["key"]
            current_item = navigation_settings.get(key, {})

            if item.get("required"):
                enabled = True
            else:
                enabled = (
                    request.form.get(
                        f"navigation_{key}"
                    )
                    == "on"
                )

            updated_navigation_settings[key] = {
                "enabled": enabled,
                "position": current_item.get(
                    "position",
                    item["position"],
                ),
            }

        save_app_settings(settings)
        save_navigation_settings(
            updated_navigation_settings
        )

        return redirect(
            "/settings?notice=success"
            "&message=Настройки сохранены"
        )

    return render_template(
        "settings.html",
        settings=settings,
        navigation_items=get_navigation_items(
            include_disabled=True
        ),
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
