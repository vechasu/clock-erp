(() => {
    if (window.__vechasuEntityTasksInitialized) return;
    window.__vechasuEntityTasksInitialized = true;
    const statusLabels = { new: "Новая", in_progress: "В работе", waiting: "Ожидаю", completed: "Выполнена", cancelled: "Отменена" };
    async function load(root) {
        const type = root.dataset.entityType, id = root.dataset.entityId;
        if (!type || !id) return;
        root.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(`/api/v1/tasks/by-entity/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.message || "Ошибка загрузки");
            const rows = payload.data || [], active = rows.filter(item => !["completed", "cancelled"].includes(item.status)), recent = rows.filter(item => ["completed", "cancelled"].includes(item.status)).slice(0, 3);
            const heading = document.createElement("h3"); heading.textContent = "Задачи";
            const create = document.createElement("a"); create.className = "entity-tasks-create"; create.href = `/app/tasks?entity_type=${encodeURIComponent(type)}&entity_id=${encodeURIComponent(id)}`; create.textContent = "Создать задачу";
            const list = document.createElement("ul");
            [...active, ...recent].forEach(task => { const li = document.createElement("li"), link = document.createElement("a"), state = document.createElement("span"); link.href = `/app/tasks?view=${["completed", "cancelled"].includes(task.status) ? "logbook" : "today"}&task=${task.id}`; link.textContent = task.title; state.textContent = statusLabels[task.status] || task.status; li.append(link, state); if (task.completion_result) { const result = document.createElement("small"); result.textContent = task.completion_result; li.append(result); } list.append(li); });
            root.replaceChildren(heading, create, list);
            if (!rows.length) { const empty = document.createElement("p"); empty.textContent = "Связанных задач пока нет."; root.insertBefore(empty, list); list.hidden = true; }
        } catch (_) { root.textContent = "Задачи временно недоступны."; }
        finally { root.removeAttribute("aria-busy"); }
    }
    function initialize(scope = document) { scope.querySelectorAll("[data-entity-tasks]:not([data-entity-tasks-ready])").forEach(root => { root.dataset.entityTasksReady = "1"; load(root); }); }
    window.VechasuEntityTasksInit = initialize;
    initialize();
})();
