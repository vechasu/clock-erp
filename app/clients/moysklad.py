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