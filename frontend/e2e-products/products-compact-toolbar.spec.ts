import { expect, test } from '@playwright/test';


test('products toolbar reuses columns and focus mode', async ({
  page,
}) => {
  await page.goto('/app/products');

  const more = page.locator('#warehouseMoreTrigger');
  const menu = page.locator('#warehouseMoreMenu');
  await more.click();
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem')).toHaveCount(2);
  await expect(menu).toContainText('Настроить столбцы');
  await expect(menu).toContainText('Развернуть таблицу');

  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await more.click();
  await page.locator('h1').click();
  await expect(menu).toBeHidden();

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
