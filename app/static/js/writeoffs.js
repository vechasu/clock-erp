(() => {
    document.querySelector('.sales-tabs [aria-current=page]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const form = document.getElementById('writeoffForm');
    const modal = document.querySelector('[data-writeoff-modal]');
    const error = document.getElementById('writeoffError');
    let key = '';
    let signature = '';
    const toggle = (open) => {
        modal.classList.toggle('is-open', open);
        modal.setAttribute('aria-hidden', String(!open));
        document.body.classList.toggle('modal-open', open);
        (open ? document.getElementById('writeoffBrandTrigger') : document.getElementById('openWriteoff'))?.focus();
    };
    document.getElementById('openWriteoff').onclick = () => toggle(true);
    document.getElementById('closeWriteoff').onclick = () => toggle(false);
    modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') toggle(false); });
    const reason = document.getElementById('writeoffReason');
    reason.onchange = () => { document.getElementById('writeoffComment').required = reason.value === 'Прочее'; };
    const quantity = document.getElementById('writeoffQuantity');
    const validateQuantity = () => {
        quantity.setCustomValidity(quantity.validity.rangeOverflow ? `Недостаточно товара. Доступно: ${quantity.max} шт.` : '');
    };
    quantity.addEventListener('input', validateQuantity);
    // The shared cascade clears child values synchronously after this event.
    form.addEventListener('catalog-combobox:change', () => queueMicrotask(() => {
        if (form.elements.product_id.value) return;
        document.getElementById('writeoffProductSummary').hidden = true;
        quantity.removeAttribute('max');
        quantity.setCustomValidity('');
        error.textContent = '';
    }));
    form.addEventListener('shared-catalog:selected', (event) => {
        if (event.detail.kind !== 'product') return;
        const p = event.detail.item;
        const available = p.is_bundle ? p.available_to_assemble : p.is_physical_component ? p.physical_stock : p.stock;
        document.getElementById('writeoffProductSummary').hidden = false;
        document.getElementById('writeoffProductDetails').textContent = [p.article, `Остаток: ${available ?? 0} шт.`].filter(Boolean).join(' · ');
        document.getElementById('writeoffProductName').textContent = p.name || p.display_name;
        const photo = document.getElementById('writeoffPhoto');
        const imageUrl = p.local_image_url || p.image_url || p.thumbnail_url;
        if (imageUrl) photo.src = imageUrl;
        else photo.removeAttribute('src');
        photo.hidden = !imageUrl;
        document.getElementById('writeoffPhotoPlaceholder').hidden = Boolean(imageUrl);
        quantity.max = String(available ?? 0);
        validateQuantity();
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
