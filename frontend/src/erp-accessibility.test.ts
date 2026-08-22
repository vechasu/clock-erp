// @vitest-environment jsdom
import { beforeEach, expect, test, vi } from 'vitest';
import statesSource from '../../app/static/js/erp-states.js?raw';

beforeEach(() => {
  document.documentElement.innerHTML = '<head></head><body></body>';
  delete (window as unknown as { VechasuStates?: unknown }).VechasuStates;
  vi.restoreAllMocks();
});

test('skip link moves focus to the main landmark', async () => {
  document.body.innerHTML = `
    <a class="erp-skip-link" href="#main-content">Перейти к основному содержимому</a>
    <main id="main-content" tabindex="-1"><h1>Товары</h1></main>
  `;
  window.eval(statesSource);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  document.querySelector<HTMLAnchorElement>('.erp-skip-link')!.click();
  await new Promise((resolve) => window.setTimeout(resolve, 1));
  expect(document.activeElement).toBe(document.querySelector('main'));
});

test('tables receive names, header scopes, sort state and scroll semantics', () => {
  document.body.innerHTML = `
    <main><h1>Продажи</h1>
      <div class="sales-table-wrap">
        <table><thead><tr><th><button class="is-active" data-sort data-direction="desc">Дата</button></th></tr></thead>
        <tbody><tr><th>Продажа 42</th><td>Готово</td></tr></tbody></table>
      </div>
    </main>
  `;
  const region = document.querySelector<HTMLElement>('.sales-table-wrap')!;
  Object.defineProperty(region, 'scrollWidth', { value: 500 });
  Object.defineProperty(region, 'clientWidth', { value: 300 });
  region.style.overflowX = 'auto';
  window.eval(statesSource);
  document.dispatchEvent(new Event('DOMContentLoaded'));

  expect(document.querySelector('table')!.getAttribute('aria-label')).toContain('Продажи');
  expect(document.querySelector('thead th')!.getAttribute('scope')).toBe('col');
  expect(document.querySelector('thead th')!.getAttribute('aria-sort')).toBe('descending');
  expect(document.querySelector('tbody th')!.getAttribute('scope')).toBe('row');
  expect(region.getAttribute('role')).toBe('region');
  expect(region.tabIndex).toBe(0);
});

test('dynamic states expose one atomic announcement region', () => {
  document.body.innerHTML = `
    <main><h1>Приходы</h1>
      <div class="erp-loading-state">Загрузка</div>
      <div class="erp-error-state">Ошибка</div>
      <div class="erp-success-state" role="status">Сохранено</div>
    </main>
  `;
  window.eval(statesSource);
  document.dispatchEvent(new Event('DOMContentLoaded'));

  expect(document.querySelector('.erp-loading-state')!.getAttribute('role')).toBe('status');
  expect(document.querySelector('.erp-error-state')!.getAttribute('role')).toBe('alert');
  document.querySelectorAll('[role="status"], [role="alert"]').forEach((node) => {
    expect(node.getAttribute('aria-atomic')).toBe('true');
  });
});
