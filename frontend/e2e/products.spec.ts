import { expect, test, type Page } from '@playwright/test';

type Product = {
  id: number;
  name: string;
  article: string;
  barcode: string;
  brand: string;
  category: string;
  cell: string;
  stock: number;
  stock_display: string;
  created_at: number;
  created_at_display: string;
  thumbnail_url: string;
  gallery: unknown[];
  price_display: string;
  source_url: string;
  match_status: string;
  updated_at: string;
};

const initialProducts: Product[] = [
  {
    id: 1,
    name: 'Casio G-Shock GA-2100',
    article: 'GA-2100-1A1',
    barcode: '460000000001',
    brand: 'Casio',
    category: 'Часы',
    cell: 'A-01',
    stock: 7,
    stock_display: '7',
    created_at: 1,
    created_at_display: '29.07.2026 12:00',
    thumbnail_url: '',
    gallery: [],
    price_display: '14 990 ₽',
    source_url: '',
    match_status: 'exact',
    updated_at: '2026-07-29T12:00:00',
  },
  {
    id: 2,
    name: 'Ремешок для часов',
    article: 'STRAP-BLACK',
    barcode: '',
    brand: 'Vechasu',
    category: 'Ремешки',
    cell: 'B-12',
    stock: 0,
    stock_display: '0',
    created_at: 2,
    created_at_display: '30.07.2026 10:15',
    thumbnail_url: '',
    gallery: [],
    price_display: '1 490 ₽',
    source_url: '',
    match_status: 'not_found',
    updated_at: '2026-07-30T10:15:00',
  },
];

async function mockProductsApi(page: Page) {
  let products = structuredClone(initialProducts);
  await page.route('**/api/v1/products**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const match = url.pathname.match(/\/products\/(\d+)$/);
    if (request.method() === 'PATCH' && url.pathname.endsWith('/products/bulk')) {
      const body = request.postDataJSON() as {
        ids: number[];
        changes: Partial<Product>;
      };
      const items: Product[] = [];
      products = products.map((product) => {
        if (!body.ids.includes(product.id)) return product;
        const updated = { ...product, ...body.changes };
        items.push(updated);
        return updated;
      });
      await route.fulfill({
        json: envelope({ items, updated: items.length, errors: [] }),
      });
      return;
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      const created = {
        ...initialProducts[1],
        ...body,
        id: 3,
        stock_display: String(body.stock),
      };
      products = [created, ...products];
      await route.fulfill({ json: envelope(created), status: 201 });
      return;
    }
    if (request.method() === 'PATCH' && match) {
      const body = request.postDataJSON();
      const index = products.findIndex((item) => item.id === Number(match[1]));
      products[index] = {
        ...products[index],
        ...body,
        stock_display: String(body.stock),
      };
      await route.fulfill({ json: envelope(products[index]) });
      return;
    }
    if (request.method() === 'DELETE' && match) {
      products = products.filter((item) => item.id !== Number(match[1]));
      await route.fulfill({
        json: envelope({ id: Number(match[1]), deleted: true }),
      });
      return;
    }
    const query = (url.searchParams.get('q') ?? '').toLocaleLowerCase('ru');
    const brand = url.searchParams.get('brand') ?? '';
    const category = url.searchParams.get('category') ?? '';
    const cell = url.searchParams.get('cell') ?? '';
    const inStock = url.searchParams.get('in_stock') === '1';
    const visible = products.filter(
      (item) =>
        (!query ||
          [item.name, item.article, item.brand, item.cell]
            .join(' ')
            .toLocaleLowerCase('ru')
            .includes(query)) &&
        (!brand || item.brand === brand) &&
        (!category || item.category === category) &&
        (!cell || item.cell === cell) &&
        (!inStock || item.stock > 0),
    );
    await route.fulfill({
      json: envelope(visible, {
        page: 1,
        page_size: 50,
        total: visible.length,
        pages: visible.length ? 1 : 0,
        stats: {
          positions: visible.length,
          total_stock: visible.reduce((sum, item) => sum + item.stock, 0),
          positive_positions: visible.filter((item) => item.stock > 0).length,
          zero_positions: visible.filter((item) => item.stock === 0).length,
        },
        facets: {
          brands: [
            { name: 'Casio', count: 1 },
            { name: 'Vechasu', count: 1 },
          ],
          categories: [
            { name: 'Часы', count: 1 },
            { name: 'Ремешки', count: 1 },
          ],
          cells: [
            { cell: 'A-01', count: 1 },
            { cell: 'B-12', count: 1 },
          ],
        },
        sort_by: 'name',
        sort_dir: 'asc',
      }),
    });
  });
}

function envelope(data: unknown, meta: Record<string, unknown> = {}) {
  return {
    data,
    meta: { request_id: 'e2e', csrf_token: 'e2e-csrf', ...meta },
    error: null,
  };
}

function visibleProductList(page: Page) {
  return page.locator('.data-table-wrap:visible, .mobile-card-list:visible');
}

test.beforeEach(async ({ page }) => {
  await mockProductsApi(page);
});

test('products support live search and a validated create flow', async ({ page }) => {
  await page.goto('/app/products');
  await expect(page.getByRole('heading', { name: 'Товары' })).toBeVisible();
  await expect(visibleProductList(page).getByText('Casio G-Shock GA-2100').first()).toBeVisible();

  await page.getByPlaceholder('Название, артикул, штрихкод, ячейка…').fill('ремешок');
  await expect(page).toHaveURL(/q=%D1%80%D0%B5%D0%BC%D0%B5%D1%88%D0%BE%D0%BA/i);
  await expect(visibleProductList(page).getByText('Ремешок для часов').first()).toBeVisible();
  await expect(page.getByText('Casio G-Shock GA-2100')).toHaveCount(0);

  await page.getByRole('button', { name: '+ Добавить товар' }).click();
  await page.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Название товара обязательно')).toBeVisible();
  await page.getByLabel('Название *').fill('Новый товар');
  await page.getByPlaceholder('Casio', { exact: true }).fill('Vechasu');
  await page.getByPlaceholder('Часы / Спортивные').fill('Аксессуары');
  await page.getByRole('spinbutton', { name: 'Остаток' }).fill('3');
  await page.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Товар добавлен')).toBeVisible();
});

test('products support edit and confirmed delete', async ({ page }) => {
  await page.goto('/app/products');
  const zeroProduct = visibleProductList(page).getByText('Ремешок для часов').first();
  await expect(zeroProduct).toBeVisible();
  const row = zeroProduct.locator('xpath=ancestor::tr | ancestor::article');
  await row.getByRole('button', { name: 'Изменить' }).click();
  await page.getByLabel('Название *').fill('Ремешок обновлён');
  await page.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('Карточка обновлена')).toBeVisible();

  const updatedRow = visibleProductList(page)
    .getByText('Ремешок обновлён')
    .first()
    .locator('xpath=ancestor::tr | ancestor::article');
  await updatedRow.getByRole('button', { name: 'Удалить' }).click();
  await expect(page.getByRole('heading', { name: 'Удалить товар?' })).toBeVisible();
  await page.getByRole('button', { name: 'Удалить' }).last().click();
  await expect(page.getByText('Товар удалён')).toBeVisible();
  await expect(page.getByText('Ремешок обновлён')).toHaveCount(0);
});

test('products persist columns and support filters and bulk edits', async ({ page }) => {
  await page.goto('/app/products');
  await page.getByText('Фильтры', { exact: true }).click();
  await page.getByRole('combobox', { name: 'Бренд', exact: true }).selectOption('Casio');
  await expect(page).toHaveURL(/brand=Casio/);
  await expect(visibleProductList(page).getByText('Casio G-Shock GA-2100').first()).toBeVisible();
  await expect(page.getByText('Ремешок для часов')).toHaveCount(0);

  await page.goto('/app/products');
  if ((page.viewportSize()?.width ?? 0) > 640) {
    await page.getByText('Столбцы', { exact: true }).click();
    await page.getByRole('group').getByLabel('Цена', { exact: true }).uncheck();
    await expect(page.getByRole('columnheader', { name: /Цена/ })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('columnheader', { name: /Цена/ })).toHaveCount(0);
  }

  await visibleProductList(page).getByLabel('Выбрать Casio G-Shock GA-2100').check();
  await page.getByRole('button', { name: 'Изменить выбранные' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Ячейка').fill('C-03');
  await dialog.getByRole('button', { name: 'Применить' }).click();
  await expect(page.getByText('Массово обновлено товаров: 1')).toBeVisible();
});
