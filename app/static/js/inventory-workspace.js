(() => {
    const root = document.querySelector('[data-inventory-workspace]');
    if (!root || root.dataset.inventoryBound === '1') return;
    root.dataset.inventoryBound = '1';

    root.querySelectorAll('[data-column-toggle]').forEach((input) => {
        const key = input.dataset.columnToggle;
        const storageKey = `vechasu.inventory.column.${key}`;
        input.checked = localStorage.getItem(storageKey) !== 'hidden';
        const sync = () => {
            root.querySelectorAll(`[data-column="${CSS.escape(key)}"]`).forEach((cell) => {
                cell.hidden = !input.checked;
            });
            localStorage.setItem(storageKey, input.checked ? 'visible' : 'hidden');
        };
        input.addEventListener('change', sync);
        sync();
    });

    root.querySelectorAll('[data-frequency-editor]').forEach((editor) => {
        const input = editor.querySelector('input[name="interval_days"]');
        const state = editor.querySelector('[data-frequency-state]');
        const original = () => editor.dataset.value;
        editor.querySelector('[data-frequency-open]')?.addEventListener('click', () => {
            editor.classList.add('is-editing');
            input.focus();
            input.select();
        });
        editor.querySelector('[data-frequency-cancel]')?.addEventListener('click', () => {
            input.value = original();
            state.textContent = '';
            editor.classList.remove('is-editing');
        });
        editor.querySelector('form')?.addEventListener('submit', async (event) => {
            event.preventDefault();
            const button = editor.querySelector('[data-frequency-save]');
            button.disabled = true;
            state.textContent = 'Сохраняем…';
            try {
                const response = await fetch(`/api/v1/inventory-brands/${editor.dataset.brand}/control`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf || ''},
                    body: JSON.stringify({interval_days: input.value, enabled: true}),
                });
                const payload = await response.json();
                if (!response.ok || payload.ok === false) throw new Error(payload.message || 'Не удалось сохранить');
                editor.dataset.value = input.value;
                editor.querySelector('[data-frequency-label]').textContent = `Раз в ${input.value} дней`;
                state.textContent = 'Сохранено';
                editor.classList.remove('is-editing');
            } catch (error) {
                state.textContent = error.message;
                input.focus();
            } finally {
                button.disabled = false;
            }
        });
    });
})();
