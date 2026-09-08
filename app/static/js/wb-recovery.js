(() => {
    const root = document.querySelector('[data-wb-recovery]');
    if (!root || root.dataset.initialized) return;
    root.dataset.initialized = '1';
    const form = root.querySelector('form');
    const importButton = root.querySelector('[data-wb-import]');
    const message = root.querySelector('[data-wb-recovery-message]');
    const table = root.querySelector('table');
    const labels = {READY:'Можно импортировать', ALREADY_IMPORTED:'Уже в ERP', ALREADY_SOLD:'Уже есть продажа', PRODUCT_NOT_FOUND:'Требует сопоставления', AMBIGUOUS_PRODUCT:'Неоднозначное сопоставление', NO_STOCK:'Нет остатка — импорт разрешён', API_ERROR:'Ошибка API'};
    let confirmation = null;
    let busy = false;
    async function request(path, body) {
        const options = {credentials:'same-origin', headers:{Accept:'application/json'}};
        if (body) {
            options.method = 'POST';
            options.headers['Content-Type'] = 'application/json';
            options.headers['X-CSRF-Token'] = root.querySelector('[data-wb-recovery-csrf]').value;
            options.body = JSON.stringify(body);
        }
        const response = await fetch('/api/orders/wildberries/recovery' + path, options);
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.ok) throw new Error(data?.message || 'Не удалось получить данные WB');
        return data;
    }
    async function loadDiagnostics() {
        try {
            const {diagnostics:d} = await request('');
            const lastSuccess = d.last_success_at ? new Date(d.last_success_at) : null;
            const time = lastSuccess && !Number.isNaN(lastSuccess.getTime())
                ? new Intl.DateTimeFormat('ru-RU', {dateStyle:'short',timeStyle:'short'}).format(lastSuccess) : '';
            const age = time ? Math.max(0, Math.floor((Date.now()-lastSuccess.getTime())/60000)) : null;
            const stale = age === null || age > 15;
            const hasIssues = stale || d.attention || (d.pending || []).length || ['partial','error','running'].includes(d.outcome) || d.full_outcome === 'partial' || d.full_outcome === 'error';
            const names = {success:'успешно', partial:'частично', error:'ошибка', running:'выполняется'};
            root.querySelector('[data-wb-health]').textContent = hasIssues
                ? `Требует внимания · ${names[d.outcome] || 'нет успешной синхронизации'}`
                : `Синхронизирован · ${time}`;
            const diagnostic = root.querySelector('[data-wb-diagnostic]');
            diagnostic.classList.toggle('warning', hasIssues);
            diagnostic.textContent = `Последняя успешная синхронизация: ${d.last_success_at || 'ещё не выполнялась'} · Последняя попытка: ${d.last_attempt_at || d.checked_at || '—'} · Результат: ${names[d.outcome] || 'неизвестен'} · Новых: ${d.new_orders || 0} · Обновлено статусов: ${d.statuses_updated || 0} · Восстановлено: ${d.recovered || 0} · Ошибок: ${d.error_count ?? (d.errors || []).length} · Возраст данных: ${age === null ? 'неизвестен' : age + ' мин.'}` + (stale ? ' · Внимание: нет успешной синхронизации за последние 15 минут.' : '') + (d.full_outcome && d.full_outcome !== 'success' ? ' · Глубокая проверка: ' + (names[d.full_outcome] || d.full_outcome) : '');
            const warning = root.querySelector('[data-wb-missing]');
            const lines = (d.supplies || []).map(s => `${s.supply_id}: WB содержит ${s.wb_count}, ERP знала ${s.erp_count}, пропущено ${s.missing}.`);
            lines.push(...(d.errors || []).map(e => e.error));
            lines.push(...(d.full_errors || []).map(e => e.error));
            lines.push(...(d.pending || []).map(row => `${row.wb_order_id}: ${row.error}`));
            warning.textContent = lines.length ? 'Найдены заказы WB, отсутствующие в ERP. ' + lines.join(' ') + ' Проверьте поставку ниже для восстановления.' : '';
            warning.hidden = !lines.length;
            const restore = root.querySelector('[data-wb-restore]');
            restore.hidden = !(d.supplies || []).length;
            restore.onclick = () => {form.elements.supply_id.value = d.supplies[0].supply_id; form.requestSubmit();};

        } catch (error) {
            root.querySelector('[data-wb-health]').textContent = 'Диагностика недоступна';
        }
    }
    form.addEventListener('input', () => {confirmation = null; importButton.hidden = true;});
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        busy = true; confirmation = null; importButton.hidden = true;
        form.querySelector('button').disabled = true;
        message.textContent = 'Проверяем поставку без записи в ERP…';
        try {
            const {report:r} = await request('/preview', {supply_id:form.elements.supply_id.value.trim()});
            table.querySelector('tbody').replaceChildren();
            for (const row of r.rows) {
                const tr = document.createElement('tr');
                for (const value of [row.wb_order_id, row.article, row.barcode, row.erp_product ? `${row.erp_product.name} / ${row.erp_product.id}` : row.candidates.map(p => `${p.name} / ${p.id}`).join('; '), row.erp_product?.stock ?? '—', labels[row.status] + (row.error ? ': ' + row.error : '')]) {
                    const td = document.createElement('td'); td.textContent = value || '—'; tr.append(td);
                }
                table.querySelector('tbody').append(tr);
            }
            table.hidden = false;
            message.textContent = `Заказов WB: ${r.wb_count}. Можно импортировать: ${r.importable}. ` + Object.entries(r.counts).map(([key,value]) => `${labels[key]}: ${value}`).join(' · ') + ' ' + r.errors.join(' ');
            confirmation = r.confirmation;
            importButton.hidden = !confirmation;
        } catch (error) {message.textContent = error.message;}
        finally {busy = false; form.querySelector('button').disabled = false;}
    });
    importButton.addEventListener('click', async () => {
        if (!confirmation || busy) return;
        busy = true; importButton.disabled = true;
        try {
            const {result:r} = await request('/import', {confirmation});
            message.textContent = `Импортировано: ${r.imported}. Пропущено существующих: ${r.skipped}. Ошибок: ${r.failed.length}. Продажи не создавались, остатки не изменялись.`;
            confirmation = null; importButton.hidden = true;
            window.VechasuNotify?.success(message.textContent);
            loadDiagnostics();
        } catch (error) {message.textContent = error.message;}
        finally {busy = false; importButton.disabled = false;}
    });
    loadDiagnostics();
    window.setInterval(() => {if (!document.hidden) loadDiagnostics();}, 60000);
})();
