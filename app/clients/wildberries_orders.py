"""Strictly GET-only Wildberries API client.

The module keeps the historical ``WildberriesOrdersReadOnlyClient`` name so the
existing FBS integration continues to work, while all WB reads share one safe
transport.  Response bodies and authorization values are never logged.
"""

from __future__ import print_function

import logging
import time
from urllib.parse import urlsplit

import requests


DEFAULT_BASE_URL = "https://marketplace-api.wildberries.ru"
SERVICE_ORIGINS = {
    "analytics": "https://seller-analytics-api.wildberries.ru",
    "common": "https://common-api.wildberries.ru",
    "content": "https://content-api.wildberries.ru",
    "marketplace": DEFAULT_BASE_URL,
    "prices": "https://discounts-prices-api.wildberries.ru",
    "statistics": "https://statistics-api.wildberries.ru",
}
RETRYABLE_STATUS_CODES = frozenset((429, 500, 502, 503, 504))
READ_ONLY_METHOD = "GET"


class WildberriesReadOnlyError(RuntimeError):
    """A user-safe API error that never contains credentials or response data."""

    def __init__(self, message, code="WB_API_ERROR", status_code=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def mask_secret(value):
    """Return a stable redaction marker without exposing any token characters."""
    return "[REDACTED]" if str(value or "") else "[NOT_CONFIGURED]"


def _retry_after(response, fallback):
    value = getattr(response, "headers", {}).get("Retry-After")
    try:
        return max(0.0, min(float(value), 30.0))
    except (TypeError, ValueError):
        return max(0.0, min(float(fallback), 30.0))


class WildberriesReadOnlyClient:
    """Allowlisted WB transport which is structurally unable to make writes."""

    def __init__(
        self,
        token,
        timeout=(3.05, 15),
        max_retries=2,
        session=None,
        sleep=None,
        logger=None,
        marketplace_base_url=DEFAULT_BASE_URL,
    ):
        self._token = str(token or "").strip()
        self.timeout = timeout
        self.max_retries = max(0, min(int(max_retries), 5))
        self.session = session or requests.Session()
        self.sleep = sleep or time.sleep
        self.logger = logger or logging.getLogger(__name__)
        self.origins = dict(SERVICE_ORIGINS)
        self.origins["marketplace"] = self._validated_marketplace_origin(
            marketplace_base_url
        )
        self.request_audit = []

    @staticmethod
    def _validated_marketplace_origin(value):
        origin = str(value or DEFAULT_BASE_URL).rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "marketplace-api.wildberries.ru"
            or parsed.port not in (None, 443)
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise WildberriesReadOnlyError(
                "Некорректный адрес Wildberries API", "WB_INVALID_BASE_URL"
            )
        return origin

    def __repr__(self):
        return "{}(token={}, max_retries={})".format(
            type(self).__name__, mask_secret(self._token), self.max_retries
        )

    def _headers(self):
        if not self._token:
            raise WildberriesReadOnlyError(
                "Wildberries API не настроен", "WB_NOT_CONFIGURED"
            )
        return {"Authorization": self._token, "Accept": "application/json"}

    def request_json(self, method, service, path, params=None):
        """Execute one allowlisted GET. No write method is accepted."""
        if str(method or "").upper() != READ_ONLY_METHOD:
            raise WildberriesReadOnlyError(
                "Записывающие запросы Wildberries запрещены",
                "WB_READ_ONLY_GUARANTEE",
            )
        if service not in self.origins:
            raise WildberriesReadOnlyError(
                "Неизвестное направление Wildberries API", "WB_INVALID_SERVICE"
            )
        if not str(path or "").startswith("/") or "?" in path or "#" in path:
            raise WildberriesReadOnlyError(
                "Некорректный путь Wildberries API", "WB_INVALID_PATH"
            )
        url = self.origins[service] + path
        headers = self._headers()
        for attempt in range(self.max_retries + 1):
            self.request_audit.append({
                "method": READ_ONLY_METHOD,
                "service": service,
                "path": path,
                "attempt": attempt + 1,
            })
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.Timeout:
                if attempt < self.max_retries:
                    self.sleep(min(0.5 * (2 ** attempt), 4.0))
                    continue
                raise WildberriesReadOnlyError(
                    "Wildberries API не ответил вовремя", "WB_TIMEOUT"
                ) from None
            except requests.RequestException:
                if attempt < self.max_retries:
                    self.sleep(min(0.5 * (2 ** attempt), 4.0))
                    continue
                raise WildberriesReadOnlyError(
                    "Wildberries API временно недоступен", "WB_UNAVAILABLE"
                ) from None

            status = int(response.status_code)
            if status in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                self.logger.warning(
                    "Wildberries GET retry service=%s status=%s attempt=%s",
                    service,
                    status,
                    attempt + 1,
                )
                self.sleep(_retry_after(response, 0.5 * (2 ** attempt)))
                continue
            if status == 401:
                raise WildberriesReadOnlyError(
                    "Wildberries отклонил токен", "WB_UNAUTHORIZED", status
                )
            if status == 403:
                raise WildberriesReadOnlyError(
                    "Токен Wildberries не имеет доступа к направлению {}".format(
                        service
                    ),
                    "WB_FORBIDDEN",
                    status,
                )
            if status == 429:
                raise WildberriesReadOnlyError(
                    "Превышен лимит запросов Wildberries",
                    "WB_RATE_LIMITED",
                    status,
                )
            if status >= 500:
                raise WildberriesReadOnlyError(
                    "Wildberries API временно недоступен", "WB_SERVER_ERROR", status
                )
            if status >= 400:
                raise WildberriesReadOnlyError(
                    "Wildberries вернул ошибку HTTP {}".format(status),
                    "WB_API_ERROR",
                    status,
                )
            try:
                return response.json()
            except ValueError:
                raise WildberriesReadOnlyError(
                    "Wildberries вернул некорректный ответ",
                    "WB_INVALID_RESPONSE",
                    status,
                ) from None
        raise WildberriesReadOnlyError(
            "Wildberries API временно недоступен", "WB_UNAVAILABLE"
        )

    def ping(self, service):
        payload = self.request_json("GET", service, "/ping")
        if not isinstance(payload, dict):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return payload

    def probe_services(self, services=None):
        result = {}
        for service in services or sorted(self.origins):
            try:
                self.ping(service)
                result[service] = {"available": True, "code": "WB_AUTH_OK"}
            except WildberriesReadOnlyError as error:
                result[service] = {"available": False, "code": error.code}
        return result

    def get_new_orders(self):
        payload = self.request_json(
            "GET", "marketplace", "/api/v3/orders/new"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in payload["orders"] if isinstance(row, dict)]

    def get_supplies(self, limit=100, next_value=0):
        payload = self.request_json(
            "GET",
            "marketplace",
            "/api/v3/supplies",
            params={"limit": max(1, min(int(limit), 1000)), "next": int(next_value)},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("supplies"), list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return payload

    def get_warehouses(self):
        payload = self.request_json("GET", "marketplace", "/api/v3/warehouses")
        if not isinstance(payload, list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in payload if isinstance(row, dict)]

    def get_prices(self, limit=1000, offset=0):
        payload = self.request_json(
            "GET",
            "prices",
            "/api/v2/list/goods/filter",
            params={"limit": max(1, min(int(limit), 1000)), "offset": max(0, int(offset))},
        )
        rows = payload.get("data", {}).get("listGoods") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in rows if isinstance(row, dict)]

    def get_statistics_stocks(self, date_from):
        payload = self.request_json(
            "GET",
            "statistics",
            "/api/v1/supplier/stocks",
            params={"dateFrom": str(date_from)},
        )
        if not isinstance(payload, list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in payload if isinstance(row, dict)]

    def get_analytics_downloads(self):
        payload = self.request_json(
            "GET", "analytics", "/api/v2/nm-report/downloads"
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def content_cards_unavailable():
        raise WildberriesReadOnlyError(
            "Карточки WB доступны только через POST и отключены политикой GET-only",
            "WB_POST_READ_BLOCKED",
        )

    @staticmethod
    def marketplace_stocks_unavailable():
        raise WildberriesReadOnlyError(
            "Остатки FBS доступны только через POST и отключены политикой GET-only",
            "WB_POST_READ_BLOCKED",
        )


class WildberriesOrdersReadOnlyClient(WildberriesReadOnlyClient):
    """Backward-compatible entry point for the existing FBS order sync."""

    def __init__(
        self,
        token,
        base_url=DEFAULT_BASE_URL,
        timeout=(3.05, 15),
        max_retries=2,
        session=None,
        sleep=None,
        logger=None,
    ):
        super().__init__(
            token=token,
            timeout=timeout,
            max_retries=max_retries,
            session=session,
            sleep=sleep,
            logger=logger,
            marketplace_base_url=base_url,
        )
