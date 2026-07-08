import requests
from app.config import BITRIX_LOGIN, BITRIX_PASSWORD, BITRIX_EXCHANGE_URL


class BitrixClient:
    def __init__(self):
        self.session = requests.Session()

    def check_connection(self):
        response = self.session.get(
            BITRIX_EXCHANGE_URL,
            params={
                "type": "sale",
                "mode": "checkauth",
            },
            auth=(BITRIX_LOGIN, BITRIX_PASSWORD),
        )

        print("Status:", response.status_code)
        print(response.text)

        return response.text