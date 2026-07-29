import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { RepairsPage } from './RepairsPage';

const repair = {
  id: 'repair-1',
  repair_number: 'R-2026-0001',
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
  problem: 'Часы не включаются',
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

function response() {
  return new Response(
    JSON.stringify({
      data: [repair],
      meta: {
        request_id: 'repairs-test',
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
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

describe('RepairsPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the shared page frame and opens long repair data in a card', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response()));
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/repairs?view=active']}>
        <AppProviders>
          <RepairsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Ремонт' })).toBeInTheDocument();
    expect((await screen.findAllByText('R-2026-0001')).length).toBeGreaterThan(0);
    expect(screen.getByRole('tab', { name: /Активные/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('У мастера').length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole('button', { name: 'Открыть' })[0]);
    expect(screen.getByRole('heading', { name: 'R-2026-0001' })).toBeInTheDocument();
    expect(screen.getByText('Требуется замена платы')).toBeInTheDocument();
    expect(screen.getByText('Накладных пока нет.')).toBeInTheDocument();
  });
});
