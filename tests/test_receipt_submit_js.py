import unittest
from pathlib import Path


class ReceiptSubmitJsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("app/static/js/receipt-submit.js").read_text(
            encoding="utf-8"
        )

    def test_create_payload_keeps_one_photo_positions_and_submit_mode(self):
        self.assertEqual(
            self.source.count('payload.set("product_image"'),
            1,
        )
        self.assertIn('payload.delete("product_image")', self.source)
        self.assertIn('payload.set(\n            "positions"', self.source)
        for field in (
            "product_id",
            "brand_id",
            "category_id",
            "quantity",
            "purchase_price",
        ):
            self.assertIn(field + ":", self.source)
        self.assertIn('payload.set(\n            "submit_mode"', self.source)
        self.assertIn(
            'submitMode === "create_next" ? "create_next" : "close"',
            self.source,
        )

    def test_idempotency_key_is_stable_for_form_retry(self):
        key_function = self.source.split("function submissionKey", 1)[1].split(
            "function successUrl", 1
        )[0]
        self.assertIn("if (!form.dataset.idempotencyKey)", key_function)
        self.assertIn("return form.dataset.idempotencyKey", key_function)
        self.assertIn('"Idempotency-Key": idempotencyKey', self.source)

    def test_errors_cover_validation_photo_session_and_server_states(self):
        for marker in (
            "Сессия завершена",
            "Максимальный размер — 3 МБ",
            "Ошибка сервера при сохранении прихода",
            "Проверьте заполненные поля прихода",
            "Не удалось связаться с сервером",
        ):
            self.assertIn(marker, self.source)

    def test_both_success_modes_keep_distinct_navigation(self):
        self.assertIn('submitMode === "create_next"', self.source)
        self.assertIn('query.set("open_receipt_modal", "1")', self.source)
        self.assertIn('return "/receipts?" + query.toString()', self.source)


if __name__ == "__main__":
    unittest.main()
