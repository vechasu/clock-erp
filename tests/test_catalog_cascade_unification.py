from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CatalogCascadeUnificationTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_current_sections_share_one_catalog_cascade(self):
        for page in (
            "frontend/src/features/products/ProductsPage.tsx",
            "frontend/src/features/sales/SalesPage.tsx",
            "frontend/src/features/receipts/ReceiptsPage.tsx",
        ):
            with self.subTest(page=page):
                self.assertIn("CatalogCascade", self.source(page))

    def test_shared_component_keeps_search_and_inline_creation(self):
        source = self.source(
            "frontend/src/features/catalog/CatalogComboboxes.tsx"
        )
        self.assertIn("SearchableSelect", source)
        self.assertIn("createCatalogValue", source)
        self.assertIn("categoryId", source)
        self.assertIn("productId", source)

    def test_global_category_backend_contract_is_preserved(self):
        source = self.source("app/services/shared_catalog.py")
        self.assertIn("normalized_name", source)
        self.assertIn("list_categories", source)
        self.assertIn("create_category", source)

    def test_removed_legacy_templates_are_absent(self):
        for name in ("warehouse.html", "sales.html", "receipts.html"):
            self.assertFalse((ROOT / "app" / "templates" / name).exists())


if __name__ == "__main__":
    unittest.main()
