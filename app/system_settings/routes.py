"""Thin Flask adapters for the existing Settings HTTP contract."""

from flask import redirect, render_template, request, url_for

from app.system_settings.application import SettingsValidationError


class SettingsRoutes:
    def __init__(
        self,
        application,
        require_csrf,
        invitation_context,
        json_payload,
        api_success,
        api_error,
    ):
        self._application = application
        self._require_csrf = require_csrf
        self._invitation_context = invitation_context
        self._json_payload = json_payload
        self._api_success = api_success
        self._api_error = api_error

    def page(self):
        settings = self._application.get()
        if request.method == "POST":
            self._require_csrf()
            self._application.save_form(request.form, settings=settings)
            return redirect(
                url_for(
                    "settings_page",
                    notice="success",
                    message="Настройки сохранены",
                )
            )

        return render_template(
            "settings.html",
            settings=settings,
            notice=(request.args.get("notice") or "").strip(),
            message=(request.args.get("message") or "").strip(),
            **self._invitation_context(),
        )

    def api_resource(self):
        settings = self._application.get()
        if request.method == "GET":
            return self._api_success(settings)

        self._require_csrf()
        try:
            payload = self._json_payload()
        except ValueError as error:
            return self._api_error(
                "SETTINGS_VALIDATION_FAILED",
                str(error),
                400,
            )
        try:
            settings, changed_fields = self._application.patch(
                payload,
                settings=settings,
            )
        except SettingsValidationError as error:
            return self._api_error(
                "SETTINGS_VALIDATION_FAILED",
                str(error),
                422,
                error.fields,
            )
        return self._api_success(
            settings,
            changed_fields=changed_fields,
        )
