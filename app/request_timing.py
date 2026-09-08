"""Opt-in, aggregate-only timings for authenticated Orders navigation.

No SQL text, parameters, URLs, credentials or response bodies are recorded.
The thread-local scope includes Flask session loading/saving and is removed
before the worker handles its next request. Disabled requests use native SQLite.
"""
import functools
import sqlite3
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs

from flask import g, request
import requests.sessions

_local = threading.local()
_connect = sqlite3.connect
_send = requests.sessions.Session.send


def _measure(group, function, *args, **kwargs):
    metrics = getattr(_local, "metrics", None)
    if metrics is None:
        return function(*args, **kwargs)
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        metrics[group] += (time.perf_counter() - started) * 1000


class TimedCursor(sqlite3.Cursor):
    def execute(self, *args, **kwargs):
        metrics = getattr(_local, "metrics", None)
        if metrics is not None:
            metrics["queries"] += 1
        return _measure("sql", super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):
        metrics = getattr(_local, "metrics", None)
        if metrics is not None:
            metrics["queries"] += 1
        return _measure("sql", super().executemany, *args, **kwargs)

    def fetchone(self):
        return _measure("sql", super().fetchone)

    def fetchmany(self, *args):
        return _measure("sql", super().fetchmany, *args)

    def fetchall(self):
        return _measure("sql", super().fetchall)

    def __next__(self):
        return _measure("sql", super().__next__)


class TimedConnection(sqlite3.Connection):
    def cursor(self, factory=TimedCursor):
        return super().cursor(factory)

    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    def commit(self):
        return _measure("sql", super().commit)

    def rollback(self):
        return _measure("sql", super().rollback)

    def __exit__(self, *args):
        return _measure("sql", super().__exit__, *args)


def _timed_connect(*args, **kwargs):
    if getattr(_local, "metrics", None) is not None and len(args) < 6 and "factory" not in kwargs:
        kwargs["factory"] = TimedConnection
    return _measure("sql", _connect, *args, **kwargs)


def _timed_send(self, *args, **kwargs):
    metrics = getattr(_local, "metrics", None)
    if metrics is not None:
        metrics["external_calls"] += 1
    return _measure("external", _send, self, *args, **kwargs)


def register_order_request_timing(app):
    sqlite3.connect = _timed_connect
    requests.sessions.Session.send = _timed_send
    original_get_template = app.jinja_env.get_template

    def get_template(*args, **kwargs):
        metrics = getattr(_local, "metrics", None)
        if metrics is not None and not metrics["template_depth"]:
            return _measure("template", original_get_template, *args, **kwargs)
        return original_get_template(*args, **kwargs)

    app.jinja_env.get_template = get_template

    class TimedTemplate(app.jinja_env.template_class):
        def render(self, *args, **kwargs):
            metrics = getattr(_local, "metrics", None)
            if metrics is None or metrics["template_depth"]:
                return super().render(*args, **kwargs)
            metrics["template_depth"] += 1
            try:
                return _measure("template", super().render, *args, **kwargs)
            finally:
                metrics["template_depth"] -= 1

    app.jinja_env.template_class = TimedTemplate

    for method in ("open_session", "save_session"):
        original = getattr(app.session_interface, method)

        def wrap(function):
            @functools.wraps(function)
            def timed(*args, **kwargs):
                return _measure("session", function, *args, **kwargs)
            return timed

        setattr(app.session_interface, method, wrap(original))

    @app.after_request
    def authorize_timing(response):
        if getattr(_local, "metrics", None) is not None and getattr(g, "current_user", None):
            request.environ["orders.timing.authorized"] = True
            if request.args.get("orders_profile") == "1":
                response.set_cookie("erp_orders_profile", "1", max_age=3600, secure=True, httponly=True, samesite="Strict")
            elif request.args.get("orders_profile") == "0":
                response.delete_cookie("erp_orders_profile")
            response.cache_control.no_store = True
        return response

    original_wsgi = app.wsgi_app

    def timed_wsgi(environ, start_response):
        path = environ.get("PATH_INFO", "")
        eligible = environ.get("REQUEST_METHOD") == "GET" and (
            path in ("/orders", "/app/orders", "/api/orders")
            or path.startswith(("/order/", "/api/orders/", "/api/v1/tasks"))
        )
        cookies = SimpleCookie()
        if eligible:
            try:
                cookies.load(environ.get("HTTP_COOKIE", ""))
            except CookieError:
                pass
        query = parse_qs(environ.get("QUERY_STRING", "")) if eligible else {}
        enabled = eligible and (query.get("orders_profile") == ["1"] or (
            "erp_orders_profile" in cookies and cookies["erp_orders_profile"].value == "1"
        ))
        if not enabled:
            return original_wsgi(environ, start_response)
        metrics = dict(sql=0.0, queries=0, template=0.0, session=0.0, external=0.0, external_calls=0, template_depth=0)
        previous = getattr(_local, "metrics", None)
        _local.metrics = metrics
        environ["orders.timing.enabled"] = True
        started = time.perf_counter()

        def timed_start(status, headers, exc_info=None):
            if environ.get("orders.timing.authorized"):
                metrics["backend"] = (time.perf_counter() - started) * 1000
                headers.append(("Server-Timing", ", ".join(
                    "{};dur={:.2f}".format(key, metrics[key])
                    for key in ("backend", "sql", "template", "session", "external", "queries", "external_calls")
                )))
            return start_response(status, headers, exc_info)

        try:
            return original_wsgi(environ, timed_start)
        finally:
            _local.metrics = previous

    app.wsgi_app = timed_wsgi
