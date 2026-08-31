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

test('calendar mode round-trips URL, navigation, week and existing drawer', async ({ page }) => {
  await page.goto('/app/tasks?view=plans&scope=all', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Календарь', exact: true }).click();
  await expect(page).toHaveURL(/view=plans.*mode=calendar/);
  await expect(page.locator('#calendarGrid')).toBeVisible();
  await expect(page.locator('.calendar-task')).not.toHaveCount(0);
  const title = await page.locator('#calendarTitle').textContent();
  await page.locator('#calendarNext').click();
  await expect(page.locator('#calendarTitle')).not.toHaveText(title || '');
  await page.locator('#calendarToday').click();
  await page.getByRole('button', { name: 'Неделя' }).click();
  await expect(page).toHaveURL(/calendar=week/);
  await expect(page.locator('.calendar-week-day')).toHaveCount(7);
  await page.locator('.calendar-task').first().click();
  await expect(page.locator('#taskModal')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Список', exact: true }).click();
  await expect(page).toHaveURL(/view=plans.*mode=list/);
});

test('calendar creates with a prefilled day and shows completed and undated tasks', async ({ page }) => {
  await page.goto('/app/tasks?mode=calendar&calendar=month', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#undatedCount')).not.toHaveText('0');
  const day = page.locator('.calendar-day:not(.outside)').first();
  await day.click({ position: { x: 10, y: 10 } });
  await expect(page.locator('#taskModal')).toBeVisible();
  await expect(page.locator('#taskForm input[name="due_date"]')).not.toHaveValue('');
  await page.locator('#closeTask').click();
  await expect(page.locator('#taskModal')).toBeHidden();
  const before = await page.locator('.calendar-task.completed').count();
  await page.locator('#calendarCompleted').check();
  await expect.poll(() => page.locator('.calendar-task.completed').count()).toBeGreaterThan(before);
});

test('calendar drag saves and failed drag rolls back', async ({ page }) => {
  await page.goto('/app/tasks?mode=calendar&calendar=month', { waitUntil: 'domcontentloaded' });
  const source = page.locator('#calendarGrid .calendar-task[draggable="true"]').first();
  const sourceId = await source.getAttribute('data-task-id');
  let scheduleRequests = 0;
  await page.route(`**/api/v1/tasks/${sourceId}/calendar-reschedule`, (route) => {
    scheduleRequests += 1;
    return route.fulfill({
      status: scheduleRequests === 1 ? 200 : 409,
      contentType: 'application/json',
      body: JSON.stringify(scheduleRequests === 1 ? { data: {} } : { message: 'Проверка отката' }),
    });
  });
  const dragToDay = (targetIndex: number) => page.evaluate(({ taskId, index }) => {
    const sourceNode = document.querySelector(`.calendar-task[data-task-id="${taskId}"]`);
    const targetNode = document.querySelectorAll('.calendar-day:not(.outside)')[index];
    if (!sourceNode || !targetNode) throw new Error('Drag fixture is unavailable');
    const transfer = new DataTransfer();
    sourceNode.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: transfer }));
    targetNode.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    targetNode.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    sourceNode.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: transfer }));
  }, { taskId: sourceId, index: targetIndex });
  await dragToDay(10);
  await expect.poll(() => scheduleRequests).toBe(1);
  await expect(page.locator('#taskModal')).toBeHidden();
  await dragToDay(12);
  await expect.poll(() => scheduleRequests).toBe(2);
  await expect(page.locator('#calendarStatus')).toContainText(/задач/);
  await expect(page.locator(`.calendar-task[data-task-id="${sourceId}"]`)).toHaveCount(1);
});

test('calendar remains readable and keyboard reachable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app/tasks?mode=calendar&calendar=month', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.calendar-day.outside').first()).toBeHidden();
  await page.locator('.calendar-day:not(.outside)').first().focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#taskModal')).toBeVisible();
  const dimensions = await page.locator('html').evaluate((root) => ({
    scrollWidth: root.scrollWidth,
    clientWidth: root.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test('month calendar positions the current week inside its own scroll area', async ({ page }) => {
  await page.goto('/app/tasks?mode=calendar&calendar=month', { waitUntil: 'domcontentloaded' });
  const scroll = page.locator('.calendar-month-scroll');
  const currentWeek = page.locator('.calendar-month-week:has(.calendar-day.is-today)');
  await expect(currentWeek).toHaveCount(1);
  await expect(currentWeek.locator('.calendar-day.is-today')).toHaveCount(1);
  await expect(currentWeek.locator('xpath=following-sibling::*[1]')).toHaveCount(1);
  const position = await scroll.evaluate((node) => {
    const week = node.querySelector(
      '.calendar-month-week:has(.calendar-day.is-today)',
    ) as HTMLElement;
    return {
      scrollTop: node.scrollTop,
      targetTop: week.offsetTop - (node as HTMLElement).offsetTop,
      pageY: window.scrollY,
    };
  });
  expect(Math.abs(position.scrollTop - position.targetTop)).toBeLessThanOrEqual(2);
  expect(position.pageY).toBe(0);
  await expect(page.locator('.calendar-toolbar')).toBeInViewport();
  await expect(page.locator('.calendar-weekdays')).toBeInViewport();

  await page.locator('#calendarNext').click();
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBe(0);
  await page.locator('#calendarToday').click();
  await expect.poll(() => scroll.evaluate((node) => {
    const week = node.querySelector(
      '.calendar-month-week:has(.calendar-day.is-today)',
    ) as HTMLElement;
    return Math.abs(
      node.scrollTop - (week.offsetTop - (node as HTMLElement).offsetTop),
    );
  })).toBeLessThanOrEqual(2);

  await scroll.focus();
  await expect(scroll).toBeFocused();
  await page.getByRole('button', { name: 'Неделя' }).click();
  await expect(page.locator('.calendar-month-scroll')).toHaveCount(0);
  await expect(page.locator('.calendar-week-day')).toHaveCount(7);
});

test('month calendar keeps four, five and six-row months and crosses a year boundary', async ({
  page,
}) => {
  for (const [date, rows] of [
    ['2027-02-15', 4],
    ['2026-02-15', 5],
    ['2026-11-15', 6],
  ] as const) {
    await page.goto(`/app/tasks?mode=calendar&calendar=month&date=${date}`, {
      waitUntil: 'domcontentloaded',
    });
    await expect(page.locator('.calendar-month-week')).toHaveCount(rows);
    await expect(page.locator('.calendar-month-scroll')).toHaveJSProperty('scrollTop', 0);
  }
  await page.goto('/app/tasks?mode=calendar&calendar=month&date=2027-01-15', {
    waitUntil: 'domcontentloaded',
  });
  await page.locator('#calendarPrev').click();
  await expect(page.locator('#calendarTitle')).toContainText('декабрь 2026');
});

test('queue badges omit zeroes and journal while exposing accessible labels', async ({ page }) => {
  await page.goto('/app/tasks?view=today&scope=mine', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-view="today"] [data-view-count]')).toHaveAttribute(
    'aria-label',
    /незавершённые задачи/,
  );
  await expect(page.locator('[data-view="someday"] [data-view-count]')).toBeHidden();
  await expect(page.locator('[data-view="logbook"] [data-view-count]')).toHaveCount(0);
  await expect(page.locator('#taskSectionStats')).toContainText(/осталось · \d+ выполнено из \d+/);
  const before = await page.locator('[data-view="today"] [data-view-count]').textContent();
  await page.locator('#taskSearch').fill('Подтвердить');
  await expect(page.locator('[data-view="today"] [data-view-count]')).toHaveText(before || '');
});
