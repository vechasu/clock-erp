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
        (open ? document.getElementById('writeoffBrandTrigger') : document.getElementById('openWriteoff'))?.focus();
    };
    document.getElementById('openWriteoff').onclick = () => toggle(true);
    document.getElementById('closeWriteoff').onclick = () => toggle(false);
    modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') toggle(false); });
    const reason = document.getElementById('writeoffReason');
    reason.onchange = () => { document.getElementById('writeoffComment').required = reason.value === 'Прочее'; };
    const quantity = document.getElementById('writeoffQuantity');
    const quantityError = document.getElementById('writeoffQuantityError');
    const photo = document.getElementById('writeoffPhoto');
    const placeholder = document.getElementById('writeoffPhotoPlaceholder');
    let available = null;
    const validateQuantity = () => {
        const value = Number(quantity.value);
        const message = !Number.isInteger(value) || value < 1
            ? 'Укажите целое количество не меньше 1.'
            : available !== null && value > available
                ? `Нельзя списать больше доступного остатка: ${available} шт.` : '';
        quantity.setCustomValidity(message);
        quantity.setAttribute('aria-invalid', String(Boolean(message)));
        quantityError.textContent = message;
        quantityError.hidden = !message;
        return !message;
    };
    quantity.addEventListener('input', validateQuantity);
    photo.addEventListener('error', () => { photo.hidden = true; placeholder.hidden = false; });
    const showProduct = (p) => {
        available = p ? Number(p.is_bundle ? p.available_to_assemble : p.is_physical_component ? p.physical_stock : p.stock) || 0 : null;
        document.getElementById('writeoffProductName').textContent = p ? p.name || p.display_name : 'Выберите товар';
        document.getElementById('writeoffProductDetails').textContent = p
            ? `Артикул: ${p.article || '—'} · Баркод: ${p.barcode || '—'} · Остаток: ${available} шт.`
            : 'Баркод, артикул и остаток появятся после выбора товара.';
        const src = p && (p.local_image_url || window.normalizeCatalogProductImageUrls(p)[0]);
        if (src) photo.src = src; else photo.removeAttribute('src');
        photo.hidden = !src;
        placeholder.hidden = Boolean(src);
        if (available === null) quantity.removeAttribute('max'); else quantity.max = String(available);
        validateQuantity();
    };
    form.addEventListener('catalog-combobox:change', (event) => {
        if (event.target.matches('[data-shared-catalog-kind]')) showProduct(null);
    });
    form.addEventListener('shared-catalog:selected', (event) => {
        if (event.detail.kind === 'product') showProduct(event.detail.item);
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
        if (!payload.product_id || available === null) { error.textContent = 'Выберите товар.'; return; }
        if (!validateQuantity()) return;
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
