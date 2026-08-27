(() => {
    const boot = window.TASKS_BOOTSTRAP || {};
    const state = { view: "today", page: 1, rows: [], pages: 1, loading: false, returnFocus: null, entity: null };
    const list = document.getElementById("taskList");
    const status = document.getElementById("taskStatus");
    const modal = document.getElementById("taskModal");
    const backdrop = document.getElementById("taskBackdrop");
    const form = document.getElementById("taskForm");
    const formError = document.getElementById("taskFormError");
    const save = document.getElementById("saveTask");
    const search = document.getElementById("taskSearch");
    const filters = [document.getElementById("assigneeFilter"), document.getElementById("priorityFilter"), document.getElementById("entityFilter")];
    const reset = document.getElementById("resetFilters");
    const entityType = form.elements.entity_type;
    const entitySearch = document.getElementById("entitySearch");
    const entityWrap = document.getElementById("entitySearchWrap");
    const entityResults = document.getElementById("entityResults");
    const selectedEntity = document.getElementById("selectedEntity");
    const labels = { inbox: "Входящие", today: "Сегодня", plans: "Планы", anytime: "В любое время", someday: "Когда-нибудь", logbook: "Журнал" };
    const priorityLabels = { urgent: "Срочно", important: "Важно", other: "Другое" };
    let searchTimer = 0;
    let entityTimer = 0;

    function notify(message, error = false) {
        status.textContent = message;
        status.classList.toggle("error", error);
        if (window.VechasuNotify) window.VechasuNotify[error ? "error" : "success"](message);
    }
    async function api(url, options = {}) {
        const headers = { Accept: "application/json", ...(options.headers || {}) };
        if (options.body) headers["Content-Type"] = "application/json";
        if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = boot.csrf;
        const response = await fetch(url, { ...options, headers });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.message || "Не удалось выполнить запрос.");
        return payload;
    }
    function taskTone(task) {
        if (state.view === "today" && task.due_date < new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" })) return "overdue";
        return task.priority;
    }
    function groupRows(rows) {
        const groups = [];
        const add = (key, label, tone, items) => { if (items.length) groups.push({ key, label, tone, items }); };
        if (state.view === "today") {
            const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
            add("overdue", "Просрочено", "overdue", rows.filter((row) => row.due_date < today));
            add("urgent", "Срочно", "urgent", rows.filter((row) => row.due_date >= today && row.priority === "urgent"));
            add("important", "Важно", "important", rows.filter((row) => row.due_date >= today && row.priority === "important"));
            add("other", "Другое", "other", rows.filter((row) => row.due_date >= today && row.priority === "other"));
        } else if (state.view === "plans") {
            [...new Set(rows.map((row) => row.due_date))].forEach((date) => add(date, formatDate(date), "other", rows.filter((row) => row.due_date === date)));
        } else add(state.view, labels[state.view], "other", rows);
        return groups;
    }
    function formatDate(value) {
        if (!value) return "Без даты";
        const [year, month, day] = value.split("-").map(Number);
        return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Moscow" }).format(new Date(Date.UTC(year, month - 1, day, 9)));
    }
    function taskRow(task) {
        const row = document.createElement("article");
        row.className = `task-row${task.completed ? " task-completed" : ""}`;
        row.dataset.id = task.id;
        row.dataset.tone = taskTone(task);
        const check = document.createElement("input");
        check.type = "checkbox"; check.className = "task-check"; check.checked = task.completed;
        check.setAttribute("aria-label", task.completed ? `Вернуть задачу «${task.title}» в работу` : `Выполнить задачу «${task.title}»`);
        check.addEventListener("change", () => toggleComplete(task, check));
        const open = document.createElement("button");
        open.type = "button"; open.className = "task-open";
        const title = document.createElement("span"); title.className = "task-title"; title.textContent = task.title; open.append(title);
        if (task.description) { const desc = document.createElement("span"); desc.className = "task-description"; desc.textContent = task.description; open.append(desc); }
        const meta = document.createElement("span"); meta.className = "task-meta";
        const values = [];
        if (task.due_date) values.push(`${formatDate(task.due_date)}${task.due_time ? ` · ${task.due_time}` : ""}`);
        values.push(priorityLabels[task.priority], task.assignee_name);
        if (task.completed_at) values.push(`Выполнено ${new Date(task.completed_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}`);
        values.forEach((value) => { const span = document.createElement("span"); span.textContent = value; meta.append(span); });
        if (task.entity_label) { const badge = document.createElement("a"); badge.className = "task-badge"; badge.href = task.entity_href; badge.textContent = task.entity_label; badge.addEventListener("click", (event) => event.stopPropagation()); meta.append(badge); }
        open.append(meta); open.addEventListener("click", () => openModal(task, open));
        row.append(check, open);
        if (task.completed) { const reopen = document.createElement("button"); reopen.type = "button"; reopen.className = "task-reopen"; reopen.textContent = "Вернуть"; reopen.addEventListener("click", () => toggleComplete(task, check, false)); row.append(reopen); }
        return row;
    }
    function render() {
        list.replaceChildren();
        if (!state.rows.length) {
            const empty = document.createElement("div"); empty.className = "tasks-empty";
            const strong = document.createElement("strong"); strong.textContent = "Задач здесь нет";
            empty.append(strong, document.createTextNode(state.view === "today" ? "На сегодня всё спокойно." : "Создайте задачу или измените фильтры.")); list.append(empty);
        } else groupRows(state.rows).forEach((group) => {
            const section = document.createElement("section"); section.className = "task-group"; section.dataset.tone = group.tone;
            const heading = document.createElement("h2"); heading.textContent = group.label; section.append(heading);
            group.items.forEach((task) => section.append(taskRow(task))); list.append(section);
        });
        const pager = document.getElementById("taskPagination");
        pager.hidden = !["plans", "logbook"].includes(state.view) || state.pages <= 1;
        document.getElementById("pageLabel").textContent = `Страница ${state.page} из ${state.pages}`;
        document.getElementById("prevPage").disabled = state.page <= 1;
        document.getElementById("nextPage").disabled = state.page >= state.pages;
    }
    async function load() {
        if (state.loading) return; state.loading = true; status.textContent = "Загружаем задачи…";
        const params = new URLSearchParams({ view: state.view, page: state.page, per_page: ["plans", "logbook"].includes(state.view) ? 30 : 100 });
        if (search.value.trim()) params.set("q", search.value.trim());
        if (filters[0].value) params.set("assignee_id", filters[0].value);
        if (filters[1].value) params.set("priority", filters[1].value);
        if (filters[2].value) params.set("entity_type", filters[2].value);
        try { const payload = await api(`/api/v1/tasks?${params}`); state.rows = payload.data.rows; state.page = payload.data.page; state.pages = payload.data.pages; render(); status.textContent = `Найдено: ${payload.data.total}`; }
        catch (error) { list.replaceChildren(); notify(error.message, true); }
        finally { state.loading = false; updateFilters(); loadCounts(); }
    }
    async function loadCounts() {
        try { const payload = await api("/api/v1/tasks/counts"); const counts = payload.data; document.querySelector('[data-view-count="today"]').textContent = counts.active || ""; }
        catch (_) { /* list error already provides actionable feedback */ }
    }
    async function toggleComplete(task, checkbox, target = !task.completed) {
        checkbox.disabled = true; const row = checkbox.closest(".task-row"); row.style.opacity = ".45";
        try { await api(`/api/v1/tasks/${task.id}/${target ? "complete" : "reopen"}`, { method: "POST", body: "{}" }); notify(target ? "Задача выполнена." : "Задача возвращена в работу."); await load(); }
        catch (error) { checkbox.checked = task.completed; row.style.opacity = ""; notify(error.message, true); }
        finally { checkbox.disabled = false; }
    }
    function setEntity(entity) {
        state.entity = entity; form.elements.entity_id.value = entity ? entity.id : "";
        selectedEntity.hidden = !entity; selectedEntity.replaceChildren();
        if (entity) { const text = document.createElement("span"); text.textContent = entity.label; const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Убрать"; remove.addEventListener("click", () => setEntity(null)); selectedEntity.append(text, remove); }
    }
    function openModal(task = null, trigger = document.activeElement) {
        form.reset(); form.elements.id.value = task ? task.id : ""; form.elements.assignee_id.value = task ? task.assignee_id : boot.currentUserId;
        ["title", "description", "section", "priority", "due_date", "due_time", "entity_type"].forEach((name) => { if (task) form.elements[name].value = task[name] || ""; });
        setEntity(task && task.entity_id ? { id: task.entity_id, label: task.entity_label, href: task.entity_href } : null);
        entityWrap.hidden = !form.elements.entity_type.value; document.getElementById("taskModalTitle").textContent = task ? "Редактирование задачи" : "Новая задача";
        ["moveInbox", "moveAnytime", "moveSomeday"].forEach((id) => document.getElementById(id).hidden = !task || task.completed);
        formError.hidden = true; modal.hidden = false; backdrop.hidden = false; document.body.style.overflow = "hidden"; state.returnFocus = trigger; form.elements.title.focus();
    }
    function closeModal() { modal.hidden = true; backdrop.hidden = true; document.body.style.overflow = ""; entityResults.hidden = true; if (state.returnFocus) state.returnFocus.focus(); }
    async function submit(event) {
        event.preventDefault(); if (!form.reportValidity() || save.disabled) return;
        if (form.elements.due_time.value && !form.elements.due_date.value) { formError.textContent = "Для времени укажите дату."; formError.hidden = false; form.elements.due_date.focus(); return; }
        const data = Object.fromEntries(new FormData(form)); const id = data.id; delete data.id;
        save.disabled = true; save.textContent = "Сохраняем…"; formError.hidden = true;
        const key = window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
        try { await api(id ? `/api/v1/tasks/${id}` : "/api/v1/tasks", { method: id ? "PATCH" : "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ ...data, idempotency_key: key }) }); closeModal(); notify(id ? "Задача обновлена." : "Задача создана."); state.page = 1; await load(); }
        catch (error) { formError.textContent = error.message; formError.hidden = false; }
        finally { save.disabled = false; save.textContent = "Сохранить"; }
    }
    async function move(section) {
        const id = form.elements.id.value; if (!id) return;
        const button = document.activeElement; button.disabled = true;
        try { await api(`/api/v1/tasks/${id}/move`, { method: "POST", body: JSON.stringify({ section }) }); closeModal(); notify("Задача перемещена."); await load(); }
        catch (error) { formError.textContent = error.message; formError.hidden = false; }
        finally { button.disabled = false; }
    }
    function updateFilters() { const active = Boolean(search.value.trim() || filters.some((item) => item.value)); reset.hidden = !active; [search, ...filters].forEach((item) => item.classList.toggle("active-filter", Boolean(item.value))); }
    document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll("[data-view]").forEach((item) => { item.classList.toggle("active", item === button); item.removeAttribute("aria-current"); }); button.setAttribute("aria-current", "page"); state.view = button.dataset.view; state.page = 1; load(); }));
    filters.forEach((filter) => filter.addEventListener("change", () => { state.page = 1; load(); }));
    search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; load(); }, 250); });
    reset.addEventListener("click", () => { search.value = ""; filters.forEach((item) => { item.value = ""; }); state.page = 1; load(); });
    document.getElementById("prevPage").addEventListener("click", () => { state.page -= 1; load(); }); document.getElementById("nextPage").addEventListener("click", () => { state.page += 1; load(); });
    document.getElementById("newTask").addEventListener("click", (event) => openModal(null, event.currentTarget)); ["closeTask", "cancelTask"].forEach((id) => document.getElementById(id).addEventListener("click", closeModal)); backdrop.addEventListener("click", closeModal); form.addEventListener("submit", submit);
    entityType.addEventListener("change", () => { entityWrap.hidden = !entityType.value; setEntity(null); entitySearch.value = ""; entityResults.hidden = true; });
    entitySearch.addEventListener("input", () => { clearTimeout(entityTimer); const q = entitySearch.value.trim(); if (!q) { entityResults.hidden = true; return; } entityTimer = setTimeout(async () => { try { const payload = await api(`/api/v1/tasks/entities?type=${encodeURIComponent(entityType.value)}&q=${encodeURIComponent(q)}`); entityResults.replaceChildren(); payload.data.forEach((entity) => { const button = document.createElement("button"); button.type = "button"; button.role = "option"; button.textContent = entity.label; button.addEventListener("click", () => { setEntity(entity); entityResults.hidden = true; }); entityResults.append(button); }); entityResults.hidden = !payload.data.length; } catch (error) { notify(error.message, true); } }, 250); });
    document.getElementById("moveInbox").addEventListener("click", () => move("inbox")); document.getElementById("moveAnytime").addEventListener("click", () => move("anytime")); document.getElementById("moveSomeday").addEventListener("click", () => move("someday"));
    modal.addEventListener("keydown", (event) => { if (event.key === "Escape") return closeModal(); if (event.key !== "Tab") return; const focusable = [...modal.querySelectorAll('button:not([disabled]):not([hidden]),input:not([disabled]),select:not([disabled]),textarea:not([disabled])')].filter((item) => !item.closest("[hidden]")); const first = focusable[0], last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } });
    load();
    if (boot.prefillType && boot.prefillId) {
        api(`/api/v1/tasks/entities?type=${encodeURIComponent(boot.prefillType)}&q=${encodeURIComponent(boot.prefillId)}`).then((payload) => {
            const entity = payload.data.find((item) => String(item.id) === String(boot.prefillId));
            openModal(null, document.getElementById("newTask"));
            entityType.value = boot.prefillType; entityWrap.hidden = false;
            if (entity) setEntity(entity);
        }).catch(() => openModal(null, document.getElementById("newTask")));
    }
})();
