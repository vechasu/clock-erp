import hashlib
import tempfile
import unittest
from pathlib import Path

from app.services.product_reconciliation import (
    ProductReconciler,
    batch_id_for,
    classify_article,
    compare_with_baseline,
    ensure_batch_is_new,
    extract_model_codes,
    normalize_text,
    reliable_article,
)


def product(identity, name, brand, article="", xml_id="", properties=None):
    return {
        "id": identity,
        "external_product_id": str(identity),
        "external_xml_id": xml_id or "xml-{}".format(identity),
        "name": name,
        "brand": brand,
        "article": article,
        "properties": properties or [],
    }


def row(number, name, brand, stock=1, article=""):
    return {
        "excel_row": number,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": article,
        "stock": stock,
        "cell": "",
        "category": "",
    }


class ProductReconciliationTest(unittest.TestCase):
    def match(self, products, rows):
        return ProductReconciler(products).reconcile(rows)

    def test_exact_name_without_brand_is_matched(self):
        result = self.match(
            [product(1, "Gravity Green", "Ziiiro")],
            [row(2, "Gravity Green", "Ziiiro")],
        )[0]
        self.assertEqual((result["match_status"], result["product_id"]), ("exact", 1))

    def test_leading_brand_in_excel_is_high_confidence(self):
        result = self.match(
            [product(1, "Ora Unica 42 mm", "Nava Design")],
            [row(2, "Nava Design Ora Unica 42 mm", "Nava Design")],
        )[0]
        self.assertEqual(result["match_status"], "high_confidence")

    def test_dashes_slashes_and_ampersands_normalize_safely(self):
        self.assertEqual(normalize_text("Terra–Time / A&B"), "terra time a b")
        result = self.match(
            [product(1, "Terra-Time A/B", "Projects")],
            [row(2, "Terra Time A & B", "Projects")],
        )[0]
        self.assertEqual(result["match_status"], "exact")

    def test_same_model_different_brands_is_not_cross_matched(self):
        result = self.match(
            [product(1, "Model X1 Black", "Brand A")],
            [row(2, "Model X1 Black", "Brand B")],
        )[0]
        self.assertEqual(result["match_status"], "ambiguous")
        self.assertEqual(result["alternatives"][0]["evidence"], "exact_name_brand_mismatch")

    def test_common_names_are_never_automatic(self):
        for name in ("Black", "Gold", "Moon", "Eclipse"):
            result = self.match([product(1, name, "Brand")], [row(2, name, "Brand")])[0]
            self.assertEqual(result["match_status"], "ambiguous")

    def test_multiple_catalog_candidates_are_ambiguous(self):
        products = [product(1, "One", "Brand"), product(2, "One", "Brand")]
        result = self.match(products, [row(2, "One", "Brand")])[0]
        self.assertEqual(result["match_status"], "ambiguous")
        self.assertEqual(len(result["alternatives"]), 2)

    def test_multiple_excel_rows_for_one_card_remain_separate_many_to_one_links(self):
        results = self.match(
            [product(1, "Model", "Brand")],
            [row(2, "Model", "Brand"), row(3, "Brand Model", "Brand")],
        )
        self.assertEqual(
            {result["match_status"] for result in results},
            {"exact", "high_confidence"},
        )
        self.assertEqual({result["product_id"] for result in results}, {1})
        self.assertEqual(
            {result["bitrix_link_cardinality"] for result in results}, {"many_to_one"}
        )
        self.assertEqual([result["shared_bitrix_row_count"] for result in results], [2, 2])

    def test_identical_excel_names_are_never_merged(self):
        results = self.match(
            [product(1, "Adventure Silver", "Bitrix Brand")],
            [
                row(212, "Adventure Silver", "Excel Brand", stock=1),
                row(219, "Adventure Silver", "Excel Brand", stock=2),
            ],
        )
        self.assertEqual([result["excel_row"] for result in results], [212, 219])
        self.assertEqual([result["stock"] for result in results], [1.0, 2.0])
        self.assertEqual([result["match_status"] for result in results], ["ambiguous", "ambiguous"])
        self.assertEqual(
            [result["bitrix_link_cardinality"] for result in results],
            ["many_to_one_candidate", "many_to_one_candidate"],
        )

    def test_display_sample_does_not_match_general_card(self):
        result = self.match(
            [product(1, "Model X1", "Brand")],
            [row(2, "Model X1 витринный образец", "Brand")],
        )[0]
        self.assertNotIn(result["match_status"], {"exact", "high_confidence"})

    def test_different_colors_are_not_automatic(self):
        result = self.match(
            [product(1, "Model X1 Black", "Brand")],
            [row(2, "Model X1 Blue", "Brand")],
        )[0]
        self.assertEqual(result["match_status"], "ambiguous")

    def test_missing_product_is_not_found(self):
        result = self.match(
            [product(1, "Other", "Brand")],
            [row(2, "Completely Missing", "Brand")],
        )[0]
        self.assertEqual(result["match_status"], "not_found")

    def test_zero_stock_is_matched_without_creating_movement(self):
        result = self.match(
            [product(1, "Model", "Brand")],
            [row(2, "Model", "Brand", stock=0)],
        )[0]
        self.assertEqual((result["match_status"], result["stock"]), ("exact", 0.0))

    def test_invalid_stock_is_rejected(self):
        result = self.match(
            [product(1, "Model", "Brand")],
            [row(2, "Model", "Brand", stock=float("nan"))],
        )[0]
        self.assertEqual(result["match_status"], "invalid")

    def test_article_notes_are_not_treated_as_reliable_ids(self):
        self.assertTrue(reliable_article("PJT-7203BL-40"))
        self.assertFalse(reliable_article("витринный образец"))
        self.assertEqual(classify_article("переучёт, проверить"), "comment")

    def test_model_extraction_normalizes_supported_formats(self):
        normalized = lambda value: {  # noqa: E731
            item["normalized"] for item in extract_model_codes(value, "excel_article")
        }
        self.assertIn("raac0m04y", normalized("RA-AC0M04Y"))
        self.assertIn("raac0m04y", normalized("RA AC0M04Y"))
        self.assertIn("raac0m04y", normalized("RAAC0M04Y"))
        self.assertEqual(normalized("PG-3"), {"pg3"})
        self.assertEqual(normalized("PG03"), {"pg3"})
        self.assertIn("x7000gay", normalized("X7000GA-Y"))
        self.assertEqual(normalized("BLACK GOLD MOON ECLIPSE"), set())

    def test_exact_model_code_in_property_is_high_confidence(self):
        properties = [{
            "id": "501", "code": "MODEL_CODE", "name": "Код модели",
            "value": "RA AC0M04Y", "display_value": "RA AC0M04Y",
        }]
        result = self.match(
            [product(1, "Different title", "Brand", properties=properties)],
            [row(2, "Warehouse title", "Brand", article="RA-AC0M04Y")],
        )[0]
        self.assertEqual(result["match_status"], "high_confidence")
        self.assertEqual(result["match_method"], "model_property")
        self.assertEqual(result["excel_model_code"], "raac0m04y")

    def test_exact_model_code_only_in_names_is_high_confidence(self):
        result = self.match(
            [product(1, "Automatic PG03 Black", "Brand")],
            [row(2, "Watch PG-3 Black", "Brand")],
        )[0]
        self.assertEqual((result["match_status"], result["product_id"]), ("high_confidence", 1))
        self.assertEqual(result["excel_model_code"], "pg3")

    def test_article_comment_code_is_not_automatic(self):
        result = self.match(
            [product(1, "Model PG03", "Brand")],
            [row(2, "Other", "Brand", article="PG03 витрина проверить")],
        )[0]
        self.assertNotIn(result["match_status"], {"exact", "high_confidence"})

    def test_same_model_different_brands_is_not_automatic(self):
        result = self.match(
            [product(1, "Model PG03", "Brand A")],
            [row(2, "Model PG-3", "Brand B")],
        )[0]
        self.assertNotIn(result["match_status"], {"exact", "high_confidence"})

    def test_same_model_different_colors_is_not_automatic(self):
        result = self.match(
            [product(1, "Model PG03 Blue", "Brand")],
            [row(2, "Model PG-3 Black", "Brand")],
        )[0]
        self.assertEqual(result["match_status"], "ambiguous")
        self.assertIn("color", result["reason"])

    def test_conflicting_property_code_blocks_exact_name(self):
        properties = [{
            "id": "501", "code": "SKU", "name": "SKU",
            "value": "RA-OTHER2", "display_value": "RA-OTHER2",
        }]
        result = self.match(
            [product(1, "Same title", "Brand", properties=properties)],
            [row(2, "Same title", "Brand", article="RA-MODEL1")],
        )[0]
        self.assertEqual(result["match_status"], "ambiguous")
        self.assertIn("коды модели конфликтуют", result["reason"])

    def test_multiple_model_candidates_are_not_automatic(self):
        result = self.match(
            [
                product(1, "Model PG03 Black", "Brand"),
                product(2, "Model PG03 Blue", "Brand"),
            ],
            [row(2, "PG-3", "Brand")],
        )[0]
        self.assertNotIn(result["match_status"], {"exact", "high_confidence"})

    def test_baseline_comparison_detects_candidate_change(self):
        results = self.match(
            [product(1, "Model PG03", "Brand")],
            [row(2, "Model PG-3", "Brand")],
        )
        metrics = compare_with_baseline(results, [{
            "excel_row": 2, "match_status": "ambiguous",
            "match_method": "manual_candidates", "product_id": 99,
        }])
        self.assertTrue(results[0]["candidate_changed"])
        self.assertEqual(results[0]["comparison_status"], "new_automatic")
        self.assertEqual(metrics["new_matches_by_model"], 1)

    def test_batch_identity_blocks_repeated_application(self):
        digest = hashlib.sha256(b"same file").hexdigest()
        batch_id = batch_id_for(digest)
        self.assertEqual(batch_id, batch_id_for(digest))
        self.assertTrue(ensure_batch_is_new(batch_id, []))
        with self.assertRaises(ValueError):
            ensure_batch_is_new(batch_id, [batch_id])

    def test_reconciliation_has_no_file_or_external_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "marker"
            marker.write_text("unchanged", encoding="utf-8")
            before = marker.read_bytes()
            first = self.match([product(1, "Model", "Brand")], [row(2, "Model", "Brand")])
            second = self.match([product(1, "Model", "Brand")], [row(2, "Model", "Brand")])
            self.assertEqual(first, second)
            self.assertEqual(marker.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
