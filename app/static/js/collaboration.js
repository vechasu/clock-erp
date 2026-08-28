(() => {
    const boot = window.COLLABORATION_BOOTSTRAP || {};
    const match = (() => {
        let found = location.pathname.match(/^\/order\/(\d+)$/);
        if (found) return ["order", found[1]];
        found = location.pathname.match(/^\/app\/customers\/(\d+)$/);
        if (found) return ["customer", found[1]];
        if (location.pathname === "/app/purchases") {
            const id = new URLSearchParams(location.search).get("request_id");
            if (id) return ["purchase", id];
        }
        if (location.pathname === "/app/repairs") {
            const id = new URLSearchParams(location.search).get("selected_id");
            if (id) return ["repair", id];
        }
        return null;
    })();
    if (!match || !Array.isArray(boot.users)) return;
    const header = document.querySelector(".erp-workspace-header,.page-head,.repair-header");
    if (!header) return;
    const root = document.createElement("section"); root.className = "responsibility-control"; root.setAttribute("aria-label", "Ответственный за объект");
    const label = document.createElement("label"); label.append(document.createTextNode("Ответственный:"));
    const select = document.createElement("select"); select.setAttribute("aria-label", "Ответственный");
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "Не назначен"; select.append(empty);
    boot.users.forEach((user) => { const option = document.createElement("option"); option.value = user.id; option.textContent = `${user.first_name || ""} ${user.last_name || ""}`.trim() || user.email; select.append(option); });
    const transfer = document.createElement("button"); transfer.type = "button"; transfer.textContent = "Передать";
    const status = document.createElement("span"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    label.append(select); root.append(label, transfer, status); header.append(root);
    const endpoint = `/api/v1/responsibility/${encodeURIComponent(match[0])}/${encodeURIComponent(match[1])}`;
    fetch(endpoint, {headers:{Accept:"application/json"}}).then((response) => response.json()).then((payload) => { const assignment = payload.data && payload.data.assignment; select.value = assignment && assignment.responsible_user_id ? String(assignment.responsible_user_id) : ""; }).catch(() => { status.textContent = "Не удалось загрузить ответственного"; });
    transfer.addEventListener("click", async () => {
        if (!select.value) { status.textContent = "Выберите активного сотрудника"; select.focus(); return; }
        const comment = window.prompt("Комментарий к передаче (необязательно)", "");
        if (comment === null) return;
        transfer.disabled = true; status.textContent = "Сохраняем…";
        try {
            const key = window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
            const response = await fetch(endpoint, {method:"POST",headers:{Accept:"application/json","Content-Type":"application/json","X-CSRF-Token":boot.csrf,"Idempotency-Key":key},body:JSON.stringify({responsible_user_id:Number(select.value),comment})});
            const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.message || "Не удалось передать ответственность");
            status.textContent = "Ответственный обновлён";
        } catch (error) { status.textContent = error.message; } finally { transfer.disabled = false; }
    });
})();
