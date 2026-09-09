import { expect, test, type Page } from '@playwright/test';

const photo = '/picker-fixture.svg';
const watch = {
  id: 71001,
  bitrix_id: 71001,
  name: 'Bauhaus Winder Midi ETA 2824-2',
  article: '011211757',
  brand: 'Laco',
  category: 'Наручные часы',
  stock: 3,
  price: 45900,
  currency: 'RUB',
  image_url: photo,
};
const products = Array.from({ length: 12 }, (_, i) => ({
  ...watch,
  id: 71001 + i,
  bitrix_id: 71001 + i,
  name: i ? `Наручные часы Laco — модель ${i + 1}` : watch.name,
}));
async function images(page: Page) {
  // Intentionally huge source image: the real picker must constrain it.
  await page.route('**/picker-fixture.svg', (route) =>
    route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1800" viewBox="0 0 120 180"><rect width="120" height="180" fill="white"/><rect x="43" y="2" width="34" height="176" rx="8" fill="#4b382b"/><circle cx="60" cy="90" r="42" fill="#a1a1aa"/><circle cx="60" cy="90" r="37" fill="#f8fafc"/><path d="M60 61V90L80 101" fill="none" stroke="#111827" stroke-width="3"/><circle cx="60" cy="90" r="3" fill="#111827"/></svg>',
    }),
  );
}
async function bounds(page: Page, selector: string) {
  const box = await page.locator(selector).boundingBox();
  const viewport = page.viewportSize()!;
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(
    false,
  );
}

test('Bitrix picker results, selection, taxonomy, duplicate actions, errors and responsive screenshots', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await images(page);
  const queries: string[] = [];
  await page.route('**/api/v1/bitrix-products/search?*', async (route) => {
    const q = new URL(route.request().url()).searchParams.get('q')!;
    queries.push(q);
    await route.fulfill({ json: { data: q === 'empty' ? [] : products } });
  });
  await page.route(/\/api\/v1\/bitrix-products\/\d+$/, (route) => {
    const id = Number(route.request().url().split('/').pop());
    return route.fulfill({
      json: {
        data: {
          ...products.find((p) => p.id === id),
          duplicate: id === 71002,
          existing: id === 71002 ? { id: 12, name: 'Laco ERP' } : null,
          brand_recognized: false,
          category_recognized: false,
        },
      },
    });
  });
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto('/warehouse?open_bitrix=1');
  const modal = page.locator('#bitrixImportModal');
  const search = page.locator('#bitrixProductSearch');
  await expect(search).toBeFocused();
  await expect(page.locator('#bitrixImportProduct')).toBeHidden();
  await search.fill('x');
  await expect(page.locator('#bitrixProductStatus')).toContainText('минимум 2');
  for (const q of ['Bauhaus', '011211757', '71001']) {
    await search.fill(q);
    await expect(page.locator('#bitrixProductResults .picker-result')).toHaveCount(12);
  }
  expect(queries).toEqual(['Bauhaus', '011211757', '71001']);
  await bounds(page, '.bitrix-import-dialog');
  await page.screenshot({ path: '../docs/screenshots/product-picker/bitrix-results-1536.png' });
  await page.locator('#bitrixProductResults .picker-result').first().click();
  await expect(page.locator('#bitrixPreviewName')).toHaveText(watch.name);
  await expect(
    page.locator('#bitrixProductResults .picker-result[aria-pressed="true"]'),
  ).toHaveCount(1);
  await expect(page.locator('#bitrixProductResults .picker-result')).toHaveCount(12);
  await expect(page.locator('#bitrixTaxonomy')).toBeVisible();
  await expect(page.locator('#bitrixImportProduct')).toBeVisible();
  await expect(page.locator('#bitrixUpdateProduct')).toBeHidden();
  await page.screenshot({ path: '../docs/screenshots/product-picker/bitrix-selected-1536.png' });
  for (const [width, height] of [
    [1440, 900],
    [390, 844],
    [320, 740],
  ]) {
    await page.setViewportSize({ width, height });
    await bounds(page, '.bitrix-import-dialog');
  }
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.locator('#bitrixProductResults .picker-result').nth(1).click();
  await expect(page.locator('#bitrixDuplicateNotice')).toBeVisible();
  await expect(page.locator('#bitrixOpenExisting')).toHaveAttribute(
    'href',
    '/warehouse/product/12',
  );
  await expect(page.locator('#bitrixUpdateProduct')).toBeVisible();
  await expect(page.locator('#bitrixImportProduct')).toBeHidden();
  await search.fill('empty');
  await expect(page.locator('#bitrixProductStatus')).toContainText('не найдены');
  await expect(page.locator('#bitrixProductPreview')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(modal).toHaveAttribute('aria-hidden', 'true');
  await page.evaluate(() =>
    (window as unknown as { openBitrixImportModal: () => void }).openBitrixImportModal(),
  );
  await expect(search).toHaveValue('');
  await expect(page.locator('#bitrixSelectionEmpty')).toBeVisible();
  await page.route(/\/api\/v1\/bitrix-products\/\d+$/, (route) =>
    route.fulfill({ status: 503, json: { message: 'Карточка недоступна' } }),
  );
  await search.fill('preview-error');
  await page.locator('#bitrixProductResults .picker-result').first().click();
  await expect(page.locator('#bitrixImportError')).toHaveText('Карточка недоступна');
  await expect(page.locator('#bitrixProductStatus')).not.toContainText('Загружаем');
  await expect(page.locator('#bitrixImportProduct')).toBeHidden();

  await images(page);
  await page.route('**/api/v1/bitrix-products/search?*', (route) =>
    route.fulfill({ status: 503, json: { message: 'Bitrix недоступен' } }),
  );
  await search.fill('error');
  await expect(page.locator('#bitrixImportError')).toHaveText('Bitrix недоступен');
  expect(errors).toEqual([]);
});

test('supply picker screenshots, clear, quantity bounds, cancel and search errors without writes', async ({
  page,
  request,
}) => {
  await images(page);
  const supply = (
    await (
      await request.post('/api/v1/receipts/supplies', {
        data: { title: 'Визуальная проверка', comment: 'Сохраняется без изменений' },
      })
    ).json()
  ).data;
  await page.route('**/api/v1/catalog/options?*', (route) =>
    route.fulfill({ json: { data: products } }),
  );
  const writes: string[] = [];
  page.on('request', (r) => {
    if (r.method() !== 'GET') writes.push(r.url());
  });
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto('/app/receipts?tab=supplies');
  await page
    .locator('#records tr')
    .filter({ hasText: 'Визуальная проверка' })
    .getByRole('button', { name: 'Открыть' })
    .click();
  await page.locator('#add-item').click();
  await expect(page.locator('#supply-product-search')).toBeFocused();
  await expect(page.locator('#supply-product-results .picker-result')).toHaveCount(12);
  await expect(page.locator('#confirm-add-item')).toBeDisabled();
  await page.screenshot({ path: '../docs/screenshots/product-picker/supply-results-1536.png' });
  await page.locator('#supply-product-results .picker-result').first().click();
  await expect(page.locator('#selected-product')).toContainText(watch.name);
  await expect(page.locator('#supply-product-results .picker-result').first()).toHaveCSS(
    'background-color',
    'rgb(239, 246, 255)',
  );
  await page.screenshot({ path: '../docs/screenshots/product-picker/supply-selected-1536.png' });
  for (const [width, height] of [
    [1536, 960],
    [1440, 900],
    [390, 844],
    [320, 740],
  ]) {
    await page.setViewportSize({ width, height });
    await bounds(page, '#add-item-dialog');
    expect(
      (await page.locator('#supply-product-results img').first().boundingBox())!.height,
    ).toBeLessThanOrEqual(72);
  }
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.locator('#quantity-plus').click();
  await expect(page.locator('#add-quantity')).toHaveValue('2');
  await page.locator('#quantity-minus').click();
  await expect(page.locator('#quantity-minus')).toBeDisabled();
  await page.locator('#add-quantity').fill('0');
  await expect(page.locator('#confirm-add-item')).toBeDisabled();
  await page.locator('#add-quantity').fill('1.5');
  await expect(page.locator('#confirm-add-item')).toBeDisabled();
  await page.locator('#cancel-add-item').click();
  await expect(page.locator('#title')).toHaveValue('Визуальная проверка');
  await expect(page.locator('#comment')).toHaveValue('Сохраняется без изменений');
  await page.locator('#add-item').click();
  await expect(page.locator('#supply-selection-empty')).toBeVisible();
  await expect(page.locator('#confirm-add-item')).toBeDisabled();
  await page.route('**/api/v1/catalog/options?*', (route) =>
    route.fulfill({ status: 503, json: { message: 'Каталог недоступен' } }),
  );
  await page.locator('#supply-product-search').fill('ошибка');
  await expect(page.locator('#supply-search-status')).toHaveText('Каталог недоступен');
  expect(writes).toEqual([]);
  const after = (await (await request.get(`/api/v1/receipts/supplies/${supply.id}`)).json()).data;
  expect(after).toEqual(supply);
});

test('Bitrix submit preserves payload, prevents double submission and redirects on success', async ({
  page,
}) => {
  await page.route('**/api/v1/bitrix-products/search?*', (route) =>
    route.fulfill({ json: { data: [watch] } }),
  );
  await page.route(/\/api\/v1\/bitrix-products\/71001$/, (route) =>
    route.fulfill({
      json: { data: { ...watch, brand_recognized: false, category_recognized: false } },
    }),
  );
  await page.goto('/warehouse?open_bitrix=1');
  await page.locator('#bitrixProductSearch').fill('71001');
  await page.locator('#bitrixProductResults .picker-result').click();
  await expect(page.locator('#bitrixBrandSelect option')).not.toHaveCount(1);
  const brand = await page.locator('#bitrixBrandSelect').selectOption({ index: 1 });
  const category = await page.locator('#bitrixCategorySelect').selectOption({ index: 1 });
  const submitted: unknown[] = [];
  await page.route('**/api/v1/bitrix-products/71001/import', async (route) => {
    submitted.push(route.request().postDataJSON());
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({ status: 503, json: { message: 'Не удалось сохранить товар' } });
  });
  await page.locator('#bitrixImportProduct').evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect(page.locator('#bitrixImportError')).toHaveText('Не удалось сохранить товар');
  expect(submitted).toEqual([{ action: 'create', quantity: 1, brand_id: brand[0], category_id: category[0] }]);
  await expect(page.locator('#bitrixImportProduct')).toBeEnabled();
  await page.route('**/api/v1/bitrix-products/71001/import', (route) =>
    route.fulfill({ status: 201, json: { data: { status: 'created', erp_product_id: 8345, product: { id: 8345, article: 'fashion-389-53-20-mm' } } } }),
  );
  await page.locator('#bitrixImportProduct').click();
  await expect(page).toHaveURL(/\/warehouse\?q=fashion-389-53-20-mm&notice=success/);
});

test('old search and preview responses cannot replace newer Bitrix selection', async ({ page }) => {
  await images(page);
  await page.route('**/api/v1/bitrix-products/search?*', async (route) => {
    const old = new URL(route.request().url()).searchParams.get('q') === 'older';
    if (old) await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({ json: { data: old ? [{ ...watch, name: 'STALE' }] : products } });
  });
  await page.route(/\/api\/v1\/bitrix-products\/\d+$/, async (route) => {
    const id = Number(route.request().url().split('/').pop());
    if (id === 71001) await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({
      json: {
        data: {
          ...products.find((p) => p.id === id),
          brand_recognized: true,
          category_recognized: true,
        },
      },
    });
  });
  await page.goto('/warehouse?open_bitrix=1');
  const oldSearch = page.waitForRequest('**/search?q=older');
  await page.locator('#bitrixProductSearch').fill('older');
  await oldSearch;
  await page.locator('#bitrixProductSearch').fill('newer');
  await expect(page.locator('#bitrixProductResults .picker-result')).toHaveCount(12);
  const oldPreview = page.waitForRequest('**/bitrix-products/71001');
  await page.locator('#bitrixProductResults .picker-result').first().click();
  await oldPreview;
  await page.locator('#bitrixProductResults .picker-result').nth(1).click();
  await expect(page.locator('#bitrixPreviewId')).toHaveText('71002');
  await page.waitForTimeout(700);
  await expect(page.locator('#bitrixPreviewId')).toHaveText('71002');
  await expect(page.locator('#bitrixProductResults')).not.toContainText('STALE');
});
