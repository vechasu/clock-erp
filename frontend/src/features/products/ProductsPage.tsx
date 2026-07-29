import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef, SortingState } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiRequestError } from '../../api/client';
import { AppShell } from '../../components/AppShell';
import { DateRangePicker, FilterPanel, LiveSearch } from '../../components/Controls';
import { DataTable } from '../../components/DataTable';
import { ConfirmDialog, Modal } from '../../components/Modal';
import { PageState } from '../../components/PageState';
import { TablePagination } from '../../components/TablePagination';
import { Toast } from '../../components/Toast';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import {
  bulkUpdateProducts,
  createBrand,
  createCategory,
  createProduct,
  deleteProduct,
  fetchProducts,
  updateProduct,
} from './api';
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

function productGalleryUrls(product: Product) {
  const urls = product.gallery
    .map((image) => {
      if (typeof image === 'string') return image;
      if (!image || typeof image !== 'object') return '';
      const record = image as Record<string, unknown>;
      return String(record.original_url || record.url || record.src || '');
    })
    .filter(Boolean);
  if (product.thumbnail_url) urls.unshift(product.thumbnail_url);
  return [...new Set(urls)];
}

export function ProductsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search);
  const [editor, setEditor] = useState<Product | 'new' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Product | null>(null);
  const [galleryTarget, setGalleryTarget] = useState<Product | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkEditorOpen, setBulkEditorOpen] = useState(false);
  const [bulkBrand, setBulkBrand] = useState('');
  const [bulkCategory, setBulkCategory] = useState('');
  const [bulkCell, setBulkCell] = useState('');
  const [taxonomyEditor, setTaxonomyEditor] = useState<{
    kind: 'brand' | 'category';
    brand: string;
  } | null>(null);
  const [taxonomyName, setTaxonomyName] = useState('');
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

  useEffect(() => {
    if (searchParams.get('open_add') === '1') {
      setEditor('new');
    }
  }, [searchParams]);

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
  const bulkMutation = useMutation({
    mutationFn: () => {
      const changes = {
        ...(bulkBrand ? { brand: bulkBrand } : {}),
        ...(bulkCategory ? { category: bulkCategory } : {}),
        ...(bulkCell ? { cell: bulkCell } : {}),
      };
      return bulkUpdateProducts([...selectedIds], changes);
    },
    onSuccess: async (result) => {
      await invalidate();
      setBulkEditorOpen(false);
      setSelectedIds(new Set());
      setBulkBrand('');
      setBulkCategory('');
      setBulkCell('');
      setToast({
        message: `Массово обновлено товаров: ${result.updated}`,
        kind: result.errors.length ? 'error' : 'success',
      });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const taxonomyMutation = useMutation({
    mutationFn: () => {
      if (!taxonomyEditor) throw new Error('Не выбран тип справочника');
      return taxonomyEditor.kind === 'brand'
        ? createBrand(taxonomyName)
        : createCategory(taxonomyEditor.brand, taxonomyName);
    },
    onSuccess: async () => {
      await invalidate();
      setToast({
        message: taxonomyEditor?.kind === 'brand' ? 'Бренд создан' : 'Категория создана',
        kind: 'success',
      });
      setTaxonomyEditor(null);
      setTaxonomyName('');
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
        id: 'select',
        header: 'Выбор',
        enableSorting: false,
        enableHiding: false,
        size: 52,
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={`Выбрать ${row.original.name}`}
            checked={selectedIds.has(row.original.id)}
            onChange={(event) => {
              setSelectedIds((current) => {
                const next = new Set(current);
                if (event.target.checked) next.add(row.original.id);
                else next.delete(row.original.id);
                return next;
              });
            }}
          />
        ),
      },
      {
        id: 'photo',
        header: 'Фото',
        enableSorting: false,
        size: 74,
        cell: ({ row }) => (
          <button
            className="thumbnail-button"
            type="button"
            onClick={() => setGalleryTarget(row.original)}
            aria-label={`Открыть изображения ${row.original.name}`}
          >
            <ProductImage product={row.original} />
          </button>
        ),
      },
      {
        id: 'name',
        accessorKey: 'name',
        header: 'Название',
        size: 270,
        cell: ({ row }) => (
          <div className="product-cell">
            <div>
              <strong>{row.original.name}</strong>
              <small>{row.original.barcode || 'Без штрихкода'}</small>
            </div>
          </div>
        ),
      },
      {
        id: 'price',
        accessorKey: 'price_display',
        header: 'Цена',
        enableSorting: false,
        size: 125,
        cell: ({ row }) => row.original.price_display || '—',
      },
      {
        id: 'article',
        accessorKey: 'article',
        header: 'Артикул',
        size: 135,
        cell: ({ row }) => row.original.article || '—',
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
    [selectedIds],
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
            <LiveSearch
              label="Поиск товаров"
              value={search}
              onChange={setSearch}
              placeholder="Название, артикул, штрихкод, ячейка…"
            />
            <FilterPanel>
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
                <DateRangePicker
                  from={searchParams.get('date_from') ?? ''}
                  to={searchParams.get('date_to') ?? ''}
                  onFromChange={(value) => setFilter('date_from', value)}
                  onToChange={(value) => setFilter('date_to', value)}
                />
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
            </FilterPanel>
          </div>
          <div className="bulk-toolbar">
            <label>
              <input
                type="checkbox"
                checked={products.length > 0 && products.every((product) => selectedIds.has(product.id))}
                onChange={(event) => {
                  setSelectedIds((current) => {
                    const next = new Set(current);
                    for (const product of products) {
                      if (event.target.checked) next.add(product.id);
                      else next.delete(product.id);
                    }
                    return next;
                  });
                }}
              />
              Выбрать текущую страницу
            </label>
            {selectedIds.size ? (
              <>
                <strong>Выбрано: {selectedIds.size}</strong>
                <button className="button secondary" type="button" onClick={() => setBulkEditorOpen(true)}>
                  Изменить выбранные
                </button>
                <button type="button" onClick={() => setSelectedIds(new Set())}>
                  Снять выбор
                </button>
              </>
            ) : null}
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
                storageKey="vechasu:products-table"
                renderMobileCard={(product) => (
                  <article className="mobile-product-card">
                    <input
                      type="checkbox"
                      aria-label={`Выбрать ${product.name}`}
                      checked={selectedIds.has(product.id)}
                      onChange={(event) => {
                        setSelectedIds((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(product.id);
                          else next.delete(product.id);
                          return next;
                        });
                      }}
                    />
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
          onCreateBrand={() => setTaxonomyEditor({ kind: 'brand', brand: '' })}
          onCreateCategory={(brand) => {
            if (!brand.trim()) {
              setToast({ message: 'Сначала укажите бренд', kind: 'error' });
              return;
            }
            setTaxonomyEditor({ kind: 'category', brand });
          }}
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
      <Modal
        open={taxonomyEditor !== null}
        title={taxonomyEditor?.kind === 'brand' ? 'Новый бренд' : 'Новая категория'}
        description={
          taxonomyEditor?.kind === 'category'
            ? `Бренд: ${taxonomyEditor.brand}`
            : 'Значение появится в общем справочнике.'
        }
        onClose={() => setTaxonomyEditor(null)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setTaxonomyEditor(null)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="button"
              disabled={!taxonomyName.trim() || taxonomyMutation.isPending}
              onClick={() => taxonomyMutation.mutate()}
            >
              Создать
            </button>
          </>
        }
      >
        <label className="form-field">
          <span>Название</span>
          <input value={taxonomyName} onChange={(event) => setTaxonomyName(event.target.value)} />
        </label>
      </Modal>
      <Modal
        open={bulkEditorOpen}
        title={`Изменить выбранные товары (${selectedIds.size})`}
        description="Заполненные поля будут применены ко всем выбранным карточкам."
        onClose={() => setBulkEditorOpen(false)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setBulkEditorOpen(false)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="button"
              disabled={
                bulkMutation.isPending || (!bulkBrand && !bulkCategory && !bulkCell)
              }
              onClick={() => bulkMutation.mutate()}
            >
              {bulkMutation.isPending ? 'Применяем…' : 'Применить'}
            </button>
          </>
        }
      >
        <div className="erp-form">
          <label className="form-field">
            <span>Бренд</span>
            <input value={bulkBrand} onChange={(event) => setBulkBrand(event.target.value)} />
          </label>
          <label className="form-field">
            <span>Категория</span>
            <input value={bulkCategory} onChange={(event) => setBulkCategory(event.target.value)} />
          </label>
          <label className="form-field">
            <span>Ячейка</span>
            <input value={bulkCell} onChange={(event) => setBulkCell(event.target.value)} />
          </label>
        </div>
      </Modal>
      <Modal
        open={galleryTarget !== null}
        title={galleryTarget?.name ?? 'Изображения товара'}
        description="Основное и дополнительные изображения карточки"
        onClose={() => setGalleryTarget(null)}
      >
        {galleryTarget && productGalleryUrls(galleryTarget).length ? (
          <div className="product-gallery">
            {productGalleryUrls(galleryTarget).map((url, index) => (
              <img
                key={`${url}-${index}`}
                src={url}
                alt={`${galleryTarget.name}, изображение ${index + 1}`}
              />
            ))}
          </div>
        ) : (
          <PageState title="Изображений нет" message="Для этой карточки фотографии не добавлены." />
        )}
      </Modal>
      {toast ? <Toast {...toast} onClose={() => setToast(null)} /> : null}
    </AppShell>
  );
}
