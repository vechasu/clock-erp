(() => {
    "use strict";
    if (window.__vechasuEventNotificationsInitialized) return;
    window.__vechasuEventNotificationsInitialized = true;

    const boot = window.ERP_EVENT_NOTIFICATIONS || {};
    const bell = document.querySelector("[data-notification-bell]");
    const center = document.querySelector("#notificationCenter");
    if (!bell || !center) return;
    const backdrop = document.querySelector("[data-notification-backdrop]");
    const count = document.querySelector("[data-notification-count]");
    const feed = center.querySelector("[data-notification-feed]");
    const settings = center.querySelector("[data-notification-settings]");
    const permissionNote = center.querySelector("[data-notification-permission-note]");
    let items = [];
    let filter = "all";
    let preferences = {
        order_sound: true, task_sound: true, browser_notifications: false,
        system_errors: true, operation_completions: true,
    };
    let audioContext = null;
    const announced = new Set();

    const headers = () => ({
        "Content-Type": "application/json",
        "X-CSRF-Token": boot.csrf || "",
        "X-Vechasu-Notify": "off",
    });

    function unlockAudio() {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        if (!audioContext) audioContext = new Context();
        if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
    }
    document.addEventListener("pointerdown", unlockAudio, {once: true, passive: true});
    document.addEventListener("keydown", unlockAudio, {once: true});

    function playSound(kind) {
        if (kind === "system") return;
        if (!preferences[kind === "order" ? "order_sound" : "task_sound"]) return;
        if (!audioContext || audioContext.state !== "running") return;
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        const now = audioContext.currentTime;
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(kind === "order" ? 660 : 520, now);
        oscillator.frequency.exponentialRampToValueAtTime(kind === "order" ? 880 : 700, now + .12);
        gain.gain.setValueAtTime(.0001, now);
        gain.gain.exponentialRampToValueAtTime(.07, now + .02);
        gain.gain.exponentialRampToValueAtTime(.0001, now + .2);
        oscillator.connect(gain).connect(audioContext.destination);
        oscillator.start(now);
        oscillator.stop(now + .21);
    }

    function localTime(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
    }

    function dueText(value) {
        if (!value) return "";
        const match = /^(\d{4})-(\d{2})-(\d{2})(?: (\d{2}:\d{2}))?$/.exec(value);
        if (!match) return value;
        const date = new Date(`${match[1]}-${match[2]}-${match[3]}T12:00:00`);
        const today = new Date();
        const tomorrow = new Date(); tomorrow.setDate(today.getDate() + 1);
        const key = (candidate) => `${candidate.getFullYear()}-${String(candidate.getMonth() + 1).padStart(2, "0")}-${String(candidate.getDate()).padStart(2, "0")}`;
        const dateKey = `${match[1]}-${match[2]}-${match[3]}`;
        const label = dateKey === key(today) ? "Сегодня" : dateKey === key(tomorrow) ? "Завтра" : date.toLocaleDateString("ru-RU", {day: "numeric", month: "short"});
        return match[4] ? `${label}, ${match[4]}` : label;
    }

    function metaText(item) {
        if (item.type === "task") {
            return [item.metadata.author, dueText(item.metadata.due)].filter(Boolean).join(" • ");
        }
        return localTime(item.created_at);
    }

    function systemAlertEnabled(item) {
        if (item.type !== "system") return true;
        return item.severity === "error" || item.severity === "warning"
            ? preferences.system_errors : preferences.operation_completions;
    }

    function updateCount(unread) {
        count.textContent = unread > 99 ? "99+" : String(unread);
        count.hidden = unread < 1;
        bell.setAttribute("aria-label", unread ? `Уведомления: ${unread} непрочитанных` : "Уведомления");
    }

    function render() {
        const visible = items.filter((item) => filter === "all" || item.type === filter);
        feed.replaceChildren();
        if (!visible.length) {
            const empty = document.createElement("p");
            empty.className = "notification-empty";
            empty.textContent = "Уведомлений пока нет";
            feed.append(empty);
            return;
        }
        visible.forEach((item) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `notification-item${item.read_at ? "" : " unread"}`;
            const icon = document.createElement("span");
            icon.className = "notification-item-icon";
            icon.textContent = item.type === "order" ? "🛒" : item.type === "task" ? "☑" : "⚙";
            const copy = document.createElement("span");
            const title = document.createElement("strong"); title.textContent = item.title;
            const message = document.createElement("p"); message.textContent = item.message;
            const meta = document.createElement("small");
            meta.textContent = [metaText(item), item.type === "task" ? localTime(item.created_at) : ""].filter(Boolean).join(" · ");
            copy.append(title, message, meta);
            button.append(icon, copy);
            button.addEventListener("click", async () => {
                await markRead(item.id);
                closeCenter();
                window.location.assign(item.target_url);
            });
            feed.append(button);
        });
    }

    function applyPreferences() {
        center.querySelectorAll("[data-notification-preference]").forEach((input) => {
            input.checked = Boolean(preferences[input.dataset.notificationPreference]);
        });
    }

    async function markRead(id) {
        try {
            await fetch(`/api/v1/notifications/${id}/read`, {method: "POST", headers: headers(), body: "{}", credentials: "same-origin"});
            const item = items.find((candidate) => candidate.id === id);
            if (item && !item.read_at) item.read_at = new Date().toISOString();
            updateCount(items.filter((candidate) => !candidate.read_at).length);
            render();
        } catch (_error) {}
    }

    function showToast(item) {
        if (!systemAlertEnabled(item)) return;
        if (!window.VechasuNotify) {
            window.setTimeout(() => showToast(item), 100);
            return;
        }
        const notify = window.VechasuNotify[item.severity] || window.VechasuNotify.info;
        notify.call(window.VechasuNotify, item.title, {
            detail: item.message,
            duration: 6500,
            operationId: `event-notification-${item.id}`,
            actor: item.type === "task" ? item.metadata.author : "",
            occurredAt: item.created_at,
            action: {label: "Открыть", href: item.target_url, onClick: async (event) => {
                event.preventDefault(); await markRead(item.id); window.location.assign(item.target_url);
            }},
        });
    }

    function showBrowserNotification(item) {
        if (!preferences.browser_notifications || !systemAlertEnabled(item) || document.visibilityState === "visible") return;
        if (!("Notification" in window) || Notification.permission !== "granted") return;
        const notice = new Notification("Vechasu ERP", {body: `${item.title}\n${item.message}`, tag: `vechasu-${item.id}`});
        notice.onclick = async () => { await markRead(item.id); window.focus(); window.location.assign(item.target_url); notice.close(); };
    }

    function announce(item) {
        if (!item || announced.has(item.id)) return;
        announced.add(item.id);
        showToast(item);
        playSound(item.type);
        showBrowserNotification(item);
    }

    async function poll() {
        try {
            const response = await fetch("/api/v1/notifications", {headers: {Accept: "application/json", "X-Vechasu-Notify": "off"}, credentials: "same-origin"});
            if (!response.ok) return;
            const payload = await response.json();
            const data = payload.data || payload;
            items = data.items || [];
            preferences = {...preferences, ...(data.preferences || {})};
            updateCount(Number(data.unread) || 0);
            applyPreferences();
            render();
            items.filter((item) => item.fresh).reverse().forEach(announce);
        } catch (_error) {}
    }

    function openCenter() {
        center.hidden = false; backdrop.hidden = false;
        bell.setAttribute("aria-expanded", "true");
        center.querySelector("[data-notification-close]").focus();
    }
    function closeCenter() {
        center.hidden = true; backdrop.hidden = true;
        bell.setAttribute("aria-expanded", "false");
    }
    bell.addEventListener("click", () => center.hidden ? openCenter() : closeCenter());
    backdrop.addEventListener("click", closeCenter);
    center.querySelector("[data-notification-close]").addEventListener("click", closeCenter);
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !center.hidden) closeCenter(); });

    center.querySelectorAll("[data-notification-filter]").forEach((button) => button.addEventListener("click", () => {
        filter = button.dataset.notificationFilter;
        center.querySelectorAll("[data-notification-filter]").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
        render();
    }));
    center.querySelector("[data-notification-read-all]").addEventListener("click", async () => {
        await fetch("/api/v1/notifications/read-all", {method: "POST", headers: headers(), body: "{}", credentials: "same-origin"});
        items.forEach((item) => { item.read_at = item.read_at || new Date().toISOString(); });
        updateCount(0); render();
    });
    center.querySelector("[data-notification-settings-toggle]").addEventListener("click", (event) => {
        settings.hidden = !settings.hidden;
        event.currentTarget.setAttribute("aria-expanded", String(!settings.hidden));
    });
    center.querySelectorAll("[data-notification-preference]").forEach((input) => input.addEventListener("change", async () => {
        const key = input.dataset.notificationPreference;
        let value = input.checked;
        permissionNote.hidden = true;
        if (key === "browser_notifications" && value) {
            if (!("Notification" in window)) {
                value = false; permissionNote.textContent = "Браузер не поддерживает системные уведомления."; permissionNote.hidden = false;
            } else {
                const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
                if (permission !== "granted") {
                    value = false; permissionNote.textContent = "Разрешение браузера не выдано. Системные уведомления выключены."; permissionNote.hidden = false;
                }
            }
        }
        input.checked = value;
        const response = await fetch("/api/v1/notification-preferences", {
            method: "PUT", headers: headers(), credentials: "same-origin", body: JSON.stringify({[key]: value}),
        });
        if (response.ok) {
            const payload = await response.json(); preferences = {...preferences, ...(payload.data || {})}; applyPreferences();
        }
    }));

    poll();
    window.setInterval(poll, 15000);
})();
