"""Application orchestration for persisted system settings."""


class SettingsValidationError(ValueError):
    def __init__(self, message, fields=None):
        super().__init__(message)
        self.fields = fields


class SettingsApplication:
    allowed_fields = {
        "company_name",
        "erp_name",
        "low_stock_threshold",
    }

    def __init__(self, load_settings, save_settings):
        self._load_settings = load_settings
        self._save_settings = save_settings

    def get(self):
        return self._load_settings()

    def save_form(self, values, settings=None):
        settings = self._load_settings() if settings is None else settings
        company_name = (values.get("company_name") or "").strip()
        erp_name = (values.get("erp_name") or "").strip()
        try:
            low_stock_threshold = int(
                values.get("low_stock_threshold") or 0
            )
        except ValueError:
            low_stock_threshold = 0

        settings.update({
            "company_name": company_name or "Tictactoy",
            "erp_name": erp_name or "Vechasu ERP",
            "low_stock_threshold": max(
                0,
                min(low_stock_threshold, 999),
            ),
        })
        self._save_settings(settings)
        return settings

    def patch(self, payload, settings=None):
        settings = self._load_settings() if settings is None else settings
        unknown_fields = set(payload) - self.allowed_fields
        if unknown_fields:
            raise SettingsValidationError(
                "Переданы неизвестные настройки.",
                fields=sorted(unknown_fields),
            )

        changes = {}
        if "company_name" in payload:
            changes["company_name"] = (
                str(payload.get("company_name") or "").strip()
                or "Tictactoy"
            )
        if "erp_name" in payload:
            changes["erp_name"] = (
                str(payload.get("erp_name") or "").strip()
                or "Vechasu ERP"
            )
        if "low_stock_threshold" in payload:
            try:
                threshold = int(payload.get("low_stock_threshold") or 0)
            except (TypeError, ValueError):
                raise SettingsValidationError(
                    "Минимальный остаток должен быть целым числом."
                )
            changes["low_stock_threshold"] = max(
                0,
                min(threshold, 999),
            )

        changed_fields = [
            key for key, value in changes.items()
            if settings.get(key) != value
        ]
        if changed_fields:
            settings.update({
                key: changes[key]
                for key in changed_fields
            })
            self._save_settings(settings)
        return settings, changed_fields
