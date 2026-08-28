(() => {
    "use strict";

    const boot = window.MAIL_BOOTSTRAP || {};
    const csrf = boot.csrf;
    const $ = (selector) => document.querySelector(selector);
    const list = $("#mailList");
    const workspace = $("#mailWorkspace");
    const connectionState = $("#connectionState");
    const headerActions = $("#mailHeaderActions");
    const backdrop = $("#mailBackdrop");
    const syncStatus = $("#syncStatus");
    const serverWarning = $("#mailServerWarning");
    let view = "all";
    let page = 1;
    let current = null;
    let linkedCustomer = false;

    const escapeHtml = (value) => {
        const node = document.createElement("span");
        node.textContent = String(value ?? "");
        return node.innerHTML;
    };
    const statusLabels = {
        new: "Новое",
        in_progress: "В работе",
        waiting_customer: "Ждём клиента",
        answered: "Отвечено",
        closed: "Закрыто",
    };

    async function api(url, options = {}) {
        const headers = new Headers(options.headers || {});
        headers.set("Accept", "application/json");
        headers.set("X-Vechasu-Notify", "off");
        if (options.method && options.method !== "GET") {
            headers.set("Content-Type", "application/json");
            headers.set("X-CSRF-Token", csrf);
        }
        const response = await fetch(url, {
            credentials: "same-origin",
            ...options,
            headers,
        });
        const payload = await response.json().catch(() => ({message: "Некорректный ответ сервера"}));
        if (!response.ok) {
            const error = new Error(payload.error?.message || payload.message || "Не удалось выполнить операцию");
            error.code = payload.error?.code || payload.code || "MAIL_REQUEST_FAILED";
            error.fields = payload.error?.fields || payload.fields || {};
            throw error;
        }
        return payload.data;
    }

    function notify(message, type = "success") {
        if (window.VechasuNotify) window.VechasuNotify[type](message);
        else if (syncStatus) syncStatus.textContent = message;
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return new Intl.DateTimeFormat("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    function setConnected(account) {
        const connected = Boolean(account && account.enabled);
        boot.account = account || null;
        if (connectionState) connectionState.hidden = connected;
        if (workspace) workspace.hidden = !connected;
        if (headerActions) headerActions.hidden = !connected;
        if (syncStatus) syncStatus.hidden = !connected;
        if (!connected && serverWarning) serverWarning.hidden = true;
    }

    function emptyListMarkup(account) {
        if (account && !account.initial_sync_complete) {
            return '<div class="mail-empty"><div><strong>Загружаем последние письма</strong><span>Первая синхронизация выполняется в фоне. Список обновится автоматически.</span></div></div>';
        }
        if (account && account.last_sync_status === "error") {
            return '<div class="mail-empty"><div><strong>Не удалось обновить письма</strong><span>Сохранённая переписка доступна. Синхронизация повторится автоматически.</span></div></div>';
        }
        return '<div class="mail-empty"><div><strong>В рабочем ящике пока нет писем</strong><span>Новые входящие появятся здесь после синхронизации.</span></div></div>';
    }

    async function load() {
        if (!workspace || workspace.hidden) return;
        list.setAttribute("aria-busy", "true");
        list.innerHTML = '<div class="mail-empty"><div>Загружаем переписку…</div></div>';
        const params = new URLSearchParams({
            view,
            page,
            q: $("#mailSearch").value,
            status: $("#mailStatus").value,
            assignee_id: $("#mailAssignee").value,
            date_from: $("#mailDate").value,
        });
        try {
            const data = await api("/api/v1/mail/threads?" + params);
            if (!data.account || !data.account.enabled) {
                setConnected(data.account);
                return;
            }
            boot.account = data.account;
            serverWarning.hidden = data.account.last_sync_status !== "error";
            if (!data.account.initial_sync_complete) {
                syncStatus.innerHTML = '<span class="mail-sync-spinner" aria-hidden="true"></span> Загружаем последние письма';
            } else if (data.account.last_sync_at) {
                syncStatus.textContent = "Последняя успешная синхронизация: " + formatDate(data.account.last_sync_at);
            } else {
                syncStatus.textContent = "Ожидаем первую синхронизацию";
            }
            list.innerHTML = data.rows.length ? data.rows.map((row) => `
                <button class="mail-thread-row ${row.unread_count ? "unread" : ""}" data-thread="${row.id}">
                    <span class="mail-unread-dot" aria-hidden="true"></span>
                    <span class="mail-thread-sender">${escapeHtml(row.sender_name || row.sender_email || "Без отправителя")}<small>${escapeHtml(row.sender_email || "")}</small></span>
                    <span class="mail-thread-subject">${escapeHtml(row.subject || "(без темы)")} ${row.has_attachments ? "📎" : ""}<small>${escapeHtml(row.last_snippet || "")}</small></span>
                    <span class="mail-status-chip">${escapeHtml(statusLabels[row.status] || row.status)}</span>
                    <span class="mail-thread-meta">${formatDate(row.last_message_at)} · ${row.message_count}</span>
                </button>`).join("") : emptyListMarkup(data.account);
            list.querySelectorAll("[data-thread]").forEach((button) => {
                button.addEventListener("click", () => openThread(button.dataset.thread));
            });
            const pagination = $("#mailPagination");
            pagination.hidden = !data.rows.length || data.pages <= 1;
            pagination.querySelector("span").textContent = `Страница ${data.page} из ${data.pages}`;
            pagination.querySelector("[data-page=prev]").disabled = data.page <= 1;
            pagination.querySelector("[data-page=next]").disabled = data.page >= data.pages;
            $("#attentionCount").textContent = data.unread_count || "";
        } catch (error) {
            list.innerHTML = `<div class="mail-empty"><div><strong>Не удалось загрузить переписку</strong><span>${escapeHtml(error.message)}</span></div></div>`;
        } finally {
            list.setAttribute("aria-busy", "false");
        }
    }

    const drawerTriggers = new WeakMap();
    function showDrawer(drawer, trigger) {
        if (!drawer) return;
        drawerTriggers.set(drawer, trigger || document.activeElement);
        backdrop.hidden = false;
        drawer.hidden = false;
        drawer.querySelector("input,select,button")?.focus();
    }
    function closeDrawer(drawer) {
        if (!drawer) return;
        drawer.hidden = true;
        if ([$("#threadDrawer"), $("#composeDrawer")].every((node) => !node || node.hidden)) {
            backdrop.hidden = true;
        }
        drawerTriggers.get(drawer)?.focus?.();
    }

    function recipientText(message, kinds) {
        return message.recipients
            .filter((recipient) => kinds.includes(recipient.kind))
            .map((recipient) => recipient.display_name ? `${recipient.display_name} <${recipient.email}>` : recipient.email)
            .join(", ");
    }

    async function openThread(id, images = false) {
        try {
            current = await api(`/api/v1/mail/threads/${id}${images ? "?show_images=1" : ""}`);
            $("#threadTitle").textContent = current.subject || "(без темы)";
            $("#threadAssignee").value = current.assignee_id || "";
            $("#threadStatus").value = current.status;
            $("#threadDue").value = (current.due_at || "").slice(0, 16);
            $("#threadArchive").textContent = current.archived ? "Вернуть из архива" : "В архив";
            linkedCustomer = current.links.some((link) => link.entity_type === "customer");
            renderThreadLinks();
            $("#threadMessages").innerHTML = current.messages.map((message) => {
                const outgoing = message.folder_role === "sent";
                const html = message.html_body || `<p>${escapeHtml(message.text_body).replace(/\n/g, "<br>")}</p>`;
                const attachments = message.attachments.map((item) => `<a href="/api/v1/mail/attachments/${item.id}">${escapeHtml(item.original_name)} · ${Math.ceil(item.size_bytes / 1024)} КБ</a>`).join("");
                return `<article class="mail-message ${outgoing ? "outgoing" : "incoming"}">
                    <header><strong>${escapeHtml(message.sender_name || message.sender_email)}</strong><time>${formatDate(message.sent_at)}</time></header>
                    <div class="mail-message-addresses">Кому: ${escapeHtml(recipientText(message, ["to"]))}${recipientText(message, ["cc"]) ? "<br>Копия: " + escapeHtml(recipientText(message, ["cc"])) : ""}</div>
                    <div class="mail-message-body">${html}</div>
                    ${message.external_images && !images ? '<div class="mail-image-notice">Внешние изображения заблокированы. <button type="button" data-show-images>Показать изображения</button></div>' : ""}
                    ${attachments ? `<div class="mail-attachments">${attachments}</div>` : ""}
                </article>`;
            }).join("");
            $("#threadMessages").querySelector("[data-show-images]")?.addEventListener("click", () => openThread(id, true));
            showDrawer($("#threadDrawer"));
            load();
        } catch (error) {
            notify(error.message, "error");
        }
    }

    function renderThreadLinks() {
        const node = $("#threadLinks");
        if (!node || !current) return;
        node.innerHTML = current.links.length ? "Связи: " + current.links.map((link) => `<span class="mail-status-chip">${escapeHtml(link.label || link.entity_type + " " + link.entity_id)} <button type="button" aria-label="Удалить связь" data-unlink-type="${escapeHtml(link.entity_type)}" data-unlink-id="${escapeHtml(link.entity_id)}">×</button></span>`).join(" ") : "Связи с клиентом, заказом, ремонтом, закупкой и задачей пока не добавлены.";
    }

    async function updateThread(patch) {
        if (!current) return;
        try {
            current = await api(`/api/v1/mail/threads/${current.id}`, {method: "PATCH", body: JSON.stringify(patch)});
            notify("Переписка обновлена");
            load();
        } catch (error) {
            notify(error.message, "error");
        }
    }

    function compose(mode = "new") {
        const form = $("#composeForm");
        form.reset();
        form.dataset.customerLinked = "0";
        form.dataset.customerId = "";
        linkedCustomer = false;
        if (current && mode !== "new") {
            const last = current.messages[current.messages.length - 1];
            form.thread_id.value = current.id;
            form.in_reply_to.value = last.message_id;
            linkedCustomer = current.links.some((link) => link.entity_type === "customer");
            form.subject.value = mode === "forward" ? `Fwd: ${current.subject}` : (/^re:/i.test(current.subject) ? current.subject : `Re: ${current.subject}`);
            if (mode === "reply") form.to.value = last.sender_email;
            else if (mode === "replyAll") {
                const emails = [last.sender_email, ...last.recipients.filter((recipient) => ["to", "cc"].includes(recipient.kind)).map((recipient) => recipient.email)]
                    .filter((value, index, all) => value && value !== boot.account?.email && all.indexOf(value) === index);
                form.to.value = emails.shift() || "";
                form.cc.value = emails.join(", ");
            } else {
                form.text_body.value = `\n\n---------- Пересланное письмо ----------\n${last.text_body || ""}`;
            }
        }
        showDrawer($("#composeDrawer"), document.activeElement);
        form.to.focus();
    }

    async function filePayload(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(new Error("Не удалось прочитать вложение"));
            reader.onload = () => resolve({
                name: file.name,
                content_type: file.type || "application/octet-stream",
                data: String(reader.result).split(",", 2)[1] || "",
            });
            reader.readAsDataURL(file);
        });
    }

    async function submitCompose(draft) {
        const form = $("#composeForm");
        const errorNode = $("#composeError");
        errorNode.hidden = true;
        const values = new FormData(form);
        const data = {};
        for (const [key, value] of values.entries()) if (key !== "attachments") data[key] = value;
        data.customer_id = form.dataset.customerId || null;
        const files = [...form.attachments.files];
        if (files.some((file) => file.size > 15 * 1024 * 1024) || files.reduce((sum, file) => sum + file.size, 0) > 25 * 1024 * 1024) {
            errorNode.textContent = "Проверьте ограничения размера вложений.";
            errorNode.hidden = false;
            return;
        }
        data.attachments = await Promise.all(files.map(filePayload));
        data.draft = draft;
        data.references = current?.messages?.map((message) => message.message_id) || [];
        if (!draft && !linkedCustomer && form.dataset.customerLinked !== "1" && !window.confirm("Адрес не связан с выбранным клиентом ERP. Всё равно отправить письмо?")) return;
        const key = form.dataset.key || (form.dataset.key = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`);
        const button = draft ? $("#saveDraft") : $("#sendMail");
        button.disabled = true;
        button.textContent = draft ? "Сохраняем…" : "Отправляется…";
        try {
            await api("/api/v1/mail/outbox", {method: "POST", headers: {"Idempotency-Key": key}, body: JSON.stringify(data)});
            notify(draft ? "Черновик сохранён" : "Письмо поставлено в безопасную очередь отправки");
            closeDrawer($("#composeDrawer"));
            form.dataset.key = "";
            load();
        } catch (error) {
            errorNode.textContent = error.message;
            errorNode.hidden = false;
        } finally {
            button.disabled = false;
            button.textContent = draft ? "Сохранить черновик" : "Отправить";
        }
    }

    document.querySelectorAll("[data-mail-view]").forEach((button) => button.addEventListener("click", () => {
        document.querySelectorAll("[data-mail-view]").forEach((item) => {
            item.classList.remove("active");
            item.removeAttribute("aria-current");
        });
        button.classList.add("active");
        button.setAttribute("aria-current", "page");
        view = button.dataset.mailView;
        page = 1;
        load();
    }));
    $("#mailFilters")?.addEventListener("submit", (event) => { event.preventDefault(); page = 1; load(); });
    $("#mailPagination")?.addEventListener("click", (event) => {
        const direction = event.target.dataset.page;
        if (!direction) return;
        page += direction === "next" ? 1 : -1;
        load();
    });
    $("#syncMail")?.addEventListener("click", async () => {
        try {
            await api("/api/v1/mail/sync", {method: "POST", body: "{}"});
            notify("Синхронизация запущена");
            syncStatus.innerHTML = '<span class="mail-sync-spinner" aria-hidden="true"></span> Синхронизация ожидает ближайшего фонового запуска';
        } catch (error) {
            notify(error.message, "error");
        }
    });
    $("#composeMail")?.addEventListener("click", () => compose("new"));
    $("#replyMail")?.addEventListener("click", () => { closeDrawer($("#threadDrawer")); compose("reply"); });
    $("#replyAll")?.addEventListener("click", () => { closeDrawer($("#threadDrawer")); compose("replyAll"); });
    $("#forwardMail")?.addEventListener("click", () => { closeDrawer($("#threadDrawer")); compose("forward"); });
    $("#threadAssignee")?.addEventListener("change", (event) => updateThread({assignee_id: event.target.value || null}));
    $("#threadStatus")?.addEventListener("change", (event) => updateThread({status: event.target.value}));
    $("#threadDue")?.addEventListener("change", (event) => updateThread({due_at: event.target.value || null}));
    $("#threadArchive")?.addEventListener("click", () => updateThread({archived: !current.archived}));
    $("#threadTask")?.addEventListener("click", async () => {
        try {
            await api(`/api/v1/mail/threads/${current.id}/tasks`, {method: "POST", headers: {"Idempotency-Key": `mail-task-${current.id}-${Date.now()}`}, body: "{}"});
            notify("Задача создана");
            openThread(current.id);
        } catch (error) {
            notify(error.message, "error");
        }
    });
    $("#composeForm")?.addEventListener("submit", (event) => { event.preventDefault(); submitCompose(false); });
    $("#saveDraft")?.addEventListener("click", () => submitCompose(true));
    document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => closeDrawer(button.closest(".mail-drawer"))));
    backdrop?.addEventListener("click", () => document.querySelectorAll(".mail-drawer:not([hidden])").forEach(closeDrawer));

    let customerTimer;
    $("#composeForm")?.to.addEventListener("input", (event) => {
        clearTimeout(customerTimer);
        linkedCustomer = false;
        event.target.form.dataset.customerLinked = "0";
        event.target.form.dataset.customerId = "";
        const query = event.target.value.trim();
        const results = $("#customerResults");
        if (query.length < 3) { results.hidden = true; return; }
        customerTimer = window.setTimeout(async () => {
            try {
                const rows = await api("/api/v1/mail/customers?q=" + encodeURIComponent(query));
                results.innerHTML = rows.map((row) => `<button type="button" data-customer-email="${escapeHtml(row.email || "")}" data-customer-id="${row.id}">${escapeHtml(row.name || "Клиент")} · ${escapeHtml(row.email || "нет email")}</button>`).join("") || `<button type="button" data-create-customer>Создать клиента с email ${escapeHtml(query)}</button>`;
                results.hidden = false;
                results.querySelectorAll("[data-customer-email]").forEach((button) => button.addEventListener("click", () => {
                    if (!button.dataset.customerEmail) return;
                    event.target.value = button.dataset.customerEmail;
                    linkedCustomer = true;
                    event.target.form.dataset.customerLinked = "1";
                    event.target.form.dataset.customerId = button.dataset.customerId;
                    results.hidden = true;
                }));
                results.querySelector("[data-create-customer]")?.addEventListener("click", async () => {
                    if (!window.confirm("Создать нового клиента с указанным email?")) return;
                    const response = await fetch("/api/v1/purchases/customers", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, "X-Vechasu-Notify": "off"},
                        body: JSON.stringify({name: query.split("@")[0], email: query}),
                    });
                    const payload = await response.json();
                    if (!response.ok) throw new Error(payload.message || "Не удалось создать клиента");
                    event.target.form.dataset.customerLinked = "1";
                    event.target.form.dataset.customerId = payload.customer.id;
                    linkedCustomer = true;
                    results.hidden = true;
                    notify("Клиент создан и выбран");
                });
            } catch (error) {
                results.innerHTML = `<small>${escapeHtml(error.message)}</small>`;
                results.hidden = false;
            }
        }, 250);
    });

    $("#threadLinks")?.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-unlink-type]");
        if (!button) return;
        try {
            await api(`/api/v1/mail/threads/${current.id}/links/${encodeURIComponent(button.dataset.unlinkType)}/${encodeURIComponent(button.dataset.unlinkId)}`, {method: "DELETE", body: "{}"});
            await openThread(current.id);
        } catch (error) {
            notify(error.message, "error");
        }
    });
    $("#addThreadLink")?.addEventListener("click", async () => {
        const type = window.prompt("Тип связи: customer, order, repair, purchase или task", "customer");
        if (!type) return;
        const id = window.prompt("ID или номер объекта ERP");
        if (!id) return;
        try {
            await api(`/api/v1/mail/threads/${current.id}/links`, {method: "POST", body: JSON.stringify({entity_type: type.trim(), entity_id: id.trim()})});
            await openThread(current.id);
            notify("Связь добавлена");
        } catch (error) {
            notify(error.message, "error");
        }
    });

    if (boot.canManage) setupConnectionWizard();

    function setupConnectionWizard() {
        const wizard = $("#mailConnectionWizard");
        const form = $("#mailSettingsForm");
        if (!wizard || !form) return;
        const providerConfigs = {
            yandex: {imap_host: "imap.yandex.com", imap_port: "993", imap_security: "ssl", smtp_host: "smtp.yandex.com", smtp_port: "465", smtp_security: "ssl"},
            mailru: {imap_host: "imap.mail.ru", imap_port: "993", imap_security: "ssl", smtp_host: "smtp.mail.ru", smtp_port: "465", smtp_security: "ssl"},
            gmail: {imap_host: "imap.gmail.com", imap_port: "993", imap_security: "ssl", smtp_host: "smtp.gmail.com", smtp_port: "465", smtp_security: "ssl"},
            other: {imap_host: "", imap_port: "993", imap_security: "ssl", smtp_host: "", smtp_port: "465", smtp_security: "ssl"},
        };
        let step = 1;
        let connectionProof = "";
        let dirty = false;
        let opener = null;

        function inferredProvider() {
            const imap = form.imap_host.value;
            return Object.keys(providerConfigs).find((name) => name !== "other" && providerConfigs[name].imap_host === imap) || (boot.account ? "other" : "");
        }
        function selectProvider(name, applyDefaults = true) {
            form.provider.value = name;
            document.querySelectorAll("[data-provider]").forEach((button) => {
                const selected = button.dataset.provider === name;
                button.classList.toggle("selected", selected);
                button.setAttribute("aria-checked", selected ? "true" : "false");
            });
            $("#providerError").hidden = true;
            if (applyDefaults && providerConfigs[name]) {
                Object.entries(providerConfigs[name]).forEach(([field, value]) => {
                    if (name !== "other" || !form[field].value) form[field].value = value;
                });
            }
            invalidateProof();
        }
        function showStep(nextStep) {
            step = nextStep;
            document.querySelectorAll("[data-wizard-step]").forEach((section) => { section.hidden = Number(section.dataset.wizardStep) !== step; });
            document.querySelectorAll("[data-step-indicator]").forEach((item) => { item.classList.toggle("active", Number(item.dataset.stepIndicator) === step); });
            $("#wizardBack").hidden = step === 1;
            $("#wizardNext").hidden = step === 3;
            $("#testMail").hidden = step !== 3;
            $("#connectMail").hidden = step !== 3;
            wizard.querySelector(`[data-wizard-step="${step}"] button, [data-wizard-step="${step}"] input, [data-wizard-step="${step}"] select`)?.focus();
        }
        function validateStep(number) {
            if (number === 1 && !form.provider.value) {
                $("#providerError").hidden = false;
                return false;
            }
            const section = wizard.querySelector(`[data-wizard-step="${number}"]`);
            const invalid = [...section.querySelectorAll("input,select")].find((field) => !field.checkValidity());
            if (invalid) { invalid.reportValidity(); invalid.focus(); return false; }
            return true;
        }
        function invalidateProof() {
            connectionProof = "";
            $("#connectMail").disabled = true;
            $("#connectionResults").hidden = true;
        }
        function openWizard(event) {
            opener = event?.currentTarget || document.activeElement;
            dirty = false;
            connectionProof = "";
            $("#settingsError").hidden = true;
            $("#connectionResults").hidden = true;
            $("#connectMail").disabled = true;
            const provider = form.provider.value || inferredProvider();
            if (provider) selectProvider(provider, false);
            showStep(1);
            wizard.showModal();
        }
        function requestClose(force = false) {
            if (!force && dirty && !window.confirm("Закрыть мастер? Введённые изменения не будут сохранены.")) return;
            wizard.close();
            opener?.focus?.();
        }
        function settingsPayload() {
            const payload = Object.fromEntries(new FormData(form).entries());
            payload.connection_proof = connectionProof;
            return payload;
        }
        function renderResult(id, value, successKey) {
            const node = $(id);
            const success = Boolean(value && value[successKey]);
            node.dataset.result = success ? "success" : "error";
            node.querySelector("span").textContent = success ? "✓" : "×";
            node.querySelector("small").textContent = value?.message || "Не проверено";
        }
        function renderConnectionResults(results) {
            $("#connectionResults").hidden = false;
            renderResult("#imapResult", results.imap, "connected");
            renderResult("#smtpResult", results.smtp, "connected");
            renderResult("#tlsResult", results.tls, "active");
        }

        document.querySelectorAll("[data-provider]").forEach((button) => button.addEventListener("click", () => { dirty = true; selectProvider(button.dataset.provider); }));
        $("#openConnectionWizard")?.addEventListener("click", openWizard);
        $("#openSettings")?.addEventListener("click", openWizard);
        $("#closeWizard").addEventListener("click", () => requestClose());
        $("#cancelWizard").addEventListener("click", () => requestClose());
        wizard.addEventListener("cancel", (event) => { event.preventDefault(); requestClose(); });
        form.addEventListener("input", (event) => {
            dirty = true;
            if (event.target.name !== "connection_proof") invalidateProof();
            if (event.target.name === "email" && (!form.login.value || form.login.dataset.auto === "1")) {
                form.login.value = event.target.value;
                form.login.dataset.auto = "1";
            }
        });
        $("#wizardNext").addEventListener("click", () => { if (validateStep(step)) showStep(step + 1); });
        $("#wizardBack").addEventListener("click", () => showStep(step - 1));
        $("#toggleMailPassword").addEventListener("click", (event) => {
            const input = form.password;
            const visible = input.type === "text";
            input.type = visible ? "password" : "text";
            event.currentTarget.textContent = visible ? "Показать" : "Скрыть";
            event.currentTarget.setAttribute("aria-label", visible ? "Показать пароль" : "Скрыть пароль");
        });
        $("#testMail").addEventListener("click", async () => {
            if (!validateStep(2) || !validateStep(3)) return;
            const button = $("#testMail");
            const errorNode = $("#settingsError");
            button.disabled = true;
            button.textContent = "Проверяем…";
            errorNode.hidden = true;
            $("#connectionResults").hidden = false;
            ["#imapResult", "#smtpResult", "#tlsResult"].forEach((selector) => {
                const node = $(selector);
                node.dataset.result = "pending";
                node.querySelector("span").textContent = "•";
                node.querySelector("small").textContent = "Проверяем…";
            });
            try {
                const results = await api("/api/v1/mail/settings/test", {method: "POST", body: JSON.stringify(settingsPayload())});
                renderConnectionResults(results);
                connectionProof = results.proof;
                $("#connectMail").disabled = false;
            } catch (error) {
                renderConnectionResults(error.fields || {});
                errorNode.textContent = error.message;
                errorNode.hidden = false;
                connectionProof = "";
                $("#connectMail").disabled = true;
            } finally {
                button.disabled = false;
                button.textContent = "Проверить подключение";
            }
        });
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!connectionProof) return;
            const errorNode = $("#settingsError");
            const button = $("#connectMail");
            button.disabled = true;
            errorNode.hidden = true;
            try {
                const data = await api("/api/v1/mail/settings", {method: "POST", body: JSON.stringify(settingsPayload())});
                dirty = false;
                requestClose(true);
                setConnected(data.account);
                syncStatus.innerHTML = '<span class="mail-sync-spinner" aria-hidden="true"></span> Загружаем последние письма';
                notify(data.message || "Почта успешно подключена");
                await load();
            } catch (error) {
                errorNode.textContent = error.message;
                errorNode.hidden = false;
            } finally {
                button.disabled = !connectionProof;
            }
        });
        $("#disableMail")?.addEventListener("click", async () => {
            if (!window.confirm("Отключить синхронизацию? Письма останутся в ERP.")) return;
            try {
                await api("/api/v1/mail/settings", {method: "DELETE", body: "{}"});
                const account = {...(boot.account || {}), enabled: false};
                dirty = false;
                requestClose(true);
                setConnected(account);
                $("#connectionTitle").textContent = "Почта отключена";
                notify("Почта отключена");
            } catch (error) {
                notify(error.message, "error");
            }
        });
    }

    const requestedThread = new URLSearchParams(window.location.search).get("thread");
    if (workspace && !workspace.hidden) {
        load().then(() => { if (requestedThread) openThread(requestedThread); });
        window.setInterval(load, 10000);
    }
})();
