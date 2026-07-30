import unittest
from pathlib import Path


class CatalogCascadeUnificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def source(self, relative_path):
        return (self.root / relative_path).read_text(encoding="utf-8")

    def test_all_three_sections_use_the_same_catalog_cascade(self):
        component_import = (
            "import { CatalogCascade } from "
            "'../catalog/CatalogComboboxes';"
        )
        for page in (
            "frontend/src/features/products/ProductsPage.tsx",
            "frontend/src/features/sales/SalesPage.tsx",
            "frontend/src/features/receipts/ReceiptsPage.tsx",
        ):
            source = self.source(page)
            self.assertIn(component_import, source)
            self.assertIn("<CatalogCascade", source)

        for form in (
            "frontend/src/features/products/ProductForm.tsx",
            "frontend/src/features/sales/SaleForm.tsx",
            "frontend/src/features/receipts/ReceiptForm.tsx",
        ):
            source = self.source(form)
            self.assertIn(component_import, source)
            self.assertIn("<CatalogCascade", source)

    def test_receipts_have_no_frontend_local_catalog(self):
        receipt_frontend = "\n".join(
            self.source(path)
            for path in (
                "frontend/src/features/receipts/api.ts",
                "frontend/src/features/receipts/schemas.ts",
                "frontend/src/features/receipts/ReceiptsPage.tsx",
                "frontend/src/features/receipts/ReceiptForm.tsx",
            )
        )
        self.assertNotIn("fetchReceiptCatalog", receipt_frontend)
        self.assertNotIn("receiptCatalogSchema", receipt_frontend)
        self.assertNotIn("/receipts/catalog", receipt_frontend)
        self.assertIn("allowCreate", self.source(
            "frontend/src/features/receipts/ReceiptForm.tsx"
        ))

    def test_shared_component_owns_layout_and_creation(self):
        component = self.source(
            "frontend/src/features/catalog/CatalogComboboxes.tsx"
        )
        styles = self.source("frontend/src/styles/global.css")
        self.assertEqual(component.count("function EntityCombobox"), 1)
        self.assertIn("CatalogCreationModal", component)
        self.assertIn('data-testid="catalog-cascade"', component)
        self.assertIn(".catalog-cascade {", styles)
        self.assertIn(
            "grid-template-columns: repeat(3, minmax(0, 1fr));",
            styles,
        )


if __name__ == "__main__":
    unittest.main()
