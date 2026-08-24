"""Read-only Wildberries FBS orders client."""

from urllib.parse import urlsplit

import requests


DEFAULT_BASE_URL = "https://marketplace-api.wildberries.ru"


class WildberriesReadOnlyError(RuntimeError):
    """A user-safe API error that never contains the token."""

    def __init__(self, message, code="WB_API_ERROR"):
        super().__init__(message)
        self.code = code


class WildberriesOrdersReadOnlyClient:
    """Fetch new FBS assembly orders without calling any write endpoint."""

    def __init__(self, token, base_url=DEFAULT_BASE_URL, timeout=(3.05, 15), session=None):
        self.token = str(token or "").strip()
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WildberriesReadOnlyError(
                "Некорректный адрес Wildberries API", "WB_INVALID_BASE_URL"
            )
        self.timeout = timeout
        self.session = session or requests.Session()

    def get_new_orders(self):
        if not self.token:
            raise WildberriesReadOnlyError(
                "Wildberries API не настроен", "WB_NOT_CONFIGURED"
            )
        try:
            response = self.session.get(
                self.base_url + "/api/v3/orders/new",
                headers={"Authorization": self.token, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise WildberriesReadOnlyError(
                "Wildberries API не ответил вовремя", "WB_TIMEOUT"
            ) from None
        except requests.RequestException:
            raise WildberriesReadOnlyError(
                "Wildberries API временно недоступен", "WB_UNAVAILABLE"
            ) from None

        messages = {
            401: ("Wildberries отклонил токен", "WB_UNAUTHORIZED"),
            403: ("Токен Wildberries не имеет доступа к заказам FBS", "WB_FORBIDDEN"),
            429: ("Превышен лимит запросов Wildberries", "WB_RATE_LIMITED"),
        }
        if response.status_code in messages:
            message, code = messages[response.status_code]
            raise WildberriesReadOnlyError(message, code)
        if response.status_code >= 500:
            raise WildberriesReadOnlyError(
                "Wildberries API временно недоступен", "WB_SERVER_ERROR"
            )
        if response.status_code >= 400:
            raise WildberriesReadOnlyError(
                "Wildberries вернул ошибку HTTP {}".format(response.status_code),
                "WB_API_ERROR",
            )
        try:
            payload = response.json()
        except ValueError:
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            ) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
            raise WildberriesReadOnlyError(
                "Wildberries вернул некорректный ответ", "WB_INVALID_RESPONSE"
            )
        return [row for row in payload["orders"] if isinstance(row, dict)]
