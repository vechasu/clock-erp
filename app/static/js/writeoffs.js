(() => {
    document.querySelector('.sales-tabs [aria-current=page]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const form = document.getElementById('writeoffForm');
    const modal = document.getElementById('writeoffModal');
    const error = document.getElementById('writeoffError');
    let key = '';
    let signature = '';
    const toggle = (open) => {
        modal.classList.toggle('is-open', open);
        modal.setAttribute('aria-hidden', String(!open));
        document.body.classList.toggle('modal-open', open);
        (open ? document.getElementById('writeoffProductTrigger') : document.getElementById('openWriteoff'))?.focus();
    };
    document.getElementById('openWriteoff').onclick = () => toggle(true);
    document.getElementById('closeWriteoff').onclick = () => toggle(false);
    modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') toggle(false); });
    const reason = document.getElementById('writeoffReason');
    reason.onchange = () => { document.getElementById('writeoffComment').required = reason.value === 'Прочее'; };
    form.addEventListener('shared-catalog:selected', (event) => {
        if (event.detail.kind !== 'product') return;
        const p = event.detail.item;
        const available = p.is_bundle ? p.available_to_assemble : p.is_physical_component ? p.physical_stock : p.stock;
        document.getElementById('writeoffProductSummary').hidden = false;
        document.getElementById('writeoffProductDetails').textContent = [p.name || p.display_name, p.article, p.brand || p.brand_name, p.category || p.category_name, `Остаток: ${available ?? 0} шт.`].filter(Boolean).join(' · ');
        const photo = document.getElementById('writeoffPhoto');
        photo.src = p.local_image_url || p.image_url || p.thumbnail_url || '';
        photo.hidden = !photo.getAttribute('src');
        document.getElementById('writeoffQuantity').max = String(available ?? 0);
    });
    const post = async (url, payload, requestKey) => {
        const response = await fetch(url, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':form.elements.csrf_token.value,...(requestKey ? {'Idempotency-Key':requestKey} : {})},body:JSON.stringify(payload)});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error?.message || body.message || 'Не удалось выполнить операцию.');
        return body.data;
    };
    const done = (message) => {
        sessionStorage.setItem('writeoffNotice',message);
        window.location.reload();
    };
    const notice = sessionStorage.getItem('writeoffNotice');
    if (notice) {
        sessionStorage.removeItem('writeoffNotice');
        const el = document.getElementById('writeoffNotice');
        el.textContent = notice; el.hidden = false;
        window.VechasuNotify?.success(notice);
    }
    form.addEventListener('submit',async (event) => {
        event.preventDefault(); error.textContent = '';
        const payload = {product_id:form.elements.product_id.value,quantity:form.elements.quantity.value,reason:reason.value,comment:form.elements.comment.value};
        if (!payload.product_id) { error.textContent = 'Выберите товар.'; return; }
        const next = JSON.stringify(payload);
        if (signature !== next) { signature = next; key = crypto.randomUUID(); }
        const button = document.getElementById('submitWriteoff'); button.disabled = true;
        try { const row = await post('/api/v1/writeoffs',payload,key); done(`Списано: ${row.product_name} ${row.article || ''} — ${row.quantity} шт.`); }
        catch (e) { error.textContent = e.message; }
        finally { button.disabled = false; }
    });
    document.querySelectorAll('[data-cancel-writeoff]').forEach((button) => {
        button.onclick = async () => {
            if (!window.confirm('Отменить списание и вернуть товар на склад?')) return;
            button.disabled = true;
            try { const row = await post(`/api/v1/writeoffs/${encodeURIComponent(button.dataset.cancelWriteoff)}/cancel`,{}); done(`Списание отменено. На склад возвращено ${row.quantity} шт.`); }
            catch (e) { const el = document.getElementById('writeoffNotice'); el.textContent = e.message; el.hidden = false; }
            finally { button.disabled = false; }
        };
    });
})();
