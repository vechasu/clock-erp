import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { ProductsPage } from './ProductsPage';

const product = {
  id: 17,
  name: 'Casio G-Shock',
  article: 'GA-2100',
  barcode: '460000000001',
  moysklad_product_id: '',
  brand: 'Casio',
  category: 'Часы',
  brand_id: 1,
  category_id: 11,
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

const zeroProduct = {
  ...product,
  id: 18,
  name: 'Товар без остатка',
  article: 'ZERO-1',
  stock: 0,
  stock_display: '0',
};

function envelope(data: unknown, meta: Record<string, unknown> = {}) {
  return {
    data,
    meta: { request_id: 'products-test', csrf_token: 'csrf', ...meta },
    error: null,
  };
}

function response(items = [product, zeroProduct]) {
  return new Response(
    JSON.stringify(
      envelope(items, {
        page: 1,
        page_size: 50,
        total: items.length,
        pages: items.length ? 1 : 0,
        stats: {
          positions: items.length,
          total_stock: items.reduce((total, item) => total + item.stock, 0),
          positive_positions: items.filter((item) => item.stock > 0).length,
          zero_positions: items.filter((item) => item.stock === 0).length,
        },
        facets: {
          brands: [{ name: 'Casio', count: items.length }],
          categories: [{ name: 'Часы', count: items.length }],
          cells: [{ cell: 'A-01', count: items.length }],
        },
        sort_by: 'name',
        sort_dir: 'asc',
      }),
    ),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function catalogResponse(url: string) {
  const kind = new URL(url, 'https://example.test').searchParams.get('type');
  if (kind === 'brand') {
    return new Response(
      JSON.stringify(envelope([{ id: 1, name: 'Casio', active: true, product_count: 2 }])),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }
  return new Response(
    JSON.stringify(
      envelope([
        {
          id: 11,
          brand_id: 1,
          name: 'Часы',
          brand_name: 'Casio',
          active: true,
          product_count: 2,
        },
      ]),
    ),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function renderProducts(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppProviders>
        <ProductsPage />
      </AppProviders>
    </MemoryRouter>,
  );
}

function photoInput() {
  return screen
    .getAllByLabelText('Фото')
    .find(
      (element) => element instanceof HTMLInputElement && element.type === 'file',
    ) as HTMLInputElement;
}

function labelledInput(label: string) {
  return screen
    .getAllByLabelText(label)
    .find(
      (element) => element instanceof HTMLInputElement && element.type !== 'checkbox',
    ) as HTMLInputElement;
}

describe('ProductsPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders stock states, validates the create form, and keeps zero-stock filtering', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/catalog/options')) return Promise.resolve(catalogResponse(url));
      const visible = url.includes('in_stock=1') ? [product] : [product, zeroProduct];
      return Promise.resolve(response(visible));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderProducts('/products?test=stock-states');

    expect((await screen.findAllByText('Casio G-Shock')).length).toBeGreaterThan(0);
    expect(screen.getByRole('columnheader', { name: 'Остаток ↕' })).toBeInTheDocument();
    const zeroRow = screen.getAllByText('Товар без остатка')[0]?.closest('tr');
    expect(zeroRow?.querySelector('.stock-value')).toHaveClass('stock-value', 'is-empty');

    await user.click(screen.getByRole('checkbox', { name: 'Скрыть нулевые' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('in_stock=1'))).toBe(
        true,
      ),
    );
    await waitFor(() => expect(screen.queryByText('Товар без остатка')).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Добавить товар' }));
    expect(screen.getByRole('heading', { name: 'Новый товар' })).toBeInTheDocument();
    expect(document.querySelector('.product-create-card')).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: 'Начальный остаток' })).toHaveValue(0);
    expect(screen.getByRole('button', { name: 'Уменьшить начальный остаток' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    expect(await screen.findByText('Название товара обязательно')).toBeInTheDocument();
  });

  it('creates with a photo once and updates the row, counters, and reset form immediately', async () => {
    let resolveCreate: ((response: Response) => void) | undefined;
    const createResponse = new Promise<Response>((resolve) => {
      resolveCreate = resolve;
    });
    let submitted: FormData | null = null;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/catalog/options')) return Promise.resolve(catalogResponse(url));
      if (init?.method === 'POST') {
        submitted = init.body as FormData;
        return createResponse;
      }
      return Promise.resolve(response([product]));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderProducts('/products?test=create-photo');

    expect((await screen.findAllByText('Casio G-Shock')).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Добавить товар' }));
    const photo = new File(['RIFFxxxxWEBPphoto'], 'new-watch.webp', { type: 'image/webp' });
    await user.upload(photoInput(), photo);
    expect(screen.getByAltText('Предпросмотр new-watch.webp')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Название товара *'), 'Новый товар с фото');
    await user.type(labelledInput('Артикул'), 'NEW-PHOTO-1');
    await user.click(screen.getByRole('button', { name: 'Увеличить начальный остаток' }));
    await user.click(screen.getByRole('button', { name: 'Увеличить начальный остаток' }));
    expect(screen.getByRole('spinbutton', { name: 'Начальный остаток' })).toHaveValue(2);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const pendingButton = screen.getByRole('button', { name: 'Добавляем…' });
    expect(pendingButton).toBeDisabled();
    await user.click(pendingButton);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);

    const created = {
      ...product,
      id: 19,
      name: 'Новый товар с фото',
      article: 'NEW-PHOTO-1',
      stock: 2,
      stock_display: '2',
      thumbnail_url: '/warehouse/product/11111111-2222-4333-8444-555555555555/thumbnail',
    };
    await act(async () => {
      resolveCreate?.(
        new Response(JSON.stringify(envelope(created)), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
      await createResponse;
    });

    expect(await screen.findByText('Товар добавлен')).toBeInTheDocument();
    expect(screen.getAllByText('Новый товар с фото').length).toBeGreaterThan(0);
    expect(screen.getAllByAltText('Фото Новый товар с фото').length).toBeGreaterThan(0);
    const summary = screen.getByRole('region', { name: 'Сводка по товарам' });
    expect(within(summary).getByText('Позиций').closest('article')).toHaveTextContent('2');
    expect(within(summary).getByText('Остаток, единиц').closest('article')).toHaveTextContent('6');
    const submittedForm = submitted as FormData | null;
    expect(submittedForm?.get('name')).toBe('Новый товар с фото');
    expect(submittedForm?.get('stock')).toBe('2');
    expect(submittedForm?.get('product_image')).toEqual(
      expect.objectContaining({ name: 'new-watch.webp', type: 'image/webp' }),
    );

    await user.click(screen.getByRole('button', { name: 'Добавить товар' }));
    expect(screen.getByLabelText('Название товара *')).toHaveValue('');
    expect(screen.getByRole('spinbutton', { name: 'Начальный остаток' })).toHaveValue(0);
    expect(screen.queryByAltText('Предпросмотр new-watch.webp')).not.toBeInTheDocument();
  });

  it('rejects an unsupported photo before submission', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response([product]));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup({ applyAccept: false });
    renderProducts('/products?test=invalid-photo');

    expect((await screen.findAllByText('Casio G-Shock')).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Добавить товар' }));
    await user.upload(
      photoInput(),
      new File(['not-an-image'], 'product.gif', { type: 'image/gif' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('JPG, JPEG, PNG, WEBP');
    expect(screen.getByRole('button', { name: 'Добавить' })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });
});
