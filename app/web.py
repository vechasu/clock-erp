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
            or query_lower in (item.get("code") or "").lower()
            or query_lower in (item.get("category") or "").lower()
            or query_lower in (item.get("cell") or "").lower()
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
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip()
    article = request.form.get("article", "").strip()

    if not name or not code:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название и код обязательны"
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
    code = request.form.get("code", "").strip()
    article = request.form.get("article", "").strip()

    if not product_id:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не найден ID товара"
        ))

    if not name or not code:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название и код обязательны"
        ))

    try:
        client = MoySkladClient()
        result = client.update_product(
            product_id=product_id,
            name=name,
            code=code,
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
    code = request.form.get("code", "").strip()

    if not code:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не указан код товара"
        ))

    try:
        client = MoySkladClient()
        product = client.find_product_by_code(code)

        if not product:
            return redirect(url_for(
                "warehouse_page",
                notice="error",
                message="Товар не найден в МойСклад"
            ))

        result = client.archive_product(product.get("id"))

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


@app.route("/repair")
def repair_page():
    from flask import render_template, request

    q = (request.args.get("q") or "").strip().lower()
    notice = request.args.get("notice") or ""
    message = request.args.get("message") or ""

    cases = load_repair_cases()
    cases = sorted(cases, key=lambda item: item.get("created_at", ""), reverse=True)

    if q:
        cases = [
            case for case in cases
            if q in " ".join([
                str(case.get("comment") or ""),
                str(case.get("order_info") or ""),
                str(case.get("communication") or ""),
                str(case.get("problem") or ""),
                str(case.get("status") or ""),
            ]).lower()
        ]

    return render_template(
        "repair.html",
        cases=cases,
        q=q,
        notice=notice,
        message=message,
        status_labels={
            "new": "Новый",
            "waiting": "Ждём",
            "in_progress": "В работе",
            "done": "Готово",
        },
    )


@app.route("/repair/add", methods=["POST"])
def repair_add():
    from flask import request, redirect
    from datetime import datetime
    import uuid

    cases = load_repair_cases()

    comment = (request.form.get("comment") or "").strip()

    if not comment:
        return redirect("/repair?notice=error&message=Комментарий обязателен")

    cases.append({
        "id": str(uuid.uuid4()),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "comment": comment,
        "order_info": (request.form.get("order_info") or "").strip(),
        "communication": (request.form.get("communication") or "").strip(),
        "problem": (request.form.get("problem") or "").strip(),
        "status": (request.form.get("status") or "new").strip(),
    })

    save_repair_cases(cases)

    return redirect("/repair?notice=success&message=Ремонт добавлен")


@app.route("/repair/status", methods=["POST"])
def repair_status():
    from flask import request, redirect

    case_id = (request.form.get("case_id") or "").strip()
    status = (request.form.get("status") or "new").strip()

    cases = load_repair_cases()

    for case in cases:
        if case.get("id") == case_id:
            case["status"] = status
            break

    save_repair_cases(cases)

    return redirect("/repair?notice=success&message=Статус обновлён")


@app.route("/repair/delete", methods=["POST"])
def repair_delete():
    from flask import request, redirect

    case_id = (request.form.get("case_id") or "").strip()

    cases = [case for case in load_repair_cases() if case.get("id") != case_id]
    save_repair_cases(cases)

    return redirect("/repair?notice=success&message=Ремонт удалён")




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


def parse_manual_sale_quantity(value):
    try:
        quantity = int(str(value or "").strip())
    except Exception:
        return 0

    return quantity if quantity in {1, 2, 3} else 0

def normalize_manual_sale_source(value, custom_value=""):
    source = str(value or "").strip()
    custom_source = str(custom_value or "").strip()

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
                message="Выберите количество: 1, 2 или 3",
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
        "product_name": product_name,
        "quantity": quantity,
        "order_number": (
            request.form.get("order_number") or ""
        ).strip(),
        "track_number": (
            request.form.get("track_number") or ""
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
                message="Выберите количество: 1, 2 или 3",
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
        sale["product_name"] = product_name
        sale["quantity"] = quantity
        sale["order_number"] = (
            request.form.get("order_number") or ""
        ).strip()
        sale["track_number"] = (
            request.form.get("track_number") or ""
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


@app.route("/sales")
def sales_page():
    from flask import request

    operations = load_stock_operations()
    stored_manual_sales = load_manual_sales()

    automatic_sales = []
    manual_sales = []
    total_quantity = 0

    for operation in operations:
        source = str(operation.get("source") or "")
        operation_type = str(operation.get("type") or "")

        if source != "Заказ Битрикс":
            continue

        if operation_type not in ["writeoff", "loss"]:
            continue

        try:
            quantity_number = float(operation.get("quantity") or 0)
        except Exception:
            quantity_number = 0

        total_quantity += quantity_number

        order_id = str(operation.get("order_id") or "")
        order_number = str(
            operation.get("order_number") or order_id or ""
        )

        automatic_sales.append({
            "id": "",
            "is_manual": False,
            "created_at": operation.get("created_at") or "",
            "source": "Битрикс",
            "order_id": order_id,
            "order_number": order_number,
            "product_name": operation.get("product_name") or "",
            "bitrix_product_name": (
                operation.get("bitrix_product_name") or ""
            ),
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
            "track_number": (
                operation.get("track_number")
                or operation.get("shipment_number")
                or ""
            ),
            "region": operation.get("region") or "",
            "note": operation.get("reason") or "",
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
            "product_name": stored_sale.get("product_name") or "",
            "bitrix_product_name": "",
            "quantity": format_stock_number(quantity_number),
            "quantity_value": quantity_number,
            "track_number": stored_sale.get("track_number") or "",
            "region": stored_sale.get("region") or "",
            "note": stored_sale.get("note") or "",
            "document_name": "",
            "document_url": "",
            "status": "",
        })

    sales = manual_sales + automatic_sales

    unique_orders = set()

    for sale in sales:
        order_number = str(sale.get("order_number") or "").strip()

        if order_number:
            unique_orders.add(order_number)

    return render_template(
        "sales.html",
        sales=sales,
        total_sales=len(sales),
        total_orders=len(unique_orders),
        total_quantity=format_stock_number(total_quantity),
        notice=(request.args.get("notice") or "").strip(),
        message=(request.args.get("message") or "").strip(),
    )

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
