import { expect, test, type Page } from '@playwright/test';

const products = [
  {
    id: 'ms-1',
    name: 'Casio G-Shock',
    article: 'GA-2100',
    code: 'CASIO-1',
    brand: 'Casio',
    category: 'Часы',
    cell: 'A-1',
    stock: 3,
    stock_display: '3',
    thumbnail_url: '',
    has_images: false,
  },
  {
    id: 'ms-2',
    name: 'Ремешок',
    article: 'STRAP',
    code: 'STRAP-1',
    brand: 'Vechasu',
    category: 'Ремешки',
    cell: 'B-1',
    stock: 0,
    stock_display: '0',
    thumbnail_url: '',
    has_images: false,
  },
];

function position(productId: string, quantity = 2, purchasePrice = 5000) {
  const product = products.find((item) => item.id === productId) ?? products[0];
  return {
    product_id: product.id,
    product_name: product.name,
    article: product.article,
    code: product.code,
    brand: product.brand,
    category: product.category,
    cell: product.cell,
    quantity,
    purchase_price: purchasePrice,
    line_total: quantity * purchasePrice,
    stock_before: product.stock,
    stock_after: product.stock + quantity,
  };
}

function receipt(id = 'receipt-1', positions = [position('ms-1')]) {
  return {
    id,
    number: id === 'receipt-1' ? 'PR-2026-0001' : 'PR-2026-0002',
    created_at: '2026-07-30 12:00',
    receipt_date: '2026-07-30',
    brand: positions[0].brand,
    category: positions[0].category,
    product_id: positions[0].product_id,
    product_name: positions[0].product_name,
    note: 'Поставка',
    status: 'posted',
    status_label: 'Проведён',
    positions,
    positions_count: positions.length,
    total_quantity: positions.reduce((sum, item) => sum + item.quantity, 0),
    total_amount: positions.reduce((sum, item) => sum + item.line_total, 0),
    moysklad_document_id: `enter-${id}`,
    moysklad_document_name: 'ОП-0001',
    moysklad_document_url: '',
  };
}

function envelope(data: unknown, meta: Record<string, unknown> = {}) {
  return {
    data,
    meta: { request_id: 'e2e', csrf_token: 'e2e-csrf', ...meta },
    error: null,
  };
}

async function mockReceiptsApi(page: Page) {
  let receipts = [receipt()];
  await page.route('**/api/v1/receipts**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith('/receipts/catalog')) {
      await route.fulfill({ json: envelope(products, { total: products.length }) });
      return;
    }
    const match = url.pathname.match(/\/receipts\/([^/]+)$/);
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      const positions = body.positions.map(
        (item: { product_id: string; quantity: number; purchase_price: number }) =>
          position(item.product_id, Number(item.quantity), Number(item.purchase_price)),
      );
      const created = {
        ...receipt('receipt-2', positions),
        receipt_date: body.receipt_date,
        note: body.note,
      };
      receipts = [created, ...receipts];
      await route.fulfill({ json: envelope(created), status: 201 });
      return;
    }
    if (request.method() === 'PATCH' && match) {
      const body = request.postDataJSON();
      const index = receipts.findIndex((item) => item.id === match[1]);
      const positions = body.positions.map(
        (item: { product_id: string; quantity: number; purchase_price: number }) =>
          position(item.product_id, Number(item.quantity), Number(item.purchase_price)),
      );
      receipts[index] = {
        ...receipts[index],
        receipt_date: body.receipt_date,
        note: body.note,
        positions,
        positions_count: positions.length,
        total_quantity: positions[0].quantity,
        total_amount: positions[0].line_total,
      };
      await route.fulfill({ json: envelope(receipts[index]) });
      return;
    }
    if (request.method() === 'DELETE' && match) {
      receipts = receipts.filter((item) => item.id !== match[1]);
      await route.fulfill({ json: envelope({ id: match[1], deleted: true }) });
      return;
    }
    const query = (url.searchParams.get('q') ?? '').toLocaleLowerCase('ru');
    const visible = query
      ? receipts.filter((item) =>
          [item.number, item.product_name, item.note]
            .join(' ')
            .toLocaleLowerCase('ru')
            .includes(query),
        )
      : receipts;
    await route.fulfill({
      json: envelope(visible, {
        page: 1,
        page_size: 50,
        total: visible.length,
        pages: visible.length ? 1 : 0,
        totals: {
          quantity: visible.reduce((sum, item) => sum + item.total_quantity, 0),
          amount: visible.reduce((sum, item) => sum + item.total_amount, 0),
        },
        facets: {
          brands: ['Casio', 'Vechasu'],
          categories: ['Часы', 'Ремешки'],
          statuses: ['posted'],
        },
        sort_by: 'receipt_date',
        sort_dir: 'desc',
      }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockReceiptsApi(page);
});

test('receipts create multiple positions with validation', async ({ page }) => {
  await page.goto('/app/receipts');
  await expect(page.getByRole('heading', { name: 'Приходы' })).toBeVisible();
  await page.getByRole('button', { name: '+ Новый приход' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Новый приход' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Провести приход' }).click();
  await expect(dialog.getByText('Выберите товар', { exact: true }).last()).toBeVisible();

  const productSelects = dialog.getByRole('combobox');
  await productSelects.nth(0).selectOption('ms-1');
  await dialog.getByRole('button', { name: '+ Добавить позицию' }).click();
  await productSelects.nth(1).selectOption('ms-2');
  const quantityInputs = dialog.getByRole('spinbutton', { name: 'Количество *' });
  await quantityInputs.nth(0).fill('2');
  await quantityInputs.nth(1).fill('3');
  const priceInputs = dialog.getByRole('spinbutton', { name: 'Цена закупки' });
  await priceInputs.nth(0).fill('5000');
  await priceInputs.nth(1).fill('500');
  await dialog.getByRole('button', { name: 'Провести приход' }).click();
  await expect(page.getByText('Приход проведён')).toBeVisible();
  await expect(page.getByText('PR-2026-0002').first()).toBeVisible();
});

test('single-position receipt can be edited and deleted', async ({ page }) => {
  await page.goto('/app/receipts');
  const row = page.getByText('PR-2026-0001').first().locator('xpath=ancestor::tr');
  await row.getByRole('button', { name: 'Изменить' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('spinbutton', { name: 'Количество *' }).fill('4');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Приход обновлён')).toBeVisible();

  await page
    .getByText('PR-2026-0001')
    .first()
    .locator('xpath=ancestor::tr')
    .getByRole('button', { name: 'Удалить' })
    .click();
  await expect(page.getByRole('heading', { name: 'Удалить приход?' })).toBeVisible();
  await page.getByRole('button', { name: 'Удалить' }).last().click();
  await expect(page.getByText('Приход удалён')).toBeVisible();
  await expect(page.getByText('PR-2026-0001')).toHaveCount(0);
});
