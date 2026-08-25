import requests
from app.config import BITRIX_LOGIN, BITRIX_PASSWORD, BITRIX_EXCHANGE_URL


class BitrixConfigurationError(RuntimeError):
    pass


class BitrixClient:
    def __init__(self, login=None, password=None, exchange_url=None, session=None):
        self.login = str(BITRIX_LOGIN if login is None else login or "").strip()
        self.password = str(BITRIX_PASSWORD if password is None else password or "").strip()
        self.exchange_url = str(
            BITRIX_EXCHANGE_URL if exchange_url is None else exchange_url or ""
        ).strip()
        self.session = session or requests.Session()

    def check_connection(self):
        if not self.login or not self.password or not self.exchange_url:
            raise BitrixConfigurationError("Bitrix exchange integration is not configured")
        response = self.session.get(
            self.exchange_url,
            params={
                "type": "sale",
                "mode": "checkauth",
            },
            auth=(self.login, self.password),
            timeout=(3.05, 15),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise BitrixConfigurationError("Bitrix exchange redirect was blocked")
        response.raise_for_status()
        return response.text
