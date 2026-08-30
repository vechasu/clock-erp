import re
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class FaviconTests(unittest.TestCase):
    def test_svg_is_transparent_black_ttt_mark(self):
        source = (STATIC / "favicon.svg").read_text(encoding="utf-8")
        self.assertIn('viewBox="0 0 32 32"', source)
        self.assertIn('fill="#000"', source)
        self.assertNotIn("<rect", source)
        self.assertNotRegex(source, r"background|circle|text|shadow")

    def test_every_jinja_document_head_includes_favicon(self):
        documents = []
        for path in TEMPLATES.glob("*.html"):
            source = path.read_text(encoding="utf-8")
            if re.search(r"<head(?:\s|>)", source):
                documents.append(path)
                head = source.split("</head>", 1)[0]
                self.assertIn(
                    '{% include "_favicon.html" %}',
                    head,
                    str(path.relative_to(ROOT)),
                )
        self.assertGreater(len(documents), 20)

    def test_fallback_dimensions_and_transparency(self):
        for filename, expected_size in (
            ("favicon-16x16.png", 16),
            ("favicon-32x32.png", 32),
        ):
            payload = (STATIC / filename).read_bytes()
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", payload[16:24])
            self.assertEqual((width, height), (expected_size, expected_size))
            self.assertEqual(payload[25], 6, "PNG must use RGBA color")

        ico = (STATIC / "favicon.ico").read_bytes()
        reserved, image_type, count = struct.unpack("<HHH", ico[:6])
        self.assertEqual((reserved, image_type, count), (0, 1, 2))

    def test_react_entrypoints_use_the_same_assets(self):
        for relative_path in ("frontend/index.html", "app/static/react/index.html"):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            head = source.split("</head>", 1)[0]
            self.assertIn('/static/favicon.svg', head)
            self.assertIn('/static/favicon-32x32.png', head)
            self.assertIn('/static/favicon-16x16.png', head)
            self.assertIn('/static/favicon.ico', head)


if __name__ == "__main__":
    unittest.main()
