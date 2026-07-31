import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { AppProviders } from '../../app/providers';
import { CatalogCascade } from './CatalogComboboxes';

function envelope(data: unknown) {
  return new Response(
    JSON.stringify({
      data,
      meta: { request_id: 'catalog-test', csrf_token: 'csrf' },
      error: null,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function Harness({ allowCreate = false }: { allowCreate?: boolean }) {
  const [brandId, setBrandId] = useState<number | null>(null);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [productId, setProductId] = useState('');
  return (
    <CatalogCascade
      allowCreate={allowCreate}
      brandId={brandId}
      categoryId={categoryId}
      productId={productId}
      onBrandChange={(nextBrandId) => {
        setBrandId(nextBrandId);
        setCategoryId(null);
        setProductId('');
      }}
      onCategoryChange={(nextCategoryId) => {
        setCategoryId(nextCategoryId);
        setProductId('');
      }}
      onProductChange={setProductId}
    />
  );
}

describe('CatalogCascade', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses server options and resets incompatible category and product', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'https://erp.test');
      const kind = url.searchParams.get('type');
      if (kind === 'brand') {
        return Promise.resolve(
          envelope([{ id: 1, name: 'Casio', active: true, product_count: 1 }]),
        );
      }
      if (kind === 'category') {
        expect(url.searchParams.get('brand_id')).toBe('1');
        return Promise.resolve(
          envelope([
            {
              id: 10,
              brand_id: 1,
              name: 'Наручные часы',
              brand_name: 'Casio',
              active: true,
              product_count: 1,
            },
          ]),
        );
      }
      expect(url.searchParams.get('brand_id')).toBe('1');
      expect(url.searchParams.get('category_id')).toBe('10');
      return Promise.resolve(
        envelope([
          {
            id: '100',
            product_id: '100',
            name: 'Casio A168',
            article: 'A168',
            barcode: '',
            brand_id: 1,
            category_id: 10,
            brand: 'Casio',
            category: 'Наручные часы',
            cell: 'A-1',
            stock: 3,
            stock_display: '3',
            active: true,
          },
        ]),
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <AppProviders>
        <Harness />
      </AppProviders>,
    );

    await user.click(screen.getByRole('combobox', { name: 'Бренд *' }));
    await user.click(await screen.findByRole('option', { name: 'Casio' }));
    await user.click(screen.getByRole('combobox', { name: 'Категория *' }));
    await user.click(await screen.findByRole('option', { name: 'Наручные часы' }));
    await user.click(screen.getByRole('combobox', { name: 'Товар *' }));
    await user.click(
      await screen.findByRole('option', {
        name: /Casio A168 · A168 · остаток 3/,
      }),
    );

    expect(screen.getByRole('combobox', { name: 'Товар *' })).toHaveValue(
      'Casio A168 · A168 · остаток 3',
    );
    await user.click(screen.getByRole('button', { name: 'Очистить поле «Бренд»' }));
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Категория *' })).toHaveValue('');
      expect(screen.getByRole('combobox', { name: 'Товар *' })).toHaveValue('');
    });
    expect(screen.getByRole('combobox', { name: 'Категория *' })).toBeDisabled();
  });

  it('runs server search from the first symbol and refreshes it after every change', async () => {
    const requestedQueries: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = new URL(String(input), 'https://erp.test');
        requestedQueries.push(url.searchParams.get('q') ?? '');
        return Promise.resolve(envelope([]));
      }),
    );
    const user = userEvent.setup();
    render(
      <AppProviders>
        <Harness />
      </AppProviders>,
    );

    const brand = screen.getByRole('combobox', { name: 'Бренд *' });
    await user.type(brand, 'C');
    await waitFor(() => expect(requestedQueries).toContain('C'));
    await user.type(brand, 'a');
    await waitFor(() => expect(requestedQueries).toContain('Ca'));
    expect(await screen.findByRole('option', { name: 'Ничего не найдено' })).toBeInTheDocument();
  });

  it('aborts an obsolete server search before applying a newer query', async () => {
    let obsoleteSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'https://erp.test');
      const query = url.searchParams.get('q') ?? '';
      if (query === 'Z') {
        obsoleteSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          obsoleteSignal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        });
      }
      return Promise.resolve(envelope([]));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <AppProviders>
        <Harness />
      </AppProviders>,
    );

    const brand = screen.getByRole('combobox', { name: 'Бренд *' });
    await user.type(brand, 'Z');
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('q=Z'))).toBe(true),
    );
    await user.type(brand, 'x');
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('q=Zx'))).toBe(true),
    );

    expect(obsoleteSignal?.aborted).toBe(true);
  });

  it('exposes the same sorted IDs and interaction in products, sales and receipts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = new URL(String(input), 'https://erp.test');
        const kind = url.searchParams.get('type');
        if (kind === 'brand') {
          return Promise.resolve(
            envelope([
              { id: 1, name: 'Casio', active: true, product_count: 1 },
              { id: 2, name: 'Seiko', active: true, product_count: 1 },
            ]),
          );
        }
        if (kind === 'category') {
          return Promise.resolve(
            envelope([
              {
                id: 10,
                brand_id: 1,
                name: 'Наручные часы',
                brand_name: 'Casio',
                active: true,
                product_count: 1,
              },
            ]),
          );
        }
        return Promise.resolve(
          envelope([
            {
              id: '100',
              product_id: '100',
              name: 'Casio A168',
              article: 'A168',
              barcode: '',
              brand_id: 1,
              category_id: 10,
              brand: 'Casio',
              category: 'Наручные часы',
              cell: 'A-1',
              stock: 3,
              stock_display: '3',
              active: true,
            },
          ]),
        );
      }),
    );
    const user = userEvent.setup();
    render(
      <AppProviders>
        {['Товары', 'Продажи', 'Приход'].map((section) => (
          <section key={section} aria-label={section}>
            <Harness />
          </section>
        ))}
      </AppProviders>,
    );

    for (const sectionName of ['Товары', 'Продажи', 'Приход']) {
      const section = screen.getByRole('region', { name: sectionName });
      const brand = within(section).getByRole('combobox', { name: 'Бренд *' });
      await user.click(brand);
      await within(section).findByRole('option', { name: 'Casio' });
      await user.keyboard('{Enter}');
      expect(brand).toHaveValue('Casio');

      const category = within(section).getByRole('combobox', { name: 'Категория *' });
      await user.click(category);
      await user.click(await within(section).findByRole('option', { name: 'Наручные часы' }));

      const product = within(section).getByRole('combobox', { name: 'Товар *' });
      await user.click(product);
      await user.click(
        await within(section).findByRole('option', {
          name: 'Casio A168 · A168 · остаток 3',
        }),
      );
      expect(product).toHaveValue('Casio A168 · A168 · остаток 3');
    }
  });

  it('creates brand, category and product in the shared catalog and selects them immediately', async () => {
    const brands: unknown[] = [];
    const categories: unknown[] = [];
    const products: unknown[] = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'https://erp.test');
      if (init?.method === 'POST' && url.pathname.endsWith('/brands')) {
        const created = { id: 7, name: 'Vechasu', active: true, product_count: 0 };
        brands.push(created);
        return Promise.resolve(envelope(created));
      }
      if (init?.method === 'POST' && url.pathname.endsWith('/categories')) {
        const created = {
          id: 70,
          brand_id: 7,
          name: 'Ремешки',
          brand_name: 'Vechasu',
          active: true,
          product_count: 0,
        };
        categories.push(created);
        return Promise.resolve(envelope(created));
      }
      if (init?.method === 'POST' && url.pathname.endsWith('/products')) {
        const created = {
          id: '700',
          name: 'Ремешок Classic',
          article: 'STRAP-700',
          barcode: '',
          brand_id: 7,
          category_id: 70,
          brand: 'Vechasu',
          category: 'Ремешки',
          cell: '',
          stock: 0,
          stock_display: '0',
        };
        products.push({ ...created, product_id: '700', active: true });
        return Promise.resolve(envelope(created));
      }
      const kind = url.searchParams.get('type');
      return Promise.resolve(
        envelope(kind === 'brand' ? brands : kind === 'category' ? categories : products),
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <AppProviders>
        <Harness allowCreate />
      </AppProviders>,
    );

    await user.click(screen.getByRole('combobox', { name: 'Бренд *' }));
    await user.click(await screen.findByRole('button', { name: '+ Добавить новый бренд' }));
    let dialog = await screen.findByRole('dialog', { name: 'Новый бренд' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Название *' }), 'Vechasu');
    await user.click(within(dialog).getByRole('button', { name: 'Создать' }));
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Бренд *' })).toHaveValue('Vechasu'),
    );

    await user.click(screen.getByRole('combobox', { name: 'Категория *' }));
    await user.click(await screen.findByRole('button', { name: '+ Добавить новую категорию' }));
    dialog = await screen.findByRole('dialog', { name: 'Новая категория' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Название *' }), 'Ремешки');
    await user.click(within(dialog).getByRole('button', { name: 'Создать' }));
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Категория *' })).toHaveValue('Ремешки'),
    );

    await user.click(screen.getByRole('combobox', { name: 'Товар *' }));
    await user.click(await screen.findByRole('button', { name: '+ Добавить новый товар' }));
    dialog = await screen.findByRole('dialog', { name: 'Новый товар' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Название *' }), 'Ремешок Classic');
    await user.type(within(dialog).getByRole('textbox', { name: 'Артикул' }), 'STRAP-700');
    await user.click(within(dialog).getByRole('button', { name: 'Создать' }));
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Товар *' })).toHaveValue(
        'Ремешок Classic · STRAP-700 · остаток 0',
      ),
    );

    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          (init as RequestInit | undefined)?.method === 'POST' &&
          String(input).startsWith('/api/v1/'),
      ),
    ).toHaveLength(3);
  });
});
