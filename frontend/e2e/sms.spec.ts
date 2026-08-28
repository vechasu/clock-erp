import { expect, test } from '@playwright/test';

test('SMS center is responsive and never sends during smoke', async ({ page }) => {
  let sendRequests = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/sms/messages')) sendRequests += 1;
  });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/app/sms', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Центр SMS' })).toBeVisible();
    await expect(page.getByText('SmsBliss не настроен.', { exact: false })).toBeVisible();
    const overflow = await page.locator('html').evaluate((root: HTMLElement) => root.scrollWidth - root.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  }
  expect(sendRequests).toBe(0);
});
