(() => {
    if (window.__vechasuEntityTasksInitialized) return;
    window.__vechasuEntityTasksInitialized = true;

    const statusLabels = {
        new: "Новая", in_progress: "В работе", waiting: "Ожидаю",
        completed: "Выполнена", cancelled: "Отменена",
    };
    let openTaskMenu = null;
    let deleteDialog = null;

    function closeTaskMenu(restoreFocus = false) {
        if (!openTaskMenu) return;
        const { button, menu } = openTaskMenu;
        menu.remove();
        button.setAttribute("aria-expanded", "false");
        openTaskMenu = null;
        if (restoreFocus && document.contains(button)) button.focus();
    }

    function positionTaskMenu(button, menu) {
        const rect = button.getBoundingClientRect();
        const width = Math.min(190, window.innerWidth - 16);
        menu.style.width = `${width}px`;
        menu.style.left = `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(rect.bottom + 5, window.innerHeight - menu.offsetHeight - 8))}px`;
    }

    function ensureDeleteDialog() {
        if (deleteDialog) return deleteDialog;
        const backdrop = document.createElement("div");
        backdrop.className = "task-delete-backdrop";
        backdrop.hidden = true;
        backdrop.innerHTML = `<section class="task-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="taskDeleteTitle" aria-describedby="taskDeleteCopy"><h2 id="taskDeleteTitle">Удалить задачу?</h2><p id="taskDeleteCopy"></p><div class="task-delete-actions"><button class="button button-secondary" type="button" data-cancel-task-delete>Отмена</button><button class="button button-danger" type="button" data-confirm-task-delete>Удалить</button></div><p class="task-delete-error" role="alert" hidden></p></section>`;
        document.body.append(backdrop);
        const cancel = backdrop.querySelector("[data-cancel-task-delete]");
        const confirm = backdrop.querySelector("[data-confirm-task-delete]");
        const error = backdrop.querySelector(".task-delete-error");
        let pending = null;
        let returnFocus = null;
        const close = () => {
            if (confirm.disabled) return;
            backdrop.hidden = true;
            document.body.classList.remove("has-task-delete-dialog");
            const target = returnFocus;
            pending = null;
            returnFocus = null;
            error.hidden = true;
            if (target && document.contains(target)) target.focus();
        };
        cancel.addEventListener("click", close);
        backdrop.addEventListener("click", event => { if (event.target === backdrop) close(); });
        backdrop.addEventListener("keydown", event => {
            if (event.key === "Escape") { event.preventDefault(); close(); return; }
            if (event.key !== "Tab") return;
            const buttons = [cancel, confirm].filter(button => !button.disabled);
            const first = buttons[0], last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        confirm.addEventListener("click", async () => {
            if (!pending || confirm.disabled) return;
            confirm.disabled = true;
            cancel.disabled = true;
            confirm.textContent = "Удаляем…";
            error.hidden = true;
            try {
                const response = await fetch(`/api/v1/tasks/${pending.task.id}`, {
                    method: "DELETE",
                    credentials: "same-origin",
                    headers: { Accept: "application/json", "X-CSRF-Token": pending.csrf },
                });
                const payload = await response.json().catch(() => null);
                if (!response.ok || !payload?.data) {
                    throw new Error(payload?.message || payload?.error?.message || "Не удалось удалить задачу.");
                }
                const root = pending.root;
                const taskId = pending.task.id;
                backdrop.hidden = true;
                document.body.classList.remove("has-task-delete-dialog");
                pending = null;
                returnFocus = null;
                root._entityTaskRows = (root._entityTaskRows || []).filter(item => Number(item.id) !== Number(taskId));
                render(root, root._entityTaskRows);
                refreshSidebarCount();
                window.VechasuNotify?.success("Задача удалена");
            } catch (failure) {
                error.textContent = failure.message || "Не удалось удалить задачу.";
                error.hidden = false;
            } finally {
                confirm.disabled = false;
                cancel.disabled = false;
                confirm.textContent = "Удалить";
            }
        });
        deleteDialog = {
            open(root, task, trigger) {
                pending = { root, task, csrf: root.dataset.csrf || "" };
                returnFocus = trigger;
                error.hidden = true;
                backdrop.querySelector("#taskDeleteCopy").textContent = `Задача “${task.title}” исчезнет из карточки заказа`;
                backdrop.hidden = false;
                document.body.classList.add("has-task-delete-dialog");
                cancel.focus();
            },
        };
        return deleteDialog;
    }

    function showTaskMenu(root, task, button) {
        closeTaskMenu();
        const menu = document.createElement("div");
        menu.className = "entity-task-menu";
        menu.setAttribute("role", "menu");
        const action = document.createElement("button");
        action.type = "button";
        action.setAttribute("role", "menuitem");
        action.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/></svg><span>Удалить задачу</span>`;
        action.addEventListener("click", () => {
            closeTaskMenu();
            ensureDeleteDialog().open(root, task, button);
        });
        menu.append(action);
        document.body.append(menu);
        button.setAttribute("aria-expanded", "true");
        openTaskMenu = { button, menu };
        positionTaskMenu(button, menu);
        action.focus();
        menu.addEventListener("keydown", event => {
            if (event.key === "Escape") { event.preventDefault(); closeTaskMenu(true); }
            if (event.key === "Tab") closeTaskMenu();
        });
    }

    function taskRow(root, task) {
        const li = document.createElement("li");
        li.dataset.taskId = task.id;
        const link = document.createElement("a");
        link.href = `/app/tasks?view=${["completed", "cancelled"].includes(task.status) ? "logbook" : "today"}&task=${task.id}`;
        link.textContent = task.title;
        const state = document.createElement("span");
        state.textContent = statusLabels[task.status] || task.status;
        li.append(link, state);
        if (task.can_delete) {
            const menuButton = document.createElement("button");
            menuButton.className = "entity-task-menu-trigger";
            menuButton.type = "button";
            menuButton.textContent = "⋯";
            menuButton.setAttribute("aria-label", `Действия с задачей «${task.title}»`);
            menuButton.setAttribute("aria-haspopup", "menu");
            menuButton.setAttribute("aria-expanded", "false");
            menuButton.addEventListener("click", event => {
                event.stopPropagation();
                if (openTaskMenu?.button === menuButton) closeTaskMenu(true);
                else showTaskMenu(root, task, menuButton);
            });
            menuButton.addEventListener("keydown", event => {
                if (event.key === "ArrowDown") { event.preventDefault(); showTaskMenu(root, task, menuButton); }
            });
            li.append(menuButton);
        }
        if (task.completion_result) {
            const result = document.createElement("small");
            result.textContent = task.completion_result;
            li.append(result);
        }
        return li;
    }

    function render(root, rows) {
        const active = rows.filter(item => !["completed", "cancelled"].includes(item.status));
        const recent = rows.filter(item => ["completed", "cancelled"].includes(item.status)).slice(0, 3);
        const head = document.createElement("div");
        head.className = "entity-tasks-head";
        const heading = document.createElement("h3");
        heading.textContent = "Задачи";
        const count = document.createElement("span");
        count.className = "entity-tasks-count";
        count.textContent = String(active.length);
        heading.append(count);
        const create = document.createElement("a");
        create.className = "entity-tasks-create";
        const label = root.dataset.entityLabel || "";
        create.href = `/app/tasks?entity_type=${encodeURIComponent(root.dataset.entityType)}&entity_id=${encodeURIComponent(root.dataset.entityId)}${label ? `&title=${encodeURIComponent(label)}` : ""}`;
        create.textContent = "Создать задачу";
        head.append(heading, create);
        const list = document.createElement("ul");
        [...active, ...recent].forEach(task => list.append(taskRow(root, task)));
        root.replaceChildren(head);
        if (!rows.length) {
            const empty = document.createElement("p");
            empty.className = "entity-tasks-empty";
            empty.textContent = root.dataset.entityType === "order" ? "В этом заказе пока нет задач" : "Связанных задач пока нет.";
            root.append(empty);
        } else root.append(list);
    }

    async function refreshSidebarCount() {
        try {
            const response = await fetch("/api/v1/tasks/counts", { headers: { Accept: "application/json" } });
            const payload = await response.json();
            if (!response.ok || !payload?.data) return;
            const link = document.querySelector('[data-navigation-key="tasks"]');
            if (!link) return;
            let badge = link.querySelector(".sidebar-count");
            const value = Number(payload.data?.active || 0);
            if (!value) { badge?.remove(); return; }
            if (!badge) { badge = document.createElement("span"); badge.className = "sidebar-count"; link.append(badge); }
            badge.textContent = String(value);
            badge.setAttribute("aria-label", `Активных задач на сегодня и просроченных: ${value}`);
        } catch (_) {}
    }

    async function load(root) {
        const type = root.dataset.entityType, id = root.dataset.entityId;
        if (!type || !id) return;
        root.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(`/api/v1/tasks/by-entity/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } });
            const payload = await response.json();
            if (!response.ok || !Array.isArray(payload?.data)) throw new Error(payload?.message || "Ошибка загрузки");
            root._entityTaskRows = payload.data || [];
            render(root, root._entityTaskRows);
        } catch (_) {
            root.textContent = "Задачи временно недоступны.";
        } finally {
            root.removeAttribute("aria-busy");
        }
    }

    document.addEventListener("pointerdown", event => {
        if (openTaskMenu && !openTaskMenu.menu.contains(event.target) && event.target !== openTaskMenu.button) closeTaskMenu();
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && openTaskMenu) { event.preventDefault(); closeTaskMenu(true); }
    });
    window.addEventListener("resize", closeTaskMenu);
    document.addEventListener("scroll", closeTaskMenu, true);

    function initialize(scope = document) {
        scope.querySelectorAll("[data-entity-tasks]:not([data-entity-tasks-ready])").forEach(root => {
            root.dataset.entityTasksReady = "1";
            load(root);
        });
    }
    window.VechasuEntityTasksInit = initialize;
    initialize();
})();
