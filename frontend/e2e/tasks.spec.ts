import { expect, test } from '@playwright/test';

test('task filters stay collapsed, produce chips and round-trip through history', async ({ page }) => {
  await page.goto('/app/tasks?view=today', { waitUntil: 'domcontentloaded' });

  const filters = page.locator('#advancedFilters');
  const toggle = page.getByRole('button', { name: 'Фильтры' });
  await expect(filters).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  await toggle.click();
  await expect(filters).toBeVisible();
  await page.locator('#priorityFilter').selectOption('important');
  await expect(page).toHaveURL(/priority=important/);
  await expect(page.locator('.tasks-filter-chip')).toContainText('Приоритет: Важно');
  await expect(page.locator('.task-row')).toHaveCount(1);

  await page.goBack();
  await expect(page.locator('#priorityFilter')).toHaveValue('');
  await expect(page.locator('.task-row')).toHaveCount(3);
  await page.goForward();
  await expect(page.locator('#priorityFilter')).toHaveValue('important');
  await expect(filters).toBeVisible();
});

test('task views use the requested grouping hierarchy', async ({ page }) => {
  await page.goto('/app/tasks?view=today', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.tasks-group-title')).toHaveText([
    /Срочные\s*1/,
    /Важные\s*1/,
    /Остальные\s*1/,
  ]);

  await page.getByRole('button', { name: /Планы/ }).click();
  await expect(page).toHaveURL(/view=plans/);
  expect(await page.locator('.tasks-group-title').count()).toBeGreaterThan(0);

  await page.getByRole('button', { name: /Журнал/ }).click();
  await expect(page).toHaveURL(/view=logbook/);
  expect(await page.locator('.tasks-group-title').count()).toBeGreaterThan(0);
});

test('task completion control suppresses duplicate requests', async ({ page }) => {
  let completionRequests = 0;
  await page.route('**/api/v1/tasks/*/complete', async (route) => {
    completionRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 100));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: {} }),
    });
  });
  await page.goto('/app/tasks?view=today', { waitUntil: 'domcontentloaded' });
  const checkbox = page.locator('.task-check').first();
  await checkbox.evaluate((element) => {
    const input = element as HTMLInputElement;
    input.checked = true;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await expect.poll(() => completionRequests).toBe(1);
});

test('existing task card restores focus after Escape', async ({ page }) => {
  await page.goto('/app/tasks?view=today', { waitUntil: 'domcontentloaded' });
  const trigger = page.getByRole('button', {
    name: 'Открыть карточку «Подтвердить наличие часов для клиента»',
  });
  await trigger.click();
  await expect(page.locator('#taskModal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#taskModal')).toBeHidden();
  await expect(trigger).toBeFocused();
});

test('tasks page has no page-level overflow at required viewports', async ({ page }) => {
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/app/tasks?view=today', { waitUntil: 'domcontentloaded' });
    const dimensions = await page.locator('html').evaluate((root) => ({
      scrollWidth: root.scrollWidth,
      clientWidth: root.clientWidth,
      bodyScrollWidth: root.ownerDocument.body.scrollWidth,
      bodyClientWidth: root.ownerDocument.body.clientWidth,
    }));
    expect(dimensions.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(dimensions.clientWidth);
    expect(dimensions.bodyScrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(dimensions.bodyClientWidth);
  }
});
