import { expect, test } from '@playwright/test';

test('product save rejects another SKU and clears both errors after a successful retry', async ({ page }) => {
  await page.goto('/app/products?q=GA-2100-1A1');
  await page.getByRole('button', { name: 'Открыть карточку', exact: true }).click();
  await page.getByRole('button', { name: 'Редактировать', exact: true }).click();
  await page.getByRole('textbox', { name: 'Артикул товара', exact: true }).fill('STRAP-CB');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.locator('#detailPhotoEditorStatus')).toContainText('уже существует');
  await expect(page.locator('.erp-toast[data-kind="error"]')).toContainText('STRAP-CB');
  await page.getByRole('textbox', { name: 'Артикул товара', exact: true }).fill('GA-2100-1A1');
  await page.getByRole('spinbutton', { name: 'Цена товара', exact: true }).fill('123.45');
  const saved = page.waitForResponse(response => response.request().method() === 'PATCH' && /\/api\/products\/\d+$/.test(new URL(response.url()).pathname));
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(200);
  await expect(page.locator('#inlineProductForm')).not.toHaveClass(/is-editing/);
  await expect(page.locator('#detailPhotoEditorStatus')).not.toHaveClass(/is-error/);
  await expect(page.locator('.erp-toast[data-kind="error"]')).toHaveCount(0);
  const persisted = await page.request.get(response.url());
  expect(Number((await persisted.json()).data.price)).toBe(123.45);
});
