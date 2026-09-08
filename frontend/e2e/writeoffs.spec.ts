import { test, expect } from '@playwright/test';

test('write-off persists and cancels once', async ({ page }, testInfo) => {
  await page.goto('/app/sales?source=writeoff');
  await expect(page.getByRole('link', { name: 'Списание', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await page.getByRole('button', { name: '+ Добавить списание', exact: true }).click();
  await expect(page.locator('#writeoffCategoryTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffCategoryTrigger')).toContainText('Сначала выберите бренд');
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await expect(page.locator('#writeoffProductTrigger')).toContainText('Сначала выберите бренд и категорию');
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await expect(page.locator('#writeoffPhotoPlaceholder')).toBeVisible();
  const choose = async (kind: string, name: string) => {
    const trigger = page.locator(`#writeoff${kind}Trigger`);
    if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
    await page.locator(`#writeoff${kind} .brand-combobox-search`).fill(name);
    await page.locator(`#writeoff${kind}Listbox .brand-combobox-option`).filter({ hasText: name }).first().click();
  };
  await choose('Brand', 'Casio');
  await expect(page.locator('#writeoffCategoryTrigger')).toBeEnabled();
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await choose('Category', 'Часы');
  await page.locator('#writeoffProductTrigger').click();
  await page.locator('#writeoffProduct .brand-combobox-search').fill('GA-2100');
  await page.locator('#writeoffProductListbox .brand-combobox-option').first().click();
  await expect(page.locator('#writeoffProductDetails')).toContainText('Остаток:');
  await expect(page.locator('#writeoffProductName')).toContainText('GA-2100');
  await expect(page.locator('#writeoffProductDetails')).toContainText('Артикул: GA-2100');
  await expect(page.locator('#writeoffProductDetails')).toContainText('Баркод:');
  await expect(page.locator('#writeoffProductListbox img')).toHaveCount(0);
  await expect(page.locator('#writeoffPhoto')).toBeVisible();
  await expect(page.locator('#writeoffPhoto')).toHaveJSProperty('complete', true);
  expect(await page.locator('#writeoffPhoto').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  const fields = await Promise.all(['Brand', 'Category', 'Product'].map((kind) => page.locator(`#writeoff${kind}Trigger`).boundingBox()));
  if (testInfo.project.name.startsWith('desktop')) {
    expect(fields[0]?.y).toBe(fields[1]?.y);
    expect(fields[1]?.y).toBe(fields[2]?.y);
  } else {
    expect(fields[0]!.y).toBeLessThan(fields[1]!.y);
    expect(fields[1]!.y).toBeLessThan(fields[2]!.y);
  }
  const stock = Number(await page.locator('#writeoffQuantity').getAttribute('max'));
  expect(stock).toBeGreaterThan(0);
  await page.locator('#writeoffQuantity').fill(String(stock + 1));
  await expect(page.locator('#writeoffQuantityError')).toContainText('Нельзя списать больше остатка');
  await page.locator('#writeoffReason').selectOption('Брак');
  let posts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/writeoffs')) posts++;
  });
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  expect(posts).toBe(0);
  await choose('Brand', 'Tissot');
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await expect(page.locator('#writeoffCategory .catalog-combobox-id')).toHaveValue('');
  await expect(page.locator('#writeoffProductTrigger')).toBeDisabled();
  await choose('Brand', 'Casio');
  await choose('Category', 'Часы');
  await choose('Product', 'GA-2100');
  await page.locator('#writeoffCategoryTrigger').click();
  await page.locator('#writeoffCategory .brand-combobox-search').fill('Часы');
  await page.locator('#writeoffCategory .brand-combobox-search').fill('');
  await expect(page.locator('#writeoffProductName')).toHaveText('Выберите товар');
  await expect(page.locator('#writeoffProductId')).toHaveValue('');
  await choose('Category', 'Часы');
  await choose('Product', 'GA-2100');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('writeoff-modal.png'), fullPage: true });
  const productId = await page.locator('#writeoffProductId').inputValue();
  await page.locator('#writeoffQuantity').fill('1');
  await page.locator('#writeoffReason').selectOption('Брак');
  await page.getByRole('button', { name: 'Списать', exact: true }).click();
  await expect(page.locator('#writeoffNotice')).toContainText('Списано:');
  const productResponse = await page.request.get(`/api/v1/products/${productId}`);
  expect(productResponse.ok()).toBe(true);
  expect((await productResponse.json()).data.stock).toBe(stock - 1);
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
