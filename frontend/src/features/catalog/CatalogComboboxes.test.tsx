import { render, screen, waitFor } from '@testing-library/react';
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

function Harness() {
  const [brandId, setBrandId] = useState<number | null>(null);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [productId, setProductId] = useState('');
  return (
    <CatalogCascade
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
});
