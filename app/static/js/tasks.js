(() => {
    if (window.__vechasuTasksInitialized) return;
    window.__vechasuTasksInitialized = true;

    const boot = window.TASKS_BOOTSTRAP || {};
    const q = (selector) => document.querySelector(selector);
    const qa = (selector) => [...document.querySelectorAll(selector)];
    const validViews = ["inbox", "overdue", "today", "plans", "waiting", "anytime", "someday", "logbook"];
    const labels = { inbox: "Входящие", overdue: "Просрочено", today: "Сегодня", plans: "Планы", waiting: "Ожидаю", anytime: "В любое время", someday: "Когда-нибудь", logbook: "Журнал" };
    const statuses = { new: "Новая", in_progress: "В работе", waiting: "Ожидаю", completed: "Выполнена", cancelled: "Отменена" };
    const priorities = { urgent: "Срочно", important: "Важно", other: "Другое" };
    const initial = new URLSearchParams(location.search);
    const state = {
        view: validViews.includes(initial.get("view")) ? initial.get("view") : "today",
        mode: initial.get("mode") === "calendar" ? "calendar" : "list",
        calendarKind: initial.get("calendar") === "week" ? "week" : "month",
        calendarDate: /^\d{4}-\d{2}-\d{2}$/.test(initial.get("date") || "") ? initial.get("date") : null,
        includeCompleted: initial.get("completed") === "1",
        scope: ["mine", "created", "team", "all"].includes(initial.get("scope")) ? initial.get("scope") : "all",
        page: Math.max(1, Number(initial.get("page")) || 1),
        rows: [], pages: 1, total: 0, requestToken: 0, task: null, links: [],
        returnFocus: null, restoreTaskFocusId: null, filtersOpen: initial.get("filters") === "1", drawerHistoryPushed: false,
        pendingCompletions: new Set(), calendarRows: [], undatedRows: [], calendarRange: null, countsToken: 0,
    };
    const list = q("#taskList"), status = q("#taskStatus"), drawer = q("#taskModal"), backdrop = q("#taskBackdrop");
    const form = q("#taskForm"), errorBox = q("#taskFormError"), save = q("#saveTask");
    const search = q("#taskSearch"), clearSearch = q("#clearSearch"), mine = q("#mineFilter");
    const filterToggle = q("#toggleFilters"), filterPanel = q("#advancedFilters"), filterCount = q("#filterCount");
    const filterSummary = q("#filterSummary"), filterChips = q("#filterChips"), reset = q("#resetFilters");
    const listMode = q("#listMode"), calendarMode = q("#calendarMode"), calendarGrid = q("#calendarGrid");
    const calendarStatus = q("#calendarStatus"), calendarCompleted = q("#calendarCompleted");
    const sectionStats = q("#taskSectionStats");
    const filterDefs = [
        { node: q("#assigneeFilter"), param: "assignee_id", label: "Ответственный" },
        { node: q("#priorityFilter"), param: "priority", label: "Приоритет" },
        { node: q("#statusFilter"), param: "status", label: "Статус" },
        { node: q("#entityFilter"), param: "entity_type", label: "Связь" },
        { node: q("#dueFilter"), param: "due", label: "Срок" },
    ];
    let searchTimer = 0, entityTimer = 0;

    function notify(message, isError = false, undo = null) {
        status.replaceChildren(document.createTextNode(message));
        status.classList.toggle("error", isError);
        if (undo) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "Отменить";
            button.addEventListener("click", undo, { once: true });
            status.append(" ", button);
        }
        if (window.VechasuNotify) window.VechasuNotify[isError ? "error" : "success"](message);
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

    function localDate(offset = 0) {
        const date = new Date();
        date.setDate(date.getDate() + offset);
        return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(date);
    }

    function formatDate(value, options = {}) {
        if (!value) return "Без даты";
        const [year, month, day] = value.split("-").map(Number);
        return new Intl.DateTimeFormat("ru-RU", {
            day: "numeric", month: "long", ...(options.year === false ? {} : { year: "numeric" }), timeZone: "Europe/Moscow",
        }).format(new Date(Date.UTC(year, month - 1, day, 9)));
    }

    function taskDate(task) { return task.status === "waiting" && task.check_date ? task.check_date : task.due_date; }
    function dateTone(task) {
        const date = taskDate(task);
        if (date && date < localDate()) return "overdue";
        if (date === localDate()) return "today";
        return date ? "future" : "none";
    }
    function initials(value) {
        const parts = String(value || "Сотрудник").trim().split(/\s+/).filter(Boolean);
        return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "С";
    }

    function activeFilterCount() {
        return Number(Boolean(search.value.trim())) + Number(mine.checked) + filterDefs.filter(({ node }) => node.value).length;
    }

    function buildUrlParams(taskId = state.task && state.task.id, newTask = false) {
        const params = new URLSearchParams();
        params.set("view", state.view);
        params.set("scope", state.scope);
        params.set("mode", state.mode);
        if (state.mode === "calendar") {
            params.set("calendar", state.calendarKind);
            params.set("date", state.calendarDate || localDate());
            if (state.includeCompleted) params.set("completed", "1");
        }
        if (state.page > 1) params.set("page", String(state.page));
        if (search.value.trim()) params.set("q", search.value.trim());
        filterDefs.forEach(({ node, param }) => { if (node.value) params.set(param, node.value); });
        if (mine.checked) params.set("only_mine", "1");
        if (state.filtersOpen) params.set("filters", "1");
        if (taskId) params.set("task", String(taskId));
        if (newTask) params.set("new", "1");
        return params;
    }

    function syncUrl(mode = "replace", taskId = state.task && state.task.id, newTask = false) {
        const params = buildUrlParams(taskId, newTask);
        history[mode === "push" ? "pushState" : "replaceState"]({ tasks: true }, "", `${location.pathname}?${params}`);
    }

    function applyFiltersFromUrl(params) {
        state.view = validViews.includes(params.get("view")) ? params.get("view") : "today";
        state.scope = ["mine", "created", "team", "all"].includes(params.get("scope")) ? params.get("scope") : "all";
        state.mode = params.get("mode") === "calendar" ? "calendar" : "list";
        state.calendarKind = params.get("calendar") === "week" ? "week" : "month";
        state.calendarDate = /^\d{4}-\d{2}-\d{2}$/.test(params.get("date") || "") ? params.get("date") : localDate();
        state.includeCompleted = params.get("completed") === "1";
        state.page = Math.max(1, Number(params.get("page")) || 1);
        search.value = params.get("q") || "";
        filterDefs.forEach(({ node, param }) => { node.value = params.get(param) || ""; });
        mine.checked = params.get("only_mine") === "1";
        state.filtersOpen = params.get("filters") === "1";
        updateViewButtons();
        updateScopeButtons();
        updateMode();
        updateFilters();
    }

    function setFiltersOpen(open, sync = true) {
        state.filtersOpen = Boolean(open);
        filterPanel.hidden = !state.filtersOpen;
        filterToggle.setAttribute("aria-expanded", String(state.filtersOpen));
        if (sync) syncUrl("replace");
    }

    function updateScopeButtons() {
        qa("[data-scope]").forEach((button) => {
            const active = button.dataset.scope === state.scope;
            button.classList.toggle("active", active);
            if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
        });
    }

    function makeFilterChip(text, clear) {
        const chip = document.createElement("span");
        chip.className = "tasks-filter-chip";
        chip.append(document.createTextNode(text));
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "×";
        button.setAttribute("aria-label", `Убрать фильтр «${text}»`);
        button.addEventListener("click", () => { clear(); state.page = 1; updateFilters(); syncUrl("push", null); load(); });
        chip.append(button);
        return chip;
    }

    function updateFilters() {
        const count = activeFilterCount();
        clearSearch.hidden = !search.value;
        search.classList.toggle("active-filter", Boolean(search.value.trim()));
        mine.closest("label").classList.toggle("active-filter", mine.checked);
        filterDefs.forEach(({ node }) => node.classList.toggle("active-filter", Boolean(node.value)));
        filterCount.hidden = count === 0;
        filterCount.textContent = String(count);
        filterSummary.hidden = count === 0;
        filterChips.replaceChildren();
        if (search.value.trim()) filterChips.append(makeFilterChip(`Поиск: ${search.value.trim()}`, () => { search.value = ""; }));
        if (mine.checked) filterChips.append(makeFilterChip("Только мои", () => { mine.checked = false; }));
        filterDefs.forEach(({ node, label }) => {
            if (!node.value) return;
            const option = node.options[node.selectedIndex];
            filterChips.append(makeFilterChip(`${label}: ${option.textContent}`, () => { node.value = ""; }));
        });
        setFiltersOpen(state.filtersOpen, false);
    }

    function entityChip(link) {
        const element = document.createElement(link.entity_href ? "a" : "span");
        element.className = "task-badge";
        if (link.entity_href) element.href = link.entity_href;
        element.textContent = link.entity_label || `${link.entity_type} ${link.entity_id}`;
        element.addEventListener("click", (event) => event.stopPropagation());
        return element;
    }

    function metaItem(className, content) {
        const item = document.createElement("span");
        item.className = `task-meta-item ${className}`;
        if (typeof content === "string") item.textContent = content;
        else item.append(content);
        return item;
    }

    function taskRow(task) {
        const article = document.createElement("article");
        article.className = `task-row${task.completed ? " task-completed" : ""}${state.task && Number(state.task.id) === Number(task.id) ? " task-opened" : ""}`;
        article.dataset.taskId = task.id;

        const check = document.createElement("input");
        check.type = "checkbox";
        check.className = "task-check";
        check.checked = task.completed;
        check.setAttribute("aria-label", `${task.completed ? "Восстановить" : "Выполнить"} «${task.title}»`);
        check.addEventListener("click", (event) => event.stopPropagation());
        check.addEventListener("change", () => quickComplete(task, check, article));

        const open = document.createElement("div");
        open.className = "task-open";
        const title = document.createElement("span");
        title.className = "task-title";
        title.textContent = task.title;
        open.append(title);
        if (task.description) {
            const description = document.createElement("span");
            description.className = "task-description";
            description.textContent = task.description;
            open.append(description);
        }

        const meta = document.createElement("span");
        meta.className = "task-meta";
        const date = taskDate(task), tone = dateTone(task);
        if (date) {
            const prefix = task.status === "waiting" ? "Проверить " : "";
            meta.append(metaItem(`task-date ${tone}`, `${prefix}${formatDate(date, { year: false })}${task.due_time ? ` · ${task.due_time}` : ""}`));
        }
        meta.append(metaItem("task-status", statuses[task.status] || task.status));
        const dot = document.createElement("i");
        dot.className = `task-priority-dot ${task.priority}`;
        dot.setAttribute("aria-hidden", "true");
        const priority = metaItem("task-priority", dot);
        priority.append(document.createTextNode(priorities[task.priority] || task.priority));
        meta.append(priority);
        const avatar = document.createElement("span");
        avatar.className = "task-avatar";
        avatar.textContent = initials(task.assignee_name);
        avatar.title = task.assignee_name || "Сотрудник";
        meta.append(metaItem("task-assignee", avatar));
        (task.links || []).forEach((link) => meta.append(entityChip(link)));
        open.append(meta);
        open.addEventListener("click", () => openTask(task.id, open));

        const actions = document.createElement("span");
        actions.className = "task-row-actions";
        const openAction = document.createElement("button");
        openAction.type = "button";
        openAction.className = "task-row-action";
        openAction.textContent = "›";
        openAction.setAttribute("aria-label", `Открыть карточку «${task.title}»`);
        openAction.addEventListener("click", (event) => { event.stopPropagation(); openTask(task.id, openAction); });
        actions.append(openAction);
        article.addEventListener("click", (event) => {
            if (!event.target.closest("button, input, a")) openTask(task.id, article);
        });
        article.append(check, open, actions);
        return article;
    }

    function grouping(rows) {
        if (state.view === "today") {
            const order = ["overdue", "urgent", "important", "other"];
            const names = { overdue: "Просроченные", urgent: "Срочные", important: "Важные", other: "Остальные" };
            const buckets = Object.fromEntries(order.map((key) => [key, []]));
            rows.forEach((task) => { const key = dateTone(task) === "overdue" ? "overdue" : task.priority; buckets[key].push(task); });
            return order.filter((key) => buckets[key].length).map((key) => ({ key, label: names[key], rows: buckets[key] }));
        }
        if (state.view === "plans") {
            const groups = new Map();
            rows.forEach((task) => { const key = taskDate(task) || "none"; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(task); });
            return [...groups].map(([key, items]) => ({ key, label: key === "none" ? "Без даты" : formatDate(key), rows: items }));
        }
        if (state.view === "logbook") {
            const groups = new Map();
            rows.forEach((task) => { const key = String(task.completed_at || task.cancelled_at || task.updated_at || "").slice(0, 10) || "none"; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(task); });
            return [...groups].map(([key, items]) => ({ key, label: key === "none" ? "Без даты" : formatDate(key), rows: items }));
        }
        return [{ key: state.view, label: "", rows }];
    }

    function emptyContent() {
        if (activeFilterCount()) return { title: "Ничего не найдено", text: "Попробуйте убрать один из фильтров или изменить запрос.", action: "Сбросить фильтры", reset: true };
        const values = {
            inbox: ["Входящие разобраны", "Новые задачи появятся здесь.", "Создать задачу"],
            overdue: ["Просроченных задач нет", "Всё идёт по плану.", "Перейти на сегодня"],
            today: ["На сегодня всё спокойно", "Можно заняться планами или добавить новую задачу.", "Создать задачу"],
            plans: ["Планов пока нет", "Назначьте задаче будущую дату.", "Создать задачу"],
            waiting: ["Ничего не ждём", "Задачи в ожидании появятся здесь.", "Создать задачу"],
            anytime: ["Список пуст", "Добавьте задачу без конкретного срока.", "Создать задачу"],
            someday: ["Идей на потом нет", "Сохраните здесь то, к чему хотите вернуться.", "Создать задачу"],
            logbook: ["Журнал пока пуст", "Выполненные и отменённые задачи появятся здесь.", "Перейти на сегодня"],
        };
        const [title, text, action] = values[state.view];
        return { title, text, action, reset: false };
    }

    function render() {
        list.replaceChildren();
        if (!state.rows.length) {
            const content = emptyContent();
            const empty = document.createElement("div");
            empty.className = "tasks-empty";
            const icon = document.createElement("span");
            icon.className = "tasks-empty-icon";
            icon.setAttribute("aria-hidden", "true");
            icon.textContent = "✓";
            const strong = document.createElement("strong");
            strong.textContent = content.title;
            const text = document.createElement("span");
            text.textContent = content.text;
            const action = document.createElement("button");
            action.type = "button";
            action.textContent = content.action;
            action.addEventListener("click", () => {
                if (content.reset) resetAll();
                else if (["overdue", "logbook"].includes(state.view)) changeView("today");
                else openNew(action);
            });
            empty.append(icon, strong, text, action);
            list.append(empty);
        } else {
            grouping(state.rows).forEach((group) => {
                const section = document.createElement("section");
                section.className = "tasks-group";
                if (group.label) {
                    const heading = document.createElement("h2");
                    heading.className = "tasks-group-title";
                    heading.append(document.createTextNode(group.label));
                    const count = document.createElement("span");
                    count.textContent = String(group.rows.length);
                    heading.append(count);
                    section.append(heading);
                }
                const items = document.createElement("div");
                items.className = "tasks-group-items";
                group.rows.forEach((task) => items.append(taskRow(task)));
                section.append(items);
                list.append(section);
            });
        }
        const pager = q("#taskPagination");
        pager.hidden = state.pages <= 1;
        q("#pageLabel").textContent = `Страница ${state.page} из ${state.pages}`;
        q("#prevPage").disabled = state.page <= 1;
        q("#nextPage").disabled = state.page >= state.pages;
        if (state.restoreTaskFocusId) {
            const taskId = Number(state.restoreTaskFocusId);
            const row = qa(".task-row").find((item) => Number(item.dataset.taskId) === taskId);
            state.restoreTaskFocusId = null;
            if (row) row.querySelector(".task-row-action")?.focus();
        }
    }

    function isoDate(date) {
        return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
    }

    function dateFromIso(value) {
        const [year, month, day] = value.split("-").map(Number);
        return new Date(Date.UTC(year, month - 1, day, 12));
    }

    function addDays(value, amount) {
        const date = dateFromIso(value);
        date.setUTCDate(date.getUTCDate() + amount);
        return isoDate(date);
    }

    function calendarRange() {
        const cursor = dateFromIso(state.calendarDate || localDate());
        if (state.calendarKind === "week") {
            const mondayOffset = (cursor.getUTCDay() + 6) % 7;
            const start = addDays(isoDate(cursor), -mondayOffset);
            return { start, end: addDays(start, 6) };
        }
        const first = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth(), 1, 12));
        const last = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0, 12));
        const start = addDays(isoDate(first), -((first.getUTCDay() + 6) % 7));
        const end = addDays(isoDate(last), 6 - ((last.getUTCDay() + 6) % 7));
        const today = localDate();
        const todayCursor = dateFromIso(today);
        const todayWeekStart = addDays(today, -((todayCursor.getUTCDay() + 6) % 7));
        const currentMonth = isoDate(cursor).slice(0, 7) === today.slice(0, 7);
        return { start, end: currentMonth && end === addDays(todayWeekStart, 6) ? addDays(end, 7) : end };
    }

    function updateMode() {
        const calendar = state.mode === "calendar";
        listMode.hidden = calendar;
        calendarMode.hidden = !calendar;
        q(".tasks-workspace").setAttribute("aria-label", calendar ? "Календарь задач" : "Список задач");
        qa("[data-mode]").forEach((button) => {
            const active = button.dataset.mode === state.mode;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        });
        qa("[data-calendar-kind]").forEach((button) => {
            const active = button.dataset.calendarKind === state.calendarKind;
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        });
        calendarCompleted.checked = state.includeCompleted;
    }

    function calendarCard(task) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = `calendar-task priority-${task.priority}${task.completed ? " completed" : ""}`;
        const tone = dateTone({ ...task, due_date: task.calendar_date, check_date: null, status: task.status });
        card.classList.add(tone);
        card.dataset.taskId = task.id;
        card.draggable = Boolean(task.can_edit);
        card.setAttribute("aria-label", `Открыть задачу «${task.title}»${task.calendar_date ? `, ${formatDate(task.calendar_date)}` : ", без даты"}`);
        const time = document.createElement("span");
        time.className = "calendar-task-time";
        time.textContent = task.due_time || (task.completed ? "✓" : "");
        const dot = document.createElement("span");
        dot.className = "calendar-priority-dot";
        dot.setAttribute("aria-hidden", "true");
        const title = document.createElement("span");
        title.className = "calendar-task-title";
        title.textContent = task.title;
        const avatar = document.createElement("span");
        avatar.className = "calendar-task-avatar";
        avatar.textContent = initials(task.assignee_name);
        avatar.title = task.assignee_name;
        card.append(time, dot, title);
        if (task.entity_type) {
            const relation = document.createElement("span");
            relation.className = "calendar-task-relation";
            relation.textContent = { customer: "К", order: "З", product: "Т", sale: "П", repair: "Р", purchase: "Зк" }[task.entity_type] || "↗";
            relation.title = task.entity_label || "Связанная запись";
            card.append(relation);
        }
        card.append(avatar);
        card.addEventListener("click", (event) => { event.stopPropagation(); openTask(task.id, card); });
        card.addEventListener("dragstart", (event) => {
            if (!task.can_edit) { event.preventDefault(); return; }
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/task-id", String(task.id));
            card.classList.add("dragging");
        });
        card.addEventListener("dragend", () => card.classList.remove("dragging"));
        return card;
    }

    function enableDrop(node, date, time) {
        node.addEventListener("dragover", (event) => { event.preventDefault(); node.classList.add("drop-target"); });
        node.addEventListener("dragleave", () => node.classList.remove("drop-target"));
        node.addEventListener("drop", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            node.classList.remove("drop-target");
            node.dataset.dropGuard = String(Date.now());
            const taskId = Number(event.dataTransfer.getData("text/task-id"));
            if (taskId) await moveCalendarTask(taskId, date, time);
        });
    }

    function clickAfterDrop(node) {
        return Date.now() - Number(node.dataset.dropGuard || 0) < 500;
    }

    async function moveCalendarTask(taskId, dueDate, dueTime) {
        const task = [...state.calendarRows, ...state.undatedRows].find((item) => Number(item.id) === Number(taskId));
        if (!task || !task.can_edit) { notify("У вас нет права переносить эту задачу.", true); return; }
        calendarGrid.classList.add("saving");
        try {
            await api(`/api/v1/tasks/${taskId}/calendar-reschedule`, {
                method: "POST",
                body: JSON.stringify({ due_date: dueDate, ...(dueTime !== undefined ? { due_time: dueTime } : {}), version: task.version, section: task.section || "inbox" }),
            });
            notify(dueDate ? "Задача перенесена." : "Дата задачи убрана.");
        } catch (error) {
            notify(`${error.message} Изменение отменено.`, true);
        } finally {
            calendarGrid.classList.remove("saving");
            await loadCalendar();
        }
    }

    function openCalendarNew(date, time, trigger) {
        openNew(trigger);
        form.elements.due_date.value = date;
        form.elements.due_time.value = time || "";
    }

    function renderUndated() {
        const wrap = q("#undatedList");
        q("#undatedCount").textContent = String(state.undatedTotal || state.undatedRows.length);
        wrap.replaceChildren();
        state.undatedRows.forEach((task) => wrap.append(calendarCard(task)));
        if (!state.undatedRows.length) {
            const empty = document.createElement("span");
            empty.className = "calendar-undated-empty";
            empty.textContent = "Нет подходящих задач без даты";
            wrap.append(empty);
        }
        enableDrop(wrap, null, null);
        q("#undatedTasks").open = state.undatedRows.length > 0 && state.undatedRows.length <= 4;
    }

    function monthCell(date, currentMonth, rows) {
        const cell = document.createElement("div");
        cell.className = "calendar-day";
        if (date.slice(0, 7) !== currentMonth) cell.classList.add("outside");
        if (date === localDate()) cell.classList.add("is-today");
        cell.tabIndex = 0;
        cell.setAttribute("role", "button");
        cell.setAttribute("aria-label", `Создать задачу на ${formatDate(date)}`);
        const number = document.createElement("span");
        number.className = "calendar-day-number";
        number.textContent = String(Number(date.slice(-2)));
        const stack = document.createElement("div");
        stack.className = "calendar-day-tasks";
        rows.forEach((task, index) => {
            const card = calendarCard(task);
            if (index >= 3) card.hidden = true;
            stack.append(card);
        });
        if (rows.length > 3) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "calendar-more";
            more.textContent = `+ ещё ${rows.length - 3}`;
            more.addEventListener("click", (event) => {
                event.stopPropagation();
                stack.querySelectorAll(".calendar-task[hidden]").forEach((item) => { item.hidden = false; });
                more.remove();
            });
            stack.append(more);
        }
        cell.append(number, stack);
        cell.addEventListener("click", () => { if (!clickAfterDrop(cell)) openCalendarNew(date, "", cell); });
        cell.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openCalendarNew(date, "", cell); }
        });
        enableDrop(cell, date, undefined);
        return cell;
    }

    function renderMonth(range) {
        calendarGrid.className = "calendar-grid calendar-month";
        const weekdayRow = document.createElement("div");
        weekdayRow.className = "calendar-weekdays";
        weekdayRow.setAttribute("aria-hidden", "true");
        const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
        weekdays.forEach((label) => {
            const head = document.createElement("div"); head.className = "calendar-weekday"; head.textContent = label; weekdayRow.append(head);
        });
        const scroll = document.createElement("div");
        scroll.className = "calendar-month-scroll";
        scroll.tabIndex = 0;
        scroll.setAttribute("aria-label", "Недели календаря; предыдущие недели доступны прокруткой вверх");
        const month = (state.calendarDate || localDate()).slice(0, 7);
        for (let weekStart = range.start; weekStart <= range.end; weekStart = addDays(weekStart, 7)) {
            const week = document.createElement("div");
            week.className = "calendar-month-week";
            week.dataset.weekStart = weekStart;
            for (let offset = 0; offset < 7; offset += 1) {
                const date = addDays(weekStart, offset);
                week.append(monthCell(date, month, state.calendarRows.filter((task) => task.calendar_date === date)));
            }
            scroll.append(week);
        }
        calendarGrid.append(weekdayRow, scroll);
        const currentMonth = month === localDate().slice(0, 7);
        const todayCursor = dateFromIso(localDate());
        const todayWeekStart = addDays(localDate(), -((todayCursor.getUTCDay() + 6) % 7));
        const currentWeek = scroll.querySelector(`[data-week-start="${todayWeekStart}"]`);
        const positionWeek = () => {
            scroll.scrollTop = currentMonth && currentWeek ? currentWeek.offsetTop - scroll.offsetTop : 0;
        };
        positionWeek();
        window.requestAnimationFrame(() => { if (document.contains(scroll)) positionWeek(); });
    }

    function renderWeek(range) {
        calendarGrid.className = "calendar-grid calendar-week";
        for (let date = range.start; date <= range.end; date = addDays(date, 1)) {
            const column = document.createElement("section");
            column.className = `calendar-week-day${date === localDate() ? " is-today" : ""}`;
            const heading = document.createElement("h3");
            heading.textContent = new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "short" }).format(dateFromIso(date));
            const allDay = document.createElement("div"); allDay.className = "calendar-all-day";
            state.calendarRows.filter((task) => task.calendar_date === date && !task.due_time).forEach((task) => allDay.append(calendarCard(task)));
            allDay.addEventListener("click", () => { if (!clickAfterDrop(allDay)) openCalendarNew(date, "", allDay); });
            enableDrop(allDay, date, "");
            column.append(heading, allDay);
            for (let hour = 8; hour <= 20; hour += 1) {
                const time = `${String(hour).padStart(2, "0")}:00`;
                const slot = document.createElement("div");
                slot.className = "calendar-time-slot";
                slot.tabIndex = 0;
                slot.setAttribute("role", "button");
                slot.setAttribute("aria-label", `Создать задачу ${formatDate(date)} в ${time}`);
                const label = document.createElement("span"); label.textContent = time; slot.append(label);
                state.calendarRows.filter((task) => task.calendar_date === date && task.due_time && Number(task.due_time.slice(0, 2)) === hour).forEach((task) => slot.append(calendarCard(task)));
                slot.addEventListener("click", () => { if (!clickAfterDrop(slot)) openCalendarNew(date, time, slot); });
                slot.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openCalendarNew(date, time, slot); } });
                enableDrop(slot, date, time);
                column.append(slot);
            }
            calendarGrid.append(column);
        }
    }

    function renderCalendar() {
        const range = state.calendarRange || calendarRange();
        calendarGrid.replaceChildren();
        const cursor = dateFromIso(state.calendarDate || localDate());
        q("#calendarTitle").textContent = state.calendarKind === "month"
            ? new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" }).format(cursor)
            : `${formatDate(range.start)} — ${formatDate(range.end)}`;
        if (state.calendarKind === "month") renderMonth(range); else renderWeek(range);
        renderUndated();
        calendarStatus.textContent = `${state.calendarRows.length} задач в периоде`;
    }

    async function loadCalendar() {
        const token = ++state.requestToken;
        const range = calendarRange();
        state.calendarRange = range;
        calendarStatus.textContent = "Загружаем календарь…";
        calendarGrid.classList.add("loading");
        calendarGrid.setAttribute("aria-busy", "true");
        const params = new URLSearchParams({ start: range.start, end: range.end, scope: state.scope });
        if (search.value.trim()) params.set("q", search.value.trim());
        filterDefs.forEach(({ node, param }) => { if (node.value) params.set(param, node.value); });
        if (mine.checked) params.set("only_mine", "1");
        if (state.includeCompleted) params.set("include_completed", "1");
        try {
            const payload = await api(`/api/v1/tasks/calendar?${params}`);
            if (token !== state.requestToken) return;
            state.calendarRows = payload.data.rows;
            state.undatedRows = payload.data.undated;
            state.undatedTotal = payload.data.undated_total;
            renderCalendar();
        } catch (error) {
            if (token !== state.requestToken) return;
            calendarGrid.replaceChildren();
            calendarStatus.textContent = error.message;
            calendarStatus.classList.add("error");
        } finally {
            if (token === state.requestToken) {
                calendarGrid.classList.remove("loading");
                calendarGrid.setAttribute("aria-busy", "false");
                updateFilters();
                loadCounts();
            }
        }
    }

    async function load() {
        updateMode();
        if (state.mode === "calendar") return loadCalendar();
        const token = ++state.requestToken;
        status.textContent = "Загружаем задачи…";
        list.classList.add("loading");
        list.setAttribute("aria-busy", "true");
        const params = new URLSearchParams({ view: state.view, page: state.page, per_page: "50" });
        params.set("scope", state.scope);
        if (search.value.trim()) params.set("q", search.value.trim());
        filterDefs.forEach(({ node, param }) => { if (node.value) params.set(param, node.value); });
        if (mine.checked) params.set("only_mine", "1");
        try {
            const payload = await api(`/api/v1/tasks?${params}`);
            if (token !== state.requestToken) return;
            state.rows = payload.data.rows;
            state.page = payload.data.page;
            state.pages = payload.data.pages;
            state.total = payload.data.total;
            render();
            status.textContent = `${labels[state.view]} · ${state.total}`;
        } catch (error) {
            if (token !== state.requestToken) return;
            list.replaceChildren();
            notify(error.message, true);
        } finally {
            if (token === state.requestToken) {
                list.classList.remove("loading");
                list.setAttribute("aria-busy", "false");
                updateFilters();
                loadCounts();
            }
        }
    }

    async function loadCounts() {
        const token = ++state.countsToken;
        const params = new URLSearchParams({ scope: state.scope, view: state.view });
        if (search.value.trim()) params.set("q", search.value.trim());
        filterDefs.forEach(({ node, param }) => { if (node.value) params.set(param, node.value); });
        if (mine.checked) params.set("only_mine", "1");
        try {
            const payload = await api(`/api/v1/tasks/counts?${params}`);
            if (token !== state.countsToken) return;
            const statistics = payload.data.statistics;
            Object.entries(payload.data).forEach(([key, value]) => {
                const node = q(`[data-view-count="${key}"]`);
                if (!node || key === "logbook" || typeof value !== "number") return;
                node.textContent = value > 999 ? "999+" : value ? String(value) : "";
                node.hidden = !value;
                if (value) node.setAttribute("aria-label", `${value} незавершённые задачи`);
                else node.removeAttribute("aria-label");
            });
            sectionStats.hidden = state.view === "logbook" || !statistics;
            sectionStats.textContent = sectionStats.hidden ? "" : `${statistics.remaining} осталось · ${statistics.completed} выполнено из ${statistics.total}`;
        } catch (_) { /* Counts are supplementary. */ }
    }

    async function quickComplete(task, checkbox, article) {
        if (state.pendingCompletions.has(task.id)) return;
        state.pendingCompletions.add(task.id);
        checkbox.disabled = true;
        article.classList.add("task-saving");
        try {
            const target = !task.completed;
            await api(`/api/v1/tasks/${task.id}/${target ? "complete" : "reopen"}`, { method: "POST", body: "{}" });
            await load();
            notify(target ? "Задача выполнена." : "Задача восстановлена.", false, async () => {
                await api(`/api/v1/tasks/${task.id}/${target ? "reopen" : "complete"}`, { method: "POST", body: "{}" });
                await load();
            });
        } catch (error) {
            checkbox.checked = task.completed;
            article.classList.remove("task-saving");
            article.classList.add("task-error");
            notify(error.message, true);
            window.setTimeout(() => article.classList.remove("task-error"), 2200);
        } finally {
            state.pendingCompletions.delete(task.id);
            checkbox.disabled = false;
        }
    }

    function renderLinks() {
        const wrap = q("#selectedEntities");
        wrap.replaceChildren();
        state.links.forEach((link, index) => {
            const item = document.createElement("span");
            item.className = "selected-entity";
            item.append(entityChip(link));
            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "×";
            remove.setAttribute("aria-label", `Убрать связь ${link.entity_label}`);
            remove.addEventListener("click", () => { state.links.splice(index, 1); renderLinks(); });
            item.append(remove);
            wrap.append(item);
        });
    }

    function renderHistory(events) {
        const section = q("#taskHistory"), ordered = section.querySelector("ol");
        ordered.replaceChildren();
        section.hidden = !events.length;
        events.forEach((event) => {
            const item = document.createElement("li"), details = event.details || {};
            item.textContent = `${new Date(event.created_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} · ${event.actor_name || "Сотрудник"} · ${event.event_type}${details.field ? ` · ${details.field}` : ""}`;
            ordered.append(item);
        });
    }

    function updateActionVisibility() {
        const value = form.elements.status.value;
        q("#waitingFields").hidden = value !== "waiting";
        q('[data-action="start"]').hidden = value !== "new";
        q('[data-action="waiting"]').hidden = ["completed", "cancelled"].includes(value);
        q('[data-action="complete"]').hidden = ["completed", "cancelled"].includes(value);
        q('[data-action="cancel"]').hidden = ["completed", "cancelled"].includes(value);
        q('[data-action="restore"]').hidden = !["completed", "cancelled"].includes(value);
    }

    function fill(task) {
        form.reset();
        state.task = task;
        state.links = task ? JSON.parse(JSON.stringify(task.links || [])) : [];
        const fields = ["title", "description", "section", "status", "priority", "due_date", "due_time", "reminder_at", "assignee_id", "source_comment", "contact_name", "contact_phone", "contact_email", "contact_channel", "waiting_for", "check_date", "waiting_comment", "repeat_type", "repeat_interval", "completion_result"];
        fields.forEach((name) => { if (task && form.elements[name]) form.elements[name].value = task[name] || ""; });
        form.elements.id.value = task ? task.id : "";
        if (!task) {
            form.elements.assignee_id.value = boot.currentUserId;
            form.elements.status.value = "new";
            form.elements.repeat_interval.value = "1";
        }
        q("#taskDrawerTitle").textContent = task ? task.title : "Новая задача";
        q("#taskDrawerKicker").textContent = task ? `Задача №${task.id} · автор: ${task.author_name}` : "Карточка задачи";
        renderLinks();
        renderHistory(task ? task.history || [] : []);
        updateActionVisibility();
        errorBox.hidden = true;
    }

    function showDrawer(trigger) {
        drawer.hidden = false;
        backdrop.hidden = false;
        document.body.classList.add("task-drawer-open");
        state.returnFocus = trigger;
        window.setTimeout(() => form.elements.title.focus(), 0);
    }

    async function openTask(id, trigger = document.activeElement, sync = true) {
        try {
            const payload = await api(`/api/v1/tasks/${id}`);
            fill(payload.data);
            showDrawer(trigger);
            qa(".task-row").forEach((row) => row.classList.toggle("task-opened", Number(row.dataset.taskId) === Number(id)));
            if (sync) { state.drawerHistoryPushed = true; syncUrl("push", id); }
        } catch (error) { notify(error.message, true); }
    }

    function openNew(trigger = document.activeElement, sync = true) {
        fill(null);
        if (boot.prefillTitle) form.elements.title.value = boot.prefillTitle;
        if (boot.prefillContext) form.elements.source_comment.value = boot.prefillContext;
        showDrawer(trigger);
        if (sync) { state.drawerHistoryPushed = true; syncUrl("push", null, true); }
    }

    function hideDrawer(restoreFocus = true) {
        drawer.hidden = true;
        backdrop.hidden = true;
        document.body.classList.remove("task-drawer-open");
        state.task = null;
        qa(".task-row.task-opened").forEach((row) => row.classList.remove("task-opened"));
        if (restoreFocus && state.returnFocus && document.contains(state.returnFocus)) state.returnFocus.focus();
    }

    function closeDrawer() {
        const restoreTaskFocusId = state.drawerHistoryPushed && state.task ? state.task.id : null;
        hideDrawer();
        if (restoreTaskFocusId) state.restoreTaskFocusId = restoreTaskFocusId;
        if (state.drawerHistoryPushed) {
            state.drawerHistoryPushed = false;
            history.back();
        } else syncUrl("replace", null, false);
    }

    function payload() {
        const data = Object.fromEntries(new FormData(form));
        delete data.id;
        if (state.task) data.version = state.task.version;
        data.links = state.links.map((link) => ({ entity_type: link.entity_type, entity_id: String(link.entity_id) }));
        return data;
    }

    async function submit(event) {
        event.preventDefault();
        if (!form.reportValidity() || save.disabled) return;
        if (form.elements.status.value === "waiting" && (!form.elements.waiting_for.value.trim() || !form.elements.check_date.value)) {
            errorBox.textContent = "Для ожидания укажите, кого ожидаем, и дату проверки.";
            errorBox.hidden = false;
            return;
        }
        const id = form.elements.id.value, key = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
        save.disabled = true;
        save.textContent = "Сохраняем…";
        try {
            await api(id ? `/api/v1/tasks/${id}` : "/api/v1/tasks", { method: id ? "PATCH" : "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ ...payload(), idempotency_key: key }) });
            hideDrawer();
            state.drawerHistoryPushed = false;
            syncUrl("replace", null, false);
            notify(id ? "Задача обновлена." : "Задача создана.");
            state.page = 1;
            await load();
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
        } finally {
            save.disabled = false;
            save.textContent = "Сохранить";
        }
    }

    async function action(name) {
        if (!state.task) return;
        const mapping = { start: "in_progress", waiting: "waiting", complete: "completed", cancel: "cancelled", restore: "new" };
        if (name === "waiting" && (!form.elements.waiting_for.value.trim() || !form.elements.check_date.value)) {
            form.elements.status.value = "waiting";
            updateActionVisibility();
            form.elements.waiting_for.focus();
            return;
        }
        const button = q(`[data-action="${name}"]`);
        button.disabled = true;
        try {
            if (name === "waiting") await api(`/api/v1/tasks/${state.task.id}`, { method: "PATCH", body: JSON.stringify(payload()) });
            await api(`/api/v1/tasks/${state.task.id}/status`, { method: "POST", body: JSON.stringify({ status: mapping[name], result: form.elements.completion_result.value }) });
            hideDrawer();
            state.drawerHistoryPushed = false;
            syncUrl("replace", null, false);
            notify(name === "complete" ? "Задача выполнена." : name === "cancel" ? "Задача отменена." : name === "restore" ? "Задача восстановлена." : "Статус обновлён.");
            await load();
        } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
        finally { button.disabled = false; }
    }

    async function quickDate(kind) {
        const offsets = { today: 0, tomorrow: 1, week: 7 };
        const value = kind === "clear" ? "" : localDate(offsets[kind]);
        form.elements.due_date.value = value;
        if (state.task) {
            try {
                await api(`/api/v1/tasks/${state.task.id}/reschedule`, { method: "POST", body: JSON.stringify({ due_date: value }) });
                hideDrawer();
                state.drawerHistoryPushed = false;
                syncUrl("replace", null, false);
                notify("Срок перенесён.");
                await load();
            } catch (error) { notify(error.message, true); }
        }
    }

    function updateViewButtons() {
        qa("[data-view]").forEach((button) => {
            const active = button.dataset.view === state.view;
            button.classList.toggle("active", active);
            if (active) button.setAttribute("aria-current", "page");
            else button.removeAttribute("aria-current");
        });
    }

    function changeView(view, mode = "push") {
        state.view = view;
        state.mode = "list";
        state.page = 1;
        updateViewButtons();
        updateMode();
        syncUrl(mode, null);
        load();
    }

    function resetAll() {
        search.value = "";
        filterDefs.forEach(({ node }) => { node.value = ""; });
        mine.checked = false;
        state.page = 1;
        updateFilters();
        syncUrl("push", null);
        load();
    }

    qa("[data-view]").forEach((button) => button.addEventListener("click", () => changeView(button.dataset.view)));
    qa("[data-mode]").forEach((button) => button.addEventListener("click", () => {
        if (state.mode === button.dataset.mode) return;
        state.mode = button.dataset.mode;
        state.page = 1;
        updateMode();
        syncUrl("push", null);
        load();
    }));
    qa("[data-calendar-kind]").forEach((button) => button.addEventListener("click", () => {
        state.calendarKind = button.dataset.calendarKind;
        updateMode();
        syncUrl("push", null);
        loadCalendar();
    }));
    q("#calendarToday").addEventListener("click", () => { state.calendarDate = localDate(); syncUrl("push", null); loadCalendar(); });
    q("#calendarPrev").addEventListener("click", () => {
        const cursor = dateFromIso(state.calendarDate || localDate());
        if (state.calendarKind === "month") { cursor.setUTCDate(1); cursor.setUTCMonth(cursor.getUTCMonth() - 1); }
        else cursor.setUTCDate(cursor.getUTCDate() - 7);
        state.calendarDate = isoDate(cursor); syncUrl("push", null); loadCalendar();
    });
    q("#calendarNext").addEventListener("click", () => {
        const cursor = dateFromIso(state.calendarDate || localDate());
        if (state.calendarKind === "month") { cursor.setUTCDate(1); cursor.setUTCMonth(cursor.getUTCMonth() + 1); }
        else cursor.setUTCDate(cursor.getUTCDate() + 7);
        state.calendarDate = isoDate(cursor); syncUrl("push", null); loadCalendar();
    });
    calendarCompleted.addEventListener("change", () => {
        state.includeCompleted = calendarCompleted.checked; syncUrl("push", null); loadCalendar();
    });
    qa("[data-scope]").forEach((button) => button.addEventListener("click", () => {
        state.scope = button.dataset.scope; state.page = 1; updateScopeButtons(); syncUrl("push", null); load();
    }));
    filterDefs.forEach(({ node }) => node.addEventListener("change", () => { state.page = 1; updateFilters(); syncUrl("push", null); load(); }));
    mine.addEventListener("change", () => { state.page = 1; updateFilters(); syncUrl("push", null); load(); });
    search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        updateFilters();
        searchTimer = window.setTimeout(() => { state.page = 1; syncUrl("replace", null); load(); }, 250);
    });
    clearSearch.addEventListener("click", () => { search.value = ""; state.page = 1; updateFilters(); syncUrl("push", null); load(); search.focus(); });
    filterToggle.addEventListener("click", () => setFiltersOpen(!state.filtersOpen));
    reset.addEventListener("click", resetAll);
    q("#prevPage").addEventListener("click", () => { state.page -= 1; syncUrl("push", null); load(); });
    q("#nextPage").addEventListener("click", () => { state.page += 1; syncUrl("push", null); load(); });
    q("#newTask").addEventListener("click", (event) => openNew(event.currentTarget));
    [q("#closeTask"), q("#cancelTask"), backdrop].forEach((node) => node.addEventListener("click", closeDrawer));
    form.addEventListener("submit", submit);
    form.elements.status.addEventListener("change", updateActionVisibility);
    qa("[data-action]").forEach((button) => button.addEventListener("click", () => action(button.dataset.action)));
    qa("[data-quick-date]").forEach((button) => button.addEventListener("click", () => quickDate(button.dataset.quickDate)));
    qa("[data-move]").forEach((button) => button.addEventListener("click", async () => {
        if (state.task) {
            await api(`/api/v1/tasks/${state.task.id}/move`, { method: "POST", body: JSON.stringify({ section: button.dataset.move }) });
            hideDrawer(); state.drawerHistoryPushed = false; syncUrl("replace", null, false); await load();
        } else { form.elements.section.value = button.dataset.move; form.elements.due_date.value = ""; }
    }));
    q("#entitySearch").addEventListener("input", (event) => {
        clearTimeout(entityTimer);
        const value = event.target.value.trim(), results = q("#entityResults");
        if (!value) { results.hidden = true; return; }
        entityTimer = window.setTimeout(async () => {
            try {
                const payload = await api(`/api/v1/tasks/entities?type=${encodeURIComponent(q("#entityType").value)}&q=${encodeURIComponent(value)}`);
                results.replaceChildren();
                payload.data.forEach((entity) => {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.role = "option";
                    button.textContent = entity.label;
                    button.addEventListener("click", () => {
                        const type = q("#entityType").value;
                        if (!state.links.some((link) => link.entity_type === type && String(link.entity_id) === String(entity.id))) state.links.push({ ...entity, entity_type: type, entity_id: String(entity.id) });
                        renderLinks(); results.hidden = true; event.target.value = "";
                    });
                    results.append(button);
                });
                results.hidden = !payload.data.length;
            } catch (error) { notify(error.message, true); }
        }, 250);
    });
    drawer.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.preventDefault(); closeDrawer(); return; }
        if (event.key !== "Tab") return;
        const focusable = qa('#taskModal button:not([disabled]):not([hidden]),#taskModal input:not([disabled]),#taskModal select:not([disabled]),#taskModal textarea:not([disabled])').filter((item) => !item.closest("[hidden]"));
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    window.addEventListener("popstate", () => {
        const params = new URLSearchParams(location.search);
        applyFiltersFromUrl(params);
        const taskId = params.get("task"), newTask = params.get("new") === "1";
        state.drawerHistoryPushed = false;
        if (taskId) openTask(taskId, q("#newTask"), false);
        else if (newTask) openNew(q("#newTask"), false);
        else hideDrawer(false);
        load();
    });

    applyFiltersFromUrl(initial);
    load();
    const requested = initial.get("task");
    if (requested) openTask(requested, q("#newTask"), false);
    else if (initial.get("new") === "1" || (boot.prefillType && boot.prefillId)) {
        openNew(q("#newTask"), false);
        q("#entityType").value = boot.prefillType || "customer";
        if (boot.prefillType && boot.prefillId) {
            api(`/api/v1/tasks/entities?type=${encodeURIComponent(boot.prefillType)}&q=${encodeURIComponent(boot.prefillId)}`).then((payload) => {
                const entity = payload.data.find((item) => String(item.id) === String(boot.prefillId));
                if (entity) { state.links = [{ ...entity, entity_type: boot.prefillType, entity_id: String(entity.id) }]; renderLinks(); }
            }).catch(() => {});
        }
    }
})();
