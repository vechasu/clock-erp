"""Server-only, redaction-safe SmsBliss JSON API client."""

from __future__ import print_function

import json
import os
from urllib.parse import urlsplit

import requests


DEFAULT_BASE_URL = "https://api.smsbliss.net/messages/v2"


class SmsBlissError(Exception):
    code = "provider_error"


class SmsBlissNotConfigured(SmsBlissError):
    code = "not_configured"


class SmsBlissSecurityError(SmsBlissError):
    code = "insecure_endpoint"


class SmsBlissUnavailable(SmsBlissError):
    code = "unavailable"


class SmsBlissInvalidResponse(SmsBlissError):
    code = "invalid_response"


class SmsBlissUnknownDelivery(SmsBlissError):
    code = "unknown_delivery"


class SmsBlissClient:
    def __init__(self, login=None, password=None, base_url=None, queue_name=None,
                 session=None, connect_timeout=5, read_timeout=15):
        self.login = str(login if login is not None else os.getenv("SMSBLISS_LOGIN", "")).strip()
        self.password = str(password if password is not None else os.getenv("SMSBLISS_PASSWORD", "")).strip()
        self.base_url = str(base_url if base_url is not None else os.getenv(
            "SMSBLISS_API_BASE_URL", DEFAULT_BASE_URL
        )).strip().rstrip("/")
        self.queue_name = str(queue_name if queue_name is not None else os.getenv(
            "SMSBLISS_STATUS_QUEUE_NAME", ""
        )).strip()
        self.session = session or requests.Session()
        self.timeout = (float(connect_timeout), float(read_timeout))
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise SmsBlissSecurityError("SmsBliss endpoint must use HTTPS")

    @property
    def configured(self):
        return bool(self.login and self.password)

    @property
    def masked_login(self):
        if not self.login:
            return "—"
        if len(self.login) <= 2:
            return "*" * len(self.login)
        return self.login[:1] + "***" + self.login[-1:]

    def _payload(self, values=None):
        if not self.configured:
            raise SmsBlissNotConfigured(
                "Set SMSBLISS_LOGIN and SMSBLISS_PASSWORD"
            )
        payload = {"login": self.login, "password": self.password}
        payload.update(values or {})
        return payload

    def _post(self, endpoint, values=None, delivery_sensitive=False):
        try:
            response = self.session.post(
                self.base_url + "/" + endpoint.lstrip("/"),
                json=self._payload(values),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError) as error:
            exception = SmsBlissUnknownDelivery if delivery_sensitive else SmsBlissUnavailable
            raise exception("SmsBliss request did not complete") from error
        except requests.RequestException as error:
            exception = SmsBlissUnknownDelivery if delivery_sensitive else SmsBlissUnavailable
            raise exception("SmsBliss request failed") from error
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            exception = SmsBlissUnknownDelivery if delivery_sensitive else SmsBlissInvalidResponse
            raise exception("SmsBliss returned invalid JSON") from error
        if not isinstance(payload, dict):
            exception = SmsBlissUnknownDelivery if delivery_sensitive else SmsBlissInvalidResponse
            raise exception("SmsBliss returned invalid data")
        return payload

    def send(self, client_message_id, phone, text, sender="", scheduled_at=""):
        message = {"clientId": client_message_id, "phone": phone, "text": text}
        if sender:
            message["sender"] = sender
        values = {"messages": [message], "showBillingDetails": True}
        if self.queue_name:
            values["statusQueueName"] = self.queue_name
        if scheduled_at:
            values["scheduleTime"] = scheduled_at
        return self._post("send.json", values, delivery_sensitive=True)

    def statuses(self, messages):
        rows = list(messages or [])
        if len(rows) > 200:
            raise ValueError("SmsBliss status request is limited to 200 messages")
        values = {"messages": [
            {key: row[key] for key in ("smscId", "clientId") if row.get(key)}
            for row in rows
        ]}
        return self._post("status.json", values)

    def status_queue(self, limit=200):
        if not self.queue_name:
            raise SmsBlissNotConfigured("Set SMSBLISS_STATUS_QUEUE_NAME")
        return self._post("statusQueue.json", {
            "statusQueueName": self.queue_name,
            "statusQueueLimit": str(max(1, min(1000, int(limit)))),
        })

    def balance(self):
        return self._post("balance.json")

    def senders(self):
        return self._post("senders.json")

    def version(self):
        return self._post("version.json")

    def connection_check(self):
        return {
            "version": self.version(),
            "balance": self.balance(),
            "senders": self.senders(),
        }
