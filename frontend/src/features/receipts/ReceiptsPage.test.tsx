import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { ReceiptsPage } from './ReceiptsPage';

const receipt = {
  id: 'receipt-1',
  number: 'PR-2026-0001',
  created_at: '2026-07-30 12:00',
  receipt_date: '2026-07-30',
  brand: 'Casio',
  category: 'Часы',
  product_id: 'ms-1',
  product_name: 'Casio G-Shock',
  note: 'Поставка',
  status: 'posted',
  status_label: 'Проведён',
  positions: [
    {
      product_id: 'ms-1',
      product_name: 'Casio G-Shock',
      article: 'GA-2100',
      code: 'CASIO-1',
      brand: 'Casio',
      category: 'Часы',
      cell: 'A-1',
      quantity: 2,
      purchase_price: 5000,
      line_total: 10000,
      stock_before: 3,
      stock_after: 5,
    },
  ],
  positions_count: 1,
  total_quantity: 2,
  total_amount: 10000,
  moysklad_document_id: 'enter-1',
  moysklad_document_name: 'ОП-0001',
  moysklad_document_url: '',
};

function listResponse() {
  return new Response(
    JSON.stringify({
      data: [receipt],
      meta: {
        request_id: 'receipts-test',
        csrf_token: 'csrf',
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
        totals: { quantity: 2, amount: 10000 },
        facets: {
          brands: ['Casio'],
          categories: ['Часы'],
          statuses: ['posted'],
        },
        sort_by: 'receipt_date',
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
          id: 'ms-1',
          name: 'Casio G-Shock',
          article: 'GA-2100',
          code: 'CASIO-1',
          brand: 'Casio',
          category: 'Часы',
          cell: 'A-1',
          stock: 3,
          stock_display: '3',
          thumbnail_url: '',
          has_images: false,
        },
      ],
      meta: { request_id: 'catalog-test', csrf_token: 'csrf', total: 1 },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

describe('ReceiptsPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders receipt totals and opens a multi-position form', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(url.includes('/receipts/catalog') ? catalogResponse() : listResponse()),
      ),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/receipts']}>
        <AppProviders>
          <ReceiptsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    expect((await screen.findAllByText('PR-2026-0001')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('10 000 ₽').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: '+ Новый приход' }));
    expect(await screen.findByRole('heading', { name: 'Новый приход' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ Добавить позицию' })).toBeInTheDocument();
    expect(screen.getByText('Выбрать JPEG или PNG')).toBeInTheDocument();
  });
});
