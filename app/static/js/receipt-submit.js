(function (global) {
    "use strict";

    function apiMessage(payload) {
        if (!payload || typeof payload !== "object") {
            return "";
        }
        if (
            payload.error
            && typeof payload.error.message === "string"
        ) {
            return payload.error.message.trim();
        }
        return typeof payload.message === "string"
            ? payload.message.trim()
            : "";
    }

    function errorMessage(response, payload) {
        if (response.status === 401) {
            return "Сессия завершена. Войдите в ERP снова.";
        }
        if (response.status === 413) {
            return "Файл слишком большой. Максимальный размер — 3 МБ.";
        }
        if (response.status >= 500) {
            return "Ошибка сервера при сохранении прихода.";
        }

        const message = apiMessage(payload);
        if (message) {
            return message;
        }
        return "Запрос отклонён. Проверьте заполненные поля прихода.";
    }

    async function readPayload(response) {
        const text = await response.text();
        if (!text) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (_error) {
            return null;
        }
    }

    async function request(form, endpoint, method, body, idempotencyKey) {
        const csrfToken = form.querySelector(
            'input[name="csrf_token"]'
        )?.value || "";
        const headers = {
            "X-CSRF-Token": csrfToken,
            "Idempotency-Key": idempotencyKey,
        };
        const isFormData = body instanceof global.FormData;
        if (!isFormData) {
            headers["Content-Type"] = "application/json";
        }

        let response;
        try {
            response = await global.fetch(endpoint, {
                method,
                headers,
                body: isFormData
                    ? body
                    : JSON.stringify(body),
            });
        } catch (_error) {
            throw new Error(
                "Не удалось связаться с сервером. Проверьте подключение."
            );
        }

        const payload = await readPayload(response);
        if (!response.ok || payload?.error) {
            throw new Error(errorMessage(response, payload));
        }
        return payload;
    }

    function buildCreatePayload(form, productId, submitMode) {
        const payload = new global.FormData(form);
        const image = form.querySelector(
            '[name="product_image"]'
        )?.files?.[0];
        if (image) {
            payload.set("product_image", image, image.name);
        } else {
            payload.delete("product_image");
        }
        payload.set(
            "positions",
            JSON.stringify([{
                product_id: productId,
                brand_id: form.querySelector(
                    '[name="brand_id"]'
                )?.value || "",
                category_id: form.querySelector(
                    '[name="category_id"]'
                )?.value || "",
                quantity: form.querySelector(
                    '[name="quantity"]'
                )?.value || "",
                purchase_price: form.querySelector(
                    '[name="purchase_price"]'
                )?.value || "",
            }])
        );
        payload.set(
            "submit_mode",
            submitMode === "create_next" ? "create_next" : "close"
        );
        return payload;
    }

    function submissionKey(form) {
        if (!form.dataset.idempotencyKey) {
            form.dataset.idempotencyKey = (
                global.crypto?.randomUUID?.()
                || (
                    String(Date.now())
                    + "-"
                    + Math.random().toString(16).slice(2)
                )
            );
        }
        return form.dataset.idempotencyKey;
    }

    function successUrl(submitMode, payload) {
        const imageMessage = String(
            payload?.meta?.image_message || ""
        ).trim();
        const message = [
            "Приход проведён.",
            imageMessage,
            submitMode === "create_next"
                ? "Форма готова для следующего прихода."
                : "",
        ].filter(Boolean).join(" ");
        const query = new global.URLSearchParams({
            notice: "success",
            message,
        });
        if (submitMode === "create_next") {
            query.set("open_receipt_modal", "1");
        }
        return "/receipts?" + query.toString();
    }

    global.VechasuReceiptSubmit = Object.freeze({
        buildCreatePayload,
        errorMessage,
        request,
        submissionKey,
        successUrl,
    });
})(window);
