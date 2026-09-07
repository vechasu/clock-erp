import { expect, test, type Page, type Request, type Response } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type BrowserEvidence = {
  consoleErrors: string[];
  pageErrors: string[];
  failedInternalRequests: string[];
  documentRequests: string[];
  legacyTabRequests: string[];
  partialRequests: number;
};

const requiredViewports = [
  { width: 320, height: 568 },
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
];

async function expectVersionedProductsTabs(page: Page) {
  const scripts = page.locator('script[src*="products-tabs.js"]');
  await expect(scripts).toHaveCount(1);
  const source = await scripts.first().getAttribute('src');
  expect(source).not.toBeNull();
  const url = new URL(source!, page.url());
  expect(url.pathname).toBe('/static/js/products-tabs.js');
  expect(url.searchParams.get('v')).toMatch(/^[a-f0-9]{64}$/);
}

function observeBrowser(page: Page): BrowserEvidence {
  const evidence: BrowserEvidence = {
    consoleErrors: [],
    pageErrors: [],
    failedInternalRequests: [],
    documentRequests: [],
    legacyTabRequests: [],
    partialRequests: 0,
  };
  const internal = (request: Request) => new URL(request.url()).origin === 'http://127.0.0.1:4174';

  page.on('console', (message) => {
    if (message.type() === 'error') evidence.consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => evidence.pageErrors.push(error.stack || error.message));
  page.on('requestfailed', (request) => {
    if (internal(request)) evidence.failedInternalRequests.push(request.url());
  });
  page.on('request', (request) => {
    if (request.resourceType() === 'document') {
      evidence.documentRequests.push(request.url());
    }
    if (request.headers()['x-requested-with'] === 'products-tabs') {
      evidence.legacyTabRequests.push(request.url());
    }
    if (request.headers()['x-erp-partial'] === 'products-v1') {
      evidence.partialRequests += 1;
    }
  });
  return evidence;
}

async function partialAction(
  page: Page,
  evidence: BrowserEvidence,
  action: () => Promise<unknown>,
): Promise<Response> {
  const before = evidence.partialRequests;
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.request().method() === 'GET' &&
        candidate.request().headers()['x-erp-partial'] === 'products-v1',
    ),
    action(),
  ]);
  await expect.poll(() => evidence.partialRequests).toBe(before + 1);
  expect(response.ok()).toBe(true);
  expect(response.headers()['x-erp-partial']).toBe('products-v1');
  return response;
}

async function expectUniqueProductsDom(page: Page, activeView: string) {
  await expect(page.locator('main#main-content')).toHaveCount(1);
  await expect(page.locator('.products-workspace-tabs')).toHaveCount(1);
  await expect(page.locator('[data-products-tab]')).toHaveCount(6);
  await expectVersionedProductsTabs(page);
  await expect(page.locator(`[data-products-tab="${activeView}"]`)).toHaveAttribute(
    'aria-current',
    'page',
  );
  if (activeView === 'products') {
    await expect(page.locator('#warehouseResults')).toHaveCount(1);
    await expect(page.locator('#warehouseProductsTable')).toHaveCount(1);
    await expect(page.locator('#editDrawer')).toHaveCount(1);
    await expect(page.locator('#warehouseProductsTable tbody tr').first()).toBeVisible();
  }
}

async function navigateTopTab(page: Page, evidence: BrowserEvidence, view: string) {
  const beforeHistory = await page.evaluate(() => history.length);
  const beforeDocuments = evidence.documentRequests.length;
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.request().resourceType() === 'document' && candidate.request().method() === 'GET',
      { timeout: 10_000 },
    ),
    page.locator(`[data-products-tab="${view}"]`).click(),
  ]);
  expect(response.ok()).toBe(true);
  await page.waitForLoadState('load');
  expect(evidence.documentRequests).toHaveLength(beforeDocuments + 1);
  expect(await page.evaluate(() => history.length)).toBeGreaterThanOrEqual(beforeHistory);
  await expectUniqueProductsDom(page, view);
}

async function openProductCard(page: Page) {
  const button = page
    .locator('#warehouseProductsTable tbody tr')
    .first()
    .getByRole('button', { name: 'Открыть карточку' });
  const expected = await button.evaluate((element) => ({
    id: (element as HTMLElement).dataset.id || '',
    name: (element as HTMLElement).dataset.name || '',
    article: (element as HTMLElement).dataset.article || '',
  }));
  expect(expected.id).not.toBe('');
  expect(expected.name).not.toBe('');
  await button.click();

  const card = page.getByRole('dialog', { name: 'Карточка товара' });
  await expect(card).toBeVisible();
  await expect(card.locator('#editProductId')).toHaveValue(expected.id);
  await expect(card.locator('#detailName')).toHaveText(expected.name);
  await expect(card.locator('#detailArticle')).toHaveText(expected.article || '—');
  await expect(card.locator('.product-history-section')).toBeVisible();
  await expect(page.locator('[data-products-tab="products"]')).toHaveAttribute(
    'aria-current',
    'page',
  );
  return { card, ...expected };
}

async function closeProductCard(page: Page) {
  const card = page.getByRole('dialog', { name: 'Карточка товара' });
  await card.locator('.close-button').click();
  await expect(card).not.toBeVisible();
}

async function assertNoBrowserFailures(evidence: BrowserEvidence) {
  expect(evidence.legacyTabRequests).toEqual([]);
  expect(evidence.failedInternalRequests).toEqual([]);
  expect(evidence.pageErrors).toEqual([]);
  expect(evidence.consoleErrors).toEqual([]);
}

function sha256(payload: Buffer | string) {
  return createHash('sha256').update(payload).digest('hex');
}

test('warm legacy cache loads the content-versioned release and rolls back safely', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const evidence = observeBrowser(page);
  const assetResponses: Response[] = [];
  page.on('response', (response) => {
    if (new URL(response.url()).pathname === '/static/js/products-tabs.js') {
      assetResponses.push(response);
    }
  });

  await request.post('/__e2e/asset-release/baseline');
  await page.goto('/__e2e/warm-cache-baseline', { waitUntil: 'load' });
  expect(await page.evaluate(() => Boolean(window.__legacyProductsTabsExecuted))).toBe(true);
  expect(assetResponses).toHaveLength(1);
  const legacyUrl = assetResponses[0].url();
  const legacyHash = sha256(await assetResponses[0].body());
  expect(legacyUrl).toBe('http://127.0.0.1:4174/static/js/products-tabs.js');

  await request.post('/__e2e/asset-release/current');
  const [releaseResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/static/js/products-tabs.js' &&
        new URL(response.url()).searchParams.has('v'),
    ),
    page.goto('/app/products', { waitUntil: 'load' }),
  ]);
  const script = page.locator('script[src*="products-tabs.js"]');
  await expect(script).toHaveCount(1);
  const releaseUrl = await script.getAttribute('src');
  const expectedSource = readFileSync(resolve(process.cwd(), '../app/static/js/products-tabs.js'));
  const expectedHash = sha256(expectedSource);
  const executedHash = sha256(await releaseResponse.body());

  expect(releaseResponse.status()).toBe(200);
  expect(releaseUrl).toContain(`/static/js/products-tabs.js?v=${expectedHash}`);
  expect(executedHash).toBe(expectedHash);
  expect(executedHash).not.toBe(legacyHash);
  expect(expectedSource.toString('utf8')).not.toContain('document.write');
  expect(await page.evaluate(() => Boolean(window.VechasuProductsTabs?.initialized))).toBe(true);
  expect(await page.evaluate(() => Boolean(window.__legacyProductsTabsExecuted))).toBe(false);
  await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(50);

  for (let cycle = 0; cycle < 3; cycle += 1) {
    const search = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, evidence, () => search.fill('Tissot'));
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(1);
    await partialAction(page, evidence, () =>
      page.getByRole('button', { name: 'В наличии', exact: true }).click(),
    );

    await page.getByRole('button', { name: 'Открыть фильтры товаров' }).click();
    await page.locator('#filterBrandComboboxTrigger').click();
    await page.locator('#filterBrandComboboxListbox [data-brand="Tissot"]').click();
    await expect(page.locator('#filterCategoryComboboxTrigger')).toBeEnabled();
    await page.locator('#filterCategoryComboboxTrigger').click();
    await page.locator('#filterCategoryComboboxListbox [data-brand="Часы"]').click();
    await expect(page.locator('#filterModelComboboxTrigger')).toBeEnabled();
    await page.locator('#filterModelComboboxTrigger').click();
    await page.locator('#filterModelComboboxListbox [data-brand="PRX"]').click();
    await partialAction(page, evidence, () =>
      page.locator('#filterDrawer form').getByRole('button', { name: 'Применить фильтры' }).click(),
    );
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(1);

    await openProductCard(page);
    await closeProductCard(page);

    await page.getByRole('button', { name: 'Открыть фильтры товаров' }).click();
    await partialAction(page, evidence, () =>
      page.locator('#filterDrawer').getByRole('link', { name: 'Сбросить фильтры' }).click(),
    );
    const resetSearch = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, evidence, () => resetSearch.fill(''));
    await partialAction(page, evidence, () =>
      page.getByRole('button', { name: 'Все', exact: true }).click(),
    );
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(50);

    await partialAction(page, evidence, () =>
      page.getByRole('link', { name: 'Следующая страница' }).click(),
    );
    await partialAction(page, evidence, () => page.goBack());
    await partialAction(page, evidence, () => page.goForward());
    await partialAction(page, evidence, () => page.goBack());
  }

  expect(evidence.documentRequests).toHaveLength(2);
  await assertNoBrowserFailures(evidence);

  await request.post('/__e2e/asset-release/baseline');
  await page.goto('/__e2e/warm-cache-baseline', { waitUntil: 'load' });
  expect(await page.evaluate(() => Boolean(window.__legacyProductsTabsExecuted))).toBe(true);
  expect(await page.locator('script[src="/static/js/products-tabs.js"]').count()).toBe(1);
  await request.post('/__e2e/asset-release/current');
});

test('product tabs, cards and history remain idempotent through three lifecycles', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const evidence = observeBrowser(page);
  const productPairs = [
    ['Tissot', 'Ремешок'],
    ['Ремешок', 'Футляр'],
    ['Футляр', 'Tissot'],
  ];

  await page.goto('/app/products?q=Tissot', { waitUntil: 'load' });
  await page.reload({ waitUntil: 'load' });
  await expectUniqueProductsDom(page, 'products');

  // Controlled replay proves that a repeated lifecycle refreshes the singleton
  // instead of registering another listener set or document-write owner.
  await page.evaluate(async () => {
    const scriptUrl = document.querySelector<HTMLScriptElement>(
      'script[src*="products-tabs.js"]',
    )?.src;
    if (!scriptUrl) throw new Error('Versioned products-tabs.js URL was not found');
    const source = await fetch(scriptUrl).then((response) => response.text());
    for (let cycle = 0; cycle < 3; cycle += 1) (0, eval)(source);
  });
  const lifecycleInitialized = await page.evaluate(() =>
    Boolean(window.VechasuProductsTabs?.initialized),
  );
  await partialAction(page, evidence, () =>
    page.getByRole('textbox', { name: 'Поиск товаров' }).fill(''),
  );

  for (const [firstQuery, secondQuery] of productPairs) {
    const search = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, evidence, () => search.fill(firstQuery));
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(1);
    const first = await openProductCard(page);
    await closeProductCard(page);

    for (const view of ['brands', 'categories', 'collections', 'analytics', 'products']) {
      await navigateTopTab(page, evidence, view);
    }
    await expect(page).toHaveURL(new RegExp(`(?:\\?|&)q=${encodeURIComponent(firstQuery)}(?:&|$)`));
    const reopened = await openProductCard(page);
    expect(reopened.id).toBe(first.id);
    await closeProductCard(page);

    const secondSearch = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, evidence, () => secondSearch.fill(secondQuery));
    await expect(page.locator('#warehouseProductsTable tbody tr')).toHaveCount(1);
    const second = await openProductCard(page);
    expect(second.id).not.toBe(first.id);
    expect(second.name).not.toBe(first.name);
    await expect(second.card.locator('#detailName')).not.toHaveText(first.name);
    await closeProductCard(page);

    const historyLength = await page.evaluate(() => history.length);
    const backResponse = await page.goBack({ waitUntil: 'load' });
    expect(backResponse?.ok()).toBe(true);
    await expectUniqueProductsDom(page, 'analytics');
    expect(await page.evaluate(() => history.length)).toBe(historyLength);
    const forwardResponse = await page.goForward({ waitUntil: 'load' });
    expect(forwardResponse?.ok()).toBe(true);
    await expectUniqueProductsDom(page, 'products');
    expect(await page.evaluate(() => history.length)).toBe(historyLength);

    const currentSearch = page.getByRole('textbox', { name: 'Поиск товаров' });
    await partialAction(page, evidence, () => currentSearch.fill(''));
    const beforePaginationHistory = await page.evaluate(() => history.length);
    await partialAction(page, evidence, () =>
      page.getByRole('link', { name: 'Следующая страница' }).click(),
    );
    await expect(page).toHaveURL(/page=2/);
    expect(await page.evaluate(() => history.length)).toBe(beforePaginationHistory + 1);
    await partialAction(page, evidence, () => page.goBack());
    await expect(page).not.toHaveURL(/page=2/);
    await partialAction(page, evidence, () => page.goForward());
    await expect(page).toHaveURL(/page=2/);
    await partialAction(page, evidence, () => page.goBack());
    await expectUniqueProductsDom(page, 'products');
  }

  expect(lifecycleInitialized).toBe(true);
  await assertNoBrowserFailures(evidence);
});

test('a cached unversioned asset cannot shadow the versioned lifecycle', async ({ page }) => {
  const requestedScripts: string[] = [];
  page.on('request', (request) => {
    if (request.resourceType() === 'script' && request.url().includes('products-tabs.js')) {
      requestedScripts.push(request.url());
    }
  });

  await page.goto('/static/js/products-tabs.js', { waitUntil: 'load' });
  await page.goto('/app/products?view=analytics', { waitUntil: 'load' });
  await expectVersionedProductsTabs(page);
  await navigateTopTab(page, observeBrowser(page), 'products');

  const versionedRequests = requestedScripts.filter((source) => {
    const url = new URL(source);
    return /^[a-f0-9]{64}$/.test(url.searchParams.get('v') || '');
  });
  expect(versionedRequests.length).toBeGreaterThan(0);
  await expect(page.locator('#warehouseProductsTable tbody tr').first()).toBeVisible();
  expect(await page.evaluate(() => Boolean(window.VechasuProductsTabs?.initialized))).toBe(true);
});

test('required product viewports preserve cards, history and zero page overflow', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const evidence = observeBrowser(page);

  for (const viewport of requiredViewports) {
    await page.setViewportSize(viewport);
    await page.goto('/app/products?q=Tissot', { waitUntil: 'load' });
    await expectUniqueProductsDom(page, 'products');
    const product = await openProductCard(page);
    const dimensions = await page.evaluate(() => ({
      html: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
      body: [document.body.scrollWidth, document.body.clientWidth],
    }));
    expect(dimensions.html[0], JSON.stringify(viewport)).toBeLessThanOrEqual(dimensions.html[1]);
    expect(dimensions.body[0], JSON.stringify(viewport)).toBeLessThanOrEqual(dimensions.body[1]);
    const editButton = product.card.getByRole('button', { name: 'Редактировать' });
    await expect(editButton).toBeInViewport();
    await expect(product.card.locator('.close-button')).toBeInViewport();
    await closeProductCard(page);

    const historyLength = await page.evaluate(() => history.length);
    await navigateTopTab(page, evidence, 'brands');
    const navigatedHistoryLength = await page.evaluate(() => history.length);
    expect(navigatedHistoryLength).toBeGreaterThanOrEqual(historyLength);
    const backResponse = await page.goBack({ waitUntil: 'load' });
    expect(backResponse?.ok()).toBe(true);
    await expectUniqueProductsDom(page, 'products');
    expect(await page.evaluate(() => history.length)).toBe(navigatedHistoryLength);
  }

  await assertNoBrowserFailures(evidence);
});

declare global {
  interface Window {
    VechasuProductsTabs?: { initialized: boolean };
    __legacyProductsTabsExecuted?: boolean;
  }
}
