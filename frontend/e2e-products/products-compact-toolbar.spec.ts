import { expect, test } from '@playwright/test';


test('products toolbar keeps collections contextual and reuses columns and focus mode', async ({
  page,
}) => {
  await page.goto('/app/products');

  const more = page.locator('#warehouseMoreTrigger');
  const menu = page.locator('#warehouseMoreMenu');
  const bulkBar = page.locator('#productCollectionBulkBar');
  const rowCheckbox = page.locator('[data-product-collection-select]').first();

  await expect(bulkBar).toBeHidden();
  await expect(rowCheckbox).toBeHidden();

  await more.click();
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem')).toHaveCount(3);
  await expect(menu).toContainText('Изменить подборки');
  await expect(menu).toContainText('Настроить столбцы');
  await expect(menu).toContainText('Развернуть таблицу');

  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await more.click();
  await page.locator('h1').click();
  await expect(menu).toBeHidden();

  await more.click();
  await page.locator('#warehouseCollectionModeTrigger').click();
  await expect(bulkBar).toBeVisible();
  await expect(rowCheckbox).toBeVisible();
  await expect(bulkBar.getByRole('button', { name: 'Добавить' })).toBeDisabled();
  await expect(bulkBar.getByRole('button', { name: 'Удалить' })).toBeDisabled();

  await rowCheckbox.check();
  await expect(page.locator('#productCollectionBulkCount')).toHaveText('Выбрано: 1');
  await page.locator('#productCollectionBulkTarget').selectOption({ index: 1 });
  await expect(bulkBar.getByRole('button', { name: 'Добавить' })).toBeEnabled();
  await expect(bulkBar.getByRole('button', { name: 'Удалить' })).toBeEnabled();

  await page.locator('#productCollectionModeClose').click();
  await expect(bulkBar).toBeHidden();
  await expect(rowCheckbox).not.toBeChecked();

  await more.click();
  await page.locator('#warehouseColumnSettingsTrigger').click();
  await expect(page.locator('#warehouseColumnSettingsPanel')).toBeVisible();
  await expect(menu).toBeHidden();
  await page.locator('h1').click();
  await expect(page.locator('#warehouseColumnSettingsPanel')).toBeHidden();

  await more.click();
  await page.locator('#warehouseFocusModeToggle').click();
  await expect(page.locator('[data-erp-focus-mode]')).toHaveClass(/erp-focus-mode/);
  await more.click();
  await expect(page.locator('#warehouseFocusModeToggle')).toContainText('Свернуть таблицу');
  await page.locator('#warehouseFocusModeToggle').click();
  await expect(page.locator('[data-erp-focus-mode]')).not.toHaveClass(/erp-focus-mode/);

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(viewport);
    await more.click();
    const box = await menu.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(viewport.width);
    await page.keyboard.press('Escape');
  }
});


test('collection bulk success resets mode while an error preserves selection', async ({ page }) => {
  let shouldFail = false;
  await page.route('**/api/v1/product-collections/bulk', async (route) => {
    const request = route.request();
    expect(request.method()).toBe('POST');
    const body = request.postDataJSON() as { action: string; product_ids: number[] };
    expect(['add', 'remove']).toContain(body.action);
    expect(body.product_ids).toHaveLength(1);
    await route.fulfill({
      status: shouldFail ? 500 : 200,
      contentType: 'application/json',
      body: JSON.stringify(
        shouldFail ? { message: 'Тестовая ошибка подборки' } : { data: { updated: 1 } },
      ),
    });
  });

  await page.goto('/app/products');

  const enterMode = async () => {
    await page.locator('#warehouseMoreTrigger').click();
    await page.locator('#warehouseCollectionModeTrigger').click();
    await page.locator('[data-product-collection-select]').first().check();
    await page.locator('#productCollectionBulkTarget').selectOption({ index: 1 });
  };
  const submitAndWaitForRefresh = async (action: 'add' | 'remove') => {
    const previousResults = await page.locator('#warehouseResults').elementHandle();
    await Promise.all([
      page.waitForFunction(
        (previous) => document.querySelector('#warehouseResults') !== previous,
        previousResults,
      ),
      page.locator(`[data-collection-bulk-action="${action}"]`).click(),
    ]);
  };

  await enterMode();
  await submitAndWaitForRefresh('add');
  await expect(page.locator('#productCollectionBulkBar')).toBeHidden();
  await expect(page.locator('[data-product-collection-select]').first()).not.toBeChecked();

  await enterMode();
  await submitAndWaitForRefresh('remove');
  await expect(page.locator('#productCollectionBulkBar')).toBeHidden();

  shouldFail = true;
  await enterMode();
  await page.locator('[data-collection-bulk-action="add"]').click();
  await expect(page.locator('#pageNotice')).toContainText('Тестовая ошибка подборки');
  await expect(page.locator('#productCollectionBulkBar')).toBeVisible();
  await expect(page.locator('[data-product-collection-select]').first()).toBeChecked();
  await expect(page.locator('#productCollectionBulkTarget')).not.toHaveValue('');
});
