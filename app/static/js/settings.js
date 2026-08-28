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

        var navigationList = document.getElementById("navigationPreferencesList");
        var navigationSave = document.getElementById("navigationPreferencesSave");
        var navigationReset = document.getElementById("navigationPreferencesReset");
        var navigationStatus = document.getElementById("navigationPreferencesStatus");
        var draggedItem = null;

        function setNavigationStatus(message, kind) {
            if (navigationStatus) {
                navigationStatus.textContent = message;
                navigationStatus.className = "settings-form-status is-" + kind;
            }
        }

        function navigationItems() {
            return navigationList
                ? Array.prototype.slice.call(
                    navigationList.querySelectorAll("[data-navigation-preference-key]")
                )
                : [];
        }

        function updateMoveButtons() {
            var items = navigationItems();
            items.forEach(function (item, index) {
                var up = item.querySelector('[data-navigation-move="up"]');
                var down = item.querySelector('[data-navigation-move="down"]');
                up.disabled = index === 0;
                down.disabled = index === items.length - 1;
            });
        }

        function moveNavigationItem(item, direction) {
            var sibling = direction === "up"
                ? item.previousElementSibling
                : item.nextElementSibling;
            if (!sibling) {
                return;
            }
            if (direction === "up") {
                navigationList.insertBefore(item, sibling);
            } else {
                navigationList.insertBefore(sibling, item);
            }
            updateMoveButtons();
            setNavigationStatus(
                "Вкладка «" + item.querySelector(".navigation-preference-label").textContent.trim()
                + "» перемещена " + (direction === "up" ? "вверх." : "вниз."),
                "info"
            );
            item.querySelector('[data-navigation-move="' + direction + '"]').focus();
        }

        function requestNavigation(method, payload) {
            navigationSave.disabled = true;
            navigationReset.disabled = true;
            setNavigationStatus("Сохраняем…", "info");
            return fetch("/api/v1/navigation-preferences", {
                method: method,
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrf ? csrf.value : ""
                },
                body: payload ? JSON.stringify(payload) : undefined
            }).then(function (response) {
                return response.json().catch(function () {
                    return {};
                }).then(function (data) {
                    if (!response.ok) {
                        throw new Error(
                            data.message || "Не удалось сохранить настройки вкладок."
                        );
                    }
                    return data;
                });
            }).catch(function (error) {
                navigationSave.disabled = false;
                navigationReset.disabled = false;
                setNavigationStatus(error.message, "error");
                showNotice(form, error.message, "error");
                return null;
            });
        }

        if (navigationList && navigationSave && navigationReset) {
            updateMoveButtons();
            navigationList.addEventListener("click", function (event) {
                var button = event.target.closest("[data-navigation-move]");
                if (button) {
                    moveNavigationItem(
                        button.closest("[data-navigation-preference-key]"),
                        button.dataset.navigationMove
                    );
                }
            });
            navigationList.addEventListener("dragstart", function (event) {
                draggedItem = event.target.closest("[data-navigation-preference-key]");
                if (!draggedItem) {
                    return;
                }
                draggedItem.classList.add("is-dragging");
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData(
                    "text/plain", draggedItem.dataset.navigationPreferenceKey
                );
            });
            navigationList.addEventListener("dragover", function (event) {
                var target = event.target.closest("[data-navigation-preference-key]");
                if (!draggedItem || !target || target === draggedItem) {
                    return;
                }
                event.preventDefault();
                navigationItems().forEach(function (item) {
                    item.classList.remove("is-drag-target");
                });
                target.classList.add("is-drag-target");
            });
            navigationList.addEventListener("drop", function (event) {
                var target = event.target.closest("[data-navigation-preference-key]");
                if (!draggedItem || !target || target === draggedItem) {
                    return;
                }
                event.preventDefault();
                var box = target.getBoundingClientRect();
                navigationList.insertBefore(
                    draggedItem,
                    event.clientY < box.top + box.height / 2
                        ? target
                        : target.nextElementSibling
                );
                setNavigationStatus("Порядок вкладок изменён.", "info");
                updateMoveButtons();
            });
            navigationList.addEventListener("dragend", function () {
                navigationItems().forEach(function (item) {
                    item.classList.remove("is-dragging", "is-drag-target");
                });
                draggedItem = null;
            });
            navigationSave.addEventListener("click", function () {
                var items = navigationItems();
                requestNavigation("PUT", {
                    order: items.map(function (item) {
                        return item.dataset.navigationPreferenceKey;
                    }),
                    hidden: items.filter(function (item) {
                        var checkbox = item.querySelector("[data-navigation-visible]");
                        return checkbox && !checkbox.checked;
                    }).map(function (item) {
                        return item.dataset.navigationPreferenceKey;
                    })
                }).then(function (result) {
                    if (!result) {
                        return;
                    }
                    sessionStorage.setItem(
                        "vechasu-navigation-notice",
                        "Настройки вкладок сохранены"
                    );
                    window.location.reload();
                });
            });
            navigationReset.addEventListener("click", function () {
                requestNavigation("DELETE").then(function (result) {
                    if (!result) {
                        return;
                    }
                    sessionStorage.setItem(
                        "vechasu-navigation-notice",
                        "Настройки вкладок сброшены"
                    );
                    window.location.reload();
                });
            });
            var savedNavigationNotice = sessionStorage.getItem(
                "vechasu-navigation-notice"
            );
            if (savedNavigationNotice) {
                sessionStorage.removeItem("vechasu-navigation-notice");
                setNavigationStatus(savedNavigationNotice, "success");
                showNotice(form, savedNavigationNotice, "success");
            }
        }

    });
})();
