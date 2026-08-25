import io
import unittest
from unittest import mock

import requests

from app.clients import moysklad


PUBLIC_DNS = lambda *args, **kwargs: [
    (2, 1, 6, "", ("93.184.216.34", 443))
]


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks or []
        self._payload = payload
        self.closed = False

    def iter_content(self, unused_size):
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def close(self):
        self.closed = True


class MoySkladUrlPolicyTests(unittest.TestCase):
    def assert_blocked(self, url, resolver=PUBLIC_DNS):
        with self.assertRaises(moysklad.MoySkladError) as raised:
            moysklad._trusted_moysklad_url(url, resolve=True, resolver=resolver)
        self.assertEqual(raised.exception.code, "MOYSKLAD_BLOCKED_URL")

    def test_exact_https_origin_normalization(self):
        for url in (
            "https://api.moysklad.ru/path",
            "https://API.MOYSKLAD.RU/path",
            "https://api.moysklad.ru:443/path",
            "https://miniature-prod.moysklad.ru/path",
            "https://tinyimage-prod.moysklad.ru/path",
        ):
            self.assertTrue(
                moysklad._trusted_moysklad_url(
                    url, resolve=True, resolver=PUBLIC_DNS
                ).startswith("https://")
            )

    def test_untrusted_and_ambiguous_urls_are_blocked(self):
        for url in (
            "http://api.moysklad.ru/path",
            "ftp://api.moysklad.ru/path",
            "file:///etc/passwd",
            "data:image/png;base64,AAAA",
            "//api.moysklad.ru/path",
            "https:///missing-host",
            "not a url",
            "https://api.moysklad.ru.attacker.test/path",
            "https://attacker-api.moysklad.ru/path",
            "https://api.moysklad.ru./path",
            "https://user:pass@api.moysklad.ru/path",
            "https://api.moysklad.ru:444/path",
            "https://localhost/path",
            "https://127.0.0.1/path",
            "https://[::1]/path",
            "https://2130706433/path",
            "https://0x7f000001/path",
            "https://017700000001/path",
            "https://169.254.169.254/latest/meta-data/",
            "https://api%2emoysklad.ru/path",
        ):
            self.assert_blocked(url)

    def test_dns_private_or_mixed_answers_are_blocked(self):
        for addresses in (
            ["127.0.0.1"],
            ["10.0.0.1"],
            ["169.254.169.254"],
            ["::1"],
            ["93.184.216.34", "192.168.1.1"],
        ):
            resolver = lambda *args, _addresses=addresses, **kwargs: [
                (2, 1, 6, "", (address, 443)) for address in _addresses
            ]
            self.assert_blocked(
                "https://api.moysklad.ru/image", resolver=resolver
            )

    def test_block_log_redacts_credentials_and_full_url(self):
        with self.assertLogs("app.clients.moysklad", level="WARNING") as captured:
            self.assert_blocked(
                "https://fixture-token@api.moysklad.ru/private?signature=secret"
            )
        output = " ".join(captured.output)
        self.assertNotIn("fixture-token", output)
        self.assertNotIn("signature", output)
        self.assertNotIn("secret", output)
        self.assertIn("host=api.moysklad.ru", output)


class MoySkladRequestSecurityTests(unittest.TestCase):
    def test_missing_token_fails_before_network(self):
        session = mock.Mock()
        client = moysklad.MoySkladClient(token="", session=session)
        with self.assertRaises(moysklad.MoySkladError) as raised:
            client.get("/entity/product")
        self.assertEqual(raised.exception.code, "MOYSKLAD_DISABLED")
        session.request.assert_not_called()

    def test_api_authorization_only_goes_to_fixed_origin(self):
        session = mock.Mock()
        session.request.return_value = FakeResponse(payload={"rows": []})
        client = moysklad.MoySkladClient(token="fixture-token", session=session)
        self.assertEqual(client.get("/entity/product"), {"rows": []})
        call = session.request.call_args
        self.assertEqual(call.args[1], "https://api.moysklad.ru/api/remap/1.2/entity/product")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer fixture-token")
        self.assertFalse(call.kwargs["allow_redirects"])
        with self.assertRaises(moysklad.MoySkladError):
            client.get("//attacker.test/steal")
        self.assertEqual(session.request.call_count, 1)

    def _client_with_images(self, responses):
        session = mock.Mock()
        session.get.side_effect = responses
        client = moysklad.MoySkladClient(
            token="fixture-token", session=session, resolver=PUBLIC_DNS
        )
        client.get_product_images = mock.Mock(return_value=[{
            "miniature": {"downloadHref": "https://api.moysklad.ru/image"}
        }])
        return client, session

    def test_valid_jpeg_png_gif_and_webp(self):
        fixtures = (
            ("image/jpeg", b"\xff\xd8\xffpayload"),
            ("image/png", b"\x89PNG\r\n\x1a\npayload"),
            ("image/gif", b"GIF89apayload"),
            ("image/webp", b"RIFF\x04\x00\x00\x00WEBPpayload"),
        )
        for content_type, content in fixtures:
            response = FakeResponse(
                headers={"Content-Type": content_type}, chunks=[content]
            )
            client, session = self._client_with_images([response])
            self.assertEqual(client.download_product_thumbnail("p"), (content, content_type))
            self.assertTrue(response.closed)
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    def test_cross_origin_redirect_is_blocked_without_second_request(self):
        redirect = FakeResponse(
            status=302, headers={"Location": "https://attacker.test/steal"}
        )
        client, session = self._client_with_images([redirect])
        with self.assertRaises(moysklad.MoySkladError) as raised:
            client.download_product_thumbnail("p")
        self.assertEqual(raised.exception.code, "MOYSKLAD_BLOCKED_URL")
        self.assertEqual(session.get.call_count, 1)
        self.assertTrue(redirect.closed)

    def test_cross_trusted_origin_redirect_does_not_receive_authorization(self):
        redirect = FakeResponse(status=302, headers={
            "Location": "https://miniature-prod.moysklad.ru/image"
        })
        client, session = self._client_with_images([redirect])
        with self.assertRaises(moysklad.MoySkladError) as raised:
            client.download_product_thumbnail("p")
        self.assertEqual(raised.exception.code, "MOYSKLAD_BLOCKED_REDIRECT")
        self.assertEqual(session.get.call_count, 1)

    def test_same_origin_relative_redirect_is_revalidated(self):
        redirect = FakeResponse(status=302, headers={"Location": "/image/final"})
        image = FakeResponse(
            headers={"Content-Type": "image/png"},
            chunks=[b"\x89PNG\r\n\x1a\nbody"],
        )
        client, session = self._client_with_images([redirect, image])
        client.download_product_thumbnail("p")
        self.assertEqual(session.get.call_count, 2)
        self.assertTrue(redirect.closed)
        self.assertTrue(image.closed)

    def test_invalid_type_signature_empty_and_oversize_are_rejected(self):
        responses = (
            FakeResponse(headers={"Content-Type": "text/html"}, chunks=[b"<html>"]),
            FakeResponse(headers={"Content-Type": "image/jpeg"}, chunks=[b"{}"]),
            FakeResponse(headers={"Content-Type": "image/jpeg"}, chunks=[]),
            FakeResponse(
                headers={
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(moysklad.MAX_THUMBNAIL_BYTES + 1),
                },
                chunks=[],
            ),
            FakeResponse(
                headers={"Content-Type": "image/jpeg", "Content-Length": "1"},
                chunks=[b"\xff\xd8\xff", b"x" * moysklad.MAX_THUMBNAIL_BYTES],
            ),
        )
        for response in responses:
            client, unused_session = self._client_with_images([response])
            with self.assertRaises(moysklad.MoySkladError):
                client.download_product_thumbnail("p")
            self.assertTrue(response.closed)

    def test_timeout_stream_failure_and_http_errors_are_sanitized(self):
        for response, code in (
            (requests.Timeout("fixture-token https://secret.invalid"), "MOYSKLAD_TIMEOUT"),
            (requests.ConnectionError("fixture-token"), "MOYSKLAD_UNAVAILABLE"),
            (FakeResponse(status=401), "MOYSKLAD_HTTP_ERROR"),
            (FakeResponse(status=403), "MOYSKLAD_HTTP_ERROR"),
            (FakeResponse(status=404), "MOYSKLAD_HTTP_ERROR"),
            (FakeResponse(status=429), "MOYSKLAD_RATE_LIMITED"),
            (FakeResponse(status=500), "MOYSKLAD_HTTP_ERROR"),
        ):
            client, unused_session = self._client_with_images([response])
            with self.assertRaises(moysklad.MoySkladError) as raised:
                client.download_product_thumbnail("p")
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("fixture-token", str(raised.exception))
            self.assertNotIn("secret.invalid", str(raised.exception))

    def test_response_is_closed_when_stream_breaks(self):
        response = FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            chunks=[b"\xff\xd8\xff", requests.ConnectionError("broken")],
        )
        client, unused_session = self._client_with_images([response])
        with self.assertRaises(moysklad.MoySkladError):
            client.download_product_thumbnail("p")
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
