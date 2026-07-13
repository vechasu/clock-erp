import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import json
import requests
from app.clients.moysklad import MoySkladClient
from flask import Flask, render_template, request, redirect, url_for

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
    selected_cell = request.args.get("cell", "").strip()

    all_items = get_warehouse_items(force=request.args.get("refresh") == "1")
    category_tree = build_category_tree(all_items)
    cell_groups = build_cell_groups(all_items)

    items = all_items

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

    total_stock = sum(float(item.get("stock") or 0) for item in items)
    total_reserve = sum(float(item.get("reserve") or 0) for item in items)
    total_available = sum(float(item.get("quantity") or 0) for item in items)

    print("CATEGORY TREE:", category_tree)

    return render_template(
        "warehouse.html",
        items=items,
        query=query,
        selected_category=selected_category,
        selected_cell=selected_cell,
        category_tree=category_tree,
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
    code = f"VECHASU-{uuid.uuid4().hex[:12].upper()}"

    if not name:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название товара обязательно"
        ))

    try:
        client = MoySkladClient()
        product = client.create_product(name=name, code=code, article=article or None)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if product:
            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция добавлена в МойСклад"
            ))

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не создал позицию"
        ))

    except Exception as error:
        print(f"Ошибка добавления позиции: {error}")
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка добавления позиции"
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

    if not product_id:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    try:
        client = MoySkladClient()
        result = client.archive_product(product_id)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if result:
            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция убрана в архив МойСклад"
            ))

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не убрал позицию"
        ))

    except Exception as error:
        print(f"Ошибка архивации позиции: {error}")
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
            category = product.get("pathName") or "Без категории"

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

            items.append({
                "id": product_id,
                "moysklad_url": (
                    product.get("meta", {}).get("uuidHref")
                    or f"https://online.moysklad.ru/app/#good/edit?id={product_id}"
                ),
                "name": name,
                "article": article,
                "code": code,
                "category": category,
                "cell": cell,
                "cell_source": cell_source,
                "cell_source_label": format_cell_source(cell_source),
                "cell_source_path": cell_source_path,
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


def parse_manual_sale_quantity(value):
    try:
        quantity = int(str(value or "").strip())
    except Exception:
        return 0

    return quantity if 1 <= quantity <= 25 else 0

def normalize_manual_sale_source(value, custom_value=""):
    source = str(value or "").strip()
    custom_source = str(custom_value or "").strip()

    if source in {"Битрикс", "Заказ Битрикс"}:
        return "Tictactoy"

    if source == "__custom__":
        return custom_source or "Свой вариант"

    return source or "Свой вариант"

@app.route("/sales/manual/add", methods=["POST"])
def manual_sale_add():
    from datetime import date
    from uuid import uuid4
    from flask import request, redirect, url_for

    product_name = (request.form.get("product_name") or "").strip()
    quantity = parse_manual_sale_quantity(request.form.get("quantity"))

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
        "quantity": quantity,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "track_number": (
            request.form.get("track_number") or ""
        ).strip(),
        "delivery_method": (
            request.form.get("delivery_method") or ""
        ).strip(),
        "region": (
            request.form.get("region") or ""
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
        sale["quantity"] = quantity
        sale["order_number"] = (
            request.form.get("order_number") or ""
        ).strip()
        sale["track_number"] = (
            request.form.get("track_number") or ""
        ).strip()
        sale["delivery_method"] = (
            request.form.get("delivery_method") or ""
        ).strip()
        sale["region"] = (
            request.form.get("region") or ""
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

    overrides = load_automatic_sales_overrides()

    overrides[operation_id] = {
        "created_at": (
            request.form.get("created_at") or ""
        ).strip(),
        "source": (
            request.form.get("source") or "Tictactoy"
        ).strip(),
        "product_name": product_name,
        "quantity": quantity,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "track_number": (
            request.form.get("track_number") or ""
        ).strip(),
        "delivery_method": (
            request.form.get("delivery_method") or ""
        ).strip(),
        "region": (
            request.form.get("region") or ""
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


@app.route("/sales")
def sales_page():
    from flask import request

    operations = load_stock_operations()
    stored_manual_sales = load_manual_sales()
    automatic_overrides = load_automatic_sales_overrides()

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
            "bitrix_product_name": (
                operation.get("bitrix_product_name") or ""
            ),
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
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

        total_quantity += quantity_number

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
            "bitrix_product_name": "",
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
            "track_number": stored_sale.get("track_number") or "",
            "delivery_method": stored_sale.get("delivery_method") or "",
            "region": stored_sale.get("region") or "",
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
            "category": item.get("category") or "",
            "stock": item.get("stock") or 0,
            "stock_display": item.get("stock_display") or "0",
        }
        for item in get_warehouse_items()
        if float(item.get("stock") or 0) > 0
    ]

    return render_template(
        "sales.html",
        sales=sales,
        warehouse_items=warehouse_items,
        russian_regions=RUSSIAN_REGIONS,
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
            "category": item.get("category") or "",
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
    )


@app.route("/receipts/create", methods=["POST"])
def receipt_create():
    from datetime import datetime
    from flask import request, redirect, url_for
    import uuid

    supplier = (request.form.get("supplier") or "").strip()
    invoice_number = (request.form.get("invoice_number") or "").strip()
    receipt_date = (
        request.form.get("receipt_date")
        or datetime.now().strftime("%Y-%m-%d")
    ).strip()
    note = (request.form.get("note") or "").strip()

    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    purchase_prices = request.form.getlist("purchase_price")

    if not supplier:
        return redirect(url_for(
            "receipts_page",
            notice="error",
            message="Укажите поставщика",
        ))

    catalog = {
        str(item.get("id") or ""): item
        for item in get_warehouse_items(force=True)
    }

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

        positions.append({
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

    reason_parts = [
        f"Vechasu ERP: приход {receipt_number}",
        f"Поставщик: {supplier}",
    ]

    if invoice_number:
        reason_parts.append(f"Накладная: {invoice_number}")

    if note:
        reason_parts.append(f"Комментарий: {note}")

    reason = ". ".join(reason_parts)

    try:
        client = MoySkladClient()
        moysklad_document = client.create_stock_enter_many(
            positions=positions,
            reason=reason,
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
            "supplier": supplier,
            "invoice_number": invoice_number,
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
                "supplier": supplier,
                "invoice_number": invoice_number,
                "purchase_price": position["purchase_price"],
                "moysklad_document_id": receipt["moysklad_document_id"],
                "moysklad_document_name": receipt["moysklad_document_name"],
                "moysklad_document_url": receipt["moysklad_document_url"],
            })

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        return redirect(url_for(
            "receipts_page",
            notice="success",
            message=f"Приход {receipt_number} проведён",
        ))

    except Exception as error:
        print(f"Ошибка проведения прихода: {error}")

        return redirect(url_for(
            "receipts_page",
            notice="error",
            message=f"Ошибка проведения прихода: {error}",
        ))


DEFAULT_APP_SETTINGS = {
    "company_name": "Tictactoy",
    "erp_name": "Vechasu ERP",
    "low_stock_threshold": 3,
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

    if request.method == "POST":
        company_name = (
            request.form.get("company_name") or ""
        ).strip()

        erp_name = (
            request.form.get("erp_name") or ""
        ).strip()

        currency = (
            request.form.get("currency") or "RUB"
        ).strip()

        timezone = (
            request.form.get("timezone") or "Europe/Moscow"
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

        save_app_settings(settings)

        return redirect(
            "/settings?notice=success"
            "&message=Настройки сохранены"
        )

    return render_template(
        "settings.html",
        settings=settings,
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
