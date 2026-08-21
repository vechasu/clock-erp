"""Safe, idempotent local storage and deterministic product-image imports."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
from urllib.parse import urljoin, urlsplit

import requests

from app.catalog_db import CatalogDatabase, PROJECT_ROOT
from app.services.brand_images import validate_image
from app.services.shared_catalog import normalized_name


MAX_PRODUCT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
# Import order follows the product brief: Bitrix, TicTacToy, then an existing
# ERP upload. A trusted higher-priority source is the only reason to replace a
# lower-priority local file.
SOURCE_PRIORITY = {"manual": 1, "tictactoy": 2, "bitrix": 3}
TICTACTOY_HOSTS = {"tictactoy.ru", "www.tictactoy.ru"}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


class ProductImageStore:
    def __init__(self, database=None, root=None):
        self.database = database or CatalogDatabase()
        self.root = Path(root or (PROJECT_ROOT / "instance" / "product_images"))

    def _save(self, content, extension, digest):
        self.root.mkdir(parents=True, exist_ok=True)
        filename = "product-{}.{}".format(digest, extension)
        target = self.root / filename
        if target.is_file():
            return filename
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".product-image-", dir=str(self.root)
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_name, 0o640)
            os.replace(temporary_name, str(target))
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return filename

    def set_image(self, product_id, content, filename, mime_type, source,
                  external_id=""):
        if source not in SOURCE_PRIORITY:
            raise ValueError("Unsupported product image source")
        extension, digest = validate_image(
            content, filename, mime_type
        )
        self.database.initialize()
        stored_name = self._save(content, extension, digest)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM catalog_excel_products WHERE id = ?",
                (int(product_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Товар не найден.")
            now = utc_now()
            connection.execute(
                "UPDATE catalog_excel_products SET local_image_path = ?, "
                "local_image_source = ?, local_image_sha256 = ?, "
                "local_image_external_id = ?, local_image_updated_at = ?, "
                "updated_at = ? WHERE id = ?",
                (stored_name, source, digest, _text(external_id) or None,
                 now, now, int(product_id)),
            )
        return stored_name, digest


class ProductImageImporter:
    """Match source records without fuzzy guesses or catalog mutations."""

    def __init__(self, database=None, store=None):
        self.database = database or CatalogDatabase()
        self.store = store or ProductImageStore(self.database)

    def _products(self):
        self.database.initialize()
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, active, excel_name_raw, excel_article, excel_brand, "
                "bitrix_external_product_id, bitrix_xml_id, bitrix_source_url, "
                "local_image_path, local_image_source, local_image_sha256, "
                "local_image_external_id FROM catalog_excel_products "
                "WHERE active = 1 ORDER BY id"
            ).fetchall()]

    @staticmethod
    def _unique_index(products, key):
        result = {}
        for product in products:
            value = key(product)
            if value:
                result.setdefault(value, []).append(product)
        return result

    def matches(self, records):
        products = self._products()
        by_external = self._unique_index(
            products, lambda row: _text(row["bitrix_external_product_id"])
        )
        by_xml = self._unique_index(
            products, lambda row: _text(row["bitrix_xml_id"]).casefold()
        )
        by_url = self._unique_index(
            products, lambda row: _text(row["bitrix_source_url"]).rstrip("/").casefold()
        )
        by_article = self._unique_index(
            products, lambda row: _text(row["excel_article"]).casefold()
        )
        by_brand_name = self._unique_index(
            products,
            lambda row: (
                normalized_name(row["excel_brand"]),
                normalized_name(row["excel_name_raw"]),
            ) if _text(row["excel_brand"]) and _text(row["excel_name_raw"]) else None,
        )
        results = []
        for record in records:
            candidates = []
            method = ""
            keys = (
                ("external_id", by_external, _text(record.get("id"))),
                ("xml_id", by_xml, _text(record.get("xml_id")).casefold()),
                ("source_url", by_url, _text(record.get("source_url")).rstrip("/").casefold()),
                ("article", by_article, _text(record.get("article")).casefold()),
                (
                    "brand_name", by_brand_name,
                    (normalized_name(record.get("brand")), normalized_name(record.get("name")))
                    if _text(record.get("brand")) and _text(record.get("name")) else None,
                ),
            )
            for candidate_method, index, value in keys:
                if value and value in index:
                    candidates = index[value]
                    method = candidate_method
                    break
            results.append((record, candidates, method))
        return products, results

    def run(self, records, source, downloader=None, apply=False):
        if source not in {"bitrix", "tictactoy"}:
            raise ValueError("Unsupported product image import source")
        products, matches = self.matches(records)
        report = {
            "mode": "apply" if apply else "dry_run",
            "source": source,
            "products_found": 0,
            "added": 0,
            "existing": 0,
            "ambiguous": [],
            "unmatched": [],
            "without_images": [],
            "errors": [],
            "writes_performed": 0,
        }
        matched_ids = set()
        for record, candidates, method in matches:
            images = record.get("images") or []
            image = images[0] if images else None
            if not image:
                continue
            report["products_found"] += 1
            if len(candidates) != 1:
                entry = {
                    "id": _text(record.get("id")),
                    "name": _text(record.get("name")),
                    "candidate_ids": [int(item["id"]) for item in candidates],
                }
                (report["ambiguous"] if candidates else report["unmatched"]).append(entry)
                continue
            product = candidates[0]
            matched_ids.add(int(product["id"]))
            current_source = product.get("local_image_source") or ""
            current_file = (
                self.store.root / Path(product.get("local_image_path") or "").name
            )
            if current_source and current_file.is_file() and (
                SOURCE_PRIORITY.get(current_source, 0) >= SOURCE_PRIORITY[source]
            ):
                report["existing"] += 1
                continue
            if not apply:
                continue
            try:
                if downloader is None:
                    raise ValueError("Image downloader is required")
                content, mime_type, filename = downloader(image)
                self.store.set_image(
                    product["id"], content, filename, mime_type, source,
                    external_id="{}:{}".format(
                        _text(record.get("id") or record.get("source_url")),
                        _text(image.get("id") or image.get("url")),
                    ),
                )
                report["added"] += 1
                report["writes_performed"] += 1
            except Exception as error:
                report["errors"].append({
                    "product_id": int(product["id"]),
                    "name": product["excel_name_raw"],
                    "error": type(error).__name__,
                })
        for product in products:
            path = Path(product.get("local_image_path") or "").name
            if not path or not (self.store.root / path).is_file():
                report["without_images"].append({
                    "product_id": int(product["id"]),
                    "article": product.get("excel_article") or "",
                    "brand": product.get("excel_brand") or "",
                    "name": product.get("excel_name_raw") or "",
                })
        return report


def _assert_public_tictactoy_url(url):
    parsed = urlsplit(_text(url))
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in TICTACTOY_HOSTS
        or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
    ):
        raise ValueError("Unsafe TicTacToy URL")
    addresses = {
        item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    }
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("TicTacToy URL resolved to a non-public address")
    return parsed.geturl()


class _MetaImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = ""

    def handle_starttag(self, tag, attrs):
        if self.image:
            return
        values = {str(key).casefold(): value for key, value in attrs}
        if tag.casefold() == "img" and "tovar-img" in _text(values.get("class")).split():
            self.image = _text(values.get("src"))
            return
        if tag.casefold() != "meta":
            return
        marker = _text(values.get("property") or values.get("name")).casefold()
        if marker in {"og:image", "twitter:image"}:
            self.image = _text(values.get("content"))


class TicTacToyImageSource:
    def __init__(self, session=None, timeout=(3.05, 15)):
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, url, accept, max_bytes):
        current = _assert_public_tictactoy_url(url)
        for _ in range(4):
            response = self.session.get(
                current, headers={"Accept": accept}, timeout=self.timeout,
                stream=True, allow_redirects=False,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                current = _assert_public_tictactoy_url(
                    urljoin(current, response.headers.get("Location") or "")
                )
                continue
            if response.status_code != 200:
                raise ValueError("TicTacToy returned HTTP {}".format(response.status_code))
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("TicTacToy response is too large")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type") or "", current
        raise ValueError("Too many TicTacToy redirects")

    def record_for_product(self, product):
        page_url = _text(product.get("bitrix_source_url"))
        if not page_url:
            return None
        content, mime, final_url = self._get(page_url, "text/html", MAX_HTML_BYTES)
        if "text/html" not in mime.casefold():
            raise ValueError("TicTacToy product page is not HTML")
        parser = _MetaImageParser()
        parser.feed(content.decode("utf-8", "replace"))
        if not parser.image:
            return None
        image_url = _assert_public_tictactoy_url(urljoin(final_url, parser.image))
        return {
            "source_url": page_url,
            "name": product.get("excel_name_raw") or "",
            "brand": product.get("excel_brand") or "",
            "article": product.get("excel_article") or "",
            "images": [{"url": image_url, "id": image_url}],
        }

    def brand_records(self):
        content, mime, final_url = self._get(
            "https://tictactoy.ru/brands/", "text/html", MAX_HTML_BYTES
        )
        if "text/html" not in mime.casefold():
            raise ValueError("TicTacToy brands page is not HTML")
        html = content.decode("utf-8", "replace")
        section = re.search(
            r'<section\s+class="[^"]*\bbrends__page\b[^"]*">(.*?)</section>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not section:
            raise ValueError("TicTacToy brands section was not found")
        records = []
        seen = set()
        for match in re.finditer(
            r'<a\s+href="([^"]+)"[^>]*>.*?<img\s+[^>]*src="([^"]+)"'
            r'[^>]*alt="([^"]+)"[^>]*>',
            section.group(1),
            re.IGNORECASE | re.DOTALL,
        ):
            page_url, image_url, name = (unescape(value) for value in match.groups())
            key = normalized_name(name)
            if not key or key in seen:
                continue
            seen.add(key)
            image_url = _assert_public_tictactoy_url(urljoin(final_url, image_url))
            records.append({
                "id": "",
                "name": name,
                "source_url": urljoin(final_url, page_url),
                "images": [{
                    "id": image_url,
                    "url": image_url,
                    "filename": Path(urlsplit(image_url).path).name,
                    "mime_type": "",
                    "updated_at": "",
                }],
            })
        return records

    def download(self, image):
        content, mime, final_url = self._get(
            image.get("url"), "image/*", MAX_PRODUCT_IMAGE_BYTES
        )
        return content, mime.split(";", 1)[0], Path(urlsplit(final_url).path).name
