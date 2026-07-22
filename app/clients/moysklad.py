import base64

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

    # === RECEIPT DOCUMENT ACTIONS CLIENT V1 ===
    def delete(self, endpoint):
        response = requests.delete(
            f"{self.BASE_URL}{endpoint}",
            headers=self.headers,
            timeout=8,
        )

        if response.status_code == 404:
            return True

        if response.status_code >= 400:
            print("Delete error:", response.status_code)
            print(response.text)
            return False

        return True
    # === RECEIPT DOCUMENT ACTIONS CLIENT V1 END ===


    def archive_product(self, product_id):
        return self.put(
            f"/entity/product/{product_id}",
            {"archived": True}
        )

    def update_product(
        self,
        product_id,
        name=None,
        code=None,
        article=None,
        product_folder=None,
        archived=None,
    ):
        payload = {}

        if name is not None:
            payload["name"] = name

        if code is not None:
            payload["code"] = code

        if article is not None:
            payload["article"] = article

        if product_folder and product_folder.get("meta"):
            payload["productFolder"] = {
                "meta": product_folder["meta"],
            }

        if archived is not None:
            payload["archived"] = bool(archived)

        return self.put(
            f"/entity/product/{product_id}",
            payload
        )

    def get_product_images(self, product_id, limit=1):
        product_id = str(product_id or "").strip()

        if not product_id:
            raise ValueError("Не указан ID товара")

        response = self.get(
            f"/entity/product/{product_id}/images",
            params={"limit": limit},
        )

        if response is None:
            raise ValueError(
                "Не удалось проверить фотографии товара в МойСклад"
            )

        if isinstance(response, dict):
            rows = response.get("rows", [])
            return rows if isinstance(rows, list) else []

        return response if isinstance(response, list) else []

    def product_has_images(self, product_id):
        return bool(self.get_product_images(product_id, limit=1))

    def download_product_thumbnail(self, product_id):
        images = self.get_product_images(product_id, limit=1)

        if not images:
            return None

        image = images[0] if isinstance(images[0], dict) else {}
        miniature = image.get("miniature") or image.get("tiny") or {}

        if not isinstance(miniature, dict):
            return None

        meta = miniature.get("meta") or {}
        url = (
            miniature.get("downloadHref")
            or miniature.get("href")
            or meta.get("downloadHref")
            or meta.get("href")
        )

        if not url:
            return None

        response = requests.get(
            url,
            headers=self.headers,
            timeout=8,
            allow_redirects=True,
        )
        response.raise_for_status()

        content = response.content
        content_type = str(
            response.headers.get("Content-Type") or ""
        ).split(";", 1)[0].strip().lower()

        if not content or not content_type.startswith("image/"):
            return None

        if len(content) > 2 * 1024 * 1024:
            raise ValueError("Миниатюра товара слишком большая")

        return content, content_type

    def upload_product_image(self, product_id, filename, content):
        product_id = str(product_id or "").strip()

        if not product_id:
            raise ValueError("Не указан ID товара")

        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ValueError("Файл изображения пуст")

        return self.put(
            f"/entity/product/{product_id}",
            {
                "images": [
                    {
                        "filename": str(filename or "product.jpg"),
                        "content": base64.b64encode(content).decode("ascii"),
                    }
                ]
            },
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


    def build_stock_enter_payload(
        self,
        positions,
        reason=None,
        moment=None,
    ):
        organization = self.get_default_organization()
        store = self.get_default_store()

        if not organization:
            raise ValueError(
                "В МойСклад не найдена организация"
            )

        if not store:
            raise ValueError(
                "В МойСклад не найден склад"
            )

        prepared_positions = []

        for position in positions:
            product_id = str(
                position.get("product_id") or ""
            ).strip()

            try:
                quantity = float(
                    position.get("quantity") or 0
                )
                purchase_price = float(
                    position.get("purchase_price") or 0
                )
            except (TypeError, ValueError):
                raise ValueError(
                    "Количество и закупочная цена "
                    "должны быть числами"
                )

            if not product_id:
                raise ValueError(
                    "У позиции отсутствует ID товара"
                )

            if quantity <= 0:
                raise ValueError(
                    "Количество товара должно быть "
                    "больше нуля"
                )

            if purchase_price < 0:
                raise ValueError(
                    "Закупочная цена не может быть "
                    "отрицательной"
                )

            prepared_positions.append({
                "quantity": quantity,
                "price": int(
                    round(purchase_price * 100)
                ),
                "overhead": 0,
                "reason": (
                    position.get("reason")
                    or reason
                    or "Приход из Vechasu ERP"
                ),
                "assortment": {
                    "meta": self.get_product_meta(
                        product_id
                    )
                },
            })

        if not prepared_positions:
            raise ValueError(
                "В приходе нет товаров"
            )

        payload = {
            "applicable": True,
            "description": (
                reason
                or "Приход из Vechasu ERP"
            ),
            "organization": {
                "meta": organization["meta"]
            },
            "store": {
                "meta": store["meta"]
            },
            "positions": prepared_positions,
        }

        moment_value = str(
            moment or ""
        ).strip()

        if moment_value:
            if len(moment_value) == 10:
                moment_value += " 00:00:00.000"

            payload["moment"] = moment_value

        return payload


    def create_stock_enter_many(
        self,
        positions,
        reason=None,
        moment=None,
    ):
        payload = self.build_stock_enter_payload(
            positions=positions,
            reason=reason,
            moment=moment,
        )

        return self.post(
            "/entity/enter",
            payload,
        )


    # === RECEIPT DOCUMENT ACTIONS CLIENT V1 ===
    def update_stock_enter_many(
        self,
        document_id,
        positions,
        reason=None,
        moment=None,
    ):
        document_id = str(
            document_id or ""
        ).strip()

        if not document_id:
            raise ValueError(
                "Не указан ID документа прихода"
            )

        payload = self.build_stock_enter_payload(
            positions=positions,
            reason=reason,
            moment=moment,
        )

        return self.put(
            f"/entity/enter/{document_id}",
            payload,
        )


    def delete_stock_enter(self, document_id):
        document_id = str(
            document_id or ""
        ).strip()

        if not document_id:
            raise ValueError(
                "Не указан ID документа прихода"
            )

        return self.delete(
            f"/entity/enter/{document_id}"
        )
    # === RECEIPT DOCUMENT ACTIONS CLIENT V1 END ===


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

    @staticmethod
    def normalize_product_folder_path(value):
        parts = [
            part.strip()
            for part in str(value or "").replace("\\", "/").split("/")
            if part.strip()
        ]

        return "/".join(parts).lower()


    def get_product_folders(self):
        response = self.get(
            "/entity/productfolder",
            params={"limit": 1000},
        )

        if not response:
            return []

        return response.get("rows", [])


    def find_product_folder_by_path(self, folder_path, folders=None):
        target = self.normalize_product_folder_path(folder_path)

        if not target:
            return None

        if folders is None:
            folders = self.get_product_folders()

        for folder in folders:
            name = str(folder.get("name") or "").strip()
            path_name = str(folder.get("pathName") or "").strip()

            full_path = "/".join(
                part
                for part in (path_name, name)
                if part
            )

            possible_paths = {
                self.normalize_product_folder_path(path_name),
                self.normalize_product_folder_path(full_path),
            }

            if target in possible_paths:
                return folder

        return None


    def get_or_create_product_folder(self, folder_path):
        parts = [
            part.strip()
            for part in str(folder_path or "").replace("\\", "/").split("/")
            if part.strip()
        ]

        if not parts:
            return None

        folders = self.get_product_folders()
        parent_folder = None
        current_parts = []

        for part in parts:
            current_parts.append(part)
            current_path = "/".join(current_parts)

            folder = self.find_product_folder_by_path(
                current_path,
                folders=folders,
            )

            if not folder:
                payload = {
                    "name": part,
                }

                if parent_folder and parent_folder.get("meta"):
                    payload["productFolder"] = {
                        "meta": parent_folder["meta"],
                    }

                folder = self.post(
                    "/entity/productfolder",
                    payload,
                )

                if not folder:
                    raise ValueError(
                        "МойСклад не создал папку товара: "
                        + current_path
                    )

                folders.append(folder)

            parent_folder = folder

        return parent_folder


    def create_product(
        self,
        name,
        code,
        article=None,
        product_folder=None,
        image=None,
    ):
        payload = {
            "name": name,
            "code": code,
        }

        if article:
            payload["article"] = article

        if product_folder and product_folder.get("meta"):
            payload["productFolder"] = {
                "meta": product_folder["meta"],
            }

        if image:
            image_content = image.get("content")

            if not isinstance(image_content, (bytes, bytearray)):
                raise ValueError("Некорректный файл изображения")

            payload["images"] = [
                {
                    "filename": str(
                        image.get("filename") or "product.jpg"
                    ),
                    "content": base64.b64encode(image_content).decode(
                        "ascii"
                    ),
                }
            ]

        response = requests.post(
            f"{self.BASE_URL}/entity/product",
            headers=self.headers,
            json=payload,
            timeout=8,
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
