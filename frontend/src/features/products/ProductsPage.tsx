import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef, SortingState } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiRequestError } from '../../api/client';
import { AppShell } from '../../components/AppShell';
import { DataTable } from '../../components/DataTable';
import { ConfirmDialog, Modal } from '../../components/Modal';
import { PageState } from '../../components/PageState';
import { TablePagination } from '../../components/TablePagination';
import { Toast } from '../../components/Toast';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { createProduct, deleteProduct, fetchProducts, updateProduct } from './api';
import { ProductForm } from './ProductForm';
import type { Product, ProductFormValues } from './schemas';

function errorMessage(error: unknown) {
  return error instanceof ApiRequestError ? error.message : 'Не удалось выполнить запрос';
}

function ProductImage({ product }: { product: Product }) {
  return product.thumbnail_url ? (
    <img className="product-thumbnail" src={product.thumbnail_url} alt="" loading="lazy" />
  ) : (
    <span className="product-thumbnail placeholder" aria-hidden="true">
      ◇
    </span>
  );
}

export function ProductsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search);
  const [editor, setEditor] = useState<Product | 'new' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Product | null>(null);
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(
    null,
  );

  useEffect(() => {
    const current = searchParams.get('q') ?? '';
    if (current === debouncedSearch) return;
    const next = new URLSearchParams(searchParams);
    if (debouncedSearch) next.set('q', debouncedSearch);
    else next.delete('q');
    next.set('page', '1');
    setSearchParams(next, { replace: true });
  }, [debouncedSearch, searchParams, setSearchParams]);

  const normalizedParams = useMemo(() => {
    const next = new URLSearchParams(searchParams);
    if (!next.has('page')) next.set('page', '1');
    if (!next.has('page_size')) next.set('page_size', '50');
    return next;
  }, [searchParams]);

  const productsQuery = useQuery({
    queryKey: ['products', normalizedParams.toString()],
    queryFn: () => fetchProducts(normalizedParams),
    placeholderData: (previous) => previous,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['products'] });
  const saveMutation = useMutation({
    mutationFn: ({ product, values }: { product: Product | 'new'; values: ProductFormValues }) =>
      product === 'new' ? createProduct(values) : updateProduct(product.id, values),
    onSuccess: async (_, variables) => {
      await invalidate();
      setEditor(null);
      setToast({
        message: variables.product === 'new' ? 'Товар добавлен' : 'Карточка обновлена',
        kind: 'success',
      });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteProduct,
    onSuccess: async () => {
      await invalidate();
      setDeleteTarget(null);
      setToast({ message: 'Товар удалён', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });

  const setFilter = (key: string, value: string, resetPage = true) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (resetPage) next.set('page', '1');
    setSearchParams(next);
  };
  const products = productsQuery.data?.products ?? [];
  const meta = productsQuery.data?.meta;
  const sorting: SortingState = [
    {
      id: searchParams.get('sort_by') ?? 'name',
      desc: searchParams.get('sort_dir') === 'desc',
    },
  ];

  const columns = useMemo<ColumnDef<Product>[]>(
    () => [
      {
        id: 'name',
        accessorKey: 'name',
        header: 'Товар',
        size: 330,
        cell: ({ row }) => (
          <div className="product-cell">
            <ProductImage product={row.original} />
            <div>
              <strong>{row.original.name}</strong>
              <small>{row.original.article || row.original.barcode || 'Без артикула'}</small>
            </div>
          </div>
        ),
      },
      { id: 'brand', accessorKey: 'brand', header: 'Бренд', size: 150 },
      { id: 'category', accessorKey: 'category', header: 'Категория', size: 190 },
      {
        id: 'stock',
        accessorKey: 'stock',
        header: 'Остаток',
        size: 110,
        cell: ({ row }) => (
          <span className={`stock-badge${row.original.stock > 0 ? ' is-positive' : ''}`}>
            {row.original.stock_display}
          </span>
        ),
      },
      { id: 'cell', accessorKey: 'cell', header: 'Ячейка', size: 110 },
      {
        id: 'created_at',
        accessorKey: 'created_at_display',
        header: 'Добавлено',
        size: 160,
      },
      {
        id: 'actions',
        header: 'Действия',
        enableSorting: false,
        enableHiding: false,
        size: 150,
        cell: ({ row }) => (
          <div className="row-actions">
            <button type="button" onClick={() => setEditor(row.original)}>
              Изменить
            </button>
            <button
              className="danger-link"
              type="button"
              onClick={() => setDeleteTarget(row.original)}
            >
              Удалить
            </button>
          </div>
        ),
      },
    ],
    [],
  );

  return (
    <AppShell>
      <div className="erp-page">
        <header className="page-header">
          <div>
            <p className="page-eyebrow">Складской каталог</p>
            <h1>Товары</h1>
            <p>Управление карточками, остатками и размещением на складе</p>
          </div>
          <button className="button primary" type="button" onClick={() => setEditor('new')}>
            + Добавить товар
          </button>
        </header>

        <section className="summary-grid" aria-label="Сводка по товарам">
          <article>
            <span>Позиций</span>
            <strong>{meta?.total ?? '—'}</strong>
          </article>
          <article>
            <span>Общий остаток</span>
            <strong>{meta?.stats.total_stock ?? '—'}</strong>
          </article>
          <article>
            <span>В наличии</span>
            <strong>{meta?.stats.positive_positions ?? '—'}</strong>
          </article>
        </section>

        <section className="workspace-card">
          <div className="list-toolbar">
            <label className="search-control">
              <span aria-hidden="true">⌕</span>
              <span className="visually-hidden">Поиск товаров</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Название, артикул, штрихкод, ячейка…"
              />
            </label>
            <details className="filter-panel">
              <summary>Фильтры</summary>
              <div className="filter-grid">
                <label>
                  Бренд
                  <select
                    value={searchParams.get('brand') ?? ''}
                    onChange={(event) => setFilter('brand', event.target.value)}
                  >
                    <option value="">Все бренды</option>
                    {meta?.facets.brands.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name} ({item.count})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Категория
                  <select
                    value={searchParams.get('category') ?? ''}
                    onChange={(event) => setFilter('category', event.target.value)}
                  >
                    <option value="">Все категории</option>
                    {meta?.facets.categories.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name} ({item.count})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Ячейка
                  <select
                    value={searchParams.get('cell') ?? ''}
                    onChange={(event) => setFilter('cell', event.target.value)}
                  >
                    <option value="">Все ячейки</option>
                    {meta?.facets.cells.map((item) => (
                      <option key={item.cell} value={item.cell}>
                        {item.cell} ({item.count})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Добавлено с
                  <input
                    type="date"
                    value={searchParams.get('date_from') ?? ''}
                    onChange={(event) => setFilter('date_from', event.target.value)}
                  />
                </label>
                <label>
                  по
                  <input
                    type="date"
                    value={searchParams.get('date_to') ?? ''}
                    onChange={(event) => setFilter('date_to', event.target.value)}
                  />
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={searchParams.get('in_stock') === '1'}
                    onChange={(event) => setFilter('in_stock', event.target.checked ? '1' : '')}
                  />
                  Только в наличии
                </label>
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => {
                    setSearch('');
                    setSearchParams({ page: '1', page_size: String(meta?.page_size ?? 50) });
                  }}
                >
                  Сбросить
                </button>
              </div>
            </details>
          </div>

          {productsQuery.isError ? (
            <PageState
              kind="error"
              title="Не удалось загрузить товары"
              message={errorMessage(productsQuery.error)}
              action={
                <button className="button secondary" type="button" onClick={() => productsQuery.refetch()}>
                  Повторить
                </button>
              }
            />
          ) : productsQuery.isPending ? (
            <div className="table-loading" role="status">
              Загружаем товары…
            </div>
          ) : products.length === 0 ? (
            <PageState
              title="Товары не найдены"
              message="Измените условия поиска или добавьте новую карточку."
              action={
                <button className="button primary" type="button" onClick={() => setEditor('new')}>
                  Добавить товар
                </button>
              }
            />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={products}
                sorting={sorting}
                onSortingChange={(nextSorting) => {
                  const next = nextSorting[0];
                  const updated = new URLSearchParams(searchParams);
                  updated.set('sort_by', next?.id ?? 'name');
                  updated.set('sort_dir', next?.desc ? 'desc' : 'asc');
                  updated.set('page', '1');
                  setSearchParams(updated);
                }}
                getRowId={(product) => String(product.id)}
                renderMobileCard={(product) => (
                  <article className="mobile-product-card">
                    <ProductImage product={product} />
                    <div>
                      <strong>{product.name}</strong>
                      <small>{[product.brand, product.category].filter(Boolean).join(' · ')}</small>
                      <p>
                        <span className={`stock-badge${product.stock > 0 ? ' is-positive' : ''}`}>
                          {product.stock_display} шт.
                        </span>
                        {product.cell ? <span>Ячейка {product.cell}</span> : null}
                      </p>
                      <div className="row-actions">
                        <button type="button" onClick={() => setEditor(product)}>
                          Изменить
                        </button>
                        <button
                          className="danger-link"
                          type="button"
                          onClick={() => setDeleteTarget(product)}
                        >
                          Удалить
                        </button>
                      </div>
                    </div>
                  </article>
                )}
              />
              {meta ? (
                <TablePagination
                  page={meta.page}
                  pageSize={meta.page_size}
                  pages={meta.pages}
                  total={meta.total}
                  onPageChange={(page) => setFilter('page', String(page), false)}
                  onPageSizeChange={(pageSize) => setFilter('page_size', String(pageSize))}
                />
              ) : null}
            </>
          )}
        </section>
      </div>

      <Modal
        open={editor !== null}
        title={editor === 'new' ? 'Новый товар' : 'Редактирование товара'}
        description="Данные сохраняются в едином каталоге Vechasu ERP."
        onClose={() => setEditor(null)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setEditor(null)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="submit"
              form="product-editor"
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </>
        }
      >
        <ProductForm
          id="product-editor"
          product={editor === 'new' ? null : editor}
          onSubmit={(values) => {
            if (editor) saveMutation.mutate({ product: editor, values });
          }}
        />
      </Modal>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить товар?"
        message={
          deleteTarget?.stock
            ? 'Товар с ненулевым остатком удалить нельзя.'
            : `Карточка «${deleteTarget?.name ?? ''}» будет скрыта из активного каталога.`
        }
        pending={deleteMutation.isPending}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
        }}
      />
      {toast ? <Toast {...toast} onClose={() => setToast(null)} /> : null}
    </AppShell>
  );
}
