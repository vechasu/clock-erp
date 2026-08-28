(() => {
  const boot = window.SMS_BOOTSTRAP || {};
  const csrf = boot.csrf || '';
  const backdrop = document.querySelector('[data-sms-backdrop]');
  const compose = document.querySelector('[data-compose-dialog]');
  const detail = document.querySelector('[data-detail-dialog]');
  const templateDialog = document.querySelector('[data-template-dialog]');
  const toast = document.querySelector('[data-sms-toast]');
  let returnFocus = null;
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const notify = message => { toast.textContent = message; toast.hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => toast.hidden = true, 3500); };
  const openDialog = dialog => { returnFocus = document.activeElement; backdrop.hidden = false; dialog.showModal(); };
  const closeDialog = dialog => { if (dialog?.open) dialog.close(); backdrop.hidden = !document.querySelector('dialog[open]'); returnFocus?.focus?.(); };
  document.querySelectorAll('[data-open-compose]').forEach(button => button.addEventListener('click', () => openDialog(compose)));
  document.querySelector('[data-close-compose]')?.addEventListener('click', () => closeDialog(compose));
  document.querySelector('[data-close-template]')?.addEventListener('click', () => closeDialog(templateDialog));
  backdrop?.addEventListener('click', () => { [compose, detail, templateDialog].forEach(closeDialog); });
  if (document.body.dataset.composeOpen === '1') openDialog(compose);

  document.querySelectorAll('[data-sms-tab]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-sms-tab]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-selected', String(item === button)); });
    document.querySelectorAll('[data-sms-panel]').forEach(panel => panel.hidden = panel.dataset.smsPanel !== button.dataset.smsTab);
  }));
  const filterForm = document.getElementById('smsFilters');
  let searchTimer;
  filterForm?.querySelector('input[type="search"]')?.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => filterForm.requestSubmit(), 450); });
  filterForm?.querySelectorAll('select').forEach(select => select.addEventListener('change', () => filterForm.requestSubmit()));

  const gsmBasic = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\u001bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
  const gsmExtended = '^{}\\[~]|€';
  const countSegments = text => { let units = 0, gsm = true; for (const character of text) { if (gsmBasic.includes(character)) units += 1; else if (gsmExtended.includes(character)) units += 2; else { gsm = false; break; } } if (!gsm) units = [...text].length; const single = gsm ? 160 : 70, multi = gsm ? 153 : 67; return {encoding:gsm?'GSM-7':'Unicode',segments:units===0?0:units<=single?1:Math.ceil(units/multi)}; };
  const composeForm = document.getElementById('smsComposeForm');
  const textArea = composeForm?.elements.text;
  const variables = {...(boot.compose || {})};
  const renderVariables = body => body.replace(/\{(client_name|order_number|order_status|amount|repair_number)\}/g, (_, key) => String(variables[key] || ''));
  const applySelectedTemplate = () => { const option = composeForm?.elements.template_id?.selectedOptions[0]; if (option?.dataset.body) { textArea.value = renderVariables(option.dataset.body); updatePreview(); } };
  const updatePreview = () => { const text = textArea.value; const info = countSegments(text); composeForm.querySelector('[data-character-count]').textContent = `${[...text].length} символов`; composeForm.querySelector('[data-segment-count]').textContent = `${info.segments} SMS · ${info.encoding}`; composeForm.querySelector('[data-long-warning]').hidden = info.segments <= 1; composeForm.querySelector('[data-message-preview]').textContent = text || 'Текст сообщения появится здесь.'; };
  textArea?.addEventListener('input', updatePreview);
  composeForm?.elements.template_id?.addEventListener('change', applySelectedTemplate);
  composeForm?.querySelector('[data-schedule-toggle]')?.addEventListener('change', event => { composeForm.querySelector('[data-schedule-field]').hidden = !event.target.checked; });
  const customerSearch = composeForm?.elements.customer_search;
  const customerResults = composeForm?.querySelector('[data-customer-results]');
  let customerTimer;
  customerSearch?.addEventListener('input', () => { clearTimeout(customerTimer); const query = customerSearch.value.trim(); if (query.length < 2) { customerResults.hidden = true; return; } customerTimer = setTimeout(async () => { try { const response = await fetch(`/api/v1/sms/customers?q=${encodeURIComponent(query)}`); const payload = await response.json(); const rows = payload.data || []; customerResults.innerHTML = rows.map(row => `<button type="button" data-customer-id="${row.id}" data-name="${escapeHtml(row.name)}" data-phone="${escapeHtml(row.phone)}"><strong>${escapeHtml(row.name)}</strong><br><small>${escapeHtml(row.phone || 'Телефон не указан')}</small></button>`).join('') || '<button type="button" disabled>Ничего не найдено</button>'; customerResults.hidden = false; } catch { customerResults.hidden = true; } }, 300); });
  const updateRelations = async customerId => { try { const response=await fetch(`/api/v1/sms/customers/${customerId}/relations`); const payload=await response.json(); if(!response.ok)return; const {orders,repairs}=payload.data; composeForm.elements.order_relation.innerHTML='<option value="">Без заказа</option>'+orders.map(row=>`<option value="${escapeHtml(row.id)}" data-number="${escapeHtml(row.number)}" data-status="${escapeHtml(row.status)}" data-amount="${escapeHtml(row.amount)}">Заказ №${escapeHtml(row.number)}</option>`).join(''); composeForm.elements.repair_relation.innerHTML='<option value="">Без ремонта</option>'+repairs.map(row=>`<option value="${escapeHtml(row.id)}" data-number="${escapeHtml(row.number)}">Ремонт №${escapeHtml(row.number)}</option>`).join(''); } catch {} };
  customerResults?.addEventListener('click', event => { const button = event.target.closest('[data-customer-id]'); if (!button) return; composeForm.elements.customer_id.value = button.dataset.customerId; composeForm.elements.customer_name.value = button.dataset.name; variables.client_name=button.dataset.name; customerSearch.value = button.dataset.name; composeForm.elements.phone.value = button.dataset.phone; customerResults.hidden = true; updateRelations(button.dataset.customerId); applySelectedTemplate(); });
  composeForm?.elements.order_relation?.addEventListener('change', event => { const option=event.target.selectedOptions[0]; composeForm.elements.order_id.value=event.target.value; composeForm.elements.order_number.value=option?.dataset.number||''; variables.order_number=option?.dataset.number||''; variables.order_status=option?.dataset.status||''; variables.amount=option?.dataset.amount||''; applySelectedTemplate(); });
  composeForm?.elements.repair_relation?.addEventListener('change', event => { const option=event.target.selectedOptions[0]; composeForm.elements.repair_id.value=event.target.value; composeForm.elements.repair_number.value=option?.dataset.number||''; variables.repair_number=option?.dataset.number||''; applySelectedTemplate(); });
  composeForm?.addEventListener('submit', async event => {
    event.preventDefault(); const submit = composeForm.querySelector('[data-send-sms]'); const error = composeForm.querySelector('[data-compose-error]'); if (submit.disabled) return;
    submit.disabled = true; submit.textContent = 'Отправляем…'; error.hidden = true;
    const data = new FormData(composeForm); const scheduled = data.get('scheduled_at');
    const payload = Object.fromEntries(data.entries()); delete payload.order_relation; delete payload.repair_relation; payload.variables = {...variables,client_name:payload.customer_name}; if (scheduled) payload.scheduled_at = new Date(scheduled).toISOString(); else delete payload.scheduled_at;
    try { const response = await fetch('/api/v1/sms/messages',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':payload.client_message_id},body:JSON.stringify(payload)}); const result = await response.json(); if (!response.ok) throw new Error(result.message || 'Не удалось отправить SMS'); notify(result.meta?.duplicate ? 'Повторный запрос распознан — второе SMS не отправлено.' : `SMS: ${result.data.status_label}`); closeDialog(compose); setTimeout(() => window.location.assign('/app/sms'), 700); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; submit.disabled = false; submit.textContent = 'Отправить сейчас'; }
  });

  const openDetail = async row => { try { const response = await fetch(`/api/v1/sms/messages/${row.dataset.messageId}`); const payload = await response.json(); if (!response.ok) throw new Error(payload.message || 'SMS недоступно'); const message = payload.data.message, history = payload.data.history; const relation = message.order_number ? `<a href="/order/${encodeURIComponent(message.order_id)}">Заказ №${escapeHtml(message.order_number)}</a>` : message.repair_number ? `<a href="/app/repairs?repair_id=${encodeURIComponent(message.repair_id)}">Ремонт №${escapeHtml(message.repair_number)}</a>` : '—'; const client = message.customer_id ? `<a href="/app/customers/${encodeURIComponent(message.customer_id)}">${escapeHtml(message.customer_name || 'Клиент')}</a>` : escapeHtml(message.customer_name || 'Получатель'); detail.querySelector('[data-detail-content]').innerHTML = `<div class="sms-detail-grid"><div><span>Получатель</span><strong>${client}</strong></div><div><span>Телефон</span><strong>${escapeHtml(message.phone_masked)}</strong></div><div><span>Связанный объект</span><strong>${relation}</strong></div><div><span>Создано / отправлено</span><strong>${escapeHtml(message.created_at.replace('T',' ').slice(0,16))} / ${escapeHtml(message.sent_at ? message.sent_at.replace('T',' ').slice(0,16) : '—')}</strong></div><div><span>Сотрудник</span><strong>${escapeHtml(message.sent_by_name || message.created_by_name)}</strong></div><div><span>Подпись</span><strong>${escapeHtml(message.sender || 'Без подписи')}</strong></div><div><span>SMS / стоимость</span><strong>${escapeHtml(message.segments || '—')} / ${escapeHtml(message.cost == null ? '—' : `${message.cost} ${message.currency || ''}`)}</strong></div></div><h3>Текст</h3><div class="sms-detail-text">${escapeHtml(message.message_text)}</div>${message.error_description ? `<div class="sms-alert error">${escapeHtml(message.error_description)}</div>` : ''}<h3>История статуса</h3><ol class="sms-history">${history.map(item => `<li><strong>${escapeHtml(item.status_label)}</strong> · ${escapeHtml(item.changed_at.replace('T',' ').slice(0,16))}${item.description ? `<br><span>${escapeHtml(item.description)}</span>` : ''}</li>`).join('')}</ol>`; openDialog(detail); } catch (failure) { notify(failure.message); } };
  document.querySelectorAll('[data-message-id]').forEach(row => { row.addEventListener('click', event => { if (!event.target.closest('a')) openDetail(row); }); row.addEventListener('keydown', event => { if (event.key === 'Enter') openDetail(row); }); });

  const postAction = async (url, method='POST', body) => { const response = await fetch(url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body?JSON.stringify(body):undefined}); const payload = await response.json(); if (!response.ok) throw new Error(payload.message || 'Операция не выполнена'); return payload; };
  document.querySelector('[data-sync-statuses]')?.addEventListener('click', async event => { event.target.disabled = true; try { const result = await postAction('/api/v1/sms/statuses/sync'); notify(`Проверено: ${result.data.checked}, обновлено: ${result.data.updated}`); setTimeout(() => location.reload(),600); } catch (failure) { notify(failure.message); event.target.disabled=false; } });
  document.querySelector('[data-check-integration]')?.addEventListener('click', async event => { event.target.disabled=true; try { await postAction('/api/v1/sms/integration/check'); notify('Соединение, баланс и подписи проверены без отправки SMS.'); setTimeout(() => location.reload(),600); } catch(failure){ notify(failure.message); event.target.disabled=false; } });

  const templateForm = templateDialog?.querySelector('[data-template-form]');
  const openTemplate = card => { templateForm.reset(); templateForm.elements.id.value = card?.dataset.id || ''; templateForm.elements.name.value = card?.dataset.name || ''; templateForm.elements.text.value = card?.dataset.text || ''; templateForm.elements.active.checked = !card || card.dataset.active === '1'; openDialog(templateDialog); };
  document.querySelector('[data-new-template]')?.addEventListener('click', () => openTemplate());
  document.querySelectorAll('[data-edit-template]').forEach(button => button.addEventListener('click', () => openTemplate(button.closest('[data-template-card]'))));
  templateForm?.addEventListener('submit', async event => { event.preventDefault(); const error=templateForm.querySelector('[data-template-error]'); error.hidden=true; const values=Object.fromEntries(new FormData(templateForm).entries()); values.active=templateForm.elements.active.checked; const id=values.id; delete values.id; try { await postAction(id?`/api/v1/sms/templates/${id}`:'/api/v1/sms/templates',id?'PUT':'POST',values); notify('Шаблон сохранён.'); closeDialog(templateDialog); setTimeout(()=>location.reload(),500); } catch(failure){error.textContent=failure.message;error.hidden=false;} });
  document.querySelectorAll('[data-delete-template]').forEach(button => button.addEventListener('click', async () => { const card=button.closest('[data-template-card]'); if (!confirm(`${button.textContent} шаблон «${card.dataset.name}»?`)) return; try { await postAction(`/api/v1/sms/templates/${card.dataset.id}`,'DELETE'); location.reload(); } catch(failure){notify(failure.message);} }));
  updatePreview();
})();
