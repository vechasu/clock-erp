import { expect, test, type Page } from '@playwright/test';

const catalog = [
  {
    id: '1',
    name: 'Casio G-Shock',
    article: 'GA-2100',
    barcode: '460000000001',
    brand: 'Casio',
    category: 'Часы',
    stock: 5,
    stock_display: '5',
  },
];

function sale(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sale-1',
    sale_type: 'manual',
    sale_type_label: 'Ручная',
    is_manual: true,
    inventory_managed: true,
    created_at: '2026-07-30',
    source: 'Tictactoy',
    source_key: 'tictactoy',
    order_number: 'ORDER-1',
    product_id: '1',
    product_name: 'Casio G-Shock',
    barcode: '460000000001',
    brand: 'Casio',
    category: 'Часы',
    quantity: 2,
    quantity_display: '2',
    net_quantity: 2,
    returned_quantity: 0,
    return_available_quantity: 2,
    returned_at: '',
    return_reason: '',
    unit_price: 1000,
    total_amount: 2000,
    gross_total_amount: 2000,
    returned_amount: 0,
    order_status: 'completed',
    order_status_label: 'Выполнен',
    is_cancelled: false,
    cancelled_at: '',
    track_number: '',
    delivery_method: '',
    delivery_cost: 0,
    region: '',
    city: '',
    note: '',
    recipient: '',
    recipient_name: '',
    payment_method: '',
    commission: '',
    commission_amount: 0,
    country: '',
    delivery_address: '',
    platform: '',
    invoice_number: '',
    sticker_number: '',
    ...overrides,
  };
}

function envelope(data: unknown, meta: Record<string, unknown> = {}) {
  return {
    data,
    meta: { request_id: 'e2e', csrf_token: 'e2e-csrf', ...meta },
    error: null,
  };
}

async function mockSalesApi(page: Page) {
  let sales = [
    sale(),
    sale({
      id: 'automatic-1',
      sale_type: 'automatic',
      sale_type_label: 'Автоматическая',
      is_manual: false,
      inventory_managed: false,
      created_at: '2026-07-29',
      order_number: 'BX-1',
      quantity: 1,
      quantity_display: '1',
      net_quantity: 1,
      return_available_quantity: 1,
      total_amount: 1000,
      gross_total_amount: 1000,
      note: 'Заказ Битрикс',
    }),
  ];
  await page.route('**/api/v1/sales**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/sales/catalog')) {
      await route.fulfill({ json: envelope(catalog, { total: 1 }) });
      return;
    }
    const returnMatch = url.pathname.match(/\/sales\/([^/]+)\/returns$/);
    const match = url.pathname.match(/\/sales\/([^/]+)$/);
    if (request.method() === 'POST' && returnMatch) {
      const body = request.postDataJSON();
      const index = sales.findIndex((item) => item.id === returnMatch[1]);
      sales[index] = {
        ...sales[index],
        returned_quantity: Number(body.quantity),
        return_available_quantity: sales[index].quantity - Number(body.quantity),
        net_quantity: sales[index].quantity - Number(body.quantity),
        returned_amount: Number(body.quantity) * Number(sales[index].unit_price),
        total_amount:
          (sales[index].quantity - Number(body.quantity)) * Number(sales[index].unit_price),
        order_status: 'partially_returned',
        order_status_label: 'Частичный возврат',
      };
      await route.fulfill({ json: envelope(sales[index]), status: 201 });
      return;
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      const created = sale({
        ...body,
        id: 'sale-2',
        quantity_display: String(body.quantity),
        net_quantity: Number(body.quantity),
        return_available_quantity: Number(body.quantity),
        total_amount: Number(body.quantity) * Number(body.unit_price),
        gross_total_amount: Number(body.quantity) * Number(body.unit_price),
      });
      sales = [created, ...sales];
      await route.fulfill({ json: envelope(created), status: 201 });
      return;
    }
    if (request.method() === 'PATCH' && match) {
      const body = request.postDataJSON();
      const index = sales.findIndex((item) => item.id === match[1]);
      sales[index] = {
        ...sales[index],
        ...body,
        quantity_display: String(body.quantity),
        total_amount: Number(body.quantity) * Number(body.unit_price),
      };
      await route.fulfill({ json: envelope(sales[index]) });
      return;
    }
    if (request.method() === 'DELETE' && match) {
      sales = sales.filter((item) => item.id !== match[1]);
      await route.fulfill({ json: envelope({ id: match[1], deleted: true }) });
      return;
    }
    const source = url.searchParams.get('source') ?? 'all';
    const query = (url.searchParams.get('q') ?? '').toLocaleLowerCase('ru');
    const visible = sales.filter(
      (item) =>
        (source === 'all' || item.source_key === source) &&
        (!query ||
          [item.order_number, item.product_name, item.note]
            .join(' ')
            .toLocaleLowerCase('ru')
            .includes(query)),
    );
    await route.fulfill({
      json: envelope(visible, {
        page: 1,
        page_size: 50,
        total: visible.length,
        pages: visible.length ? 1 : 0,
        totals: {
          active: visible.filter((item) => !item.is_cancelled).length,
          cancelled: visible.filter((item) => item.is_cancelled).length,
          quantity: visible.reduce((sum, item) => sum + item.net_quantity, 0),
          revenue: visible.reduce((sum, item) => sum + Number(item.total_amount), 0),
          returned: visible.reduce((sum, item) => sum + item.returned_amount, 0),
        },
        facets: {
          sources: ['tictactoy'],
          brands: ['Casio'],
          categories: ['Часы'],
          statuses: ['completed'],
        },
        sort_by: 'created_at',
        sort_dir: 'desc',
      }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockSalesApi(page);
});

test('sales support source tabs, live search and validated creation', async ({ page }) => {
  await page.goto('/app/sales?source=all');
  await expect(page.getByRole('heading', { name: 'Продажи' })).toBeVisible();
  await page.getByPlaceholder('Заказ, товар, трек-номер, получатель…').fill('BX-1');
  await expect(page).toHaveURL(/q=BX-1/);
  await expect(page.getByText('BX-1').first()).toBeVisible();
  await expect(page.getByText('ORDER-1')).toHaveCount(0);

  await page.getByRole('button', { name: '+ Новая продажа' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(dialog.getByText('Выберите товар').last()).toBeVisible();
  await dialog.getByRole('combobox', { name: 'Товар *' }).selectOption('1');
  await dialog.getByRole('spinbutton', { name: 'Количество *' }).fill('2');
  await dialog.getByRole('spinbutton', { name: 'Цена продажи *' }).fill('1500');
  await dialog.getByRole('textbox', { name: 'Номер заказа' }).fill('ORDER-2');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Продажа добавлена')).toBeVisible();
});

test('sales support managed returns and automatic edit/delete', async ({ page }) => {
  await page.goto('/app/sales?source=all');
  const managedRow = page.getByText('ORDER-1').first().locator('xpath=ancestor::tr');
  await managedRow.getByRole('button', { name: 'Возврат' }).click();
  let dialog = page.getByRole('dialog');
  await dialog.getByRole('spinbutton', { name: 'Количество' }).fill('1');
  await dialog.getByRole('textbox', { name: 'Причина' }).fill('Не подошло');
  await dialog.getByRole('button', { name: 'Оформить возврат' }).click();
  await expect(page.getByText('Возврат оформлен')).toBeVisible();

  const automaticRow = page.getByText('BX-1').first().locator('xpath=ancestor::tr');
  await automaticRow.getByRole('button', { name: 'Изменить' }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Комментарий' }).fill('Обновлено');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Изменения сохранены')).toBeVisible();

  await page
    .getByText('BX-1')
    .first()
    .locator('xpath=ancestor::tr')
    .getByRole('button', { name: 'Удалить' })
    .click();
  await page.getByRole('button', { name: 'Удалить' }).last().click();
  await expect(page.getByText('Продажа удалена')).toBeVisible();
  await expect(page.getByText('BX-1')).toHaveCount(0);
});
