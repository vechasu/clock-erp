import { render, screen, waitFor } from '@testing-library/react';
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
  brand_id: 1,
  category_id: 10,
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
      brand_id: 1,
      category_id: 10,
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

function catalogResponse(url: URL) {
  const kind = url.searchParams.get('type');
  const data =
    kind === 'brand'
      ? [{ id: 1, name: 'Casio', active: true, product_count: 1 }]
      : kind === 'category'
        ? [
            {
              id: 10,
              brand_id: 1,
              name: 'Часы',
              brand_name: 'Casio',
              active: true,
              product_count: 1,
            },
          ]
        : [
            {
              id: 'ms-1',
              product_id: 'ms-1',
              name: 'Casio G-Shock',
              article: 'GA-2100',
              barcode: 'CASIO-1',
              brand: 'Casio',
              category: 'Часы',
              brand_id: 1,
              category_id: 10,
              cell: 'A-1',
              stock: 3,
              stock_display: '3',
              active: true,
            },
          ];
  return new Response(
    JSON.stringify({
      data,
      meta: { request_id: 'catalog-test', csrf_token: 'csrf', total: data.length },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

describe('ReceiptsPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  function mockApi() {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = new URL(String(input), 'https://erp.test');
        return Promise.resolve(
          url.pathname.includes('/catalog/options') ? catalogResponse(url) : listResponse(),
        );
      }),
    );
  }

  it('renders receipt totals and opens a multi-position form', async () => {
    mockApi();
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
    await user.click(screen.getByRole('button', { name: 'Новый приход' }));
    expect(await screen.findByRole('heading', { name: 'Новый приход' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ Добавить позицию' })).toBeInTheDocument();
    expect(screen.getByText('Выбрать JPEG или PNG')).toBeInTheDocument();
  });

  it('restores the selected shared IDs when editing a receipt', async () => {
    mockApi();
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/receipts']}>
        <AppProviders>
          <ReceiptsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    await screen.findAllByText('PR-2026-0001');
    await user.click(screen.getAllByRole('button', { name: 'Изменить' })[0]);
    expect(await screen.findByRole('heading', { name: 'Приход PR-2026-0001' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Бренд *' })).toHaveValue('Casio');
      expect(screen.getByRole('combobox', { name: 'Категория *' })).toHaveValue('Часы');
      expect(screen.getByRole('combobox', { name: 'Товар *' })).toHaveValue(
        'Casio G-Shock · GA-2100 · остаток 3',
      );
    });
  });

  it('applies the shared brand and category IDs to receipt filters', async () => {
    const requestedUrls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = new URL(String(input), 'https://erp.test');
        requestedUrls.push(url.toString());
        return Promise.resolve(
          url.pathname.includes('/catalog/options') ? catalogResponse(url) : listResponse(),
        );
      }),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/receipts']}>
        <AppProviders>
          <ReceiptsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    await screen.findAllByText('PR-2026-0001');
    await user.click(screen.getByText('Фильтры'));
    await user.click(screen.getByRole('combobox', { name: 'Бренд' }));
    await user.click(await screen.findByRole('option', { name: 'Casio' }));
    await user.click(screen.getByRole('combobox', { name: 'Категория' }));
    await user.click(await screen.findByRole('option', { name: 'Часы' }));

    await waitFor(() =>
      expect(
        requestedUrls.some(
          (url) =>
            url.includes('/api/v1/receipts?') &&
            url.includes('brand_id=1') &&
            url.includes('category_id=10'),
        ),
      ).toBe(true),
    );
  });
});
