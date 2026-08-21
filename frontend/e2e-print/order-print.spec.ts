import { expect, test } from '@playwright/test';

test('shows a one-item A4 preview and prints only on an explicit click', async ({ page }) => {
  let printCalls = 0;
  await page.addInitScript(() => {
    window.print = () => {
      document.documentElement.dataset.printCalled = 'yes';
    };
  });
  await page.goto('/app/orders/18593/print');

  await expect(page.getByRole('heading', { name: 'Заказ №18593' })).toBeVisible();
  await expect(page.locator('.items tbody tr')).toHaveCount(1);
  await expect(page.locator('html')).not.toHaveAttribute('data-print-called', 'yes');
  await page.getByRole('button', { name: 'Печать' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-print-called', 'yes');

  await page.emulateMedia({ media: 'print' });
  await expect(page.locator('.screen-actions')).toBeHidden();
  printCalls = await page
    .locator('html')
    .getAttribute('data-print-called')
    .then((value) => (value === 'yes' ? 1 : 0));
  expect(printCalls).toBe(1);
});

test('renders long multi-page content as an A4 portrait PDF', async ({ page }) => {
  await page.goto('/app/orders/18593/print?case=many');

  await expect(page.locator('.items tbody tr')).toHaveCount(64);
  await expect(page.getByText('Позиция 64:', { exact: false })).toBeVisible();
  const pdf = await page.pdf({
    format: 'A4',
    printBackground: true,
    preferCSSPageSize: true,
  });
  const pdfSource = pdf.toString('latin1');
  const pageObjects = pdfSource.match(/\/Type\s*\/Page\b/g) ?? [];

  expect(pdf.subarray(0, 4).toString()).toBe('%PDF');
  expect(pdf.byteLength).toBeGreaterThan(25_000);
  expect(pageObjects.length).toBeGreaterThanOrEqual(2);
});
