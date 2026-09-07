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
  await expect(submit).toHaveText('Передать мастеру');
  await page.locator('[data-repair-action-form] summary').click();
  await page.locator('[name="action"]').selectOption('receive');
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
  await expect(form.locator('[name="waiting_for"]')).toBeHidden();
  await expect(form.locator('[name="next_action"]')).toBeHidden();
  await expect(form.locator('[name="control_date"]')).toBeHidden();
  await expect(form.locator('[name="external_condition"]')).toBeHidden();
  await form.locator('[name="client_name"]').fill('Новый тестовый клиент');
  await form.locator('[name="contact"]').fill('@repair_test');
  await form.locator('[name="model"]').fill('Тестовые часы');
  await form.locator('[name="problem"]').fill('Не идут');
  await form.locator('[name="location"]').selectOption('at_us');
  await page.locator('#repairDrawerFooter button[type="submit"]').click();
  await expect(page.locator('.repair-next-step button[type="submit"]')).toHaveText(
    'Передать мастеру',
  );
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

test('default presentation and short create at desktop and narrow widths', async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      'vechasu:repair-columns-v2',
      JSON.stringify({ channel: true, control: true, event: true }),
    ),
  );
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 768 });
    await page.goto('/app/repairs');
    if (width === 1440) {
      await expect(page.locator('.repair-table th:visible')).toHaveText([
        'Статус',
        'Клиент / заказ',
        'Товар',
        'Проблема',
        'Сейчас',
        'Что делать',
      ]);
    }
    await expect(page.locator('.repair-queue')).toContainText('Нужно действие');
    await expect(page.locator('.repair-table [data-ux="review"]')).toBeVisible();
    await page.locator('#repairAdd').click();
    await expect(page.locator('#repairEditor [name="location"]')).toBeVisible();
    await expect(page.locator('#repairDrawerFooter button[type="submit"]')).toHaveText(
      'Создать ремонт',
    );
    expect(
      await page.locator('#repairDrawer').evaluate((el) => el.scrollWidth - el.clientWidth),
    ).toBeLessThanOrEqual(1);
    if (width === 1440) {
      expect(
        await page.locator('#repairDrawerBody').evaluate((el) => el.scrollHeight - el.clientHeight),
      ).toBeLessThanOrEqual(1);
    }
    await expect(page.locator('.repair-create-extra')).not.toHaveAttribute('open', '');
    await page.locator('#repairEditor [name="has_order"][value="yes"]').check();
    await expect(page.locator('#repairEditor [name="client_name"]')).toBeHidden();
    await expect(page.locator('#repairEditor [name="contact"]')).toBeHidden();
    await expect(page.locator('[data-order-search]')).toBeVisible();
    if (width === 1440) {
      expect(
        await page.locator('#repairDrawerBody').evaluate((el) => el.scrollHeight - el.clientHeight),
      ).toBeLessThanOrEqual(1);
    }

    await page.locator('#repairDrawerClose').click();
  }
});

test('legacy item at us hands over in one click and retains both history transitions', async ({
  page,
  request,
}) => {
  const response = await request.post('/api/v1/repairs', {
    data: {
      client_name: 'Legacy fixture',
      contact: '@fixture',
      product_name: 'Legacy watch',
      problem: 'Stopped',
      location: 'at_us',
      next_action: 'Передать мастеру',
      waiting_for: 'us',
      control_date: '2026-10-01',
    },
  });
  expect(response.status()).toBe(201);
  const id = (await response.json()).data.id;
  await page.goto(`/app/repairs?repair_id=${id}`);
  await page.locator('.repair-next-step button[type="submit"]').click();
  await expect(page.locator('.repair-next-step button[type="submit"]')).toHaveText(
    'Получить результат диагностики',
  );
  await page.reload();
  await expect(page.locator('.repair-next-step')).toContainText('Ждём мастера');
  const saved = (await (await request.get(`/api/v1/repairs/${id}`)).json()).data;
  expect(saved.status).toBe('diagnostics');
  expect(saved.location).toBe('with_master');
  expect(saved.history.filter((event: { field: string }) => event.field === 'status')).toHaveLength(
    2,
  );
});

test('create from our order uses its customer and exact product without duplicate inputs', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 768 });
  await page.goto('/app/repairs');
  await page.locator('#repairAdd').click();
  await page.locator('#repairEditor [name="has_order"][value="yes"]').check();
  await page.locator('[data-order-search]').fill('7002');
  await page.locator('[data-order-results] button').first().click();
  await expect(page.locator('#repairEditor [name="model"]')).toBeHidden();
  await expect(page.locator('#repairEditor [name="contact"]')).toBeHidden();
  await page.locator('#repairEditor [name="problem"]').fill('Отстают');
  await page.locator('#repairDrawerFooter button[type="submit"]').click();
  await expect(page.locator('.repair-next-step button[type="submit"]')).toHaveText(
    'Передать мастеру',
  );
  const id = new URL(page.url()).searchParams.get('repair_id');
  const saved = (await (await request.get(`/api/v1/repairs/${id}`)).json()).data;
  expect(saved.order_id).toBe('7002');
  expect(saved.order_item_id).toBeTruthy();
  expect(saved.product_name).toContain('GA-2100');
  expect(saved.contact).toContain('444');
});
