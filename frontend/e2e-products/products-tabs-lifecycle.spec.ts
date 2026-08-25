import { expect, test, type Page, type Response } from '@playwright/test';

async function partialAction(
  page: Page,
  partialRequests: () => number,
  action: () => Promise<unknown>,
): Promise<Response> {
  const before = partialRequests();
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.request().method() === 'GET' &&
        candidate.headers()['x-erp-partial'] === 'products-v1',
    ),
    action(),
  ]);
  await expect.poll(partialRequests).toBe(before + 1);
  expect(response.ok()).toBe(true);
  expect(response.headers()['x-erp-partial']).toBe('products-v1');
  return response;
}

test('products partial lifecycle stays idempotent through three history cycles', async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedRequests: string[] = [];
  const documentRequests: string[] = [];
  const productsTabScripts: string[] = [];
  let partialRequestCount = 0;

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.stack || error.message));
  page.on('requestfailed', (request) => failedRequests.push(request.url()));
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documentRequests.push(request.url());
    if (request.url().includes('/static/js/products-tabs.js')) {
      productsTabScripts.push(request.url());
    }
    if (request.headers()['x-erp-partial'] === 'products-v1') {
      partialRequestCount += 1;
    }
  });

  await page.goto('/app/products', { waitUntil: 'load' });
  await expect(page.locator('#warehouseProductsTable')).toBeVisible();
  await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(50);

  for (let cycle = 0; cycle < 3; cycle += 1) {
    const search = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, () => partialRequestCount, () => search.fill('Casio'));
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(1);

    const availabilitySelect = page.locator(
      '.warehouse-availability-select select',
    );
    const segmentedAvailability = page.locator(
      '.warehouse-availability-segments',
    );
    const useSegments = await segmentedAvailability.isVisible();
    await partialAction(page, () => partialRequestCount, () =>
      useSegments
        ? page.getByRole('button', { name: 'В наличии', exact: true }).click()
        : availabilitySelect.selectOption('in'),
    );
    await expect(page).toHaveURL(/q=Casio/);
    await expect(page).toHaveURL(/stock_state=in/);

    await partialAction(page, () => partialRequestCount, () => search.fill(''));
    await partialAction(page, () => partialRequestCount, () =>
      useSegments
        ? page.getByRole('button', { name: 'Все', exact: true }).click()
        : availabilitySelect.selectOption('all'),
    );
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(50);

    await partialAction(page, () => partialRequestCount, () =>
      page.getByRole('link', { name: 'Следующая страница' }).click(),
    );
    await expect(page).toHaveURL(/page=2/);

    const firstRow = page.locator('#warehouseProductsTable tbody tr').first();
    await firstRow.getByRole('button', { name: 'Открыть карточку' }).click();
    const card = page.getByRole('dialog', { name: 'Карточка товара' });
    await expect(card).toBeVisible();
    await expect(card.getByText('История', { exact: true })).toBeVisible();
    await card.getByRole('button', { name: '×', exact: true }).click();
    await expect(card).not.toBeVisible();

    await partialAction(page, () => partialRequestCount, () => page.goBack());
    await expect(page).not.toHaveURL(/page=2/);
    await partialAction(page, () => partialRequestCount, () => page.goForward());
    await expect(page).toHaveURL(/page=2/);
    await partialAction(page, () => partialRequestCount, () => page.goBack());
  }

  expect(documentRequests).toHaveLength(1);
  expect(productsTabScripts).toHaveLength(1);
  expect(failedRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
