import os
from dotenv import load_dotenv

load_dotenv()

MOYSKLAD_TOKEN = os.getenv("MOYSKLAD_TOKEN")

BITRIX_LOGIN = os.getenv("BITRIX_LOGIN")
BITRIX_PASSWORD = os.getenv("BITRIX_PASSWORD")
BITRIX_EXCHANGE_URL = os.getenv("BITRIX_EXCHANGE_URL")