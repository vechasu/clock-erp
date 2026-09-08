import { expect, test } from '@playwright/test';

test('orders reuse one table, preserve scrolling and open exact search without a document reload', async ({
  page,
}) => {
  await page.goto('/app/orders', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-orders-table-scroll]')).toHaveCount(1);
  await page.locator('#ordersLayoutSwitch [data-layout-mode="list"]').click();
  await expect(page.locator('.orders-list-table')).toBeVisible();
  await page.locator('#ordersLayoutSwitch [data-layout-mode="split"]').click();
  const table = await page.locator('[data-orders-table-scroll]').elementHandle();
  const documents: string[] = [];
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documents.push(request.url());
  });
  await page.locator('.orders-split-table a.order-number').first().click();
  await expect(page.locator('.order-detail-panel')).toContainText('7001');
  expect(await table?.evaluate((node) => node.isConnected)).toBe(true);
  await page.locator('#orderSearch').fill('7002');
  await page.locator('#orderSearch').press('Enter');
  await expect(page.locator('.order-detail-panel')).toContainText('7002');
  expect(documents).toEqual([]);
  await expect(page.locator('[data-orders-table-scroll]')).toHaveCount(1);
});

test('a late list response cannot replace a newer selected order', async ({ page }) => {
  await page.goto('/app/orders', { waitUntil: 'domcontentloaded' });
  let started = false;
  await page.route('**/api/orders?**', async (route) => {
    started = true;
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ response });
  });
  await page.locator('[data-status-filter="all"]').click();
  await expect.poll(() => started).toBe(true);
  await page.locator('.orders-split-table a.order-number').first().click();
  await expect(page.locator('.order-detail-panel')).toContainText('7001');
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(/\/order\/7001/);
  await expect(page.locator('.order-detail-panel')).toContainText('7001');
});

test('rapid selections send only the first and final card and history stays partial', async ({
  page,
}) => {
  await page.goto('/app/orders', { waitUntil: 'domcontentloaded' });
  let cardRequests = 0;
  let releaseFirst: () => void = () => {};
  const firstGate = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  await page.route('**/order/*', async (route) => {
    if (!route.request().headers()['x-order-detail']) {
      await route.continue();
      return;
    }
    cardRequests += 1;
    const response = await route.fetch();
    if (cardRequests === 1) await firstGate;
    await route.fulfill({ response });
  });
  const rows = page.locator('.orders-split-table a.order-number');
  for (let i = 0; i < 10; i++) await rows.nth(i % 2).press('Enter');
  releaseFirst();
  await expect(page.locator('.order-detail-panel')).toContainText('7002');
  expect(cardRequests).toBeLessThanOrEqual(2);
  const documents: string[] = [];
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documents.push(request.url());
  });
  await page.goBack();
  await expect(page.locator('.order-detail-panel')).toContainText('Выберите заказ');
  expect(documents).toEqual([]);
});
