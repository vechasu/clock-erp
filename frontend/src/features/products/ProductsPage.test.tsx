import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { ProductsPage } from './ProductsPage';

const product = {
  id: 17,
  name: 'Casio G-Shock',
  article: 'GA-2100',
  barcode: '460000000001',
  brand: 'Casio',
  category: 'Часы',
  cell: 'A-01',
  stock: 4,
  stock_display: '4',
  created_at: 1,
  created_at_display: '29.07.2026 12:00',
  thumbnail_url: '',
  gallery: [],
  price_display: '15 000 ₽',
  source_url: '',
  match_status: 'not_found',
  updated_at: '2026-07-29T12:00:00',
};

function response() {
  return new Response(
    JSON.stringify({
      data: [product],
      meta: {
        request_id: 'products-test',
        csrf_token: 'csrf',
        page: 1,
        page_size: 50,
        total: 1,
        pages: 1,
        stats: {
          positions: 1,
          total_stock: 4,
          positive_positions: 1,
          zero_positions: 0,
        },
        facets: {
          brands: [{ name: 'Casio', count: 1 }],
          categories: [{ name: 'Часы', count: 1 }],
          cells: [{ cell: 'A-01', count: 1 }],
        },
        sort_by: 'name',
        sort_dir: 'asc',
      },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

describe('ProductsPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders server data and opens the validated product editor', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response()));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/products']}>
        <AppProviders>
          <ProductsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    expect((await screen.findAllByText('Casio G-Shock')).length).toBeGreaterThan(0);
    expect(screen.getByText('4 шт.')).toBeInTheDocument();
    expect(
      screen
        .getAllByRole('link', { name: 'Товары' })
        .some((link) => link.classList.contains('is-active')),
    ).toBe(true);
    expect(
      fetchMock,
      JSON.stringify(fetchMock.mock.calls.map(([input]) => String(input))),
    ).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Добавить товар' }));
    expect(screen.getByRole('heading', { name: 'Новый товар' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Название товара обязательно')).toBeInTheDocument();
  });
});
