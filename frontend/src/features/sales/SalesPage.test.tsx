import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { SalesPage } from './SalesPage';

const sale = {
  id: 'sale-1',
  sale_type: 'manual',
  sale_type_label: 'Ручная',
  is_manual: true,
  inventory_managed: true,
  created_at: '2026-07-30',
  source: 'Tictactoy',
  source_key: 'tictactoy',
  order_number: 'ORDER-1',
  product_id: '1',
  product_name: 'Casio G-Shock',
  barcode: '460000000001',
  brand: 'Casio',
  category: 'Часы',
  quantity: 2,
  quantity_display: '2',
  net_quantity: 2,
  returned_quantity: 0,
  return_available_quantity: 2,
  returned_at: '',
  return_reason: '',
  unit_price: 1000,
  total_amount: 2000,
  gross_total_amount: 2000,
  returned_amount: 0,
  order_status: 'completed',
  order_status_label: 'Выполнен',
  is_cancelled: false,
  cancelled_at: '',
  track_number: '',
  delivery_method: '',
  delivery_cost: 0,
  region: '',
  city: '',
  note: 'Проверка',
  recipient: '',
  recipient_name: '',
  payment_method: '',
  commission: '',
  commission_amount: 0,
  country: '',
  delivery_address: '',
  platform: '',
  invoice_number: '',
  sticker_number: '',
};

function listResponse() {
  return new Response(
    JSON.stringify({
      data: [sale],
      meta: {
        request_id: 'sales-test',
        csrf_token: 'csrf',
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
        totals: { active: 1, cancelled: 0, quantity: 2, revenue: 2000, returned: 0 },
        facets: {
          sources: ['tictactoy'],
          brands: ['Casio'],
          categories: ['Часы'],
          statuses: ['completed'],
        },
        sort_by: 'created_at',
        sort_dir: 'desc',
      },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function catalogResponse() {
  return new Response(
    JSON.stringify({
      data: [
        {
          id: '1',
          name: 'Casio G-Shock',
          article: 'GA-2100',
          barcode: '460000000001',
          brand: 'Casio',
          category: 'Часы',
          stock: 3,
          stock_display: '3',
        },
      ],
      meta: { request_id: 'catalog-test', csrf_token: 'csrf' },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

describe('SalesPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders source tabs and exposes managed return instead of delete', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(url.includes('/sales/catalog') ? catalogResponse() : listResponse()),
      ),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/sales?source=all']}>
        <AppProviders>
          <SalesPage />
        </AppProviders>
      </MemoryRouter>,
    );

    expect((await screen.findAllByText('ORDER-1')).length).toBeGreaterThan(0);
    expect(screen.getByRole('tab', { name: 'Tictactoy' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Возврат' }).length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole('button', { name: 'Возврат' })[0]);
    expect(screen.getByRole('heading', { name: 'Оформить возврат' })).toBeInTheDocument();
    expect(screen.getByText('Доступно: 2')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Отмена' }));
    await user.click(screen.getByRole('button', { name: '+ Новая продажа' }));
    expect(await screen.findByRole('heading', { name: 'Новая продажа' })).toBeInTheDocument();
  });
});
