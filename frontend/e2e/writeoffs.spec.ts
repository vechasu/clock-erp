import { test, expect } from '@playwright/test';

test('write-off persists and cancels once', async ({ page }, testInfo) => {
  await page.goto('/app/sales?source=writeoff');
  await expect(page.getByRole('link', { name: 'Списание', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await page.getByRole('button', { name: '+ Добавить списание', exact: true }).click();
  await page.locator('#writeoffProductTrigger').click();
  await page.locator('#writeoffProduct .brand-combobox-search').fill('GA-2100');
  await page.locator('#writeoffProductListbox .brand-combobox-option').first().click();
  await expect(page.locator('#writeoffProductDetails')).toContainText('Остаток:');
  await page.locator('#writeoffQuantity').fill('1');
  await page.locator('#writeoffReason').selectOption('Брак');
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  await expect(page.locator('#writeoffNotice')).toContainText('Списано:');
  await page.screenshot({ path: testInfo.outputPath('writeoffs.png'), fullPage: true });
  await page.reload();
  await expect(page.locator('tbody')).toContainText('GA-2100');
  page.on('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Отменить списание', exact: true }).first().click();
  await expect(page.locator('#writeoffNotice')).toContainText('На склад возвращено 1 шт.');
  await expect(page.locator('tbody')).toContainText('Отменено');
  await expect(page.getByRole('button', { name: 'Отменить списание', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
