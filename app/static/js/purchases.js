(() => {
    const config = window.PURCHASES_CONFIG || {};
    const pause = (fn, delay = 250) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; };
    async function api(url, options = {}) {
        const headers = { Accept: "application/json", ...(options.headers || {}) };
        if (options.body) headers["Content-Type"] = "application/json";
        if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = config.csrf || "";
        const response = await fetch(url, { ...options, headers });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error?.message || payload.message || "Не удалось выполнить действие.");
        return payload;
    }
    function notify(message, failed = false) {
        if (window.VechasuNotify) window.VechasuNotify[failed ? "error" : "success"](message);
        else if (failed) window.alert(message);
    }
    function reload(tab) { const url = new URL(location.href); if (tab) url.searchParams.set("tab", tab); url.searchParams.delete("request_id"); location.assign(url); }
    document.querySelectorAll("[data-open]").forEach((button) => button.addEventListener("click", () => {
        const dialog = document.getElementById(button.dataset.open); if (!dialog) return;
        if (dialog.id === "order-modal") {
            const selected = [...document.querySelectorAll('input[name="plan_item"]:checked')];
            if (!selected.length) return notify("Выберите хотя бы одну позицию плана.", true);
            dialog.dataset.planIds = selected.map((item) => item.value).join(",");
        }
        dialog.showModal(); const first = dialog.querySelector("input:not([type=hidden]),select,button"); if (first) first.focus();
    }));
    document.querySelectorAll("dialog form[method=dialog]").forEach((form) => form.addEventListener("click", (event) => {
        if (event.target.matches('button[value="cancel"]')) form.closest("dialog").close();
    }));
    const requestForm = document.getElementById("requestForm");
    if (requestForm) {
        const requested = requestForm.elements.requested_at;
        const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16); requested.value = local;
        const modes = requestForm.elements.product_mode;
        const updateMode = () => { const unknown = modes.value === "unknown"; document.querySelectorAll(".unknown-fields").forEach((el) => el.style.setProperty("display", unknown ? "flex" : "none", "important")); document.querySelectorAll(".existing-fields").forEach((el) => el.style.setProperty("display", unknown ? "none" : "flex", "important")); if (unknown) requestForm.elements.product_id.value = ""; };
        [...modes].forEach((radio) => radio.addEventListener("change", updateMode)); updateMode();
        function attachSearch(input, results, endpoint, select) {
            input.addEventListener("input", pause(async () => {
                const query = input.value.trim(); results.replaceChildren(); if (query.length < 2) return;
                try { const payload = await api(`${endpoint}?q=${encodeURIComponent(query)}`); payload.items.forEach((item) => { const button = document.createElement("button"); button.type = "button"; button.textContent = select.label(item); button.addEventListener("click", () => { select.choose(item); results.replaceChildren(); }); results.append(button); }); }
                catch (error) { notify(error.message, true); }
            }));
        }
        attachSearch(document.getElementById("customerSearch"), document.getElementById("customerResults"), "/api/v1/purchases/customers", { label: (item) => `${item.name || "Без имени"} · ${item.phone || item.email || "без контакта"}`, choose: (item) => { requestForm.elements.customer_id.value = item.id; document.getElementById("customerSearch").value = `${item.name} · ${item.phone || item.email || ""}`; } });
        attachSearch(document.getElementById("productSearch"), document.getElementById("productResults"), "/api/v1/purchases/products", { label: (item) => `${item.brand || ""} ${item.model || item.product_name} · ${item.article || "без артикула"}`, choose: (item) => { requestForm.elements.product_id.value = item.id; document.getElementById("productSearch").value = `${item.brand || ""} ${item.model || item.product_name}`; } });
        document.getElementById("quickCustomer").addEventListener("click", async () => {
            const name = prompt("Имя клиента"); if (!name) return; const phone = prompt("Телефон (можно оставить пустым)") || ""; const email = prompt("Email (можно оставить пустым)") || "";
            try { const payload = await api("/api/v1/purchases/customers", { method: "POST", body: JSON.stringify({ name, phone, email }) }); requestForm.elements.customer_id.value = payload.customer.id; document.getElementById("customerSearch").value = `${payload.customer.name} · ${payload.customer.phone || payload.customer.email || ""}`; notify(payload.created ? "Клиент создан." : "Найден существующий клиент."); }
            catch (error) { notify(error.message, true); }
        });
        requestForm.addEventListener("submit", async (event) => {
            event.preventDefault(); if (!requestForm.reportValidity()) return;
            const data = Object.fromEntries(new FormData(requestForm)); delete data.product_mode;
            if (!data.customer_id) return notify("Выберите клиента из результатов поиска.", true);
            if (modes.value === "existing" && !data.product_id) return notify("Выберите товар из результатов поиска.", true);
            data.product_name = data.model || document.getElementById("productSearch").value;
            try { await api("/api/v1/purchases/requests", { method: "POST", body: JSON.stringify(data) }); requestForm.closest("dialog").close(); notify("Запрос создан."); reload("requests"); }
            catch (error) { requestForm.querySelector(".form-error").textContent = error.message; }
        });
    }
    document.querySelectorAll("[data-plan-request]").forEach((button) => button.addEventListener("click", async () => { button.disabled = true; try { await api(`/api/v1/purchases/requests/${button.dataset.planRequest}/plan`, { method: "POST", body: "{}" }); notify("Запрос добавлен в план."); reload("plan"); } catch (error) { notify(error.message, true); button.disabled = false; } }));
    document.querySelectorAll("[data-request-status]").forEach((select) => select.addEventListener("change", async () => { try { await api(`/api/v1/purchases/requests/${select.dataset.requestStatus}`, { method: "PATCH", body: JSON.stringify({ status: select.value }) }); notify("Статус сохранён."); reload("requests"); } catch (error) { notify(error.message, true); } }));
    document.querySelectorAll("[data-archive-request]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/v1/purchases/requests/${button.dataset.archiveRequest}/archive`, { method: "POST", body: JSON.stringify({ archived: button.dataset.archived === "1" }) }); reload("requests"); } catch (error) { notify(error.message, true); } }));
    document.querySelectorAll("[data-plan-quantity]").forEach((input) => input.addEventListener("change", async () => { try { await api(`/api/v1/purchases/plan/${input.dataset.planQuantity}`, { method: "PATCH", body: JSON.stringify({ actual_quantity: input.value }) }); notify("Количество обновлено."); } catch (error) { notify(error.message, true); } }));
    document.querySelectorAll("[data-remove-plan]").forEach((button) => button.addEventListener("click", async () => { if (!confirm("Убрать позицию из плана? Исходные запросы сохранятся.")) return; try { await api(`/api/v1/purchases/plan/${button.dataset.removePlan}/remove`, { method: "POST", body: "{}" }); reload("plan"); } catch (error) { notify(error.message, true); } }));
    const orderForm = document.getElementById("orderForm");
    if (orderForm) orderForm.addEventListener("submit", async (event) => { event.preventDefault(); if (!orderForm.reportValidity()) return; const dialog = orderForm.closest("dialog"); const data = Object.fromEntries(new FormData(orderForm)); data.plan_item_ids = (dialog.dataset.planIds || "").split(",").filter(Boolean); data.prices = {}; data.plan_item_ids.forEach((id) => { data.prices[id] = document.querySelector(`[data-plan-price="${id}"]`)?.value || 0; }); try { await api("/api/v1/purchases/supplier-orders", { method: "POST", body: JSON.stringify(data) }); dialog.close(); notify("Заказ поставщику создан."); reload("orders"); } catch (error) { orderForm.querySelector(".form-error").textContent = error.message; } });
    document.querySelectorAll("[data-order-detail]").forEach((button) => button.addEventListener("click", async () => {
        try { const { order } = await api(`/api/v1/purchases/supplier-orders/${button.dataset.orderDetail}`); const host = document.getElementById("orderDetail"); host.replaceChildren(); const head = document.createElement("div"); head.className = "detail-head"; const title = document.createElement("h2"); title.textContent = `${order.internal_number} · ${order.supplier_name}`; const close = document.createElement("button"); close.textContent = "×"; close.addEventListener("click", () => host.closest("dialog").close()); head.append(title, close); host.append(head); const status = document.createElement("select"); Object.entries(config.orderStatusLabels).forEach(([key, label]) => { const option = new Option(label, key, key === order.status, key === order.status); status.add(option); }); status.addEventListener("change", async () => { await api(`/api/v1/purchases/supplier-orders/${order.id}/status`, { method: "POST", body: JSON.stringify({ status: status.value }) }); reload("orders"); }); host.append(status); order.items.forEach((item) => { const row = document.createElement("p"); row.textContent = `${item.brand || ""} ${item.model || item.product_name}: ${item.received_quantity}/${item.quantity} × ${item.purchase_price} ${order.currency}`; const receive = document.createElement("button"); receive.className = "purchase-button"; receive.textContent = "Принять"; receive.addEventListener("click", async () => { const value = prompt("Получено единиц", item.quantity); if (!value) return; await api(`/api/v1/purchases/supplier-items/${item.id}/receive`, { method: "POST", body: JSON.stringify({ received_quantity: value }) }); reload("orders"); }); row.append(" ", receive); host.append(row); }); const receipt = document.createElement("a"); receipt.className = "purchase-button ghost"; receipt.href = "/app/receipts"; receipt.textContent = "Создать приход"; receipt.title = "Приход проводится отдельно и только через действующий раздел"; host.append(receipt); host.closest("dialog").showModal(); }
        catch (error) { notify(error.message, true); }
    }));
    const filterSearch = document.querySelector('#purchaseFilters input[name="q"]'); if (filterSearch) filterSearch.addEventListener("input", pause(() => document.getElementById("purchaseFilters").requestSubmit(), 450));
    document.querySelectorAll("[data-page-size]").forEach((select) => select.addEventListener("change", () => { const url = new URL(location.href); url.searchParams.set("per_page", select.value); url.searchParams.set("page", "1"); location.assign(url); }));
    document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => { const url = new URL(location.href); url.searchParams.set("page", button.dataset.page); location.assign(url); }));
    const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" }); document.querySelectorAll("tr[data-valid-until]").forEach((row) => { if (row.dataset.validUntil && row.dataset.validUntil < today && !row.querySelector(".status-closed,.status-sold")) row.classList.add("is-overdue"); });
})();
