import { expect, test, type Page } from '@playwright/test';

const repair = {
  id: 'repair-e2e-1',
  repair_number: 'R-2026-9001',
  created_at: '2026-07-30 10:00',
  updated_at: '2026-07-30 12:00',
  archived_at: '',
  is_archived: false,
  responsible: 'Максим',
  status: 'at_master',
  status_label: 'У мастера',
  request_type: 'warranty_repair',
  request_type_label: 'Гарантийный ремонт',
  location: 'with_master',
  location_label: 'У мастера',
  communication_channel: 'telegram',
  channel_label: 'Telegram',
  order_number: '20735',
  order_source: 'our',
  order_label: 'Наш №20735',
  client_name: 'Иван Петров',
  client_phone: '+79990000000',
  client_email: 'ivan@example.test',
  client_messenger: '@ivan',
  contact: '@ivan',
  product_id: '42',
  product_name: 'Vechasu Voyager',
  brand: 'Vechasu',
  model: 'Voyager',
  article: 'VV-42',
  serial_number: 'SN-42',
  product_url: '',
  product_image_url: '',
  problem: 'Часы не включаются после зарядки',
  diagnostic_result: 'Требуется замена платы',
  master_conclusion: '',
  decision: '',
  estimate_cost: '0',
  final_cost: '',
  master: 'Алексей',
  equipment: 'Часы и коробка',
  request_at: '2026-07-30',
  request_at_display: '30.07.2026',
  customer_sent_at: '',
  accepted_at: '2026-07-30',
  accepted_at_display: '30.07.2026',
  master_handoff_at: '2026-07-30',
  master_handoff_at_display: '30.07.2026',
  repair_completed_at: '',
  returned_at: '',
  due_date: '2026-08-10',
  communication: '',
  internal_comment: '',
  latest_event: 'Передано мастеру',
  shipments: [],
  attachments: [],
  history: [
    {
      id: 'event-1',
      timestamp: '2026-07-30 12:00',
      actor: 'Максим',
      action: 'Часы переданы мастеру',
      field: 'status',
      old_value: 'У нас',
      new_value: 'У мастера',
      comment: '',
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/repairs?**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        data: [repair],
        meta: {
          request_id: 'repairs-e2e',
          csrf_token: 'csrf',
          page: 1,
          page_size: 50,
          total: 1,
          pages: 1,
          stats: {
            active: 1,
            at_us: 0,
            at_master: 1,
            delivery: 0,
            waiting_payment: 0,
            archived: 0,
          },
          facets: {
            statuses: [
              { value: 'new', label: 'Новая' },
              { value: 'at_master', label: 'У мастера' },
            ],
            types: [{ value: 'warranty_repair', label: 'Гарантийный ремонт' }],
            locations: [{ value: 'with_master', label: 'У мастера' }],
            channels: [{ value: 'telegram', label: 'Telegram' }],
          },
          sort_by: 'request_at',
          sort_dir: 'desc',
          view: 'active',
        },
        error: null,
      }),
    });
  });
});

function visibleRepairList(page: Page) {
  return page.locator('.data-table-wrap:visible, .mobile-card-list:visible');
}

test('repair workspace shares navigation, filters, cards and details', async ({ page }) => {
  await page.goto('/app/repairs?view=active');

  await expect(page.getByRole('heading', { name: 'Ремонт' })).toBeVisible();
  await expect(page.getByRole('tab', { name: /Активные/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(visibleRepairList(page).getByText('R-2026-9001').first()).toBeVisible();

  const openAction = visibleRepairList(page).getByRole('button', { name: 'Открыть' }).first();
  await openAction.click();
  const dialog = page.getByRole('dialog', { name: 'R-2026-9001' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Требуется замена платы')).toBeVisible();
  await expect(dialog.getByText('Накладных пока нет.')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('mobile navigation keeps all four primary modules available', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.includes('mobile'));
  await page.goto('/app/repairs');

  const mobileNavigation = page.getByRole('navigation', {
    name: 'Основная мобильная навигация',
  });
  await expect(mobileNavigation.getByRole('link', { name: 'Товары' })).toBeVisible();
  await expect(mobileNavigation.getByRole('link', { name: 'Продажи' })).toBeVisible();
  await expect(mobileNavigation.getByRole('link', { name: 'Приход' })).toBeVisible();
  await expect(mobileNavigation.getByRole('link', { name: 'Ремонт' })).toBeVisible();
});
