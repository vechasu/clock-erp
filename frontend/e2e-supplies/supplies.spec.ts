import { expect, test } from '@playwright/test';
test('supply posts two local movements and remains read-only', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/app/receipts');
  await expect(page.locator('[data-tab="all"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#records')).toContainText('21096');
  await page.locator('[data-tab="supplies"]').click();
  await page.locator('#new-supply').click();
  await page.locator('#title').fill('Casio — сентябрь 2026');
  await page.locator('#add-bitrix').click();
  await page.locator('#bitrix-query').fill('Casio');
  await page.locator('#bitrix-search').click();
  await page.locator('[data-bitrix="90101"]').click();
  await page.locator('[data-bitrix="90102"]').click();
  await page.locator('[data-quantity="0"]').fill('5');
  await page.locator('[data-quantity="1"]').fill('6');
  await expect(page.locator('[data-after="0"]')).toHaveText('8');
  await expect(page.locator('[data-after="1"]')).toHaveText('6');
  await page.locator('#save-supply').click();
  await expect(page.locator('#dialog-message')).toContainText('Остаток не изменён');
  await page.locator('#post-supply').click();
  await expect(page.locator('#dialog-message')).toContainText('Поставка проведена');
  await expect(page.locator('#post-supply')).toBeHidden();
  await expect(page.locator('#title')).toBeDisabled();
  await expect(page.locator('[data-quantity]')).toHaveCount(0);
  await page.locator('#close-supply').click();
  await page.locator('[data-tab="all"]').click();
  await page.locator('#query').fill('Casio — сентябрь');
  await page.locator('#filters').getByRole('button', { name: 'Найти', exact: true }).click();
  await expect(page.locator('#records tbody tr')).toHaveCount(2);
  await page.locator('#records [data-open]').first().click();
  await expect(page.locator('#title')).toBeDisabled();
  await page.locator('#close-supply').click();
  for (const width of [1440, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await expect
      .poll(() => page.locator('main').evaluate((el) => el.getBoundingClientRect().width))
      .toBeGreaterThan(width > 767 ? width - 250 : width - 20);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    );
    expect(overflow).toBe(false);
  }
  expect(errors).toEqual([]);
  await page.screenshot({ path: '/tmp/clock-erp-supplies-mobile.png', fullPage: true });
});

test('draft validation, duplicate prevention, search and pagination', async ({ page, request }) => {
  for (let i = 0; i < 28; i++) {
    const response = await request.post('/api/v1/receipts/supplies', {
      data: { title: `Pagination supply ${i}` },
    });
    expect(response.status()).toBe(201);
  }
  await page.goto('/app/receipts?tab=supplies');
  await page.locator('#query').fill('Pagination supply');
  await page.locator('#filters').getByRole('button', { name: 'Найти', exact: true }).click();
  await expect(page.locator('#count')).toHaveText('28');
  await expect(page.locator('#quantity')).toHaveText('0');
  await expect(page.locator('#records tbody tr')).toHaveCount(25);
  await page.locator('#next').click();
  await expect(page.locator('#records tbody tr')).toHaveCount(3);
  await page.locator('#new-supply').click();
  await page.locator('#title').fill('Duplicate protection');
  await page.locator('#add-bitrix').click();
  await page.locator('#bitrix-query').fill('Casio');
  await page.locator('#bitrix-search').click();
  await page.locator('[data-bitrix="90101"]').click();
  await page.locator('[data-bitrix="90101"]').click();
  await expect(page.locator('#dialog-message')).toContainText('Товар уже есть');
  await expect(page.locator('#items tbody tr')).toHaveCount(1);
  await page.locator('[data-quantity="0"]').fill('0');
  await page.locator('#save-supply').click();
  await expect(page.locator('#dialog-message')).toContainText('целым положительным');
  await page.locator('[data-quantity="0"]').fill('2');
  await page.locator('#save-supply').click();
  await expect(page.locator('#dialog-message')).toContainText('Черновик сохранён');
  await page.screenshot({ path: '/tmp/clock-erp-supplies-desktop.png', fullPage: true });
  await page.locator('#delete-supply').click();
  await expect(page.locator('#supply-dialog')).not.toBeVisible();
  await page.locator('[data-tab="cancellations"]').click();
  await page.locator('#filters').getByRole('button', { name: 'Сбросить' }).click();
  await expect(page.locator('#records')).toContainText('21096');
  await expect(page.locator('#count')).toHaveText('1');
});

test('period filter uses the displayed local calendar day', async ({ page }) => {
  await page.route('**/api/v1/receipts/movements', (route) =>
    route.fulfill({
      json: {
        ok: true,
        data: [
          {
            id: 'midnight',
            source_type: 'legacy',
            title: 'After midnight',
            name: 'Watch',
            created_at: '2026-09-07T22:30:00+00:00',
            quantity: 1,
            stock_before: 0,
            stock_after: 1,
          },
        ],
      },
    }),
  );
  await page.goto('/app/receipts');
  await page.locator('#date-from').fill('2026-09-08');
  await page.locator('#date-to').fill('2026-09-08');
  await page.locator('#filters').getByRole('button', { name: 'Найти', exact: true }).click();
  await expect(page.locator('#count')).toHaveText('1');
  await expect(page.locator('#records')).toContainText('08.09.2026');
});

test('append goods to a posted supply using the shared ERP picker', async ({ page, request }) => {
  const product = (await (await request.post('/api/v1/receipts/bitrix/90101')).json()).data;
  const other = (await (await request.post('/api/v1/receipts/bitrix/90102')).json()).data;
  const supply = (
    await (
      await request.post('/api/v1/receipts/supplies', {
        data: { title: 'Additional goods smoke', items: [{ product_id: product.id, quantity: 3 }] },
      })
    ).json()
  ).data;
  await request.post(`/api/v1/receipts/supplies/${supply.id}/post`);
  const writes: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST') writes.push(r.url());
  });
  await page.goto('/app/receipts?tab=supplies');
  await page
    .locator('#records tr')
    .filter({ hasText: 'Additional goods smoke' })
    .getByRole('button', { name: 'Открыть' })
    .click();
  await page.locator('#add-item').click();
  await page.locator('#supply-productTrigger').click();
  await page.locator('#supply-product .brand-combobox-search').fill('SUP-90102');
  await page
    .locator('#supply-product .brand-combobox-option')
    .filter({ hasText: 'Casio F91W' })
    .click();
  await expect(page.locator('#selected-product')).toContainText('Остаток:');
  await page.locator('#add-quantity').fill('2');
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#add-item-dialog')).not.toBeVisible();
  await expect(page.locator('#items tbody tr')).toHaveCount(2);
  await expect(page.locator('#items')).toContainText('Casio F91W');
  await expect(page.locator('#supply-totals')).toHaveText('Позиций: 2 · Единиц: 5');
  await expect(page.locator('#dialog-message')).toContainText('+2 шт.');
  await page.locator('#add-item').click();
  await page.locator('#add-quantity').fill('2');
  await expect(page.locator('#duplicate-confirmation')).toContainText(
    'Сейчас: 2 шт. Добавить ещё 2 шт.?',
  );
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#supply-totals')).toHaveText('Позиций: 2 · Единиц: 7');
  await expect(page.locator('#post-supply')).toBeHidden();
  const result = (await (await request.get(`/api/v1/receipts/supplies/${supply.id}`)).json()).data;
  expect(result.status).toBe('posted');
  expect(result.items.find((i: { product_id: number }) => i.product_id === other.id).quantity).toBe(
    4,
  );
  expect(writes).toHaveLength(2);
  expect(writes.every((url) => url.endsWith('/items'))).toBe(true);
  await page.locator('#add-item').click();
  await page.locator('#supply-productTrigger').click();
  await page.locator('#supply-product .brand-combobox-search').fill('NONEXISTENT-SUPPLY-SKU');
  await expect(page.locator('#add-item-form')).toContainText('Сначала добавьте его в каталог');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(
      false,
    );
  }
});

test('a lost response and refresh retry the same addition exactly once', async ({
  page,
  request,
}) => {
  await page.addInitScript(() => Object.defineProperty(crypto, 'randomUUID', { value: undefined }));
  const p = (await (await request.post('/api/v1/receipts/bitrix/90101')).json()).data;
  const supply = (
    await (
      await request.post('/api/v1/receipts/supplies', {
        data: { title: 'Retry smoke', items: [{ product_id: p.id, quantity: 1 }] },
      })
    ).json()
  ).data;
  await request.post(`/api/v1/receipts/supplies/${supply.id}/post`);
  await page.goto('/app/receipts?tab=supplies');
  const open = async () => {
    await page
      .locator('#records tr')
      .filter({ hasText: 'Retry smoke' })
      .getByRole('button', { name: 'Открыть' })
      .click();
    await page.locator('#add-item').click();
  };
  await open();
  await page.locator('#supply-productTrigger').click();
  await page.locator('#supply-product .brand-combobox-search').fill('SUP-90101');
  await page
    .locator('#supply-product .brand-combobox-option')
    .filter({ hasText: 'Casio A168' })
    .click();
  await page.locator('#add-quantity').fill('2');
  await page.route(
    '**/supplies/*/items',
    async (route) => {
      await route.fetch();
      await route.abort('failed');
    },
    { times: 1 },
  );
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#add-item-message')).toBeVisible();
  await page.reload();
  await open();
  await expect(page.locator('#confirm-add-item')).toHaveText('Проверить предыдущую операцию');
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#supply-totals')).toHaveText('Позиций: 1 · Единиц: 3');
  const result = (await (await request.get(`/api/v1/receipts/supplies/${supply.id}`)).json()).data;
  expect(result.total_quantity).toBe(3);
  expect(Object.keys(result.additions)).toHaveLength(1);
});
