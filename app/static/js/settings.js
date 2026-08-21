(function () {
    "use strict";

    function showNotice(form, message, kind) {
        var status = document.getElementById("settingsFormStatus");
        if (status) {
            status.textContent = message;
            status.className = "settings-form-status is-" + kind;
        }
        if (window.VechasuNotify) {
            window.VechasuNotify[
                kind === "error" ? "error" : kind === "success" ? "success" : "info"
            ](message);
            return;
        }
        var notice = document.getElementById("settingsNotice");
        if (!notice) {
            notice = document.createElement("div");
            notice.id = "settingsNotice";
            notice.setAttribute("role", "status");
            form.parentNode.insertBefore(notice, form);
        }
        notice.className = "notice notice-" + kind;
        notice.textContent = message;
    }

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("settingsForm");
        if (!form || !window.fetch) {
            return;
        }
        var submit = form.querySelector('button[type="submit"]');
        var csrf = form.querySelector('input[name="csrf_token"]');
        var fields = ["company_name", "erp_name", "low_stock_threshold"];
        var baseline = {};
        var saving = false;

        function errorNode(name) {
            return form.querySelector('[data-settings-error="' + name + '"]');
        }

        function clearFieldError(name) {
            var input = form.elements[name];
            var error = errorNode(name);
            if (input) {
                input.removeAttribute("aria-invalid");
            }
            if (error) {
                error.textContent = "";
            }
        }

        function setFieldError(name, message) {
            var input = form.elements[name];
            var error = errorNode(name);
            if (!input || !error) {
                return false;
            }
            input.setAttribute("aria-invalid", "true");
            error.textContent = message;
            return true;
        }

        function clearFieldErrors() {
            fields.forEach(clearFieldError);
        }

        fields.forEach(function (name) {
            baseline[name] = form.elements[name].value;
            form.elements[name].addEventListener("input", function () {
                clearFieldError(name);
            });
            form.elements[name].addEventListener("invalid", function () {
                var message = name === "low_stock_threshold"
                    ? "Введите целое число от 0 до 999."
                    : "Заполните это поле.";
                setFieldError(name, message);
            });
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            if (saving) {
                return;
            }
            clearFieldErrors();
            var changes = {};
            fields.forEach(function (name) {
                var value = form.elements[name].value;
                if (value !== baseline[name]) {
                    changes[name] = name === "low_stock_threshold"
                        ? Number(value || 0)
                        : value;
                }
            });
            if (!Object.keys(changes).length) {
                showNotice(form, "Настройки не изменились", "info");
                return;
            }

            saving = true;
            submit.disabled = true;
            submit.setAttribute("aria-disabled", "true");
            form.setAttribute("aria-busy", "true");
            submit.textContent = "Сохраняем…";
            fetch("/api/v1/settings", {
                method: "PATCH",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrf ? csrf.value : ""
                },
                body: JSON.stringify(changes)
            }).then(function (response) {
                return response.json().catch(function () {
                    return {};
                }).then(function (payload) {
                    if (!response.ok) {
                        var requestError = new Error(
                            payload.message || "Сервер не смог сохранить настройки. Повторите позже."
                        );
                        requestError.code = payload.code;
                        requestError.fields = payload.fields || [];
                        throw requestError;
                    }
                    return payload;
                });
            }).then(function (payload) {
                fields.forEach(function (name) {
                    form.elements[name].value = String(payload.data[name]);
                    baseline[name] = form.elements[name].value;
                });
                var title = document.querySelector(".sidebar-brand-title");
                var subtitle = document.querySelector(".sidebar-brand-subtitle");
                if (title) {
                    title.textContent = payload.data.erp_name;
                }
                if (subtitle) {
                    subtitle.textContent = payload.data.company_name;
                }
                showNotice(form, "Настройки сохранены", "success");
            }).catch(function (error) {
                var fieldNames = error.fields || [];
                var fieldErrorShown = false;
                fieldNames.forEach(function (name) {
                    fieldErrorShown = setFieldError(name, error.message) || fieldErrorShown;
                });
                if (
                    !fieldErrorShown &&
                    error.code === "SETTINGS_VALIDATION_FAILED" &&
                    error.message.indexOf("Минимальный остаток") !== -1
                ) {
                    fieldErrorShown = setFieldError(
                        "low_stock_threshold",
                        error.message
                    );
                }
                showNotice(form, error.message, "error");
                if (fieldErrorShown) {
                    form.querySelector('[aria-invalid="true"]').focus();
                }
            }).finally(function () {
                saving = false;
                submit.disabled = false;
                submit.removeAttribute("aria-disabled");
                form.removeAttribute("aria-busy");
                submit.textContent = "Сохранить настройки";
            });
        });

    });
})();
