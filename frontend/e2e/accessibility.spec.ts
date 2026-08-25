import AxeBuilder from '@axe-core/playwright';
import { expect, test as base } from '@playwright/test';

const test = base.extend<{ failOnConsoleErrors: void }>({
  failOnConsoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text());
      });
      page.on('pageerror', (error) => errors.push(error.message));

      await use();

      expect(errors).toEqual([]);
    },
    { auto: true },
  ],
});

const routes = [
  '/app/products',
  '/app/sales',
  '/app/receipts',
  '/app/orders',
  '/app/repairs',
  '/app/journal',
  '/app/settings',
  '/app/products/inventory',
  '/login',
  '/register',
];

for (const route of routes) {
  test(`${route} has no serious or critical axe violations`, async ({ page }) => {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === 'serious' || impact === 'critical',
    );
    expect(
      blocking.map(({ id, impact, nodes }) => ({
        id,
        impact,
        targets: nodes.slice(0, 5).map(({ target }) => target),
      })),
    ).toEqual([]);
  });
}

test('skip link is first, visible on focus, and targets main', async ({ page }) => {
  await page.goto('/app/products', { waitUntil: 'domcontentloaded' });
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Перейти к основному содержимому' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.locator('main#main-content')).toBeFocused();
});

test('core pages expose one main landmark and one h1', async ({ page }) => {
  for (const route of routes) {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('main')).toHaveCount(1);
    await expect(page.locator('h1')).toHaveCount(1);
  }
});

test('product modal traps focus, closes on Escape, and restores trigger', async ({ page }) => {
  await page.goto('/app/products', { waitUntil: 'domcontentloaded' });
  const trigger = page.locator('#openWarehouseAddModal');
  await trigger.focus();
  await trigger.press('Enter');
  const modal = page.locator('#warehouseAddModal');
  await expect(modal).toHaveAttribute('aria-hidden', 'false');
  await expect(modal.locator(':focus')).toHaveCount(1);

  const focusable = modal.locator(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  const count = await focusable.count();
  expect(count).toBeGreaterThan(1);
  await focusable.nth(count - 1).focus();
  await page.keyboard.press('Tab');
  await expect(focusable.nth(0)).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(modal).toHaveAttribute('aria-hidden', 'true');
  await expect(trigger).toBeFocused();
});

test('tables expose names, scoped headers and sortable state', async ({ page }) => {
  for (const route of ['/app/products', '/app/sales', '/app/receipts']) {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    const tables = page.locator('table');
    expect(await tables.count()).toBeGreaterThan(0);
    await expect(tables.first()).toHaveAttribute('aria-label', /таблица/i);
    const unscoped = page.locator('table thead th:not([scope="col"])');
    await expect(unscoped).toHaveCount(0);
  }
  await page.goto('/app/receipts', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('th[aria-sort]')).not.toHaveCount(0);
});

test('dynamic announcements are atomic live regions without focus capture', async ({ page }) => {
  await page.goto('/register', { waitUntil: 'domcontentloaded' });
  const states = page.locator('[role="status"], [role="alert"]');
  const count = await states.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expect(states.nth(index)).toHaveAttribute('aria-atomic', 'true');
  }
  await expect(page.locator('[role="status"][tabindex="0"]')).toHaveCount(0);
});

test('key pages reflow at required widths and 200/400 percent zoom', async ({ page }) => {
  for (const width of [1440, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ['/app/products', '/app/sales', '/login', '/register']) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      const overflow = await page
        .locator('html')
        .evaluate(
          (root: { scrollWidth: number; clientWidth: number }) =>
            root.scrollWidth - root.clientWidth,
        );
      expect(overflow, `${route} at ${width}px`).toBeLessThanOrEqual(1);
    }
  }

  for (const zoom of [2, 4]) {
    await page.setViewportSize({ width: Math.floor(1440 / zoom), height: 900 });
    for (const route of ['/app/products', '/login']) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      const overflow = await page
        .locator('html')
        .evaluate(
          (root: { scrollWidth: number; clientWidth: number }) =>
            root.scrollWidth - root.clientWidth,
        );
      expect(overflow, `${route} at ${zoom * 100}%`).toBeLessThanOrEqual(1);
    }
  }
});

test('/app/products keeps page-level overflow at zero at required viewports', async ({ page }) => {
  for (const viewport of [
    { width: 320, height: 568 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 768, height: 1024 },
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/app/products', { waitUntil: 'domcontentloaded' });

    const dimensions = await page
      .locator('html')
      .evaluate(
        (root: {
          scrollWidth: number;
          clientWidth: number;
          ownerDocument: { body: { scrollWidth: number; clientWidth: number } };
        }) => ({
          html: {
            scrollWidth: root.scrollWidth,
            clientWidth: root.clientWidth,
          },
          body: {
            scrollWidth: root.ownerDocument.body.scrollWidth,
            clientWidth: root.ownerDocument.body.clientWidth,
          },
        }),
      );

    expect(dimensions.html.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(
      dimensions.html.clientWidth,
    );
    expect(dimensions.body.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(
      dimensions.body.clientWidth,
    );
  }
});
