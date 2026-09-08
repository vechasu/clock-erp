import { test, expect } from '@playwright/test';

test('write-off persists and cancels once', async ({ page }, testInfo) => {
  await page.goto('/app/sales?source=writeoff');
  await expect(page.getByRole('link', { name: 'Списание', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await page.getByRole('button', { name: '+ Добавить списание', exact: true }).click();
  const choose = async (id: string, query: string) => {
    await page.locator(`#${id}Trigger`).click();
    await page.locator(`#${id} .brand-combobox-search`).fill(query);
    await page
      .locator(`#${id}Listbox .brand-combobox-option`)
      .filter({ hasText: query })
      .first()
      .click();
  };
  await expect(page.locator('#writeoffCategoryTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffCategoryTrigger')).toContainText('Сначала выберите бренд');
  await expect(page.locator('#writeoffProductTrigger')).toContainText(
    'Сначала выберите бренд и категорию',
  );
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await expect(page.locator('#writeoffPhotoPlaceholder')).toBeVisible();
  await choose('writeoffBrand', 'Casio');
  await expect(page.locator('#writeoffCategoryTrigger')).toBeEnabled();
  await choose('writeoffCategory', 'Часы');
  await choose('writeoffProduct', 'GA-2100');
  await expect(page.locator('#writeoffProductListbox img')).toHaveCount(0);
  await expect(page.locator('#writeoffProductName')).toContainText('Casio G-Shock GA-2100');
  await expect(page.locator('#writeoffProductDetails')).toContainText('Артикул: GA-2100-1A1');
  await expect(page.locator('#writeoffProductDetails')).toContainText('Баркод:');
  await expect(page.locator('#writeoffPhoto')).toBeVisible();
  expect(
    await page
      .locator('#writeoffPhoto')
      .evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0),
  ).toBe(true);
  await page
    .locator('#writeoffPhoto')
    .evaluate((el: HTMLImageElement) => el.dispatchEvent(new Event('error')));
  await expect(page.locator('#writeoffPhotoPlaceholder')).toBeVisible();
  const before = Number(await page.locator('#writeoffQuantity').getAttribute('max'));
  expect(before).toBeGreaterThan(0);
  await page.locator('#writeoffQuantity').fill(String(before + 1));
  await expect(page.locator('#writeoffError')).toContainText('Нельзя списать больше');
  expect(
    await page.locator('#writeoffQuantity').evaluate((el: HTMLInputElement) => el.checkValidity()),
  ).toBe(false);
  await choose('writeoffBrand', 'Casio');
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await expect(page.locator('[name="category_id"]')).toHaveValue('');
  await choose('writeoffCategory', 'Часы');
  await choose('writeoffProduct', 'GA-2100');
  await choose('writeoffCategory', 'Часы');
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await choose('writeoffProduct', 'GA-2100');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('writeoff-modal.png'), fullPage: true });
  await page.locator('#writeoffQuantity').fill('1');
  await page.locator('#writeoffReason').selectOption('Брак');
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  await expect(page.locator('#writeoffNotice')).toContainText('Списано:');
  await page.screenshot({ path: testInfo.outputPath('writeoffs.png'), fullPage: true });
  await page.reload();
  await expect(page.locator('tbody')).toContainText('GA-2100');
  await page.getByRole('button', { name: '+ Добавить списание', exact: true }).click();
  await choose('writeoffBrand', 'Casio');
  await choose('writeoffCategory', 'Часы');
  await choose('writeoffProduct', 'GA-2100');
  await expect(page.locator('#writeoffQuantity')).toHaveAttribute('max', String(before - 1));
  await page.locator('#closeWriteoff').click();
  page.on('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Отменить списание', exact: true }).first().click();
  await expect(page.locator('#writeoffNotice')).toContainText('На склад возвращено 1 шт.');
  await expect(page.locator('tbody')).toContainText('Отменено');
  await expect(page.getByRole('button', { name: 'Отменить списание', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
