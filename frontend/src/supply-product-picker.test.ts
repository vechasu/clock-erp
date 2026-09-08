// @vitest-environment jsdom
import { beforeEach, expect, test, vi } from 'vitest';
import picker from '../../app/static/js/product-picker.js?raw';
import source from '../../app/static/js/supplies.js?raw';
import template from '../../app/templates/supplies.html?raw';

const product = {
  id: 42,
  name: 'Watch',
  article: 'BX-501',
  brand: 'Known',
  category: 'Watches',
  stock: 0,
};
let supply: Record<string, unknown>;
let importFailure = false;
let addFailure = false;
let fetchMock: ReturnType<typeof vi.fn>;
const el = (id: string) => document.getElementById(id)!;
const click = (id: string) => el(id).click();
const input = (id: string, value: string) => {
  (el(id) as HTMLInputElement).value = value;
  el(id).dispatchEvent(new Event('input'));
};
const flush = async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
};

beforeEach(async () => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  history.replaceState(null, '', '?tab=supplies');
  document.body.innerHTML = '<meta name="csrf-token" content="test">' + template;
  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.showModal = () => dialog.setAttribute('open', '');
    dialog.close = () => {
      dialog.removeAttribute('open');
      dialog.dispatchEvent(new Event('close'));
    };
  });
  supply = {
    id: 'supply:test',
    number: 'P-1',
    title: 'Test',
    comment: '',
    status: 'draft',
    created_at: '2026-09-08',
    created_by: 'Test',
    items: [],
    total_quantity: 0,
    position_count: 0,
  };
  importFailure = addFailure = false;
  fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    let data: unknown = [];
    let status = 200;
    let message = '';
    if (path.endsWith('/supplies')) data = [supply];
    else if (path.endsWith('/supplies/supply%3Atest')) data = supply;
    else if (path.includes('/catalog/options')) data = [product];
    else if (path.includes('/bitrix-products/search'))
      data = [{ ...product, id: undefined, bitrix_id: '501' }];
    else if (path.endsWith('/bitrix-products/501'))
      data = { ...product, id: undefined, bitrix_id: '501', brand_id: 1, category_id: 2 };
    else if (path.endsWith('/501/import')) {
      if (importFailure) {
        status = 503;
        message = 'Bitrix недоступен';
      }
      data = { erp_product_id: 42, product, status: 'created' };
    } else if (path.endsWith('/items')) {
      if (addFailure) {
        status = 503;
        message = 'Сохранение недоступно';
      } else {
        const body = JSON.parse(String(options?.body));
        supply = {
          ...supply,
          items: [
            {
              ...product,
              product_id: 42,
              quantity: body.quantity,
              stock_before: 0,
              stock_after: body.quantity,
            },
          ],
          total_quantity: body.quantity,
          position_count: 1,
        };
        data = supply;
      }
    }
    return {
      ok: status === 200,
      status,
      json: async () => ({ data, message, ok: status === 200 }),
    };
  });
  Object.defineProperty(window, 'fetch', { value: fetchMock, writable: true, configurable: true });
  window.eval(picker);
  window.eval(source);
  await flush();
  document.querySelector<HTMLButtonElement>('[data-open]')!.click();
  await flush();
  click('add-item');
  await flush();
});

async function selectBitrix() {
  (el('supply-product-source') as HTMLSelectElement).value = 'bitrix';
  el('supply-product-source').dispatchEvent(new Event('change'));
  input('supply-product-search', '501');
  await vi.waitFor(() =>
    expect(document.querySelector('#supply-product-results button')).not.toBeNull(),
  );
  document.querySelector<HTMLButtonElement>('#supply-product-results button')!.click();
  await flush();
}
function submit() {
  el('add-item-form').dispatchEvent(new Event('submit', { cancelable: true }));
}

test('existing ERP product adds directly without Bitrix and refreshes supply table', async () => {
  document.querySelector<HTMLButtonElement>('#supply-product-results button')!.click();
  input('add-quantity', '2');
  submit();
  await flush();
  expect(fetchMock.mock.calls.some(([path]) => String(path).includes('bitrix-products'))).toBe(
    false,
  );
  expect(el('items').textContent).toContain('Watch');
  expect(el('dialog-message').textContent).toContain('+2');
});

test('imports selected Bitrix card then adds its ERP id in the same supply', async () => {
  await selectBitrix();
  expect(el('confirm-add-item').textContent).toBe('Импортировать и добавить');
  input('add-quantity', '3');
  submit();
  submit();
  await flush();
  const imports = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/import'));
  const additions = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/items'));
  expect(imports).toHaveLength(1);
  expect(JSON.parse(String(imports[0][1].body)).supply_id).toBe('supply:test');
  expect(additions).toHaveLength(1);
  expect(JSON.parse(String(additions[0][1].body))).toEqual({ product_id: 42, quantity: 3 });
  expect(el('items').textContent).toContain('Watch');
  expect(el('dialog-message').textContent).toContain('+3');
});

test('failed import never adds a supply item', async () => {
  await selectBitrix();
  importFailure = true;
  submit();
  await flush();
  expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/items'))).toBe(false);
  expect(el('add-item-message').textContent).toContain('Bitrix недоступен');
});

test('failed addition explains saved card and retries the identical operation without import', async () => {
  await selectBitrix();
  addFailure = true;
  submit();
  await flush();
  expect(el('add-item-message').textContent).toContain(
    'Товар сохранён в ERP, но добавление в поставку не подтверждено',
  );
  const first = fetchMock.mock.calls.find(([path]) => String(path).endsWith('/items'))!;
  addFailure = false;
  submit();
  await flush();
  const additions = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/items'));
  expect(additions).toHaveLength(2);
  expect(additions[1][1]).toEqual(first[1]);
  expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/import'))).toHaveLength(1);
  expect(sessionStorage.getItem('supply-add:supply:test')).toBeNull();
});

test.each(['0', '-1', 'abc'])('invalid quantity %s never imports or adds', async (quantity) => {
  await selectBitrix();
  input('add-quantity', quantity);
  submit();
  await flush();
  expect(fetchMock.mock.calls.some(([path]) => /\/(import|items)$/.test(String(path)))).toBe(false);
});
