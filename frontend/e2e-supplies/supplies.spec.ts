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
