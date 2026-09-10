"""Safe, idempotent local storage and deterministic product-image imports."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
import ipaddress
import os
from pathlib import Path
import re
import socket
import struct
import tempfile
import zlib
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
MAX_IMAGE_PIXELS = 40 * 1000 * 1000
MAX_IMAGE_DIMENSION = 12000


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value):
    return " ".join(str(value or "").split())


def _safe_dimensions(width, height):
    return bool(
        0 < width <= MAX_IMAGE_DIMENSION
        and 0 < height <= MAX_IMAGE_DIMENSION
        and width * height <= MAX_IMAGE_PIXELS
    )


def _png_dimensions(content):
    if len(content) < 45 or content[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    dimensions = None
    saw_end = False
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset:offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(content):
            return None
        chunk_type = content[offset + 4:offset + 8]
        data = content[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(
            ">I", content[offset + 8 + length:chunk_end]
        )[0]
        if zlib.crc32(chunk_type + data) & 0xffffffff != expected_crc:
            return None
        if offset == 8:
            if chunk_type != b"IHDR" or length != 13:
                return None
            dimensions = struct.unpack(">II", data[:8])
        if chunk_type == b"IEND":
            saw_end = length == 0 and chunk_end == len(content)
            break
        offset = chunk_end
    return dimensions if dimensions and saw_end else None


def _jpeg_dimensions(content):
    if (
        len(content) < 12
        or content[:2] != b"\xff\xd8"
        or content[-2:] != b"\xff\xd9"
    ):
        return None
    offset = 2
    while offset + 4 <= len(content):
        if content[offset] != 0xff:
            return None
        while offset < len(content) and content[offset] == 0xff:
            offset += 1
        if offset >= len(content):
            return None
        marker = content[offset]
        offset += 1
        if marker in {0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
            continue
        if marker == 0xda:
            return None
        if offset + 2 > len(content):
            return None
        length = struct.unpack(">H", content[offset:offset + 2])[0]
        if length < 2 or offset + length > len(content):
            return None
        if marker in {
            0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
            0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
        }:
            if length < 7:
                return None
            height, width = struct.unpack(
                ">HH", content[offset + 3:offset + 7]
            )
            return width, height
        offset += length
    return None


def _webp_dimensions(content):
    if (
        len(content) < 25
        or content[:4] != b"RIFF"
        or content[8:12] != b"WEBP"
        or struct.unpack("<I", content[4:8])[0] + 8 != len(content)
    ):
        return None
    chunk = content[12:16]
    if chunk == b"VP8X" and len(content) >= 30:
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", content[26:30])
        return width & 0x3fff, height & 0x3fff
    if chunk == b"VP8L" and content[20] == 0x2f:
        bits = int.from_bytes(content[21:25], "little")
        return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    return None


def validate_product_image(content, filename, mime_type):
    extension, digest = validate_image(content, filename, mime_type)
    dimensions = {
        "jpg": _jpeg_dimensions,
        "png": _png_dimensions,
        "webp": _webp_dimensions,
    }[extension](content)
    if not dimensions:
        raise ValueError("Файл изображения повреждён.")
    if not _safe_dimensions(*dimensions):
        raise ValueError("Изображение имеет недопустимо большие размеры.")
    return extension, digest


class ProductImageStore:
    def __init__(self, database=None, root=None):
        self.database = database or CatalogDatabase()
        database_root = (
            Path(self.database.path).resolve().parent
            if str(self.database.path) != ":memory:"
            else PROJECT_ROOT / "instance"
        )
        self.root = Path(
            root
            or os.getenv("PRODUCT_IMAGE_ROOT", "").strip()
            or (database_root / "product_images")
        )

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

    def prepare_image(self, content, filename, mime_type):
        """Validate and persist content before its database transaction."""
        extension, digest = validate_product_image(
            content, filename, mime_type
        )
        stored_name = self._save(content, extension, digest)
        return {
            "path": stored_name,
            "sha256": digest,
            "updated_at": utc_now(),
        }

    def _remove_if_unused(self, filename):
        filename = Path(str(filename or "")).name
        if not re.match(
            r"^product-[a-f0-9]{64}\.(jpg|png|webp)$", filename
        ):
            return
        with self.database.connect() as connection:
            used = connection.execute(
                "SELECT 1 FROM catalog_excel_products "
                "WHERE local_image_path = ? "
                "OR bitrix_gallery_json LIKE ? LIMIT 1",
                (filename, "%{}%".format(filename)),
            ).fetchone()
        if used is None:
            path = self.root / filename
            if path.is_file():
                path.unlink()

    def discard_prepared(self, prepared):
        self._remove_if_unused((prepared or {}).get("path"))

    def set_image(self, product_id, content, filename, mime_type, source,
                  external_id=""):
        if source not in SOURCE_PRIORITY:
            raise ValueError("Unsupported product image source")
        prepared = self.prepare_image(content, filename, mime_type)
        digest = prepared["sha256"]
        self.database.initialize()
        stored_name = prepared["path"]
        old_path = ""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id, local_image_path FROM catalog_excel_products "
                "WHERE id = ?",
                (int(product_id),),
            ).fetchone()
            if row is None:
                self.discard_prepared(prepared)
                raise ValueError("Товар не найден.")
            old_path = row["local_image_path"] or ""
            now = utc_now()
            connection.execute(
                "UPDATE catalog_excel_products SET local_image_path = ?, "
                "local_image_source = ?, local_image_sha256 = ?, "
                "local_image_external_id = ?, local_image_updated_at = ?, "
                "updated_at = ? WHERE id = ?",
                (stored_name, source, digest, _text(external_id) or None,
                 now, now, int(product_id)),
            )
        if old_path != stored_name:
            self._remove_if_unused(old_path)
        return stored_name, digest

    def remove_image(self, product_id):
        self.database.initialize()
        old_path = ""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT local_image_path FROM catalog_excel_products "
                "WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Товар не найден.")
            old_path = row["local_image_path"] or ""
            now = utc_now()
            connection.execute(
                "UPDATE catalog_excel_products SET local_image_path = NULL, "
                "local_image_source = NULL, local_image_sha256 = NULL, "
                "local_image_external_id = NULL, local_image_updated_at = ?, "
                "updated_at = ? WHERE id = ?",
                (now, now, int(product_id)),
            )
        self._remove_if_unused(old_path)
        return bool(old_path)


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
