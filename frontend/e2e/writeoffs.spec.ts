import { test, expect, type Page } from '@playwright/test';

async function choose(page: Page, id: string, text: string) {
  await page.locator(`#${id}Trigger`).click();
  await page.locator(`#${id} .brand-combobox-search`).fill(text);
  await page.locator(`#${id}Listbox .brand-combobox-option`).filter({ hasText: text }).first().click();
}

async function stock(page: Page) {
  const response = await page.request.get('/api/v1/catalog/options?type=product&q=GA-2100');
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const rows = body.data;
  return Number(rows.find((row: { article: string; stock: number }) => row.article === 'GA-2100-1A1').stock);
}

test('sale picker cascade, stock validation, write-off and cancellation', async ({ page }, testInfo) => {
  await page.goto('/app/sales?source=writeoff');
  const before = await stock(page);
  await page.getByRole('button', { name: '+ Добавить списание', exact: true }).click();
  await expect(page.locator('#writeoffBrandTrigger')).toBeEnabled();
  await expect(page.locator('#writeoffCategoryTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffCategoryTrigger')).toContainText('Сначала выберите бренд');
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffProductTrigger')).toContainText('Сначала выберите бренд и категорию');
  await expect(page.locator('#writeoffProductSummary')).toBeHidden();
  await choose(page, 'writeoffBrand', 'Vechasu');
  await page.locator('#writeoffCategoryTrigger').click();
  await expect(page.locator('#writeoffCategoryListbox .brand-combobox-option')).toHaveCount(1);
  await expect(page.locator('#writeoffCategoryListbox')).toContainText('Ремешки');
  await page.locator('#writeoffCategoryListbox .brand-combobox-option').click();
  await page.locator('#writeoffProductTrigger').click();
  await expect(page.locator('#writeoffProductListbox .brand-combobox-option')).toHaveCount(1);
  await expect(page.locator('#writeoffProductListbox')).toContainText('STRAP-CB');
  await page.locator('#writeoffProductListbox .brand-combobox-option').click();
  await expect(page.locator('#writeoffProductDetails')).toContainText('STRAP-CB');
  // Clearing the category uses the same shared combobox event as selecting another category.
  await page.locator('#writeoffCategoryTrigger').click();
  await page.locator('#writeoffCategory .brand-combobox-search').fill('Ремешки');
  await page.locator('#writeoffCategory .brand-combobox-search-clear').click();
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await expect(page.locator('#writeoffProductSummary')).toBeHidden();
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await page.locator('#writeoffCategoryTrigger').click();
  await choose(page, 'writeoffCategory', 'Ремешки');
  await choose(page, 'writeoffProduct', 'STRAP-CB');
  await choose(page, 'writeoffBrand', 'Casio');
  await expect(page.locator('#writeoffForm [name=category_id]')).toHaveValue('');
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await expect(page.locator('#writeoffProductSummary')).toBeHidden();
  await expect(page.locator('#writeoffQuantity')).not.toHaveAttribute('max');
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await choose(page, 'writeoffCategory', 'Часы');
  await choose(page, 'writeoffProduct', 'GA-2100');
  await expect(page.locator('#writeoffProductName')).toContainText('Casio G-Shock GA-2100');
  await expect(page.locator('#writeoffProductDetails')).toHaveText(`GA-2100-1A1 · Остаток: ${before} шт.`);
  if (testInfo.project.use.viewport!.width >= 1024) {
    const boxes = await Promise.all(['Brand', 'Category', 'Product'].map(kind => page.locator(`#writeoff${kind}Trigger`).boundingBox()));
    expect(boxes[0]!.y).toBe(boxes[1]!.y);
    expect(boxes[1]!.y).toBe(boxes[2]!.y);
  }
  await page.locator('#writeoffQuantity').fill(String(before + 1));
  await page.locator('#writeoffReason').selectOption('Брак');
  await page.locator('#writeoffComment').fill('Проверка каскада и точного остатка');
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  expect(await page.locator('#writeoffQuantity').evaluate((el: HTMLInputElement) => el.validationMessage)).toContain('Недостаточно товара');
  expect(await stock(page)).toBe(before);
  await page.locator('#writeoffQuantity').fill('2');
  await page.screenshot({ path: testInfo.outputPath('writeoff-modal.png'), fullPage: true });
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  await expect(page.locator('#writeoffNotice')).toContainText('Списано:');
  await page.reload();
  await expect(page.locator('tbody')).toContainText('GA-2100');
  await expect(page.locator('tbody')).toContainText('Проверка каскада и точного остатка');
  expect(await stock(page)).toBe(before - 2);
  page.on('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Отменить списание', exact: true }).first().click();
  await expect(page.locator('#writeoffNotice')).toContainText('На склад возвращено 2 шт.');
  expect(await stock(page)).toBe(before);
  await expect(page.getByRole('button', { name: 'Отменить списание', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('ordinary sale retains its shared product picker', async ({ page }) => {
  await page.goto('/app/sales?source=tictactoy');
  await page.locator('#openManualSaleModal').click();
  await page.locator('#saleSourceChoice [data-sale-source=tictactoy]').click();
  await choose(page, 'saleBrand', 'Casio');
  await choose(page, 'saleCategory', 'Часы');
  await choose(page, 'saleProduct', 'GA-2100');
  await expect(page.locator('#saleProductPhotoText')).toContainText('GA-2100');
  await expect(page.locator('#product_id')).not.toHaveValue('');
});
