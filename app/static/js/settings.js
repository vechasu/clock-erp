(function () {
    "use strict";

    function showNotice(form, message, kind) {
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

    function setNavigationVisibility(key, enabled) {
        document.querySelectorAll("[data-navigation-key]").forEach(function (element) {
            if (element.dataset.navigationKey === key && !element.classList.contains("js-navigation-toggle")) {
                element.hidden = !enabled;
            }
        });
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

        fields.forEach(function (name) {
            baseline[name] = form.elements[name].value;
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
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
                showNotice(form, "Изменений нет", "success");
                return;
            }

            submit.disabled = true;
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
                return response.json().then(function (payload) {
                    if (!response.ok) {
                        throw new Error(payload.message || "Не удалось сохранить настройки");
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
                showNotice(form, error.message, "error");
            }).finally(function () {
                submit.disabled = false;
                submit.textContent = "Сохранить настройки";
            });
        });

        document.querySelectorAll(".js-navigation-toggle").forEach(function (toggleForm) {
            toggleForm.addEventListener("submit", function (event) {
                event.preventDefault();
                var button = toggleForm.querySelector('button[type="submit"]');
                var token = toggleForm.querySelector('input[name="csrf_token"]');
                button.disabled = true;
                fetch(toggleForm.action, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Accept": "application/json",
                        "X-CSRF-Token": token ? token.value : ""
                    }
                }).then(function (response) {
                    return response.json().then(function (payload) {
                        if (!response.ok) {
                            throw new Error(payload.message || "Не удалось изменить вкладку");
                        }
                        return payload;
                    });
                }).then(function (payload) {
                    var item = payload.data;
                    button.setAttribute("aria-checked", item.enabled ? "true" : "false");
                    button.setAttribute(
                        "aria-label",
                        (item.enabled ? "Отключить" : "Включить") + " вкладку " + item.label
                    );
                    setNavigationVisibility(item.key, item.enabled);
                    showNotice(
                        form,
                        "Раздел «" + item.label + "» " + (item.enabled ? "включён" : "отключён"),
                        "success"
                    );
                }).catch(function (error) {
                    showNotice(form, error.message, "error");
                }).finally(function () {
                    button.disabled = false;
                });
            });
        });
    });
})();
