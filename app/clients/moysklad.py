import requests
from app.config import MOYSKLAD_TOKEN


class MoySkladClient:
    BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {MOYSKLAD_TOKEN}",
            "Accept": "application/json;charset=utf-8",
            "Content-Type": "application/json;charset=utf-8",
        }

    def get(self, endpoint, params=None):
        response = requests.get(
            f"{self.BASE_URL}{endpoint}",
            headers=self.headers,
            params=params,
            timeout=8,
        )

        if response.status_code >= 400:
            print("Error:", response.status_code)
            print(response.text)
            return None

        return response.json()

    def post(self, endpoint, payload):
        url = f"{self.BASE_URL}{endpoint}"

        response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=8
        )

        response.raise_for_status()
        return response.json()

    def put(self, endpoint, payload):
        response = requests.put(
            f"{self.BASE_URL}{endpoint}",
            headers=self.headers,
            json=payload,
            timeout=8,
        )

        if response.status_code >= 400:
            print("Error:", response.status_code)
            print(response.text)
            return None

        return response.json()

    def archive_product(self, product_id):
        return self.put(
            f"/entity/product/{product_id}",
            {"archived": True}
        )

    def update_product(self, product_id, name=None, code=None, article=None):
        payload = {}

        if name is not None:
            payload["name"] = name

        if code is not None:
            payload["code"] = code

        if article is not None:
            payload["article"] = article

        return self.put(
            f"/entity/product/{product_id}",
            payload
        )

    def get_products(self, limit=10):
        data = self.get("/entity/product", params={"limit": limit})
        if not data:
            return []

        products = data.get("rows", [])

        for product in products:
            print(
                product.get("name"),
                "| code:",
                product.get("code"),
                "| article:",
                product.get("article", "no article"),
            )

        return products

    def find_product_by_code(self, code):
        data = self.get(
            "/entity/product",
            params={"filter": f"code={code}", "limit": 1},
        )

        if not data or not data.get("rows"):
            print("Product not found")
            return None

        product = data["rows"][0]

        print("Product found:")
        print("Name:", product.get("name"))
        print("Code:", product.get("code"))
        print("Article:", product.get("article", "no article"))
        print("ID:", product.get("id"))

        return product



    def get_product_metadata(self):
        return self.get("/entity/product/metadata")

    def get_product_attributes(self):
        response = self.get("/entity/product/metadata/attributes")

        if isinstance(response, dict):
            rows = response.get("rows", [])

            if isinstance(rows, list):
                return rows

            return []

        if isinstance(response, list):
            return response

        return []

    def find_product_attribute(self, name):
        target_name = str(name or "").strip().lower()

        for attribute in self.get_product_attributes():
            if not isinstance(attribute, dict):
                continue

            attribute_name = str(attribute.get("name") or "").strip().lower()

            if attribute_name == target_name:
                return attribute

        return None

    def create_product_string_attribute(self, name):
        return self.post(
            "/entity/product/metadata/attributes",
            {
                "name": name,
                "type": "string",
                "required": False
            }
        )

    def get_or_create_product_cell_attribute(self):
        attribute_name = "Ячейка склада"

        attribute = self.find_product_attribute(attribute_name)

        if attribute:
            return attribute

        return self.create_product_string_attribute(attribute_name)

    def update_product_cell_attribute(self, product_id, cell):
        attribute = self.get_or_create_product_cell_attribute()

        return self.put(
            f"/entity/product/{product_id}",
            {
                "attributes": [
                    {
                        "meta": attribute["meta"],
                        "name": attribute.get("name"),
                        "type": attribute.get("type", "string"),
                        "value": str(cell or "")
                    }
                ]
            }
        )



    def get_first_row(self, endpoint):
        data = self.get(endpoint, params={"limit": 1})

        if not data:
            return None

        rows = data.get("rows", [])

        if not rows:
            return None

        return rows[0]

    def get_default_organization(self):
        return self.get_first_row("/entity/organization")

    def get_default_store(self):
        return self.get_first_row("/entity/store")

    def get_product_meta(self, product_id):
        return {
            "href": f"{self.BASE_URL}/entity/product/{product_id}",
            "metadataHref": f"{self.BASE_URL}/entity/product/metadata",
            "type": "product",
            "mediaType": "application/json",
        }

    def create_stock_loss(self, product_id, quantity, reason=None):
        organization = self.get_default_organization()
        store = self.get_default_store()

        if not organization:
            raise ValueError("В МойСклад не найдена организация")

        if not store:
            raise ValueError("В МойСклад не найден склад")

        payload = {
            "applicable": True,
            "description": reason or "Списание из ТТТ ERP",
            "organization": {
                "meta": organization["meta"]
            },
            "store": {
                "meta": store["meta"]
            },
            "positions": [
                {
                    "quantity": float(quantity),
                    "reason": reason or "Изменение остатка из ТТТ ERP",
                    "assortment": {
                        "meta": self.get_product_meta(product_id)
                    }
                }
            ]
        }

        return self.post("/entity/loss", payload)

    def create_stock_enter(self, product_id, quantity, reason=None):
        organization = self.get_default_organization()
        store = self.get_default_store()

        if not organization:
            raise ValueError("В МойСклад не найдена организация")

        if not store:
            raise ValueError("В МойСклад не найден склад")

        payload = {
            "applicable": True,
            "description": reason or "Оприходование из ТТТ ERP",
            "organization": {
                "meta": organization["meta"]
            },
            "store": {
                "meta": store["meta"]
            },
            "positions": [
                {
                    "quantity": float(quantity),
                    "price": 0,
                    "overhead": 0,
                    "reason": reason or "Изменение остатка из ТТТ ERP",
                    "assortment": {
                        "meta": self.get_product_meta(product_id)
                    }
                }
            ]
        }

        return self.post("/entity/enter", payload)


    def get_stock(self, limit=20):
        data = self.get("/report/stock/all", params={"limit": limit})
        if not data:
            return []

        rows = data.get("rows", [])

        for row in rows:
            print(row.get("name"), "| stock:", row.get("stock"))

        return rows

    def find_stock_by_name(self, product_name):
        data = self.get("/report/stock/all", params={"limit": 1000})
        if not data:
            return None

        rows = data.get("rows", [])
        query = product_name.lower()

        for row in rows:
            name = row.get("name", "")
            stock = row.get("stock")

            if query in name.lower():
                print("Stock found:")
                print("Name:", name)
                print("Stock:", stock)
                return row

        print("Stock not found")
        return None
    def create_product(self, name, code, article=None):
        payload = {
            "name": name,
            "code": code,
        }

        if article:
            payload["article"] = article

        response = requests.post(
            f"{self.BASE_URL}/entity/product",
            headers=self.headers,
            json=payload,
        )

        print("Status:", response.status_code)

        if response.status_code >= 400:
            print("Error:", response.text)
            return None

        product = response.json()

        print("Product created:")
        print("Name:", product.get("name"))
        print("Code:", product.get("code"))
        print("Article:", product.get("article", "no article"))
        print("ID:", product.get("id"))

        return product