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
  createSale,
  deleteSale,
  fetchSaleCatalog,
  fetchSaleLocations,
  fetchSales,
  returnSale,
  updateSale,
} from './api';
import { SaleForm } from './SaleForm';
import type { Sale, SaleFormValues } from './schemas';

const sourceTabs = [
  { key: 'all', label: 'Все продажи' },
  { key: 'tictactoy', label: 'Tictactoy' },
  { key: 'wildberries', label: 'Wildberries' },
  { key: 'amazon', label: 'Amazon' },
];

function errorMessage(error: unknown) {
  return error instanceof ApiRequestError ? error.message : 'Не удалось выполнить запрос';
}

function formatMoney(value: number | null) {
  if (value === null) return '—';
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽`;
}

export function SalesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search);
  const [editor, setEditor] = useState<Sale | 'new' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Sale | null>(null);
  const [returnTarget, setReturnTarget] = useState<Sale | null>(null);
  const [returnQuantity, setReturnQuantity] = useState('1');
  const [returnReason, setReturnReason] = useState('');
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
    if (!next.has('source')) next.set('source', 'all');
    if (!next.has('page')) next.set('page', '1');
    if (!next.has('page_size')) next.set('page_size', '50');
    return next;
  }, [searchParams]);
  const salesQuery = useQuery({
    queryKey: ['sales', normalizedParams.toString()],
    queryFn: () => fetchSales(normalizedParams),
    placeholderData: (previous) => previous,
  });
  const catalogQuery = useQuery({
    queryKey: ['sale-catalog'],
    queryFn: fetchSaleCatalog,
    enabled: editor !== null,
    staleTime: 60_000,
  });
  const locationsQuery = useQuery({
    queryKey: ['sale-locations'],
    queryFn: fetchSaleLocations,
    enabled: editor !== null,
    staleTime: 24 * 60 * 60 * 1000,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['sales'] });
  const saveMutation = useMutation({
    mutationFn: ({ sale, values }: { sale: Sale | 'new'; values: SaleFormValues }) =>
      sale === 'new' ? createSale(values) : updateSale(sale.id, values),
    onSuccess: async (_, variables) => {
      await invalidate();
      setEditor(null);
      setToast({
        message: variables.sale === 'new' ? 'Продажа добавлена' : 'Изменения сохранены',
        kind: 'success',
      });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteSale,
    onSuccess: async () => {
      await invalidate();
      setDeleteTarget(null);
      setToast({ message: 'Продажа удалена', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const returnMutation = useMutation({
    mutationFn: ({ id, quantity, reason }: { id: string; quantity: number; reason: string }) =>
      returnSale(id, quantity, reason),
    onSuccess: async () => {
      await invalidate();
      setReturnTarget(null);
      setReturnQuantity('1');
      setReturnReason('');
      setToast({ message: 'Возврат оформлен', kind: 'success' });
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
  const sales = salesQuery.data?.sales ?? [];
  const meta = salesQuery.data?.meta;
  const activeSource = searchParams.get('source') ?? 'all';
  const sorting: SortingState = [{
    id: searchParams.get('sort_by') ?? 'created_at',
    desc: searchParams.get('sort_dir') !== 'asc',
  }];

  const columns = useMemo<ColumnDef<Sale>[]>(
    () => [
      {
        id: 'created_at',
        accessorKey: 'created_at',
        header: 'Дата',
        size: 115,
        cell: ({ row }) => row.original.created_at.slice(0, 10).split('-').reverse().join('.'),
      },
      {
        id: 'order_number',
        accessorKey: 'order_number',
        header: 'Заказ',
        size: 135,
        cell: ({ row }) => row.original.order_number || '—',
      },
      {
        id: 'product_name',
        accessorKey: 'product_name',
        header: 'Товар',
        size: 260,
        cell: ({ row }) => (
          <div className="sale-product-cell">
            <strong>{row.original.product_name}</strong>
            <small>{[row.original.brand, row.original.category].filter(Boolean).join(' · ')}</small>
          </div>
        ),
      },
      {
        id: 'source',
        accessorKey: 'source',
        header: 'Источник',
        size: 125,
        cell: ({ row }) => <span className="source-badge">{row.original.source}</span>,
      },
      {
        id: 'quantity_value',
        accessorKey: 'quantity',
        header: 'Кол-во',
        size: 90,
      },
      {
        id: 'total_amount',
        accessorKey: 'total_amount',
        header: 'Сумма',
        size: 130,
        cell: ({ row }) => formatMoney(row.original.total_amount),
      },
      {
        id: 'order_status',
        accessorKey: 'order_status_label',
        header: 'Статус',
        size: 145,
        cell: ({ row }) => (
          <span
            className={`sale-status is-${row.original.order_status}${
              row.original.returned_quantity ? ' has-return' : ''
            }`}
          >
            {row.original.order_status_label}
          </span>
        ),
      },
      {
        id: 'actions',
        header: 'Действия',
        enableSorting: false,
        enableHiding: false,
        size: 205,
        cell: ({ row }) => (
          <div className="row-actions">
            <button type="button" onClick={() => setEditor(row.original)}>
              Изменить
            </button>
            {row.original.inventory_managed && row.original.return_available_quantity > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setReturnTarget(row.original);
                  setReturnQuantity(String(row.original.return_available_quantity));
                }}
              >
                Возврат
              </button>
            ) : null}
            {!row.original.inventory_managed ? (
              <button
                className="danger-link"
                type="button"
                onClick={() => setDeleteTarget(row.original)}
              >
                Удалить
              </button>
            ) : null}
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
            <p className="page-eyebrow">Коммерческие операции</p>
            <h1>Продажи</h1>
            <p>Ручные и автоматические продажи, статусы и возвраты</p>
          </div>
          <div className="header-actions">
            <a className="button secondary" href="/sales/report">
              Отчёт
            </a>
            <button className="button primary" type="button" onClick={() => setEditor('new')}>
              + Новая продажа
            </button>
          </div>
        </header>

        <section className="summary-grid sales-summary" aria-label="Сводка по продажам">
          <article>
            <span>Продаж</span>
            <strong>{meta?.totals.active ?? '—'}</strong>
          </article>
          <article>
            <span>Единиц</span>
            <strong>{meta?.totals.quantity ?? '—'}</strong>
          </article>
          <article>
            <span>Выручка</span>
            <strong>{meta ? formatMoney(meta.totals.revenue) : '—'}</strong>
          </article>
          <article>
            <span>Возвраты</span>
            <strong>{meta ? formatMoney(meta.totals.returned) : '—'}</strong>
          </article>
        </section>

        <section className="workspace-card">
          <div className="source-tabs" role="tablist" aria-label="Источники продаж">
            {sourceTabs.map((tab) => (
              <button
                key={tab.key}
                role="tab"
                type="button"
                aria-selected={activeSource === tab.key}
                className={activeSource === tab.key ? 'is-active' : ''}
                onClick={() => setFilter('source', tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="list-toolbar">
            <LiveSearch
              label="Поиск продаж"
              value={search}
              onChange={setSearch}
              placeholder="Заказ, товар, трек-номер, получатель…"
            />
            <FilterPanel>
                <DateRangePicker
                  from={searchParams.get('date_from') ?? ''}
                  to={searchParams.get('date_to') ?? ''}
                  onFromChange={(value) => setFilter('date_from', value)}
                  onToChange={(value) => setFilter('date_to', value)}
                />
                <label>
                  Тип
                  <select
                    value={searchParams.get('sale_type') ?? ''}
                    onChange={(event) => setFilter('sale_type', event.target.value)}
                  >
                    <option value="">Все типы</option>
                    <option value="manual">Ручные</option>
                    <option value="automatic">Автоматические</option>
                  </select>
                </label>
                <label>
                  Статус
                  <select
                    value={searchParams.get('status') ?? ''}
                    onChange={(event) => setFilter('status', event.target.value)}
                  >
                    <option value="">Все статусы</option>
                    <option value="completed">Выполнен</option>
                    <option value="processing">В работе</option>
                    <option value="cancelled">Отменён</option>
                    <option value="partially_returned">Частичный возврат</option>
                    <option value="returned">Возвращён</option>
                  </select>
                </label>
                <label>
                  Бренд
                  <select
                    value={searchParams.get('brand') ?? ''}
                    onChange={(event) => setFilter('brand', event.target.value)}
                  >
                    <option value="">Все бренды</option>
                    {meta?.facets.brands.map((item) => (
                      <option key={item}>{item}</option>
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
                      <option key={item}>{item}</option>
                    ))}
                  </select>
                </label>
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => {
                    setSearch('');
                    setSearchParams({
                      source: activeSource,
                      page: '1',
                      page_size: String(meta?.page_size ?? 50),
                    });
                  }}
                >
                  Сбросить
                </button>
            </FilterPanel>
          </div>

          {salesQuery.isError ? (
            <PageState
              kind="error"
              title="Не удалось загрузить продажи"
              message={errorMessage(salesQuery.error)}
              action={
                <button className="button secondary" type="button" onClick={() => salesQuery.refetch()}>
                  Повторить
                </button>
              }
            />
          ) : salesQuery.isPending ? (
            <div className="table-loading" role="status">
              Загружаем продажи…
            </div>
          ) : sales.length === 0 ? (
            <PageState
              title="Продажи не найдены"
              message="Измените фильтры или добавьте ручную продажу."
              action={
                <button className="button primary" type="button" onClick={() => setEditor('new')}>
                  Новая продажа
                </button>
              }
            />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={sales}
                sorting={sorting}
                onSortingChange={(nextSorting) => {
                  const next = nextSorting[0];
                  const updated = new URLSearchParams(searchParams);
                  updated.set('sort_by', next?.id ?? 'created_at');
                  updated.set('sort_dir', next?.desc ? 'desc' : 'asc');
                  updated.set('page', '1');
                  setSearchParams(updated);
                }}
                getRowId={(sale) => sale.id}
                storageKey="vechasu:sales-table"
                renderMobileCard={(sale) => (
                  <article className="mobile-document-card mobile-sale-card">
                    <div>
                      <strong>{sale.order_number || sale.sale_type_label}</strong>
                      <time>{sale.created_at.slice(0, 10).split('-').reverse().join('.')}</time>
                    </div>
                    <h2>{sale.product_name}</h2>
                    <p>
                      {sale.quantity_display} шт. · {formatMoney(sale.total_amount)} · {sale.source}
                    </p>
                    <div className="row-actions">
                      <button type="button" onClick={() => setEditor(sale)}>
                        Изменить
                      </button>
                      {sale.inventory_managed && sale.return_available_quantity > 0 ? (
                        <button
                          type="button"
                          onClick={() => {
                            setReturnTarget(sale);
                            setReturnQuantity(String(sale.return_available_quantity));
                          }}
                        >
                          Возврат
                        </button>
                      ) : null}
                      {!sale.inventory_managed ? (
                        <button
                          className="danger-link"
                          type="button"
                          onClick={() => setDeleteTarget(sale)}
                        >
                          Удалить
                        </button>
                      ) : null}
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
        size="large"
        title={editor === 'new' ? 'Новая продажа' : 'Редактирование продажи'}
        description={
          editor !== 'new' && editor?.inventory_managed
            ? 'Товар и количество проведённой продажи защищены. Для изменения остатка оформите возврат.'
            : 'Данные проверяются на сервере перед сохранением.'
        }
        onClose={() => setEditor(null)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setEditor(null)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="submit"
              form="sale-editor"
              disabled={saveMutation.isPending || catalogQuery.isPending}
            >
              {saveMutation.isPending ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </>
        }
      >
        {catalogQuery.isError || locationsQuery.isError ? (
          <PageState
            kind="error"
            title="Справочники недоступны"
            message={errorMessage(catalogQuery.error || locationsQuery.error)}
          />
        ) : catalogQuery.isPending || locationsQuery.isPending ? (
          <div className="table-loading">Загружаем каталог…</div>
        ) : (
          <SaleForm
            id="sale-editor"
            sale={editor === 'new' ? null : editor}
            products={catalogQuery.data ?? []}
            locations={locationsQuery.data ?? {}}
            onSubmit={(values) => {
              if (editor) saveMutation.mutate({ sale: editor, values });
            }}
          />
        )}
      </Modal>
      <Modal
        open={returnTarget !== null}
        title="Оформить возврат"
        description={`Продажа ${returnTarget?.order_number || returnTarget?.id || ''}`}
        onClose={() => setReturnTarget(null)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setReturnTarget(null)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="button"
              disabled={returnMutation.isPending}
              onClick={() => {
                if (returnTarget) {
                  returnMutation.mutate({
                    id: returnTarget.id,
                    quantity: Number(returnQuantity),
                    reason: returnReason,
                  });
                }
              }}
            >
              {returnMutation.isPending ? 'Оформляем…' : 'Оформить возврат'}
            </button>
          </>
        }
      >
        <div className="erp-form">
          <label className="form-field">
            <span>Количество</span>
            <input
              type="number"
              min="1"
              max={returnTarget?.return_available_quantity}
              value={returnQuantity}
              onChange={(event) => setReturnQuantity(event.target.value)}
            />
            <small>Доступно: {returnTarget?.return_available_quantity ?? 0}</small>
          </label>
          <label className="form-field">
            <span>Причина</span>
            <input value={returnReason} onChange={(event) => setReturnReason(event.target.value)} />
          </label>
        </div>
      </Modal>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить продажу?"
        message="Запись будет скрыта из списка. Проведённые продажи удаляются только через возврат."
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
