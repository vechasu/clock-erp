(function (global) {
    "use strict";

    if (global.VechasuNotify) return;

    const MAX_VISIBLE = 3;
    const STORAGE_KEY = "vechasu.erp.pending-notification.v1";
    const PAGE_ID = String(Date.now()) + Math.random().toString(16).slice(2);
    const ICONS = {success: "✓", error: "×", warning: "!", info: "i", loading: "↻"};
    const DEFAULT_LIFETIME = {success: 3600, info: 5000, warning: 8000, error: 0, loading: 0};
    const items = new Map();
    const visible = [];
    const queue = [];
    let sequence = 0;
    let region = null;

    function text(value, fallback) {
        const result = String(value == null ? "" : value).replace(/\s+/g, " ").trim();
        return result || fallback || "";
    }

    function ensureRegion() {
        if (region && region.isConnected) return region;
        region = document.createElement("section");
        region.className = "erp-toast-region";
        region.setAttribute("aria-label", "Системные уведомления");
        document.body.appendChild(region);
        return region;
    }

    function normalize(kind, title, options) {
        const safeKind = Object.prototype.hasOwnProperty.call(ICONS, kind) ? kind : "info";
        const settings = typeof options === "string" ? {detail: options} : (options || {});
        return {
            id: settings.id || "erp-toast-" + (++sequence),
            kind: safeKind,
            title: text(title, "Системное уведомление"),
            detail: text(settings.detail),
            duration: Number.isFinite(settings.duration) ? Math.max(0, settings.duration) : DEFAULT_LIFETIME[safeKind],
            action: settings.action || null,
            progress: Number.isFinite(settings.progress) ? Math.max(0, Math.min(100, settings.progress)) : null,
            persist: Boolean(settings.persist),
            timer: null,
            element: null,
        };
    }

    function duplicateOf(item) {
        return Array.from(items.values()).find(function (candidate) {
            return candidate.kind === item.kind
                && candidate.title === item.title
                && candidate.detail === item.detail;
        });
    }

    function schedule(item) {
        if (item.timer) global.clearTimeout(item.timer);
        item.timer = null;
        if (item.duration > 0) {
            item.timer = global.setTimeout(function () { dismiss(item.id); }, item.duration);
        }
    }

    function render(item) {
        const toast = document.createElement("article");
        toast.className = "erp-toast";
        toast.dataset.kind = item.kind;
        toast.dataset.notificationId = item.id;
        toast.setAttribute("role", item.kind === "error" ? "alert" : "status");
        toast.setAttribute("aria-live", item.kind === "error" ? "assertive" : "polite");
        toast.setAttribute("aria-atomic", "true");

        const icon = document.createElement("span");
        icon.className = "erp-toast-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = ICONS[item.kind];

        const copy = document.createElement("div");
        copy.className = "erp-toast-copy";
        const title = document.createElement("div");
        title.className = "erp-toast-title";
        title.textContent = item.title;
        copy.appendChild(title);
        if (item.detail) {
            const detail = document.createElement("div");
            detail.className = "erp-toast-detail";
            detail.textContent = item.detail;
            copy.appendChild(detail);
        }
        if (item.progress !== null) {
            const progress = document.createElement("div");
            progress.className = "erp-toast-progress";
            progress.setAttribute("role", "progressbar");
            progress.setAttribute("aria-valuemin", "0");
            progress.setAttribute("aria-valuemax", "100");
            progress.setAttribute("aria-valuenow", String(item.progress));
            const bar = document.createElement("span");
            bar.style.setProperty("--toast-progress", item.progress + "%");
            progress.appendChild(bar);
            copy.appendChild(progress);
        }
        if (item.action && text(item.action.label)) {
            const action = document.createElement(item.action.href ? "a" : "button");
            action.className = "erp-toast-action";
            action.textContent = text(item.action.label);
            if (item.action.href) action.href = item.action.href;
            else action.type = "button";
            if (typeof item.action.onClick === "function") {
                action.addEventListener("click", function (event) {
                    item.action.onClick(event, item);
                });
            }
            copy.appendChild(action);
        }

        const close = document.createElement("button");
        close.type = "button";
        close.className = "erp-toast-close";
        close.setAttribute("aria-label", "Закрыть уведомление");
        close.textContent = "×";
        close.addEventListener("click", function () { dismiss(item.id); });
        toast.append(icon, copy, close);
        item.element = toast;
        ensureRegion().appendChild(toast);
        schedule(item);
    }

    function flush() {
        while (visible.length < MAX_VISIBLE && queue.length) {
            const id = queue.shift();
            const item = items.get(id);
            if (!item) continue;
            visible.push(id);
            render(item);
        }
    }

    function show(kind, title, options) {
        const item = normalize(kind, title, options);
        const duplicate = duplicateOf(item);
        if (duplicate) {
            schedule(duplicate);
            return duplicate.id;
        }
        items.set(item.id, item);
        queue.push(item.id);
        flush();
        return item.id;
    }

    function dismiss(id) {
        const item = items.get(id);
        if (!item) return false;
        if (item.timer) global.clearTimeout(item.timer);
        if (item.element) item.element.remove();
        items.delete(id);
        const visibleIndex = visible.indexOf(id);
        if (visibleIndex >= 0) visible.splice(visibleIndex, 1);
        const queueIndex = queue.indexOf(id);
        if (queueIndex >= 0) queue.splice(queueIndex, 1);
        flush();
        return true;
    }

    function update(id, changes) {
        const current = items.get(id);
        if (!current) return null;
        const next = normalize(changes.kind || current.kind, changes.title || current.title, {
            id,
            detail: Object.prototype.hasOwnProperty.call(changes, "detail") ? changes.detail : current.detail,
            duration: Object.prototype.hasOwnProperty.call(changes, "duration") ? changes.duration : undefined,
            action: Object.prototype.hasOwnProperty.call(changes, "action") ? changes.action : current.action,
            progress: Object.prototype.hasOwnProperty.call(changes, "progress") ? changes.progress : current.progress,
            persist: current.persist,
        });
        if (!Object.prototype.hasOwnProperty.call(changes, "duration")) {
            next.duration = changes.kind ? DEFAULT_LIFETIME[next.kind] : current.duration;
        }
        const wasVisible = Boolean(current.element);
        if (current.timer) global.clearTimeout(current.timer);
        if (current.element) current.element.remove();
        items.set(id, next);
        if (wasVisible) render(next);
        return id;
    }

    function apiMessage(payload) {
        if (!payload || typeof payload !== "object") return "";
        if (payload.error && typeof payload.error.message === "string") return text(payload.error.message);
        return typeof payload.message === "string" ? text(payload.message) : "";
    }

    function safeError(response, payload, fallback) {
        if (!response) return "Не удалось связаться с сервером. Проверьте подключение.";
        if (response.status === 401) return "Сессия завершена. Войдите в ERP снова.";
        if (response.status === 403) return "Недостаточно прав или форма устарела. Обновите страницу.";
        if (response.status === 404) return apiMessage(payload) || "Запрошенный объект не найден.";
        if (response.status === 409) return apiMessage(payload) || "Операция конфликтует с текущим состоянием данных.";
        if (response.status === 413) return "Файл слишком большой для загрузки.";
        if (response.status === 422 || response.status === 400) return apiMessage(payload) || "Проверьте заполненные данные.";
        if (response.status >= 500) return "Сервер не смог выполнить операцию. Повторите позже.";
        return apiMessage(payload) || fallback || "Не удалось выполнить операцию.";
    }

    function safeServerNotice(value) {
        const message = text(value);
        if (/traceback|integrityerror|sqlite|sqlalchemy|exception|\/opt\/|\/users\//i.test(message)) {
            return "Сервер не смог выполнить операцию. Повторите позже.";
        }
        return message;
    }

    function bodyPayload(body) {
        if (!body) return {};
        if (typeof body === "string") {
            try { return JSON.parse(body); } catch (_error) { return {}; }
        }
        if (global.FormData && body instanceof global.FormData) {
            const result = {};
            body.forEach(function (value, key) {
                if (typeof value === "string") result[key] = value;
            });
            return result;
        }
        if (global.URLSearchParams && body instanceof global.URLSearchParams) {
            return Object.fromEntries(body.entries());
        }
        return {};
    }

    function entityLabel(payload, responsePayload) {
        const data = responsePayload && responsePayload.data;
        return text(
            (data && (data.name || data.article || data.number || data.repair_number || data.id))
            || payload.name || payload.article || payload.number || payload.document_number || payload.id
        );
    }

    function operation(method, rawUrl, payload, responsePayload) {
        const path = new URL(rawUrl, global.location.href).pathname.replace(/^\/api\/v1/, "/api");
        const label = entityLabel(payload, responsePayload);
        const suffix = label ? " «" + label + "»" : "";
        const rules = [
            [/^\/api\/products\/bulk$/, "Товары обновляются…", "Обновление товаров завершено"],
            [/^\/api\/products(?:\/\d+)?$/, method === "POST" ? "Товар создаётся…" : method === "DELETE" ? "Товар удаляется…" : "Товар сохраняется…", method === "POST" ? "Товар" + suffix + " создан" : method === "DELETE" ? "Товар" + suffix + " удалён" : "Товар" + suffix + " сохранён"],
            [/^\/api\/brands(?:\/\d+)?$/, "Бренд сохраняется…", method === "DELETE" ? "Бренд удалён" : method === "POST" ? "Бренд" + suffix + " создан" : "Бренд сохранён"],
            [/^\/api\/categories(?:\/\d+)?$/, "Категория сохраняется…", method === "DELETE" ? "Категория удалена" : method === "POST" ? "Категория" + suffix + " создана" : "Категория сохранена"],
            [/^\/api\/receipts(?:\/[^/]+)?$/, "Приход сохраняется…", method === "DELETE" ? "Приход" + suffix + " отменён" : method === "POST" ? "Приход" + suffix + " проведён" : "Приход" + suffix + " сохранён"],
            [/^\/api\/sales\/[^/]+\/cancel$/, "Продажа отменяется…", "Продажа" + suffix + " отменена"],
            [/^\/api\/sales\/[^/]+\/returns$/, "Возврат проводится…", "Возврат по продаже" + suffix + " проведён"],
            [/^\/api\/sales(?:\/[^/]+)?$/, "Продажа сохраняется…", method === "DELETE" ? "Продажа" + suffix + " удалена" : method === "POST" ? "Продажа" + suffix + " проведена" : "Продажа" + suffix + " сохранена"],
            [/^\/api\/repairs\/[^/]+\/status$/, "Статус ремонта меняется…", "Статус ремонта" + suffix + " изменён"],
            [/^\/api\/repairs\/[^/]+\/restore$/, "Ремонт восстанавливается…", "Ремонт" + suffix + " восстановлен"],
            [/^\/api\/repairs\/[^/]+\/shipments$/, "Накладная добавляется…", "Накладная добавлена"],
            [/^\/api\/repairs\/[^/]+\/attachments$/, "Файлы загружаются…", "Файлы ремонта загружены"],
            [/^\/api\/repairs(?:\/[^/]+)?$/, "Ремонт сохраняется…", method === "DELETE" ? "Ремонт" + suffix + " перенесён в архив" : method === "POST" ? "Ремонт" + suffix + " создан" : "Ремонт" + suffix + " сохранён"],
            [/^\/api\/settings$/, "Настройки сохраняются…", "Настройки ERP сохранены"],
            [/^\/warehouse\/bulk-edit$/, "Товары обновляются…", "Обновление товаров завершено"],
            [/^\/warehouse\/(?:add|edit|stock|archive)$/, "Товар сохраняется…", "Товар" + suffix + " сохранён"],
            [/^\/warehouse\/(?:cell|category-cell)$/, "Складская ячейка сохраняется…", "Складская ячейка сохранена"],
            [/^\/products\/\d+\/delete$/, "Товар удаляется…", "Товар удалён"],
            [/^\/products\/\d+\/match$/, "Связь товара сохраняется…", "Связь товара сохранена"],
            [/^\/sales\/(?:manual\/add|manual\/update|automatic\/update)$/, "Продажа сохраняется…", "Продажа сохранена"],
            [/^\/sales\/(?:cancel|return)$/, "Продажа обрабатывается…", path.endsWith("/cancel") ? "Продажа отменена" : "Возврат по продаже проведён"],
            [/^\/sales\/(?:manual\/delete|delete)$/, "Продажа удаляется…", "Продажа удалена"],
            [/^\/sales\/status$/, "Статус продажи меняется…", "Статус продажи изменён"],
            [/^\/receipts\/(?:create|update)$/, "Приход сохраняется…", "Приход сохранён"],
            [/^\/receipts\/delete$/, "Приход отменяется…", "Приход отменён"],
            [/^\/receipts\/catalog\/create$/, "Каталог обновляется…", "Элемент каталога создан"],
            [/^\/catalog\/mapping\/confirm$/, "Связь каталога сохраняется…", "Связь каталога сохранена"],
            [/^\/settings(?:\/invitations(?:\/\d+\/revoke)?)?$/, "Настройки сохраняются…", "Настройки ERP сохранены"],
        ];
        const match = rules.find(function (rule) { return rule[0].test(path); });
        if (match) return {loading: match[1], success: match[2]};
        if (method === "DELETE") return {loading: "Удаление…", success: "Данные удалены"};
        if (method === "POST") return {loading: "Операция выполняется…", success: "Данные созданы"};
        return {loading: "Сохранение…", success: "Изменения сохранены"};
    }

    function failureTitle(method, rawUrl) {
        const path = new URL(rawUrl, global.location.href).pathname.replace(/^\/api\/v1/, "/api");
        if (/products|warehouse/.test(path)) {
            return method === "DELETE" || /archive|delete/.test(path)
                ? "Не удалось удалить товар" : "Не удалось сохранить товар";
        }
        if (/brands/.test(path)) return "Не удалось сохранить бренд";
        if (/categories/.test(path)) return "Не удалось сохранить категорию";
        if (/receipts|products\/receipts/.test(path)) return "Не удалось сохранить приход";
        if (/sales/.test(path)) return "Не удалось выполнить операцию с продажей";
        if (/repairs|repair/.test(path)) return "Не удалось сохранить ремонт";
        if (/settings/.test(path)) return "Не удалось сохранить настройки";
        if (/catalog/.test(path)) return "Не удалось обновить каталог";
        return "Не удалось выполнить операцию";
    }

    function partialDetails(payload) {
        const data = payload && payload.data;
        const errors = data && Array.isArray(data.errors) ? data.errors.length : 0;
        const updated = data && Number.isFinite(data.updated) ? data.updated : 0;
        const warnings = payload && payload.meta && (payload.meta.warning || payload.meta.warnings || payload.meta.partial_message);
        if (errors) return updated + " успешно, " + errors + " — ошибка";
        if (Array.isArray(warnings)) return warnings.map(text).filter(Boolean).join(". ");
        return text(warnings, "Часть операции требует проверки.");
    }

    function remember(item) {
        try {
            global.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                source: PAGE_ID,
                expires: Date.now() + 12000,
                item,
            }));
        } catch (_error) { /* storage is optional */ }
    }

    function restoreRemembered() {
        try {
            const stored = JSON.parse(global.sessionStorage.getItem(STORAGE_KEY) || "null");
            global.sessionStorage.removeItem(STORAGE_KEY);
            if (stored && stored.source !== PAGE_ID && stored.expires > Date.now() && stored.item) {
                show(stored.item.kind, stored.item.title, stored.item);
            }
        } catch (_error) { /* ignore invalid storage */ }
    }

    const originalFetch = global.fetch && global.fetch.bind(global);
    if (originalFetch) {
        global.fetch = async function (input, init) {
            const settings = init || {};
            const method = text(settings.method || (input && input.method) || "GET").toUpperCase();
            const isMutation = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
            const headers = new Headers(settings.headers || (input && input.headers) || {});
            if (!isMutation || headers.get("X-Vechasu-Notify") === "off") {
                return originalFetch(input, settings);
            }

            const rawUrl = typeof input === "string" ? input : input.url;
            const requestPayload = bodyPayload(settings.body);
            const descriptor = operation(method, rawUrl, requestPayload, null);
            const activeButton = document.activeElement && document.activeElement.closest
                ? document.activeElement.closest("button, input[type='submit']")
                : null;
            const priorDisabled = activeButton ? activeButton.disabled : false;
            if (activeButton) {
                activeButton.disabled = true;
                activeButton.setAttribute("aria-busy", "true");
            }
            const notificationId = show("loading", headers.get("X-Vechasu-Loading") || descriptor.loading);
            let response;
            let timeoutId = null;
            let requestSettings = settings;
            try {
                const existingSignal = settings.signal || (input && input.signal);
                if (!existingSignal && global.AbortController) {
                    const controller = new global.AbortController();
                    requestSettings = Object.assign({}, settings, {signal: controller.signal});
                    timeoutId = global.setTimeout(function () { controller.abort(); }, 45000);
                }
                response = await originalFetch(input, requestSettings);
                let payload = null;
                try { payload = await response.clone().json(); } catch (_error) { /* non-JSON response */ }
                const resolved = operation(method, rawUrl, requestPayload, payload);
                if (!response.ok || (payload && payload.error)) {
                    update(notificationId, {
                        kind: "error",
                        title: failureTitle(method, rawUrl),
                        detail: safeError(response, payload, resolved.success),
                    });
                } else if (response.status === 207 || (payload && payload.meta && payload.meta.partial_success) || (payload && payload.data && Array.isArray(payload.data.errors) && payload.data.errors.length)) {
                    update(notificationId, {
                        kind: "warning",
                        title: resolved.success + " частично",
                        detail: partialDetails(payload),
                    });
                    remember({kind: "warning", title: resolved.success + " частично", detail: partialDetails(payload)});
                } else {
                    const detail = text(payload && payload.meta && (payload.meta.image_message || payload.meta.message));
                    update(notificationId, {kind: "success", title: resolved.success, detail});
                    remember({kind: "success", title: resolved.success, detail});
                }
                return response;
            } catch (error) {
                update(notificationId, {
                    kind: "error",
                    title: failureTitle(method, rawUrl),
                    detail: error && error.name === "AbortError"
                        ? "Сервер не ответил вовремя. Проверьте результат перед повтором."
                        : "Не удалось связаться с сервером. Проверьте подключение.",
                });
                throw error;
            } finally {
                if (timeoutId) global.clearTimeout(timeoutId);
                if (activeButton && activeButton.isConnected) {
                    activeButton.disabled = priorDisabled;
                    activeButton.removeAttribute("aria-busy");
                }
            }
        };
    }

    function consumeServerNotice() {
        const url = new URL(global.location.href);
        const message = safeServerNotice(url.searchParams.get("message"));
        const notice = text(url.searchParams.get("notice")).toLowerCase();
        if (message) {
            const kind = notice === "error" || notice === "danger" ? "error"
                : notice === "warning" ? "warning" : "success";
            show(kind, message);
            url.searchParams.delete("notice");
            url.searchParams.delete("message");
            global.history.replaceState(global.history.state, "", url.pathname + url.search + url.hash);
        }
        let consumed = Boolean(message);
        document.querySelectorAll("[data-erp-notification-source]").forEach(function (source) {
            const sourceText = safeServerNotice(source.textContent);
            if (sourceText && sourceText !== message) {
                const className = source.className || "";
                const kind = /error|danger/.test(className) ? "error" : /warning/.test(className) ? "warning" : "success";
                show(kind, sourceText);
                consumed = true;
            }
            source.dataset.erpNotificationConsumed = "true";
        });
        return consumed;
    }

    const manager = {
        success: function (title, options) { return show("success", title, options); },
        error: function (title, options) { return show("error", title, options); },
        warning: function (title, options) { return show("warning", title, options); },
        info: function (title, options) { return show("info", title, options); },
        loading: function (title, options) { return show("loading", title, options); },
        update,
        dismiss,
        clear: function () { Array.from(items.keys()).forEach(dismiss); },
    };
    global.VechasuNotify = Object.freeze(manager);
    global.NotificationManager = global.VechasuNotify;

    document.addEventListener("DOMContentLoaded", function () {
        ensureRegion();
        if (!consumeServerNotice()) restoreRemembered();
    });

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.method.toUpperCase() === "GET") return;
        global.setTimeout(function () {
            if (event.defaultPrevented || !form.isConnected) return;
            const submitter = event.submitter || form.querySelector("button[type='submit'], input[type='submit']");
            if (submitter) {
                submitter.disabled = true;
                submitter.setAttribute("aria-busy", "true");
            }
            show("loading", text(form.dataset.loadingMessage, "Операция выполняется…"));
        }, 0);
    }, true);
})(window);
