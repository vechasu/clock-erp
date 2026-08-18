"""Safe persistent brand images and idempotent Bitrix imports."""

from datetime import datetime, timezone
import hashlib
import imghdr
import os
from pathlib import Path
import re
import tempfile

from app.catalog_db import CatalogDatabase, PROJECT_ROOT
from app.services.shared_catalog import normalized_name, potential_alias_key


MAX_BRAND_IMAGE_BYTES = 5 * 1024 * 1024
MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
INPUT_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


class BrandImageValidationError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _extension(filename):
    name = Path(str(filename or "")).name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def validate_image(content, filename, declared_mime=""):
    """Validate size, extension, MIME and file signature; SVG is rejected."""
    if not isinstance(content, bytes) or not content:
        raise BrandImageValidationError("Файл изображения пуст.")
    if len(content) > MAX_BRAND_IMAGE_BYTES:
        raise BrandImageValidationError("Изображение больше 5 МБ.")
    extension = _extension(filename)
    if extension not in INPUT_EXTENSIONS:
        raise BrandImageValidationError("Поддерживаются PNG, JPG и WEBP.")
    mime = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if mime not in MIME_EXTENSIONS:
        raise BrandImageValidationError("Недопустимый MIME-тип изображения.")
    detected = imghdr.what(None, content)
    if detected == "jpeg":
        detected_mime = "image/jpeg"
    elif detected == "png":
        detected_mime = "image/png"
    elif detected == "webp" or (
        len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        detected_mime = "image/webp"
    else:
        raise BrandImageValidationError("Содержимое файла не является изображением.")
    if detected_mime != mime:
        raise BrandImageValidationError("MIME-тип не совпадает с содержимым файла.")
    canonical_extension = MIME_EXTENSIONS[mime]
    if extension not in {canonical_extension, "jpeg" if canonical_extension == "jpg" else canonical_extension}:
        raise BrandImageValidationError("Расширение не совпадает с типом изображения.")
    return canonical_extension, hashlib.sha256(content).hexdigest()


class BrandImageStore:
    def __init__(self, database=None, root=None):
        self.database = database or CatalogDatabase()
        self.root = Path(root or (PROJECT_ROOT / "instance" / "brand_images"))

    def _save(self, content, brand_id, extension, digest):
        self.root.mkdir(parents=True, exist_ok=True)
        filename = "brand-{}-{}.{}".format(
            int(brand_id), digest[:16], extension
        )
        target = self.root / filename
        if target.exists():
            return filename
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".brand-image-", dir=str(self.root)
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

    def _remove_if_unused(self, filename):
        filename = Path(str(filename or "")).name
        if not filename or not re.match(r"^brand-[0-9]+-[a-f0-9]{16}\.(jpg|png|webp)$", filename):
            return
        with self.database.connect() as connection:
            used = connection.execute(
                "SELECT 1 FROM erp_brands WHERE image_path = ? LIMIT 1",
                (filename,),
            ).fetchone()
        if used is None:
            path = self.root / filename
            if path.is_file():
                path.unlink()

    def set_image(self, brand_id, content, filename, mime_type, source,
                  bitrix_brand_id=None, external_id=None):
        if source not in {"manual", "bitrix"}:
            raise ValueError("Unsupported brand image source")
        extension, digest = validate_image(content, filename, mime_type)
        self.database.initialize()
        old_path = None
        stored_name = self._save(content, brand_id, extension, digest)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT image_path FROM erp_brands WHERE id = ?",
                (int(brand_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Бренд не найден.")
            old_path = row["image_path"]
            connection.execute(
                "UPDATE erp_brands SET bitrix_brand_id = COALESCE(?, bitrix_brand_id), "
                "image_path = ?, image_source = ?, image_sha256 = ?, "
                "image_external_id = ?, image_updated_at = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    str(bitrix_brand_id) if bitrix_brand_id not in (None, "") else None,
                    stored_name, source, digest, external_id, utc_now(), utc_now(),
                    int(brand_id),
                ),
            )
        if old_path and old_path != stored_name:
            self._remove_if_unused(old_path)
        return stored_name, digest

    def set_manual_image(self, brand_id, uploaded):
        content = uploaded.read(MAX_BRAND_IMAGE_BYTES + 1)
        return self.set_image(
            brand_id, content, uploaded.filename,
            getattr(uploaded, "mimetype", ""), "manual",
        )

    def remove_image(self, brand_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT image_path FROM erp_brands WHERE id = ?",
                (int(brand_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Бренд не найден.")
            old_path = row["image_path"]
            connection.execute(
                "UPDATE erp_brands SET image_path = NULL, image_source = NULL, "
                "image_sha256 = NULL, image_external_id = NULL, "
                "image_updated_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), int(brand_id)),
            )
        self._remove_if_unused(old_path)


class BitrixBrandImageImporter:
    """Match exported Bitrix brands without creating or reactivating ERP brands."""

    def __init__(self, database=None, store=None):
        self.database = database or CatalogDatabase()
        self.store = store or BrandImageStore(self.database)

    @staticmethod
    def _external_image_id(image):
        return "{}:{}".format(
            image.get("id") or "", image.get("updated_at") or ""
        )

    def _matches(self, records):
        with self.database.connect() as connection:
            erp = [dict(row) for row in connection.execute(
                "SELECT * FROM erp_brands ORDER BY id"
            ).fetchall()]
        by_bitrix = {
            str(row["bitrix_brand_id"]): row for row in erp
            if row.get("bitrix_brand_id")
        }
        by_exact = {}
        by_alias = {}
        for row in erp:
            if not row["active"]:
                continue
            by_exact.setdefault(normalized_name(row["name"]), []).append(row)
            by_alias.setdefault(potential_alias_key(row["name"]), []).append(row)
        results = []
        for record in records:
            bitrix_id = str(record.get("id") or "")
            candidates = [by_bitrix[bitrix_id]] if bitrix_id in by_bitrix else []
            method = "bitrix_id" if candidates else ""
            if not candidates:
                candidates = by_exact.get(normalized_name(record.get("name")), [])
                method = "exact_name" if candidates else ""
            if not candidates:
                candidates = by_alias.get(potential_alias_key(record.get("name")), [])
                method = "alias" if candidates else ""
            results.append((record, candidates, method))
        return results

    def run(self, records, downloader=None, apply=False):
        report = {
            "mode": "apply" if apply else "dry_run",
            "writes_performed": 0,
            "brands_found": len(records),
            "with_images": 0,
            "matched": 0,
            "to_import": 0,
            "current": 0,
            "skipped": 0,
            "ambiguous": [],
            "unmatched": [],
            "errors": [],
            "imported": 0,
        }
        for record, candidates, method in self._matches(records):
            images = record.get("images") or []
            image = images[0] if images else None
            if image:
                report["with_images"] += 1
            if len(candidates) > 1:
                report["ambiguous"].append({
                    "bitrix_id": str(record.get("id") or ""),
                    "name": record.get("name") or "",
                    "erp_brand_ids": [int(row["id"]) for row in candidates],
                })
                report["skipped"] += 1
                continue
            if not candidates:
                report["unmatched"].append({
                    "bitrix_id": str(record.get("id") or ""),
                    "name": record.get("name") or "",
                })
                report["skipped"] += 1
                continue
            row = candidates[0]
            report["matched"] += 1
            if not image:
                report["skipped"] += 1
                continue
            if row.get("image_source") == "manual":
                report["skipped"] += 1
                continue
            external_id = self._external_image_id(image)
            file_exists = bool(
                row.get("image_path")
                and (self.store.root / Path(row["image_path"]).name).is_file()
            )
            if row.get("image_external_id") == external_id and file_exists:
                report["current"] += 1
                continue
            report["to_import"] += 1
            if not apply:
                continue
            try:
                if downloader is None:
                    raise ValueError("Bitrix image downloader is required")
                content, mime_type, filename = downloader(image)
                self.store.set_image(
                    row["id"], content, filename, mime_type, "bitrix",
                    bitrix_brand_id=record.get("id"), external_id=external_id,
                )
                report["imported"] += 1
                report["writes_performed"] += 1
            except Exception as error:
                report["errors"].append({
                    "bitrix_id": str(record.get("id") or ""),
                    "name": record.get("name") or "",
                    "error": type(error).__name__,
                    "match_method": method,
                })
        return report
