import { expect, test } from '@playwright/test';

test('orders compact header and list remain stable at target widths', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });
  page.on('pageerror', (error) => browserErrors.push(error.message));

  for (const width of [1920, 1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 900 });
    await page.goto('/app/orders', { waitUntil: 'networkidle' });
    await expect(page.locator('.orders-command-bar')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Заказы' })).toBeVisible();
    await expect(page.getByRole('searchbox', { name: 'Поиск по всем полям заказов' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Обновить WB' })).toBeVisible();
    await expect(page.getByText('Управление заказами интернет-магазина')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Мои', exact: true })).toHaveCount(0);
    await page.getByRole('radio', { name: 'Список', exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const headers = await page.locator('.orders-list-table th').allTextContents();
    expect(headers).toEqual([
      'Заказ', 'Создан', 'Статус', 'Сумма', 'Покупатель',
      'Товары', 'Доставка', 'Оплата', 'Комментарий сотрудника', 'Действия',
    ]);
    const structure = await page.locator('.orders-list-table').evaluate((table) => table.innerHTML);
    await page.waitForTimeout(1200);
    await expect(page.locator('.orders-list-table')).toHaveJSProperty('innerHTML', structure);
  }
  expect(browserErrors).toEqual([]);
});

test('orders status filter and live-search clearing update without page navigation', async ({ page }) => {
  await page.goto('/app/orders', { waitUntil: 'networkidle' });
  const search = page.getByRole('searchbox', { name: 'Поиск по всем полям заказов' });
  await search.fill('несуществующий заказ');
  await expect(page.getByRole('button', { name: 'Очистить поиск' })).toBeVisible();
  await page.getByRole('button', { name: 'Очистить поиск' }).click();
  await expect(search).toHaveValue('');
  await page.locator('[data-status-filter="A"]').click();
  await expect(page.locator('[data-status-filter="A"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page).toHaveURL(/status=A/);
});

test('split rows retain all products, separate comments, and neutral missing values', async ({ page }) => {
  await page.goto('/app/orders', { waitUntil: 'networkidle' });
  await page.getByRole('radio', { name: 'Разделение', exact: true }).click();
  const first = page.locator('.orders-split-table tr[data-order-id="7001"]');
  await expect(first).toContainText('Tissot PRX Powermatic 80');
  await expect(first).toContainText('Ремешок Cordura Black');
  await expect(first).toContainText('Внутренний');
  await expect(first).toContainText('Клиент');
  await expect(first).toContainText('Оплачен');
  await expect(first).toContainText('Не проведена');
  await expect(page.locator('.orders-split-table tr[data-order-id="7003"]')).toContainText('—');
});

test('list rows keep one product preview and expose the existing open action', async ({ page }) => {
  await page.goto('/app/orders', { waitUntil: 'networkidle' });
  await page.getByRole('radio', { name: 'Список', exact: true }).click();
  const first = page.locator('.orders-list-table tr[data-order-id="7001"]');
  await expect(first).toContainText('Tissot PRX Powermatic 80 ×1');
  await expect(first).toContainText('+ ещё 1');
  await expect(first).not.toContainText('Ремешок Cordura Black');
  await expect(first.getByRole('link', { name: 'Открыть', exact: true })).toBeVisible();
});
