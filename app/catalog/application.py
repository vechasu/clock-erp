"""Catalog orchestration independent of Flask HTTP globals."""

from app.services.shared_catalog import CatalogReferenceError


class CatalogApplication:
    def __init__(
        self,
        shared_catalog_factory,
        product_catalog_factory,
        normalize_label,
        label_key,
        remember_classification,
        invalidate_product_caches,
    ):
        self._shared_catalog_factory = shared_catalog_factory
        self._product_catalog_factory = product_catalog_factory
        self._normalize_label = normalize_label
        self._label_key = label_key
        self._remember_classification = remember_classification
        self._invalidate_product_caches = invalidate_product_caches

    def create_global_category(self, name, actor):
        category = self._shared_catalog_factory().create_global_category(
            name,
            **actor
        )
        self._invalidate_product_caches()
        return category

    def rename_category(self, category_id, name, actor):
        category = self._shared_catalog_factory().rename_category(
            category_id,
            name,
            **actor
        )
        self._invalidate_product_caches()
        return category

    def delete_global_category(
        self,
        category_id,
        confirmation,
        target_category_id,
        actor,
    ):
        if str(confirmation or "").strip() != "УДАЛИТЬ":
            raise CatalogReferenceError(
                "Подтвердите операцию удаления категории."
            )
        result = self.delete_empty_category(
            category_id, actor, target_category_id
        )
        return result

    def delete_empty_category(
        self, category_id, actor=None, expected_product_count=None
    ):
        result = self._shared_catalog_factory().delete_category_with_products(
            category_id,
            expected_product_count,
            **(actor or {})
        )
        self._invalidate_product_caches()
        return result

    def category_overviews(
        self,
        *,
        query,
        limit,
        offset,
        sort_by,
        sort_dir,
    ):
        return self._shared_catalog_factory().list_category_overviews(
            query=query,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    def category_delete_plan(self, category_id):
        return self._shared_catalog_factory().category_delete_plan(category_id)

    def create_brand(self, name, actor):
        brand = self._shared_catalog_factory().create_brand(name, **actor)
        self._invalidate_product_caches()
        return brand

    def rename_brand(self, brand_id, name, actor):
        brand = self._shared_catalog_factory().rename_brand(
            brand_id,
            name,
            **actor
        )
        self._invalidate_product_caches()
        return brand

    def create_brand_category(self, brand_id, name, actor):
        category = self._shared_catalog_factory().create_brand_category(
            brand_id,
            name,
            **actor
        )
        self._invalidate_product_caches()
        return category

    def rename_brand_category(self, brand_id, category_id, name, actor):
        brand = self._shared_catalog_factory().get_brand_overview(brand_id)
        category = next(
            (
                item
                for item in (brand or {}).get("categories", [])
                if item["id"] == category_id
            ),
            None,
        )
        if brand is None or category is None or category_id == 0:
            return None
        result = self._shared_catalog_factory().rename_category(
            category_id,
            name,
            **actor
        )
        self._invalidate_product_caches()
        return result

    def brand_overviews(self, query):
        return self._shared_catalog_factory().list_brand_summaries(
            query=query,
            limit=500,
        )

    def brand_for_delete(self, brand_id):
        return self._shared_catalog_factory().get_brand_overview(brand_id)

    def delete_brand(self, brand_id, force, actor):
        result = self._product_catalog_factory().delete_brand_catalog(
            brand_id,
            force=force,
            **actor
        )
        self._invalidate_product_caches()
        return result

    def brand_category_for_delete(self, brand_id, category_id):
        brand = self._shared_catalog_factory().get_brand_overview(brand_id)
        category = next(
            (
                item
                for item in (brand or {}).get("categories", [])
                if item["id"] == category_id
            ),
            None,
        )
        return brand, category

    def delete_brand_category(
        self,
        brand_id,
        category_id,
        force,
        actor,
    ):
        result = self._product_catalog_factory().delete_brand_catalog(
            brand_id,
            category_id=category_id,
            force=force,
            **actor
        )
        self._invalidate_product_caches()
        return result

    def catalog_values(
        self,
        kind,
        *,
        query,
        limit,
        brand_id=None,
        legacy_category_shape=False,
    ):
        catalog = self._shared_catalog_factory()
        if kind == "brand":
            return [
                {**item, "count": item["product_count"]}
                for item in catalog.list_brands(
                    query=query,
                    limit=limit,
                )
            ]
        values = [
            {
                **item,
                "brand": item["brand_name"],
                "count": item["product_count"],
            }
            for item in catalog.list_categories(
                brand_id=brand_id,
                query=query,
                limit=limit,
            )
        ]
        if not legacy_category_shape:
            return values
        return [
            {
                "brand": item["brand"],
                "name": item["name"],
                "count": item["count"],
            }
            for item in values
        ]

    def create_api_brand(self, name, actor=None):
        brand, resolution = (
            self._shared_catalog_factory().resolve_or_create_brand(
                name, **(actor or {})
            )
        )
        self._remember_classification(brand["name"])
        return (
            {**brand, "count": brand["product_count"]},
            resolution,
        )

    def create_api_category(self, brand_id, brand_name, name):
        brand_name = self._normalize_label(brand_name)
        if brand_id in (None, "") and brand_name:
            brand = next(
                (
                    item
                    for item in self._shared_catalog_factory().list_brands(
                        query=brand_name,
                        limit=200,
                    )
                    if self._label_key(item["name"])
                    == self._label_key(brand_name)
                ),
                None,
            )
            brand_id = brand["id"] if brand else None
        created = self._shared_catalog_factory().create_category(
            brand_id,
            name,
        )
        self._remember_classification(
            created["brand_name"],
            created["name"],
        )
        return {
            **created,
            "brand": created["brand_name"],
            "count": created["product_count"],
        }

    def update_api_brand(self, brand_id, method, name=None):
        catalog = self._shared_catalog_factory()
        if method == "DELETE":
            catalog.archive_brand(brand_id)
            return {"id": brand_id, "archived": True}
        updated = catalog.rename_brand(brand_id, name)
        return {**updated, "count": updated["product_count"]}

    def update_api_category(
        self, category_id, method, name=None, actor=None,
        expected_product_count=None,
    ):
        catalog = self._shared_catalog_factory()
        if method == "DELETE":
            return self.delete_empty_category(
                category_id, actor, expected_product_count
            )
        updated = catalog.rename_category(category_id, name, **(actor or {}))
        return {**updated, "count": updated["product_count"]}

    def catalog_options(
        self,
        kind,
        *,
        query,
        limit,
        brand_id=None,
        category_id=None,
        only_used_by_brand=False,
        in_stock=False,
    ):
        catalog = self._shared_catalog_factory()
        if kind == "brand":
            items = catalog.list_brands(query=query, limit=limit)
        elif kind == "category":
            items = catalog.list_category_options(
                brand_id=brand_id,
                query=query,
                limit=limit,
                only_used_by_brand=only_used_by_brand,
            )
        elif kind == "product":
            items = catalog.list_products(
                brand_id=brand_id,
                category_id=category_id,
                query=query,
                limit=limit,
                in_stock=in_stock,
            )
        else:
            return None
        if kind in {"brand", "category"}:
            items = [
                {**item, "count": item.get("product_count", 0)}
                for item in items
            ]
        total = (
            catalog.count_products(
                query=query,
                brand_id=brand_id,
                category_id=category_id,
                in_stock=in_stock,
            )
            if kind == "product"
            else len(items)
        )
        return items, total

    def duplicate_audit(self):
        return self._shared_catalog_factory().duplicate_audit()
