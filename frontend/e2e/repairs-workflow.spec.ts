import { expect, test } from '@playwright/test';

test('queue, legacy safety, archive and responsive cards', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/app/repairs');
    await expect(page.locator('.repair-table thead [data-column="channel"]')).toBeHidden();
    expect(
      await page.locator('html').evaluate((root) => root.scrollWidth - root.clientWidth),
    ).toBeLessThanOrEqual(1);
    await page.locator('.repair-table [data-open-repair="ux-1"]').click();
    await expect(page.locator('.repair-next-step button[type="submit"]')).toHaveText(
      'Передать мастеру',
    );
    await expect(page.locator('.repair-history')).toBeHidden();
    expect(
      await page.locator('#repairDrawer').evaluate((root) => root.scrollWidth - root.clientWidth),
    ).toBeLessThanOrEqual(1);
    await page.locator('#repairDrawerClose').click();
  }
  await page.goto('/app/repairs?queue=review');
  await expect(page.locator('.repair-table tbody tr')).toHaveCount(1);
  await page.locator('.repair-table [data-open-repair="ux-5"]').click();
  await expect(page.locator('[data-review-repair]')).toBeVisible();
  await expect(page.locator('[data-repair-action-form]')).toHaveCount(0);
  const rejected = await request.post('/api/v1/repairs/ux-5/actions/receive', {
    data: { guided: true, control_date: '2026-10-01' },
  });
  expect(rejected.status()).toBe(409);
  await page.goto('/app/repairs?view=archive');
  await expect(page.locator('.repair-table tbody tr')).toHaveCount(2);
  await page.locator('.repair-table [data-open-repair="ux-7"]').click();
  await expect(page.locator('[data-repair-action-form]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('guided repair persists through refresh and history, free repair and pickup', async ({
  page,
  request,
}) => {
  await page.goto('/app/repairs?repair_id=ux-0');
  const submit = page.locator('[data-repair-action-form] button[type="submit"]');
  await submit.click();
  await expect(submit).toHaveText('Передать мастеру');
  await page.reload();
  await expect(submit).toHaveText('Передать мастеру');
  await submit.click();
  await expect(submit).toHaveText('Получить результат диагностики');
  await page.locator('[name="diagnostic_result"]').fill('Заменить механизм');
  await page.locator('[name="proposed_solution"]').fill('Гарантийная замена');
  await submit.click();
  await expect(submit).toHaveText('Зафиксировать решение клиента');
  await page.locator('[name="action"]').selectOption('accept_free');
  await page.locator('[name="customer_decision"]').fill('Согласовано');
  await submit.click();
  await expect(submit).toHaveText('Получили готовый товар');
  await submit.click();
  await expect(submit).toHaveText('Оформить возврат клиенту');
  await page.locator('[name="action"]').selectOption('complete');
  await page.locator('[name="completion_result"]').selectOption('repaired');
  await submit.click();
  await expect(page.locator('.repair-next-step')).toContainText('Ремонт завершён');
  await page.reload();
  await expect(page.locator('.repair-next-step')).toContainText('Ремонт завершён');
  const data = (await (await request.get('/api/v1/repairs/ux-0')).json()).data;
  expect(data.status).toBe('completed');
  expect(data.location).toBe('delivered');
  expect(data.return_method).toBe('pickup');
  expect(data.history.some((event: { field: string }) => event.field === 'status')).toBeTruthy();
  await page.goto('/app/repairs');
  await expect(page.locator('.repair-table [data-open-repair="ux-0"]')).toHaveCount(0);
});

test('create without order and edit preserve the customer and product', async ({
  page,
  request,
}) => {
  await page.goto('/app/repairs');
  await page.locator('#repairAdd').click();
  const form = page.locator('#repairEditor');
  await form.locator('[name="client_name"]').fill('Новый тестовый клиент');
  await form.locator('[name="contact"]').fill('@repair_test');
  await form.locator('[name="model"]').fill('Тестовые часы');
  await form.locator('[name="problem"]').fill('Не идут');
  await form.locator('[name="location"]').selectOption('at_us');
  await page.locator('#repairDrawerFooter button[type="submit"]').click();
  await expect(page.locator('.repair-next-step button[type="submit"]')).toHaveText('Принять товар');
  const id = new URL(page.url()).searchParams.get('repair_id');
  expect(id).toBeTruthy();
  await page.locator('.repair-footer-more > summary').click();
  await page.locator('[data-edit-repair]').click();
  await form.locator('[name="problem"]').fill('Не идут после падения');
  await page.locator('#repairDrawerFooter button[type="submit"]').click();
  await expect(page.locator('.repair-detail-summary')).toContainText('Не идут после падения');
  await page.reload();
  await expect(page.locator('.repair-detail-summary')).toContainText('Не идут после падения');
  const data = (await (await request.get(`/api/v1/repairs/${id}`)).json()).data;
  expect(data.client_name).toBe('Новый тестовый клиент');
  expect(data.contact).toBe('@repair_test');
  expect(data.product_name).toBe('Тестовые часы');
  expect(data.order_source).toBe('none');
});
