(function () {
    "use strict";
    var boot = window.SERVICES_BOOTSTRAP || {};
    var state = {services: boot.services || [], filter: "all", query: "", archived: false};
    var grid = document.getElementById("serviceGrid");
    var empty = document.getElementById("serviceEmpty");
    var count = document.getElementById("serviceCount");
    var toast = document.getElementById("servicesToast");
    var passwordTimers = new Map();

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>'"]/g, function (character) {
            return {"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character];
        });
    }
    function notify(message, error) {
        toast.textContent = message;
        toast.className = "services-toast is-visible" + (error ? " is-error" : "");
        clearTimeout(notify.timer);
        notify.timer = setTimeout(function () { toast.className = "services-toast"; }, 2600);
    }
    async function api(url, options) {
        options = options || {};
        options.headers = Object.assign({}, options.headers || {}, options.method && options.method !== "GET" ? {"X-CSRF-Token": boot.csrf} : {});
        var response = await fetch(url, options);
        var payload = await response.json().catch(function () { return {error:"Ошибка сервера"}; });
        if (!response.ok) throw new Error(payload.error || "Ошибка сервера");
        return payload;
    }
    function categoryLabel(value) {
        return {sites:"Сайты",sales:"Продажи",delivery:"Доставка",infrastructure:"Инфраструктура"}[value] || value;
    }
    function iconMarkup(service) {
        if (service.has_custom_icon) return '<img src="/api/services/' + service.id + '/icon" alt="">';
        var icons = {globe:"◎",cart:"₽",truck:"→",server:"▦",cloud:"☁",lock:"◇"};
        return escapeHtml(icons[service.icon] || service.name.slice(0, 1).toUpperCase());
    }
    function accountMarkup(service, account) {
        var login = account.has_login && (service.permissions.can_view_login || service.permissions.can_copy_login) ?
            '<div class="credential-row"><span class="credential-label">Логин</span><span class="credential-value" data-login="' + account.id + '">' + (service.permissions.can_view_login ? 'Загрузка…' : 'Скрыт') + '</span><span class="credential-actions">' +
            (service.permissions.can_copy_login ? '<button type="button" data-copy-login="' + account.id + '" aria-label="Копировать логин">⧉</button>' : '') + '</span></div>' : '';
        var password = account.has_password && (service.permissions.can_view_password || service.permissions.can_copy_password) ?
            '<div class="credential-row"><span class="credential-label">Пароль</span><span class="credential-value" data-password="' + account.id + '">••••••••••••</span><span class="credential-actions">' + (service.permissions.can_view_password ? '<button type="button" data-show-password="' + account.id + '" aria-label="Показать пароль">◉</button>' : '') +
            (service.permissions.can_copy_password ? '<button type="button" data-copy-password="' + account.id + '" aria-label="Копировать пароль">⧉</button>' : '') + '</span></div>' : '';
        return '<div class="service-account"><div class="service-account-title">' + escapeHtml(account.label) + '</div>' + login + password + '</div>';
    }
    function cardMarkup(service, index) {
        var accounts = service.accounts.map(function (account) { return accountMarkup(service, account); }).join("");
        if (!accounts) accounts = '<div class="service-access">Без логина и пароля</div>';
        var controls = service.archived ?
            (service.permissions.can_archive ? '<button class="minor" type="button" data-restore="' + service.id + '">Восстановить</button>' : '') :
            (service.permissions.can_open ? '<button class="service-open" type="button" data-open="' + service.id + '">Открыть</button>' : '') +
            (service.permissions.can_edit ? '<button class="minor" type="button" data-edit="' + service.id + '">Изменить</button>' : '') +
            (service.permissions.can_archive ? '<button class="minor" type="button" data-archive="' + service.id + '">В архив</button>' : '');
        var move = !service.archived && state.filter === "all" && !state.query ? '<div class="move-actions"><button type="button" data-move="up" data-id="' + service.id + '" aria-label="Переместить выше" ' + (index === 0 ? 'disabled' : '') + '>↑</button><button type="button" data-move="down" data-id="' + service.id + '" aria-label="Переместить ниже">↓</button></div>' : '';
        return '<article class="service-card' + (service.archived ? ' is-archived' : '') + '" data-id="' + service.id + '"><div class="service-card-head"><div class="service-icon">' + iconMarkup(service) + '</div><div><h2>' + escapeHtml(service.name) + '</h2><span class="service-domain" title="' + escapeHtml(service.url) + '">' + escapeHtml(service.domain) + '</span></div>' + (!service.archived ? '<button type="button" class="favorite-button' + (service.favorite ? ' is-active' : '') + '" data-favorite="' + service.id + '" aria-label="Избранное">★</button>' : '<span></span>') + '</div><p class="service-description">' + escapeHtml(service.description || "Без описания") + '</p><span class="service-category">' + escapeHtml(categoryLabel(service.category)) + '</span>' + accounts + '<div class="service-access">' + (service.permissions.can_view_password ? 'Доступ к реквизитам разрешён' : 'Доступ ограничен') + '</div><div class="service-card-actions">' + controls + move + '</div></article>';
    }
    function visibleServices() {
        var term = state.query.trim().toLocaleLowerCase("ru");
        return state.services.filter(function (service) {
            var filterMatch = state.filter === "all" || (state.filter === "favorite" ? service.favorite : service.category === state.filter);
            var haystack = [service.name, service.description, service.category, service.url].concat(service.accounts.map(function (item) { return item.label; })).join(" ").toLocaleLowerCase("ru");
            return filterMatch && (!term || haystack.indexOf(term) !== -1);
        });
    }
    function render() {
        hideAllPasswords(false);
        var visible = visibleServices();
        grid.innerHTML = visible.map(cardMarkup).join("");
        count.textContent = visible.length + " " + (visible.length === 1 ? "сервис" : "сервисов");
        empty.hidden = visible.length !== 0;
        if (!visible.length) {
            empty.querySelector("h2").textContent = state.services.length ? "Ничего не найдено" : (state.archived ? "Архив пуст" : "Сервисы пока не добавлены");
            empty.querySelector("p").textContent = state.services.length ? "Измените запрос или фильтр." : "Добавьте первый рабочий сайт или приложение.";
        }
        visible.forEach(function (service) {
            if (service.archived || !service.permissions.can_view_login) return;
            service.accounts.filter(function (account) { return account.has_login; }).forEach(loadLogin);
        });
    }
    async function loadLogin(account) {
        try {
            var payload = await api("/api/service-accounts/" + account.id + "/login");
            var node = document.querySelector('[data-login="' + account.id + '"]');
            if (node) { node.textContent = payload.value || "—"; node.dataset.value = payload.value || ""; }
        } catch (error) {
            var failed = document.querySelector('[data-login="' + account.id + '"]');
            if (failed) failed.textContent = "Недоступен";
        }
    }
    function hidePassword(accountId, announce) {
        var node = document.querySelector('[data-password="' + accountId + '"]');
        if (node) { node.textContent = "••••••••••••"; delete node.dataset.value; }
        clearTimeout(passwordTimers.get(String(accountId)));
        passwordTimers.delete(String(accountId));
        if (announce) notify("Пароль скрыт");
    }
    function hideAllPasswords(announce) {
        Array.from(document.querySelectorAll("[data-password]")).forEach(function (node) { hidePassword(node.dataset.password, announce); });
    }
    async function showPassword(accountId) {
        var node = document.querySelector('[data-password="' + accountId + '"]');
        if (!node) return;
        if (node.dataset.value) { hidePassword(accountId, true); return; }
        try {
            var payload = await api("/api/service-accounts/" + accountId + "/password");
            node.textContent = payload.value || "—";
            node.dataset.value = payload.value || "";
            passwordTimers.set(String(accountId), setTimeout(function () { hidePassword(accountId, true); }, 15000));
        } catch (error) { notify(error.message, true); }
    }
    async function copyCredential(accountId, kind) {
        try {
            var value;
            var node = kind === "login" ? document.querySelector('[data-login="' + accountId + '"]') : null;
            value = node && node.dataset.value;
            if (value == null) value = (await api("/api/service-accounts/" + accountId + "/copied", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:kind})})).value;
            if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Буфер обмена недоступен");
            await navigator.clipboard.writeText(value || "");
            if (node && node.dataset.value != null) await api("/api/service-accounts/" + accountId + "/copied", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:kind})});
            notify(kind === "password" ? "Пароль скопирован" : "Логин скопирован");
            value = "";
        } catch (error) { notify(error.message || "Не удалось скопировать", true); }
    }
    async function refresh() {
        var payload = await api("/api/services?archived=" + (state.archived ? "1" : "0"));
        state.services = payload.services;
        render();
    }
    async function openService(id) {
        var popup = window.open("about:blank", "_blank");
        if (popup) {
            popup.opener = null;
            var referrer = popup.document.createElement("meta");
            referrer.name = "referrer"; referrer.content = "no-referrer";
            popup.document.head.appendChild(referrer);
        }
        try {
            var payload = await api("/api/services/" + id + "/open", {method:"POST"});
            if (!popup) throw new Error("Браузер заблокировал новую вкладку");
            popup.location.replace(payload.url);
        } catch (error) { if (popup) popup.close(); notify(error.message, true); }
    }
    grid.addEventListener("click", async function (event) {
        var button = event.target.closest("button"); if (!button) return;
        var id;
        if (button.dataset.showPassword) return showPassword(button.dataset.showPassword);
        if (button.dataset.copyPassword) return copyCredential(button.dataset.copyPassword, "password");
        if (button.dataset.copyLogin) return copyCredential(button.dataset.copyLogin, "login");
        if (button.dataset.open) return openService(button.dataset.open);
        if (button.dataset.edit) return openForm(Number(button.dataset.edit));
        try {
            if (button.dataset.favorite) {
                id = Number(button.dataset.favorite); var service = state.services.find(function (item) { return item.id === id; });
                await api("/api/services/" + id + "/favorite", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({favorite:!service.favorite})}); await refresh();
            } else if (button.dataset.archive) {
                id = Number(button.dataset.archive); if (!window.confirm("Переместить сервис в архив?")) return;
                await api("/api/services/" + id + "/archive", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({archived:true})}); notify("Сервис перемещён в архив"); await refresh();
            } else if (button.dataset.restore) {
                id = Number(button.dataset.restore); await api("/api/services/" + id + "/archive", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({archived:false})}); notify("Сервис восстановлен"); await refresh();
            } else if (button.dataset.move) {
                id = Number(button.dataset.id); var index = state.services.findIndex(function (item) { return item.id === id; });
                var target = button.dataset.move === "up" ? index - 1 : index + 1; if (target < 0 || target >= state.services.length) return;
                var moved = state.services.splice(index, 1)[0]; state.services.splice(target, 0, moved); render();
                await api("/api/services/reorder", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ordered_ids:state.services.map(function(item){return item.id;})})});
            }
        } catch (error) { notify(error.message, true); }
    });
    document.getElementById("serviceSearch").addEventListener("input", function (event) { state.query = event.target.value; render(); });
    document.getElementById("serviceFilters").addEventListener("click", function (event) { var button=event.target.closest("button"); if(!button)return; state.filter=button.dataset.filter; this.querySelectorAll("button").forEach(function(item){item.classList.toggle("is-active",item===button);}); render(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) hideAllPasswords(false); });
    window.addEventListener("pagehide", function () { hideAllPasswords(false); });

    var dialog = document.getElementById("serviceDialog"), form = document.getElementById("serviceForm"), accountFields = document.getElementById("accountFields"), permissionFields = document.getElementById("permissionFields");
    function addAccountField(account) {
        account = account || {};
        var node = document.createElement("div"); node.className = "account-form"; node.dataset.id = account.id || "";
        node.innerHTML = '<label>Название аккаунта<input data-account="label" value="' + escapeHtml(account.label || "Основной аккаунт") + '" maxlength="120"></label><label>Логин<input data-account="login" autocomplete="off" placeholder="' + (account.id ? "Оставьте пустым без изменений" : "") + '"></label><label>Пароль<input data-account="password" type="password" autocomplete="new-password" placeholder="' + (account.id ? "Оставьте пустым без изменений" : "") + '"></label><button type="button" class="account-remove">Удалить</button>';
        node.querySelector(".account-remove").addEventListener("click", function(){node.remove();}); accountFields.appendChild(node);
    }
    function buildPermissions(service) {
        permissionFields.innerHTML = "";
        var canManage = !service || service.permissions.can_manage_access;
        document.getElementById("permissionsSection").hidden = !canManage;
        if (!canManage) return;
        (boot.users || []).filter(function(user){return user.role !== "admin";}).forEach(function(user){
            var grant = service && (service.grants || []).find(function(item){return item.user_id === user.id;}) || {};
            var row=document.createElement("div"); row.className="permission-user"; row.dataset.userId=user.id;
            row.innerHTML='<strong>'+escapeHtml(user.display_name)+'</strong><div class="permission-options">'+[["can_view","Видеть"],["can_open","Открывать"],["can_view_login","Видеть логин"],["can_copy_login","Копировать логин"],["can_view_password","Видеть пароль"],["can_copy_password","Копировать пароль"],["can_edit","Редактировать"],["can_manage_access","Управлять доступами"],["can_archive","Архивировать"]].map(function(pair){return '<label><input type="checkbox" data-permission="'+pair[0]+'" '+(grant[pair[0]]?'checked':'')+'> '+pair[1]+'</label>';}).join("")+'</div>'; permissionFields.appendChild(row);
        });
        if (!permissionFields.children.length) permissionFields.innerHTML='<span class="field-hint">Других пользователей пока нет.</span>';
    }
    function openForm(id) {
        var service = id ? state.services.find(function(item){return item.id===id;}) : null;
        form.reset(); accountFields.innerHTML=""; document.getElementById("serviceId").value=service?service.id:""; document.getElementById("serviceVersion").value=service?service.version:"";
        document.getElementById("serviceDialogTitle").textContent=service?"Изменить сервис":"Добавить сервис";
        if(service){form.elements.name.value=service.name;form.elements.url.value=service.url;form.elements.description.value=service.description;form.elements.category.value=service.category;form.elements.icon.value=service.icon;form.elements.favorite.checked=service.favorite;service.accounts.forEach(addAccountField);}else addAccountField();
        buildPermissions(service); document.getElementById("serviceFormStatus").textContent=""; dialog.showModal();
    }
    if (dialog) {
        document.getElementById("addService").addEventListener("click",function(){openForm();}); document.getElementById("addAccount").addEventListener("click",function(){addAccountField();});
        dialog.querySelectorAll("[data-close]").forEach(function(button){button.addEventListener("click",function(){dialog.close();});});
        form.addEventListener("submit",async function(event){event.preventDefault();var status=document.getElementById("serviceFormStatus");status.textContent="";
            var accounts=Array.from(accountFields.children).map(function(row){return {id:Number(row.dataset.id||0),label:row.querySelector('[data-account="label"]').value,login:row.querySelector('[data-account="login"]').value,password:row.querySelector('[data-account="password"]').value};});
            var permissions=document.getElementById("permissionsSection").hidden?null:Array.from(permissionFields.querySelectorAll(".permission-user")).map(function(row){var result={user_id:Number(row.dataset.userId)};row.querySelectorAll("[data-permission]").forEach(function(input){result[input.dataset.permission]=input.checked;});return result;});
            var payload={name:form.elements.name.value,url:form.elements.url.value,description:form.elements.description.value,category:form.elements.category.value,icon:form.elements.icon.value,favorite:form.elements.favorite.checked,version:Number(document.getElementById("serviceVersion").value||0),accounts:accounts,permissions:permissions};
            var body=new FormData();body.append("payload",JSON.stringify(payload));if(form.elements.icon_file.files[0])body.append("icon",form.elements.icon_file.files[0]);var id=document.getElementById("serviceId").value;
            try{await api(id?"/api/services/"+id:"/api/services",{method:id?"PUT":"POST",body:body});dialog.close();notify(id?'Сервис «'+payload.name+'» сохранён':'Сервис «'+payload.name+'» добавлен');await refresh();}catch(error){status.textContent=error.message;}
        });
    }
    var archiveToggle=document.getElementById("archiveToggle"); if(archiveToggle)archiveToggle.addEventListener("click",async function(){state.archived=!state.archived;state.filter="all";state.query="";document.getElementById("serviceSearch").value="";this.textContent=state.archived?"Вернуться к сервисам":"Показать архив";try{await refresh();}catch(error){notify(error.message,true);}});
    render();
}());
