"""Authenticated access to Bitrix's single mutable manager-comment field."""

import time
from urllib.parse import urlsplit

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class BitrixOrderCommentError(RuntimeError):
    def __init__(self, message, code="request_failed", current=None):
        super().__init__(message)
        self.code = code
        self.current = current


class BitrixOrderCommentsClient:
    """Read and update Bitrix ``sale_order.COMMENTS`` through our endpoint."""

    def __init__(self, endpoint, token, timeout=(3.05, 10), max_retries=2,
                 session=None):
        parsed = urlsplit(str(endpoint or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            raise BitrixOrderCommentError(
                "Bitrix comment endpoint must use HTTPS", "not_configured"
            )
        if not str(token or "").strip():
            raise BitrixOrderCommentError(
                "Bitrix comment token is not configured", "not_configured"
            )
        self.endpoint = str(endpoint)
        self.headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + str(token).strip(),
        }
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.session = session or requests.Session()

    def _request(self, method, *, params=None, json=None):
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method, self.endpoint, params=params, json=json,
                    headers=self.headers, timeout=self.timeout,
                )
            except requests.RequestException as error:
                if attempt < self.max_retries:
                    time.sleep(min(0.25 * (2 ** attempt), 2))
                    continue
                raise BitrixOrderCommentError(
                    "Bitrix comment request failed ({})".format(
                        type(error).__name__
                    )
                ) from None

            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code == 409:
                raise BitrixOrderCommentError(
                    "Bitrix manager comment changed concurrently",
                    "conflict",
                    payload.get("current") if isinstance(payload, dict) else None,
                )
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                time.sleep(min(0.25 * (2 ** attempt), 2))
                continue
            if response.status_code in {401, 403}:
                raise BitrixOrderCommentError(
                    "Bitrix comment access denied", "access_denied"
                )
            if response.status_code >= 400:
                code = payload.get("error") if isinstance(payload, dict) else None
                raise BitrixOrderCommentError(
                    "Bitrix comment request failed: HTTP {}".format(
                        response.status_code
                    ),
                    str(code or "request_failed"),
                )
            if not isinstance(payload, dict) or not isinstance(payload.get("comment"), dict):
                raise BitrixOrderCommentError(
                    "Bitrix comment response has an unexpected structure"
                )
            return payload["comment"]
        raise BitrixOrderCommentError("Bitrix comment request failed")

    def get(self, order_id):
        return self._request("GET", params={"order_id": str(order_id)})

    def update(self, order_id, text, expected_hash):
        return self._request("POST", json={
            "order_id": str(order_id),
            "text": str(text),
            "expected_hash": str(expected_hash or ""),
        })
