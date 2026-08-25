import base64
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import requests
from app.config import MOYSKLAD_TOKEN


LOGGER = logging.getLogger(__name__)
MOYSKLAD_TRUSTED_ORIGINS = {
    ("https", "api.moysklad.ru", 443),
    ("https", "miniature-prod.moysklad.ru", 443),
    ("https", "tinyimage-prod.moysklad.ru", 443),
}
IMAGE_CONTENT_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
MAX_IMAGE_REDIRECTS = 3


class MoySkladError(RuntimeError):
    """Sanitized integration error which never contains credentials or URLs."""

    def __init__(self, message, code="MOYSKLAD_ERROR", status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def _blocked_url(reason, hostname="", code="MOYSKLAD_BLOCKED_URL"):
    safe_hostname = "".join(
        character for character in str(hostname or "").casefold()[:100]
        if character.isalnum() or character in ".-:"
    )
    LOGGER.warning(
        "MoySklad request blocked operation=image category=%s host=%s",
        reason,
        safe_hostname or "unparsed",
    )
    return MoySkladError("URL заблокирован политикой МойСклад", code)


def _trusted_moysklad_url(url, resolve=False, resolver=None):
    """Validate an exact MoySklad HTTPS origin before attaching credentials."""
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise _blocked_url("parse_error")
    hostname = parsed.hostname or ""
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or hostname.endswith(".")
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme.casefold(), hostname.casefold(), port or 443)
        not in MOYSKLAD_TRUSTED_ORIGINS
    ):
        raise _blocked_url("origin_policy", hostname)
    if resolve:
        lookup = resolver or socket.getaddrinfo
        try:
            records = lookup(hostname, port or 443, 0, socket.SOCK_STREAM)
            addresses = {record[4][0].split("%", 1)[0] for record in records}
            safe = bool(addresses) and all(
                ipaddress.ip_address(address).is_global for address in addresses
            )
        except (OSError, ValueError):
            raise _blocked_url("dns_error", hostname)
        if not safe:
            raise _blocked_url("non_public_dns", hostname)
    return parsed.geturl()


def _valid_image_signature(content, content_type):
    signatures = IMAGE_CONTENT_TYPES.get(content_type) or ()
    if content_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return any(content.startswith(signature) for signature in signatures)


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port or 443


class MoySkladClient:
    BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

    def __init__(self, token=None, session=None, resolver=None):
        self.token = str(MOYSKLAD_TOKEN if token is None else token or "").strip()
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
        self.resolver = resolver
        self.headers = {
            "Accept": "application/json;charset=utf-8",
            "Content-Type": "application/json;charset=utf-8",
        }
        if self.token:
            self.headers["Authorization"] = "Bearer " + self.token

    def _require_configured(self):
        if not self.token:
            raise MoySkladError("Интеграция МойСклад не настроена", "MOYSKLAD_DISABLED")

    def _api_url(self, endpoint):
        self._require_configured()
        endpoint = str(endpoint or "")
        if not endpoint.startswith("/") or endpoint.startswith("//"):
            raise MoySkladError("Некорректный endpoint МойСклад", "MOYSKLAD_BLOCKED_URL")
        url = self.BASE_URL + endpoint
        _trusted_moysklad_url(url)
        return url

    def _request_json(self, method, endpoint, params=None, payload=None):
        try:
            response = self.session.request(
                method,
                self._api_url(endpoint),
                headers=self.headers,
                params=params,
                json=payload,
                timeout=(3.05, 8),
                allow_redirects=False,
            )
        except MoySkladError:
            raise
        except requests.Timeout:
            raise MoySkladError("МойСклад не ответил вовремя", "MOYSKLAD_TIMEOUT") from None
        except requests.RequestException:
            raise MoySkladError("МойСклад временно недоступен", "MOYSKLAD_UNAVAILABLE") from None
        if 300 <= response.status_code < 400:
            raise MoySkladError("Неожиданный redirect МойСклад", "MOYSKLAD_BLOCKED_REDIRECT")
        if response.status_code == 429:
            raise MoySkladError("Превышен лимит запросов МойСклад", "MOYSKLAD_RATE_LIMITED", 429)
        if response.status_code >= 400:
            raise MoySkladError(
                "МойСклад вернул HTTP {}".format(response.status_code),
                "MOYSKLAD_HTTP_ERROR",
                response.status_code,
            )
        if method == "DELETE":
            return True
        try:
            return response.json()
        except ValueError:
            raise MoySkladError("МойСклад вернул некорректный JSON", "MOYSKLAD_INVALID_RESPONSE") from None

    def get(self, endpoint, params=None):
        return self._request_json("GET", endpoint, params=params)

    def post(self, endpoint, payload):
        return self._request_json("POST", endpoint, payload=payload)

    def put(self, endpoint, payload):
        return self._request_json("PUT", endpoint, payload=payload)

    # === RECEIPT DOCUMENT ACTIONS CLIENT V1 ===
    def delete(self, endpoint):
        try:
            return self._request_json("DELETE", endpoint)
        except MoySkladError as error:
            if error.status == 404:
                return True
            raise
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

        self._require_configured()
        current = _trusted_moysklad_url(url, resolve=True, resolver=self.resolver)
        visited = set()
        for redirect_count in range(MAX_IMAGE_REDIRECTS + 1):
            if current in visited:
                raise MoySkladError("Циклический redirect МойСклад", "MOYSKLAD_BLOCKED_REDIRECT")
            visited.add(current)
            response = None
            try:
                response = self.session.get(
                    current,
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "Accept": "image/jpeg,image/png,image/gif,image/webp",
                    },
                    timeout=(3.05, 8),
                    stream=True,
                    allow_redirects=False,
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location") or ""
                    if not location or redirect_count >= MAX_IMAGE_REDIRECTS:
                        raise MoySkladError(
                            "Небезопасный redirect изображения МойСклад",
                            "MOYSKLAD_BLOCKED_REDIRECT",
                        )
                    redirected = _trusted_moysklad_url(
                        urljoin(current, location), resolve=True, resolver=self.resolver
                    )
                    if _origin(redirected) != _origin(current):
                        LOGGER.warning(
                            "MoySklad request blocked operation=image "
                            "category=cross_origin_redirect host=%s",
                            _origin(redirected)[1],
                        )
                        raise MoySkladError(
                            "Redirect изображения МойСклад заблокирован",
                            "MOYSKLAD_BLOCKED_REDIRECT",
                        )
                    current = redirected
                    continue
                if response.status_code == 429:
                    raise MoySkladError(
                        "Превышен лимит запросов МойСклад",
                        "MOYSKLAD_RATE_LIMITED",
                        429,
                    )
                if response.status_code >= 400:
                    raise MoySkladError(
                        "МойСклад вернул HTTP {}".format(response.status_code),
                        "MOYSKLAD_HTTP_ERROR",
                        response.status_code,
                    )
                content_type = str(response.headers.get("Content-Type") or "").split(
                    ";", 1
                )[0].strip().lower()
                if content_type not in IMAGE_CONTENT_TYPES:
                    raise MoySkladError(
                        "МойСклад вернул неподдерживаемый тип изображения",
                        "MOYSKLAD_INVALID_IMAGE",
                    )
                try:
                    declared_size = int(response.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    declared_size = 0
                if declared_size > MAX_THUMBNAIL_BYTES:
                    raise MoySkladError(
                        "Миниатюра товара слишком большая", "MOYSKLAD_IMAGE_TOO_LARGE"
                    )
                chunks = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_THUMBNAIL_BYTES:
                        raise MoySkladError(
                            "Миниатюра товара слишком большая",
                            "MOYSKLAD_IMAGE_TOO_LARGE",
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                if not content or not _valid_image_signature(content, content_type):
                    raise MoySkladError(
                        "МойСклад вернул некорректное изображение",
                        "MOYSKLAD_INVALID_IMAGE",
                    )
                return content, content_type
            except MoySkladError:
                raise
            except requests.Timeout:
                raise MoySkladError(
                    "МойСклад не ответил вовремя", "MOYSKLAD_TIMEOUT"
                ) from None
            except requests.RequestException:
                raise MoySkladError(
                    "Не удалось скачать изображение МойСклад",
                    "MOYSKLAD_UNAVAILABLE",
                ) from None
            finally:
                if response is not None:
                    response.close()
        raise MoySkladError("Слишком много redirect МойСклад", "MOYSKLAD_BLOCKED_REDIRECT")

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

    def delete_product_images(self, product_id):
        product_id = str(product_id or "").strip()

        if not product_id:
            raise ValueError("Не указан ID товара")

        return self.put(
            f"/entity/product/{product_id}",
            {"images": []},
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
        with ThreadPoolExecutor(max_workers=2) as executor:
            organization_future = executor.submit(
                self.get_default_organization
            )
            store_future = executor.submit(
                self.get_default_store
            )
            organization = organization_future.result()
            store = store_future.result()

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
                raw_purchase_price = position.get("purchase_price")
                purchase_price = (
                    None
                    if raw_purchase_price is None
                    or (
                        isinstance(raw_purchase_price, str)
                        and not raw_purchase_price.strip()
                    )
                    else float(raw_purchase_price)
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

            if purchase_price is not None and purchase_price < 0:
                raise ValueError(
                    "Закупочная цена не может быть "
                    "отрицательной"
                )

            prepared_position = {
                "quantity": quantity,
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
            }
            if purchase_price is not None:
                prepared_position["price"] = int(round(purchase_price * 100))
            prepared_positions.append(prepared_position)

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

        return self.post("/entity/product", payload)
