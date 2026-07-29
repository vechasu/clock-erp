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
  createReceipt,
  deleteReceipt,
  fetchReceiptCatalog,
  fetchReceipts,
  updateReceipt,
} from './api';
import { ReceiptForm } from './ReceiptForm';
import type { Receipt, ReceiptFormValues } from './schemas';

function errorMessage(error: unknown) {
  return error instanceof ApiRequestError ? error.message : 'Не удалось выполнить запрос';
}

function formatMoney(value: number) {
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽`;
}

export function ReceiptsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search);
  const [editor, setEditor] = useState<Receipt | 'new' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Receipt | null>(null);
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
  const receiptsQuery = useQuery({
    queryKey: ['receipts', normalizedParams.toString()],
    queryFn: () => fetchReceipts(normalizedParams),
    placeholderData: (previous) => previous,
  });
  const catalogQuery = useQuery({
    queryKey: ['receipt-catalog'],
    queryFn: () => fetchReceiptCatalog(),
    enabled: editor !== null,
    staleTime: 60_000,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['receipts'] });
  const saveMutation = useMutation({
    mutationFn: ({ receipt, values }: { receipt: Receipt | 'new'; values: ReceiptFormValues }) =>
      receipt === 'new' ? createReceipt(values) : updateReceipt(receipt.id, values),
    onSuccess: async (_, variables) => {
      await invalidate();
      setEditor(null);
      setToast({
        message: variables.receipt === 'new' ? 'Приход проведён' : 'Приход обновлён',
        kind: 'success',
      });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteReceipt,
    onSuccess: async () => {
      await invalidate();
      setDeleteTarget(null);
      setToast({ message: 'Приход удалён', kind: 'success' });
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
  const receipts = receiptsQuery.data?.receipts ?? [];
  const meta = receiptsQuery.data?.meta;
  const sorting: SortingState = [{
    id: searchParams.get('sort_by') ?? 'receipt_date',
    desc: searchParams.get('sort_dir') !== 'asc',
  }];

  const columns = useMemo<ColumnDef<Receipt>[]>(
    () => [
      {
        id: 'receipt_date',
        accessorKey: 'receipt_date',
        header: 'Дата',
        size: 120,
        cell: ({ row }) => (
          <time dateTime={row.original.receipt_date}>
            {row.original.receipt_date.split('-').reverse().join('.')}
          </time>
        ),
      },
      {
        id: 'number',
        accessorKey: 'number',
        header: 'Номер',
        size: 150,
        cell: ({ row }) => <strong className="document-number">{row.original.number}</strong>,
      },
      {
        id: 'product_name',
        accessorKey: 'product_name',
        header: 'Товары',
        enableSorting: false,
        size: 300,
        cell: ({ row }) => (
          <div className="receipt-products-cell">
            <strong>{row.original.positions[0]?.product_name || row.original.product_name}</strong>
            <small>
              {row.original.positions_count > 1
                ? `Ещё ${row.original.positions_count - 1} поз.`
                : [row.original.brand, row.original.category].filter(Boolean).join(' · ')}
            </small>
          </div>
        ),
      },
      {
        id: 'total_quantity',
        accessorKey: 'total_quantity',
        header: 'Количество',
        size: 120,
      },
      {
        id: 'total_amount',
        accessorKey: 'total_amount',
        header: 'Сумма',
        size: 140,
        cell: ({ row }) => formatMoney(row.original.total_amount),
      },
      {
        id: 'status',
        accessorKey: 'status_label',
        header: 'Статус',
        enableSorting: false,
        size: 120,
        cell: ({ row }) => <span className="status-badge">{row.original.status_label}</span>,
      },
      {
        id: 'actions',
        header: 'Действия',
        enableSorting: false,
        enableHiding: false,
        size: 150,
        cell: ({ row }) => (
          <div className="row-actions">
            <button
              type="button"
              disabled={row.original.positions_count !== 1}
              title={
                row.original.positions_count !== 1
                  ? 'Редактирование доступно только для одной позиции'
                  : undefined
              }
              onClick={() => setEditor(row.original)}
            >
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
            <p className="page-eyebrow">Складские документы</p>
            <h1>Приходы</h1>
            <p>Поступления товаров и синхронизация документов МоегоСклада</p>
          </div>
          <div className="header-actions">
            <a className="button secondary" href="/receipts/report">
              Отчёт
            </a>
            <button className="button primary" type="button" onClick={() => setEditor('new')}>
              + Новый приход
            </button>
          </div>
        </header>

        <section className="summary-grid" aria-label="Сводка по приходам">
          <article>
            <span>Приходов</span>
            <strong>{meta?.total ?? '—'}</strong>
          </article>
          <article>
            <span>Единиц принято</span>
            <strong>{meta?.totals.quantity ?? '—'}</strong>
          </article>
          <article>
            <span>Сумма закупки</span>
            <strong>{meta ? formatMoney(meta.totals.amount) : '—'}</strong>
          </article>
        </section>

        <section className="workspace-card">
          <div className="list-toolbar">
            <LiveSearch
              label="Поиск приходов"
              value={search}
              onChange={setSearch}
              placeholder="Номер, товар, бренд, комментарий…"
            />
            <FilterPanel>
                <DateRangePicker
                  from={searchParams.get('date_from') ?? ''}
                  to={searchParams.get('date_to') ?? ''}
                  onFromChange={(value) => setFilter('date_from', value)}
                  onToChange={(value) => setFilter('date_to', value)}
                />
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
                <label>
                  Статус
                  <select
                    value={searchParams.get('status') ?? ''}
                    onChange={(event) => setFilter('status', event.target.value)}
                  >
                    <option value="">Все статусы</option>
                    {meta?.facets.statuses.map((item) => (
                      <option key={item} value={item}>
                        {item === 'posted' ? 'Проведён' : item}
                      </option>
                    ))}
                  </select>
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

          {receiptsQuery.isError ? (
            <PageState
              kind="error"
              title="Не удалось загрузить приходы"
              message={errorMessage(receiptsQuery.error)}
              action={
                <button className="button secondary" type="button" onClick={() => receiptsQuery.refetch()}>
                  Повторить
                </button>
              }
            />
          ) : receiptsQuery.isPending ? (
            <div className="table-loading" role="status">
              Загружаем приходы…
            </div>
          ) : receipts.length === 0 ? (
            <PageState
              title="Приходов пока нет"
              message="Создайте первый документ поступления товаров."
              action={
                <button className="button primary" type="button" onClick={() => setEditor('new')}>
                  Новый приход
                </button>
              }
            />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={receipts}
                sorting={sorting}
                onSortingChange={(nextSorting) => {
                  const next = nextSorting[0];
                  const updated = new URLSearchParams(searchParams);
                  updated.set('sort_by', next?.id ?? 'receipt_date');
                  updated.set('sort_dir', next?.desc ? 'desc' : 'asc');
                  updated.set('page', '1');
                  setSearchParams(updated);
                }}
                getRowId={(receipt) => receipt.id}
                storageKey="vechasu:receipts-table"
                renderMobileCard={(receipt) => (
                  <article className="mobile-document-card">
                    <div>
                      <strong>{receipt.number}</strong>
                      <time>{receipt.receipt_date.split('-').reverse().join('.')}</time>
                    </div>
                    <h2>{receipt.positions[0]?.product_name || receipt.product_name}</h2>
                    <p>
                      {receipt.total_quantity} шт. · {formatMoney(receipt.total_amount)}
                    </p>
                    <div className="row-actions">
                      <button
                        type="button"
                        disabled={receipt.positions_count !== 1}
                        onClick={() => setEditor(receipt)}
                      >
                        Изменить
                      </button>
                      <button
                        className="danger-link"
                        type="button"
                        onClick={() => setDeleteTarget(receipt)}
                      >
                        Удалить
                      </button>
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
        title={editor === 'new' ? 'Новый приход' : `Приход ${editor?.number ?? ''}`}
        description="Документ проводится в МойСклад после серверной проверки."
        onClose={() => setEditor(null)}
        footer={
          <>
            <button className="button secondary" type="button" onClick={() => setEditor(null)}>
              Отмена
            </button>
            <button
              className="button primary"
              type="submit"
              form="receipt-editor"
              disabled={saveMutation.isPending || catalogQuery.isPending}
            >
              {saveMutation.isPending
                ? 'Сохраняем…'
                : editor === 'new'
                  ? 'Провести приход'
                  : 'Сохранить'}
            </button>
          </>
        }
      >
        {catalogQuery.isError ? (
          <PageState
            kind="error"
            title="Каталог недоступен"
            message={errorMessage(catalogQuery.error)}
          />
        ) : catalogQuery.isPending ? (
          <div className="table-loading">Загружаем каталог…</div>
        ) : (
          <ReceiptForm
            id="receipt-editor"
            receipt={editor === 'new' ? null : editor}
            products={catalogQuery.data ?? []}
            onSubmit={(values) => {
              if (editor) saveMutation.mutate({ receipt: editor, values });
            }}
          />
        )}
      </Modal>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Удалить приход?"
        message={`Документ ${deleteTarget?.number ?? ''} будет удалён в МойСклад и локальном журнале.`}
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
