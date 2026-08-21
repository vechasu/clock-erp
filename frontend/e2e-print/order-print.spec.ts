import { expect, test } from '@playwright/test';

const cases = [
  { key: 'one', rows: 1 },
  { key: 'multiple', rows: 4 },
  { key: 'long', rows: 3 },
  { key: 'missing', rows: 2 },
] as const;

for (const scenario of cases) {
  test(`${scenario.key}: landscape reference layout`, async ({ page }, testInfo) => {
    await page.goto(`/app/orders/18593/print?case=${scenario.key}`);
    await expect(page.locator('.items-body tr')).toHaveCount(scenario.rows);
    await expect(page.locator('.brand')).toBeVisible();
    await expect(page.locator('.qr-code')).toBeVisible();
    await expect(page.locator('.social-icon')).toHaveCount(3);
    await expect(page.locator('button, nav, .app-shell')).toHaveCount(0);

    const layout = await page.evaluate(() => {
      const box = (selector: string) => {
        const rect = document.querySelector(selector)!.getBoundingClientRect();
        return {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
        };
      };
      const overflowing = [...document.querySelectorAll('.line, .name, .amount-words')].some(
        (element) => element.scrollWidth > element.clientWidth + 1,
      );
      return {
        sheet: box('.sheet'),
        logo: box('.brand'),
        order: box('.order'),
        buyer: box('.buyer'),
        address: box('.address'),
        shipping: box('.shipping'),
        table: box('.items'),
        footer: box('.document-footer'),
        overflowing,
      };
    });

    expect(
      Math.abs(
        (layout.logo.left + layout.logo.right) / 2 - (layout.sheet.left + layout.sheet.right) / 2,
      ),
    ).toBeLessThan(2);
    expect(layout.order.left).toBeLessThan(layout.buyer.left);
    expect(layout.order.top).toBeLessThan(layout.address.top);
    expect(layout.buyer.top).toBeLessThan(layout.shipping.top);
    expect(layout.table.width).toBeGreaterThan(360);
    expect(layout.table.width).toBeLessThan(366);
    expect(layout.footer.bottom).toBeLessThanOrEqual(layout.sheet.bottom + 1);
    expect(layout.overflowing).toBe(false);

    if (scenario.key === 'missing') {
      await expect(page.locator('body')).not.toContainText('None');
      await expect(page.locator('body')).not.toContainText('null');
      await expect(page.locator('body')).not.toContainText('undefined');
      await expect(page.getByText('Индекс:', { exact: false })).toHaveCount(0);
    }

    await page.screenshot({ path: testInfo.outputPath(`${scenario.key}.png`), fullPage: true });
    await page.pdf({
      path: testInfo.outputPath(`${scenario.key}.pdf`),
      printBackground: true,
      preferCSSPageSize: true,
    });
  });
}

test('many: exactly two landscape A4 pages with repeated header and intact totals', async ({
  page,
}, testInfo) => {
  await page.goto('/app/orders/18593/print?case=many');
  await expect(page.locator('.items-body tr')).toHaveCount(32);
  await expect(page.getByText('Будильник BC03 Black, позиция 32', { exact: true })).toBeVisible();

  await page.screenshot({ path: testInfo.outputPath('many-screen.png'), fullPage: true });
  const pdf = await page.pdf({
    path: testInfo.outputPath('many.pdf'),
    printBackground: true,
    preferCSSPageSize: true,
  });
  const pdfSource = pdf.toString('latin1');
  const pageObjects = pdfSource.match(/\/Type\s*\/Page\b/g) ?? [];

  expect(pdf.subarray(0, 4).toString()).toBe('%PDF');
  expect(pdf.byteLength).toBeGreaterThan(25_000);
  expect(pageObjects).toHaveLength(2);

  await page.emulateMedia({ media: 'print' });
  const printRules = await page.evaluate(() => {
    const header = getComputedStyle(document.querySelector('.items thead')!).display;
    const summaryBreak = getComputedStyle(document.querySelector('.summary')!).breakInside;
    const firstRowBreak = getComputedStyle(document.querySelector('.items-body tr')!).breakInside;
    return { header, summaryBreak, firstRowBreak };
  });
  expect(printRules.header).toBe('table-header-group');
  expect(printRules.summaryBreak).toBe('avoid');
  expect(printRules.firstRowBreak).toBe('avoid');
});
