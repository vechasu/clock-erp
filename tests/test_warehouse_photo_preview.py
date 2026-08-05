import unittest
from pathlib import Path


class WarehousePhotoPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Path("app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )

    def test_only_real_thumbnails_are_preview_buttons(self):
        self.assertIn("warehouse-product-thumb-button", self.template)
        self.assertIn("openWarehousePhotoPreview(event, this)", self.template)
        self.assertIn(
            '<span class="warehouse-product-thumb '
            'warehouse-product-thumb-placeholder" aria-hidden="true">⌚</span>',
            self.template,
        )
        self.assertNotIn("onclick=\"openEditDrawer(this)\"\n"
                         "                                                >\n"
                         "                                                    <img", self.template)

    def test_preview_uses_lazy_cached_detail_request(self):
        self.assertIn("const warehousePhotoGalleryCache = new Map()", self.template)
        self.assertIn("warehousePhotoGalleryCache.has(detailUrl)", self.template)
        self.assertIn("fetch(detailUrl", self.template)
        self.assertIn("lightbox.dataset.productId === String(productId)", self.template)

    def test_single_and_multiple_photo_controls(self):
        self.assertIn("const multiple = productGalleryLightboxUrls.length > 1", self.template)
        self.assertIn("productGalleryLightboxPrev", self.template)
        self.assertIn("productGalleryLightboxNext", self.template)
        self.assertIn("productGalleryLightboxCounter", self.template)
        self.assertIn("productGalleryLightboxThumbnails", self.template)
        self.assertIn('event.key === "ArrowLeft"', self.template)
        self.assertIn('event.key === "ArrowRight"', self.template)

    def test_close_restores_focus_and_escape_is_supported(self):
        self.assertIn("productGalleryLightboxReturnFocus?.focus()", self.template)
        self.assertIn('event.key === "Escape"', self.template)
        self.assertIn('role="dialog"', self.template)
        self.assertIn('aria-modal="true"', self.template)


if __name__ == "__main__":
    unittest.main()
