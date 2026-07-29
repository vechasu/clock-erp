import { expect, test } from '@playwright/test';

test('React entry point opens the unified ERP workspace', async ({ page }) => {
  await page.goto('/app/');

  await expect(page).toHaveTitle('Vechasu ERP');
  await expect(page.getByRole('heading', { name: 'Товары' })).toBeVisible();
  await expect(page).toHaveURL(/\/app\/products$/);
});

test('unknown React routes keep a controlled empty state', async ({ page }) => {
  await page.goto('/app/unknown');

  await expect(page.getByRole('heading', { name: 'Раздел ещё не перенесён' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Вернуться к инфраструктуре' })).toHaveAttribute(
    'href',
    '/app',
  );
});
