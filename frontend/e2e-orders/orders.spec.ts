import { expect, test } from '@playwright/test';

test('sources, counts and scoped searches', async ({ page }) => {
  await page.goto('/app/orders');
  await expect(page.locator('[data-source-filter="all"]')).toHaveText('Все заказы 128');
  await expect(page.locator('[data-source-filter="tictactoy"]')).toHaveText('TicTacToy 3');
  await expect(page.locator('[data-source-filter="wildberries"]')).toHaveText('Wildberries 125');
  await page.locator('[data-source-filter="wildberries"]').click();
  await expect(page.locator('[data-status-filter="N"]')).toHaveCount(0);
  await page.locator('[data-status-filter="WB_SOLD"]').click();
  await expect(page).toHaveURL(/status=WB_SOLD/);
  await expect(page.locator('.orders-split-table tbody tr').first()).toContainText('Получен покупателем');
  await page.locator('[data-source-filter="tictactoy"]').click();
  await expect(page.locator('.orders-split-table tbody tr')).toHaveCount(3);
  await page.locator('#orderSearch').fill('WATCH');
  await expect(page.locator('.erp-no-results-state')).toBeVisible();
  await expect(page).toHaveURL(/q=WATCH/);
  await page.locator('[data-source-filter="wildberries"]').click();
  await expect(page.locator('#orderSearch')).toHaveValue('WATCH');
  await expect(page.locator('.orders-split-table tbody tr')).toHaveCount(50);
});

test('distant pages, card selection and scroll survive back', async ({ page }) => {
  await page.goto('/app/orders?source=wildberries&page_size=20&page=4');
  await expect(page.locator('.list-footer')).toContainText('Показано 61–80 из 125');
  await page.locator('.orders-split-table-scroll').evaluate(el => { el.scrollTop = 420; });
  const number = await page.locator('.orders-split-table .order-number').nth(4).textContent();
  await page.locator('.orders-split-table .order-number').nth(4).click();
  await expect(page.locator('.card-title h2')).toContainText(number!.trim());
  await expect(page).toHaveURL(/page=4/);
  expect(await page.locator('.orders-split-table-scroll').evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await page.goBack();
  await expect(page.locator('.list-footer')).toContainText('Показано 61–80 из 125');
  expect(await page.locator('.orders-split-table-scroll').evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await page.locator('[aria-label="Страница 7"]').click();
  await expect(page.locator('.list-footer')).toContainText('Показано 121–125 из 125');
});

test('WB products, sale action, tasks, history and source-safe selection', async ({ page }) => {
  await page.goto('/order/wildberries/9001?source=wildberries');
  await expect(page.locator('.order-control-actions').getByRole('link', { name: 'Открыть продажу', exact: true })).toBeVisible();
  await expect(page.locator('[data-open-sale-dialog]')).toHaveCount(0);
  await expect(page.locator('.order-products img')).toBeVisible();
  await expect(page.locator('.order-wb-history')).toContainText('Заказ восстановлен из Wildberries');
  await expect(page.locator('[data-entity-tasks]')).toBeHidden();
  await expect(page.locator('.order-control-actions').getByRole('link', { name: '+ Создать задачу', exact: true })).toBeVisible();
  await expect(page.getByText('Только чтение', { exact: true })).toHaveCount(0);
  await expect(page.locator('.order-technical-fields')).toBeHidden();
  await page.locator('.order-technical summary').click();
  await expect(page.locator('.order-technical-fields')).toContainText('supplier_status');
  await page.locator('[data-source-filter="tictactoy"]').click();
  await expect(page.locator('.card-title h2')).toHaveCount(0);
});

test('diagnostics retain errors, import and sync', async ({ page }) => {
  await page.goto('/app/orders?source=wildberries');
  await expect(page.locator('[data-wb-health]')).toContainText('Требует внимания');
  await expect(page.locator('[data-wb-preview-form]')).toBeHidden();
  await page.locator('[data-wb-recovery] > summary').click();
  await expect(page.getByRole('heading', { name: 'Диагностика Wildberries' })).toBeVisible();
  await expect(page.locator('[data-wb-missing]')).toContainText('Тестовая ошибка API');
  await expect(page.getByRole('button', { name: 'Обновить WB' })).toBeVisible();
  await page.route('**/api/orders/wildberries/recovery/preview', route => route.fulfill({json:{ok:true,report:{rows:[],wb_count:1,importable:1,counts:{READY:1},errors:[],confirmation:'fixture-only'}}}));
  await page.locator('[name="supply_id"]').fill('WB-GI-TEST');
  await page.getByRole('button', { name: 'Проверить поставку' }).click();
  await expect(page.locator('[data-wb-import]')).toBeVisible();
});

test('responsive layout and explicit units', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/order/wildberries/9001?source=wildberries');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('.orders-split-table .order-units').first()).toHaveText('1 шт.');
    await page.screenshot({path:`/tmp/orders-progressive-${width}.png`,fullPage:true});
  }
  expect(errors).toEqual([]);
});

test('sale submit uses existing endpoint and prevents repeated submit', async ({ page }) => {
  let submits = 0;
  await page.route('**/order/wildberries/9003/conduct-sale', async route => {
    submits += 1;
    await route.fulfill({json:{ok:true,sale_id:'fixture-sale',message:'Продажа проведена'}});
  });
  await page.goto('/order/wildberries/9003?source=wildberries');
  await page.locator('[data-open-sale-dialog]').click();
  await page.locator('[data-order-sale-form] button[type="submit"]').click();
  await expect(page.locator('.order-control-actions').getByRole('link', {name:'Открыть продажу',exact:true})).toHaveAttribute('href','/sales?source=wildberries&sale_id=fixture-sale');
  await expect(page.locator('[data-open-sale-dialog]')).toHaveCount(0);
  expect(submits).toBe(1);
});

test('card selection keeps the list DOM and uses one detail request', async ({ page }) => {
  await page.goto('/app/orders?source=wildberries');
  await page.locator('.orders-split-table-scroll').evaluate(el => {
    el.scrollTop = 350;
    el.setAttribute('data-preserved-list', 'yes');
  });
  const requests: string[] = [];
  page.on('request', request => requests.push(request.url()));
  const firstNumber = (await page.locator('.orders-split-table .order-number').nth(4).textContent())!.trim();
  const secondNumber = (await page.locator('.orders-split-table .order-number').nth(5).textContent())!.trim();
  await page.locator('.orders-split-table .order-number').nth(4).click();
  await expect(page.locator('.card-title h2')).toBeVisible();
  await expect(page.locator('[data-preserved-list]')).toHaveCount(1);
  expect(await page.locator('[data-preserved-list]').evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  expect(requests.filter(url => /\/order\/wildberries\//.test(url))).toHaveLength(1);
  expect(requests.filter(url => /\/api\/orders(?:\?|$)|\/static\//.test(url))).toHaveLength(0);
  await page.locator('.orders-split-table .order-number').nth(5).click();
  await expect(page.locator('.card-title h2')).toContainText(secondNumber);
  await page.goBack();
  await expect(page.locator('.card-title h2')).toContainText(firstNumber);
  await expect(page.locator('[data-preserved-list]')).toHaveCount(1);
});
