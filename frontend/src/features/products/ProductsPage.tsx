import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef, SortingState } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiRequestError } from '../../api/client';
import { AppShell } from '../../components/AppShell';
import { DateRangePicker, FilterPanel, LiveSearch } from '../../components/Controls';
import { DataTable } from '../../components/DataTable';
import { Icon } from '../../components/Icons';
import {
  ActionLink,
  BulkActionBar,
  Button,
  LoadingState,
  PageHeader,
  StatsGrid,
  Toolbar,
} from '../../components/Layout';
import { ConfirmDialog, Modal } from '../../components/Modal';
import { PageState } from '../../components/PageState';
import { TablePagination } from '../../components/TablePagination';
import { Toast } from '../../components/Toast';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { CatalogCascade } from '../catalog/CatalogComboboxes';
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
      <Icon name="package" />
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
  const [bulkBrandId, setBulkBrandId] = useState<number | null>(null);
  const [bulkCategoryId, setBulkCategoryId] = useState<number | null>(null);
  const [bulkCell, setBulkCell] = useState('');
  const [taxonomyEditor, setTaxonomyEditor] = useState<{
    kind: 'brand' | 'category';
    brandId: number | null;
  } | null>(null);
  const [taxonomyName, setTaxonomyName] = useState('');
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null);

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
        ...(bulkBrandId ? { brand_id: bulkBrandId } : {}),
        ...(bulkCategoryId ? { category_id: bulkCategoryId } : {}),
        ...(bulkCell ? { cell: bulkCell } : {}),
      };
      return bulkUpdateProducts([...selectedIds], changes);
    },
    onSuccess: async (result) => {
      await invalidate();
      setBulkEditorOpen(false);
      setSelectedIds(new Set());
      setBulkBrandId(null);
      setBulkCategoryId(null);
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
        : createCategory(taxonomyEditor.brandId!, taxonomyName);
    },
    onSuccess: async () => {
      await invalidate();
      await queryClient.invalidateQueries({ queryKey: ['catalog-options'] });
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
  const setCatalogFilters = (
    brandId: number | null,
    categoryId: number | null,
    productId: string,
  ) => {
    const next = new URLSearchParams(searchParams);
    const values = {
      brand_id: brandId ? String(brandId) : '',
      category_id: categoryId ? String(categoryId) : '',
      product_id: productId,
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    next.delete('brand');
    next.delete('category');
    next.set('page', '1');
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
        meta: { align: 'right' },
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
        meta: { align: 'center' },
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
        meta: { align: 'right' },
        cell: ({ row }) => (
          <div className="row-actions">
            <button
              type="button"
              title="Редактировать товар"
              onClick={() => setEditor(row.original)}
            >
              Изменить
            </button>
            <button
              className="danger-link"
              type="button"
              title="Удалить товар"
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
        <PageHeader
          eyebrow="Складской каталог"
          title="Товары"
          description="Управление карточками, остатками и размещением на складе"
          actions={
            <>
              <ActionLink href="/warehouse/export.xlsx" icon="download">
                Excel
              </ActionLink>
              <ActionLink href="/warehouse/export.pdf" icon="download">
                PDF
              </ActionLink>
              <ActionLink href="/warehouse" icon="warehouse">
                Карта склада
              </ActionLink>
              <Button tone="primary" icon="plus" type="button" onClick={() => setEditor('new')}>
                Добавить товар
              </Button>
            </>
          }
        />

        <StatsGrid
          label="Сводка по товарам"
          loading={productsQuery.isPending}
          items={[
            { label: 'Позиций', value: meta?.total ?? '—', tone: 'info' },
            { label: 'Общий остаток', value: meta?.stats.total_stock ?? '—' },
            {
              label: 'В наличии',
              value: meta?.stats.positive_positions ?? '—',
              tone: 'success',
            },
          ]}
        />

        <section className="workspace-card">
          <Toolbar>
            <LiveSearch
              label="Поиск товаров"
              value={search}
              onChange={setSearch}
              placeholder="Название, артикул, штрихкод, ячейка…"
            />
            <FilterPanel
              count={
                [
                  'brand_id',
                  'category_id',
                  'product_id',
                  'cell',
                  'date_from',
                  'date_to',
                  'in_stock',
                ].filter((key) => searchParams.get(key)).length
              }
            >
              <CatalogCascade
                brandId={Number(searchParams.get('brand_id')) || null}
                categoryId={Number(searchParams.get('category_id')) || null}
                productId={searchParams.get('product_id') ?? ''}
                onBrandChange={(brandId) => setCatalogFilters(brandId, null, '')}
                onCategoryChange={(categoryId) =>
                  setCatalogFilters(Number(searchParams.get('brand_id')) || null, categoryId, '')
                }
                onProductChange={(productId) =>
                  setCatalogFilters(
                    Number(searchParams.get('brand_id')) || null,
                    Number(searchParams.get('category_id')) || null,
                    productId,
                  )
                }
              />
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
          </Toolbar>
          <div className="bulk-toolbar">
            <label>
              <input
                type="checkbox"
                checked={
                  products.length > 0 && products.every((product) => selectedIds.has(product.id))
                }
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
            {selectedIds.size ? <strong>Выбрано: {selectedIds.size}</strong> : null}
          </div>

          {productsQuery.isError ? (
            <PageState
              kind="error"
              title="Не удалось загрузить товары"
              message={errorMessage(productsQuery.error)}
              action={
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => productsQuery.refetch()}
                >
                  Повторить
                </button>
              }
            />
          ) : productsQuery.isPending ? (
            <LoadingState label="Загружаем товары…" />
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
              <BulkActionBar count={selectedIds.size} onClear={() => setSelectedIds(new Set())}>
                <Button tone="secondary" icon="edit" onClick={() => setBulkEditorOpen(true)}>
                  Изменить выбранные
                </Button>
              </BulkActionBar>
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
          onCreateBrand={() => setTaxonomyEditor({ kind: 'brand', brandId: null })}
          onCreateCategory={(brandId) => {
            if (!brandId) {
              setToast({ message: 'Сначала укажите бренд', kind: 'error' });
              return;
            }
            setTaxonomyEditor({ kind: 'category', brandId });
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
            ? `Категория будет связана с брендом ID ${taxonomyEditor.brandId}.`
            : 'Значение появится в общем справочнике.'
        }
        onClose={() => setTaxonomyEditor(null)}
        footer={
          <>
            <button
              className="button secondary"
              type="button"
              onClick={() => setTaxonomyEditor(null)}
            >
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
            <button
              className="button secondary"
              type="button"
              onClick={() => setBulkEditorOpen(false)}
            >
              Отмена
            </button>
            <button
              className="button primary"
              type="button"
              disabled={bulkMutation.isPending || (!bulkBrandId && !bulkCategoryId && !bulkCell)}
              onClick={() => bulkMutation.mutate()}
            >
              {bulkMutation.isPending ? 'Применяем…' : 'Применить'}
            </button>
          </>
        }
      >
        <div className="erp-form">
          <CatalogCascade
            showProduct={false}
            required={false}
            brandId={bulkBrandId}
            categoryId={bulkCategoryId}
            onBrandChange={(brandId) => {
              setBulkBrandId(brandId);
              setBulkCategoryId(null);
            }}
            onCategoryChange={(categoryId) => setBulkCategoryId(categoryId)}
          />
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
