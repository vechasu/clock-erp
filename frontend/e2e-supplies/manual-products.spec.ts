import { expect, test, type Page } from '@playwright/test';

async function chooseCatalogValue(page: Page, id: string, value: string) {
  const combobox = page.locator(`#${id}`);
  await combobox.locator('.brand-combobox-trigger').click();
  await combobox.locator('.brand-combobox-search').fill(value);
  const exactOption = combobox
    .locator('.brand-combobox-option')
    .filter({ has: page.getByText(value, { exact: true }) });
  const createAction = combobox.locator('[data-catalog-create-action]');
  await expect.poll(async () =>
    (await exactOption.first().isVisible()) || (await createAction.isVisible()),
  ).toBe(true);
  if (await exactOption.first().isVisible()) await exactOption.first().click();
  else await createAction.click();
}

test('manual and Bitrix products are available in a new supply before posting', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/warehouse');
  await page.locator('#openWarehouseAddModal').click();
  await page.getByRole('menuitem', { name: 'Создать самостоятельно' }).click();
  const manual = page.locator('#manual-product-dialog');
  await expect(manual).toBeVisible();
  await chooseCatalogValue(page, 'manualProductBrandCombobox', 'Casio');
  await chooseCatalogValue(page, 'manualProductCategoryCombobox', 'Часы');
  await chooseCatalogValue(page, 'manualProductCombobox', 'Smoke manual catalog');
  await manual.locator('[name=article]').fill('SMOKE-MANUAL-CATALOG');
  await manual.locator('[name=product_image]').setInputFiles({
    name: 'watch.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
      'base64',
    ),
  });
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/products') && r.request().method() === 'POST',
  );
  await manual.getByRole('button', { name: 'Добавить товар', exact: true }).click();
  const cardResponse = await created;
  expect(cardResponse.status()).toBe(201);
  const card = (await cardResponse.json()).data;
  expect(card.stock).toBe(0);
  await expect(manual).not.toBeVisible();
  await page.waitForLoadState('networkidle');
  await page.locator('#openWarehouseAddModal').click();
  await page.getByRole('menuitem', { name: 'Добавить из Bitrix' }).click();
  await page.locator('#bitrixProductSearch').fill('90103');
  await page
    .locator('#bitrixProductResults')
    .getByRole('button', { name: /Casio smoke catalog/ })
    .click();
  await page.locator('#bitrixImportQuantity').fill('2');
  await expect(page.locator('#bitrixImportProduct')).toBeVisible();
  const imported = page.waitForResponse((r) => r.url().endsWith('/90103/import'));
  await page.locator('#bitrixImportProduct').click();
  expect((await imported).status()).toBe(201);
  await page.waitForLoadState('networkidle');

  await page.goto('/app/receipts?tab=supplies');
  await page.locator('#new-supply').click();
  await expect(page.locator('#add-item')).toBeVisible();
  await page.locator('#title').fill('Smoke manual supply');
  await page.locator('#comment').fill('Isolated browser fixture only');
  await page.locator('#add-item').click();
  await page.locator('#supply-product-search').fill('SMOKE-MANUAL-CATALOG');
  await page.locator('#supply-product-results .picker-result').first().click();
  await page.locator('#add-quantity').fill('3');
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#items tbody tr')).toHaveCount(1);

  await page.locator('#add-item').click();
  await page.locator('#supply-product-source').selectOption('bitrix');
  await page.locator('#supply-product-search').fill('90104');
  await page
    .locator('#supply-product-results')
    .getByRole('button', { name: /Casio smoke supply/ })
    .click();
  await page.locator('#add-quantity').fill('4');
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#items tbody tr')).toHaveCount(2);

  await page.locator('#add-item').click();
  await page.locator('#create-manual-supply-product').click();
  await chooseCatalogValue(page, 'manualProductBrandCombobox', 'Casio');
  await chooseCatalogValue(page, 'manualProductCategoryCombobox', 'Часы');
  await chooseCatalogValue(page, 'manualProductCombobox', 'Smoke manual inline');
  await manual.locator('[name=article]').fill('SMOKE-MANUAL-INLINE');
  const inlineCreated = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/products') && r.request().method() === 'POST',
  );
  await manual.getByRole('button', { name: 'Добавить товар', exact: true }).click();
  const inlineResponse = await inlineCreated;
  expect(inlineResponse.status()).toBe(201);
  const inline = (await inlineResponse.json()).data;
  await expect(page.locator('#selected-product')).toContainText('Smoke manual inline');
  await expect(page).toHaveURL(/app\/receipts/);
  await page.locator('#add-quantity').fill('5');
  await page.locator('#confirm-add-item').click();
  await expect(page.locator('#items tbody tr')).toHaveCount(3);
  await page.locator('[data-quantity="2"]').fill('6');
  const stock = async (id: number) =>
    (await (await request.get('/api/v1/products/' + id)).json()).data.stock;
  expect(await stock(card.id)).toBe(0);
  expect(await stock(inline.id)).toBe(0);
  const draft = (await (await request.get('/api/v1/receipts/supplies')).json()).data.find(
    (s: { title: string }) => s.title === 'Smoke manual supply',
  );
  expect(draft.status).toBe('draft');
  const bitrixCardId = draft.items.find(
    (i: { article: string }) => i.article === 'SUP-90104',
  ).product_id;
  expect(await stock(bitrixCardId)).toBe(0);
  await page.screenshot({ path: '/tmp/manual-products-before-post.png', fullPage: true });
  await page.locator('#post-supply').click();
  await expect(page.locator('#dialog-message')).toContainText('Поставка проведена');
  expect(await stock(card.id)).toBe(3);
  expect(await stock(inline.id)).toBe(6);
  expect(await stock(bitrixCardId)).toBe(4);
  await page.locator('#close-supply').click();
  await page.locator('#new-supply').click();
  await page.locator('#title').fill('Responsive smoke');
  await page.locator('#add-item').click();
  await page.locator('#create-manual-supply-product').click();
  await page.setViewportSize({ width: 390, height: 844 });
  const box = (await manual.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(391);
  await page.screenshot({ path: '/tmp/manual-products-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});
