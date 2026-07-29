import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef, SortingState } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiRequestError } from '../../api/client';
import { AppShell } from '../../components/AppShell';
import { DateRangePicker, FilterPanel, LiveSearch } from '../../components/Controls';
import { DataTable } from '../../components/DataTable';
import { FileUpload } from '../../components/FileUpload';
import {
  Button,
  LoadingState,
  PageHeader,
  StatsGrid,
  StatusBadge,
  Tabs,
  Toolbar,
} from '../../components/Layout';
import { ConfirmDialog, Modal } from '../../components/Modal';
import { PageState } from '../../components/PageState';
import { TablePagination } from '../../components/TablePagination';
import { Toast } from '../../components/Toast';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import {
  addRepairShipment,
  archiveRepair,
  changeRepairStatus,
  createRepair,
  fetchRepairCatalog,
  fetchRepairs,
  restoreRepair,
  updateRepair,
  uploadRepairAttachment,
} from './api';
import { RepairForm } from './RepairForm';
import type { Repair, RepairFormValues } from './schemas';

const viewTabs = [
  { key: 'active', label: 'Активные' },
  { key: 'archive', label: 'Архив' },
  { key: 'all', label: 'Все обращения' },
];

function errorMessage(error: unknown) {
  return error instanceof ApiRequestError ? error.message : 'Не удалось выполнить запрос';
}

function formatDate(value: string, withTime = false) {
  if (!value) return '—';
  const [date, time] = value.split(/[ T]/);
  const formatted = date.split('-').reverse().join('.');
  return withTime && time ? `${formatted}, ${time.slice(0, 5)}` : formatted;
}

function repairStatusTone(status: string) {
  if (status === 'completed') return 'success' as const;
  if (status === 'waiting_payment') return 'warning' as const;
  if (status.includes('transit')) return 'info' as const;
  if (status === 'at_master' || status === 'waiting_decision') return 'purple' as const;
  return 'neutral' as const;
}

export function RepairsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const debouncedSearch = useDebouncedValue(search);
  const [editor, setEditor] = useState<Repair | 'new' | null>(null);
  const [detailTarget, setDetailTarget] = useState<Repair | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<Repair | null>(null);
  const [shipmentTarget, setShipmentTarget] = useState<Repair | null>(null);
  const [attachment, setAttachment] = useState<File | null>(null);
  const [shipment, setShipment] = useState({
    direction: 'inbound',
    carrier: '',
    track_number: '',
    sent_at: new Date().toISOString().slice(0, 10),
    status: '',
    received_at: '',
  });
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

  const normalizedParams = useMemo(() => {
    const next = new URLSearchParams(searchParams);
    if (!next.has('view')) next.set('view', 'active');
    if (!next.has('page')) next.set('page', '1');
    if (!next.has('page_size')) next.set('page_size', '50');
    return next;
  }, [searchParams]);
  const repairsQuery = useQuery({
    queryKey: ['repairs', normalizedParams.toString()],
    queryFn: () => fetchRepairs(normalizedParams),
    placeholderData: (previous) => previous,
  });
  const catalogQuery = useQuery({
    queryKey: ['repair-catalog'],
    queryFn: fetchRepairCatalog,
    enabled: editor !== null,
    staleTime: 60_000,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['repairs'] });
  const saveMutation = useMutation({
    mutationFn: ({ repair, values }: { repair: Repair | 'new'; values: RepairFormValues }) =>
      repair === 'new' ? createRepair(values) : updateRepair(repair.id, values),
    onSuccess: async (_, variables) => {
      await invalidate();
      setEditor(null);
      setToast({
        message: variables.repair === 'new' ? 'Обращение создано' : 'Обращение обновлено',
        kind: 'success',
      });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const archiveMutation = useMutation({
    mutationFn: archiveRepair,
    onSuccess: async () => {
      await invalidate();
      setArchiveTarget(null);
      setDetailTarget(null);
      setToast({ message: 'Обращение перенесено в архив', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const restoreMutation = useMutation({
    mutationFn: restoreRepair,
    onSuccess: async () => {
      await invalidate();
      setDetailTarget(null);
      setToast({ message: 'Обращение восстановлено', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => changeRepairStatus(id, status),
    onSuccess: async (repair) => {
      await invalidate();
      setDetailTarget(repair);
      setToast({ message: 'Статус обновлён', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const shipmentMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => addRepairShipment(id, shipment),
    onSuccess: async (repair) => {
      await invalidate();
      setShipmentTarget(null);
      setDetailTarget(repair);
      setShipment({
        direction: 'inbound',
        carrier: '',
        track_number: '',
        sent_at: new Date().toISOString().slice(0, 10),
        status: '',
        received_at: '',
      });
      setToast({ message: 'Накладная добавлена', kind: 'success' });
    },
    onError: (error) => setToast({ message: errorMessage(error), kind: 'error' }),
  });
  const attachmentMutation = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) => uploadRepairAttachment(id, file),
    onSuccess: async (repair) => {
      await invalidate();
      setDetailTarget(repair);
      setAttachment(null);
      setToast({ message: 'Вложение загружено', kind: 'success' });
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
  const repairs = repairsQuery.data?.repairs ?? [];
  const meta = repairsQuery.data?.meta;
  const activeView = searchParams.get('view') ?? 'active';
  const sorting: SortingState = [
    {
      id: searchParams.get('sort_by') ?? 'request_at',
      desc: searchParams.get('sort_dir') !== 'asc',
    },
  ];

  const columns = useMemo<ColumnDef<Repair>[]>(
    () => [
      {
        id: 'request_at',
        accessorKey: 'request_at',
        header: 'Обращение',
        size: 118,
        cell: ({ row }) => formatDate(row.original.request_at || row.original.created_at),
      },
      {
        id: 'repair_number',
        accessorKey: 'repair_number',
        header: 'Номер',
        size: 142,
        cell: ({ row }) => (
          <button
            className="document-number repair-open-link"
            type="button"
            title="Открыть карточку обращения"
            onClick={() => setDetailTarget(row.original)}
          >
            {row.original.repair_number}
          </button>
        ),
      },
      {
        id: 'client_name',
        accessorKey: 'client_name',
        header: 'Клиент',
        size: 190,
        cell: ({ row }) => (
          <div className="sale-product-cell" title={row.original.client_name}>
            <strong>{row.original.client_name || '—'}</strong>
            <small>
              {row.original.client_phone || row.original.contact || 'Контакт не указан'}
            </small>
          </div>
        ),
      },
      {
        id: 'product_name',
        accessorKey: 'product_name',
        header: 'Товар и проблема',
        size: 300,
        cell: ({ row }) => (
          <div
            className="sale-product-cell"
            title={`${row.original.product_name}. ${row.original.problem}`}
          >
            <strong>{row.original.product_name || '—'}</strong>
            <small>{row.original.problem || 'Неисправность не указана'}</small>
          </div>
        ),
      },
      {
        id: 'status',
        accessorKey: 'status',
        header: 'Статус',
        size: 174,
        meta: { align: 'center' },
        cell: ({ row }) => (
          <StatusBadge
            label={row.original.status_label}
            tone={repairStatusTone(row.original.status)}
          />
        ),
      },
      {
        id: 'location',
        accessorKey: 'location',
        header: 'Где находится',
        size: 150,
        meta: { align: 'center' },
        cell: ({ row }) => (
          <StatusBadge
            label={row.original.location_label}
            tone={row.original.location === 'unknown' ? 'warning' : 'neutral'}
          />
        ),
      },
      {
        id: 'updated_at',
        accessorKey: 'updated_at',
        header: 'Обновлено',
        size: 150,
        cell: ({ row }) => formatDate(row.original.updated_at, true),
      },
      {
        id: 'actions',
        header: 'Действия',
        enableSorting: false,
        enableHiding: false,
        size: 235,
        meta: { align: 'right' },
        cell: ({ row }) => (
          <div className="row-actions">
            <button
              type="button"
              title="Открыть обращение"
              onClick={() => setDetailTarget(row.original)}
            >
              Открыть
            </button>
            <button
              type="button"
              title="Редактировать обращение"
              onClick={() => setEditor(row.original)}
            >
              Изменить
            </button>
            {row.original.is_archived ? (
              <button
                type="button"
                title="Восстановить обращение"
                onClick={() => restoreMutation.mutate(row.original.id)}
              >
                Восстановить
              </button>
            ) : (
              <button
                className="danger-link"
                type="button"
                title="Перенести обращение в архив"
                onClick={() => setArchiveTarget(row.original)}
              >
                В архив
              </button>
            )}
          </div>
        ),
      },
    ],
    [restoreMutation],
  );

  return (
    <AppShell>
      <div className="erp-page">
        <PageHeader
          eyebrow="Сервис и гарантия"
          title="Ремонт"
          description="Обращения клиентов, диагностика, логистика и статусы ремонта"
          actions={
            <Button tone="primary" icon="plus" onClick={() => setEditor('new')}>
              Новое обращение
            </Button>
          }
        />

        <StatsGrid
          label="Сводка по ремонту"
          loading={repairsQuery.isPending}
          items={[
            { label: 'Активные', value: meta?.stats.active ?? '—', tone: 'info' },
            { label: 'У нас', value: meta?.stats.at_us ?? '—' },
            { label: 'У мастера', value: meta?.stats.at_master ?? '—', tone: 'purple' },
            { label: 'В доставке', value: meta?.stats.delivery ?? '—' },
            {
              label: 'Ожидают оплату',
              value: meta?.stats.waiting_payment ?? '—',
              tone: meta?.stats.waiting_payment ? 'warning' : 'default',
            },
          ]}
        />

        <section className="workspace-card">
          <Tabs
            label="Обращения по состоянию"
            active={activeView}
            items={viewTabs.map((tab) => ({
              ...tab,
              count:
                tab.key === 'active'
                  ? meta?.stats.active
                  : tab.key === 'archive'
                    ? meta?.stats.archived
                    : undefined,
            }))}
            onChange={(key) => setFilter('view', key)}
          />
          <Toolbar>
            <LiveSearch
              label="Поиск ремонтов"
              value={search}
              onChange={setSearch}
              placeholder="Номер, клиент, товар, проблема, трек-номер…"
            />
            <FilterPanel
              count={
                ['status', 'type', 'location', 'channel', 'date_from', 'date_to'].filter((key) =>
                  searchParams.get(key),
                ).length
              }
            >
              <DateRangePicker
                from={searchParams.get('date_from') ?? ''}
                to={searchParams.get('date_to') ?? ''}
                onFromChange={(value) => setFilter('date_from', value)}
                onToChange={(value) => setFilter('date_to', value)}
              />
              {[
                ['status', 'Статус', meta?.facets.statuses ?? []],
                ['type', 'Тип обращения', meta?.facets.types ?? []],
                ['location', 'Местонахождение', meta?.facets.locations ?? []],
                ['channel', 'Канал связи', meta?.facets.channels ?? []],
              ].map(([key, label, options]) => (
                <label key={String(key)}>
                  {String(label)}
                  <select
                    value={searchParams.get(String(key)) ?? ''}
                    onChange={(event) => setFilter(String(key), event.target.value)}
                  >
                    <option value="">Все</option>
                    {(options as Array<{ value: string; label: string }>).map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <Button
                tone="secondary"
                type="button"
                onClick={() => {
                  setSearch('');
                  setSearchParams({
                    view: activeView,
                    page: '1',
                    page_size: String(meta?.page_size ?? 50),
                  });
                }}
              >
                Сбросить
              </Button>
            </FilterPanel>
          </Toolbar>

          {repairsQuery.isError ? (
            <PageState
              kind="error"
              title="Не удалось загрузить обращения"
              message={errorMessage(repairsQuery.error)}
              action={
                <Button tone="secondary" icon="refresh" onClick={() => repairsQuery.refetch()}>
                  Повторить
                </Button>
              }
            />
          ) : repairsQuery.isPending ? (
            <LoadingState label="Загружаем обращения…" />
          ) : repairs.length === 0 ? (
            <PageState
              title={activeView === 'archive' ? 'Архив пуст' : 'Обращения не найдены'}
              message={
                activeView === 'archive'
                  ? 'Завершённые и архивные обращения появятся здесь.'
                  : 'Измените фильтры или создайте новое обращение.'
              }
              action={
                activeView !== 'archive' ? (
                  <Button tone="primary" icon="plus" onClick={() => setEditor('new')}>
                    Новое обращение
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={repairs}
                sorting={sorting}
                onSortingChange={(nextSorting) => {
                  const next = nextSorting[0];
                  const updated = new URLSearchParams(searchParams);
                  updated.set('sort_by', next?.id ?? 'request_at');
                  updated.set('sort_dir', next?.desc ? 'desc' : 'asc');
                  updated.set('page', '1');
                  setSearchParams(updated);
                }}
                getRowId={(repair) => repair.id}
                storageKey="vechasu:repairs-table"
                renderMobileCard={(repair) => (
                  <article className="mobile-repair-card">
                    <div>
                      <button
                        className="document-number repair-open-link"
                        type="button"
                        onClick={() => setDetailTarget(repair)}
                      >
                        {repair.repair_number}
                      </button>
                      <StatusBadge
                        label={repair.status_label}
                        tone={repairStatusTone(repair.status)}
                      />
                    </div>
                    <h2>{repair.product_name}</h2>
                    <p>
                      {repair.client_name} · {repair.location_label}
                    </p>
                    <p title={repair.problem}>{repair.problem}</p>
                    <div className="row-actions">
                      <button type="button" onClick={() => setDetailTarget(repair)}>
                        Открыть
                      </button>
                      <button type="button" onClick={() => setEditor(repair)}>
                        Изменить
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
        title={
          editor === 'new' ? 'Новое обращение' : `Редактирование ${editor?.repair_number ?? ''}`
        }
        description="Все сведения хранятся в общей карточке ремонта."
        onClose={() => setEditor(null)}
        footer={
          <>
            <Button tone="secondary" type="button" onClick={() => setEditor(null)}>
              Отмена
            </Button>
            <Button
              tone="primary"
              type="submit"
              form="repair-editor"
              disabled={saveMutation.isPending || catalogQuery.isPending}
            >
              {saveMutation.isPending ? 'Сохраняем…' : 'Сохранить'}
            </Button>
          </>
        }
      >
        {catalogQuery.isError ? (
          <PageState
            kind="error"
            title="Каталог недоступен"
            message={errorMessage(catalogQuery.error)}
          />
        ) : catalogQuery.isPending || !meta ? (
          <LoadingState label="Загружаем форму…" />
        ) : (
          <RepairForm
            id="repair-editor"
            repair={editor === 'new' ? null : editor}
            products={catalogQuery.data ?? []}
            statuses={meta.facets.statuses}
            types={meta.facets.types}
            locations={meta.facets.locations}
            channels={meta.facets.channels}
            onSubmit={(values) => {
              if (editor) saveMutation.mutate({ repair: editor, values });
            }}
          />
        )}
      </Modal>

      <Modal
        open={detailTarget !== null}
        size="large"
        title={detailTarget?.repair_number ?? 'Карточка обращения'}
        description={
          detailTarget ? `${detailTarget.client_name} · ${detailTarget.product_name}` : undefined
        }
        onClose={() => setDetailTarget(null)}
        footer={
          detailTarget ? (
            <>
              <Button tone="secondary" icon="edit" onClick={() => setEditor(detailTarget)}>
                Изменить
              </Button>
              <Button
                tone="secondary"
                icon="receipt"
                onClick={() => setShipmentTarget(detailTarget)}
              >
                Добавить накладную
              </Button>
              {detailTarget.is_archived ? (
                <Button
                  tone="primary"
                  onClick={() => restoreMutation.mutate(detailTarget.id)}
                  disabled={restoreMutation.isPending}
                >
                  Восстановить
                </Button>
              ) : (
                <Button tone="danger" onClick={() => setArchiveTarget(detailTarget)}>
                  В архив
                </Button>
              )}
            </>
          ) : null
        }
      >
        {detailTarget ? (
          <div className="repair-detail-grid">
            <section>
              <h3>Статус и маршрут</h3>
              <dl>
                <dt>Статус</dt>
                <dd>
                  <select
                    aria-label="Изменить статус ремонта"
                    value={detailTarget.status}
                    disabled={statusMutation.isPending}
                    onChange={(event) =>
                      statusMutation.mutate({
                        id: detailTarget.id,
                        status: event.target.value,
                      })
                    }
                  >
                    {meta?.facets.statuses.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </dd>
                <dt>Местонахождение</dt>
                <dd>{detailTarget.location_label}</dd>
                <dt>Обращение</dt>
                <dd>{formatDate(detailTarget.request_at || detailTarget.created_at)}</dd>
                <dt>Обновлено</dt>
                <dd>{formatDate(detailTarget.updated_at, true)}</dd>
              </dl>
            </section>
            <section>
              <h3>Клиент</h3>
              <dl>
                <dt>Имя</dt>
                <dd>{detailTarget.client_name || '—'}</dd>
                <dt>Телефон</dt>
                <dd>{detailTarget.client_phone || '—'}</dd>
                <dt>Почта</dt>
                <dd>{detailTarget.client_email || '—'}</dd>
                <dt>Заказ</dt>
                <dd>{detailTarget.order_label}</dd>
              </dl>
            </section>
            <section>
              <h3>Товар</h3>
              <dl>
                <dt>Название</dt>
                <dd>{detailTarget.product_name || '—'}</dd>
                <dt>Бренд / модель</dt>
                <dd>
                  {[detailTarget.brand, detailTarget.model].filter(Boolean).join(' · ') || '—'}
                </dd>
                <dt>Артикул</dt>
                <dd>{detailTarget.article || '—'}</dd>
                <dt>Серийный номер</dt>
                <dd>{detailTarget.serial_number || '—'}</dd>
              </dl>
            </section>
            <section>
              <h3>Ремонт</h3>
              <dl>
                <dt>Мастер</dt>
                <dd>{detailTarget.master || '—'}</dd>
                <dt>Решение</dt>
                <dd>{detailTarget.decision || '—'}</dd>
                <dt>Стоимость</dt>
                <dd>{detailTarget.final_cost ? `${detailTarget.final_cost} ₽` : '—'}</dd>
                <dt>Срок</dt>
                <dd>{formatDate(detailTarget.due_date)}</dd>
              </dl>
            </section>
            <section className="span-2">
              <h3>Неисправность и диагностика</h3>
              <dl>
                <dt>Проблема</dt>
                <dd>{detailTarget.problem || '—'}</dd>
                <dt>Диагностика</dt>
                <dd>{detailTarget.diagnostic_result || '—'}</dd>
                <dt>Заключение</dt>
                <dd>{detailTarget.master_conclusion || '—'}</dd>
              </dl>
            </section>
            <section>
              <h3>Логистика</h3>
              {detailTarget.shipments.length ? (
                <ul className="repair-detail-list">
                  {detailTarget.shipments.map((item) => (
                    <li key={item.id}>
                      <strong>{item.direction_label}</strong>
                      <span>{[item.carrier, item.track_number].filter(Boolean).join(' · ')}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted-copy">Накладных пока нет.</p>
              )}
            </section>
            <section>
              <h3>Вложения</h3>
              {detailTarget.attachments.length ? (
                <ul className="repair-detail-list">
                  {detailTarget.attachments.map((item) => (
                    <li key={item.id}>
                      <a href={item.url}>{item.name}</a>
                      <span>{Math.ceil(item.size / 1024)} КБ</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted-copy">Вложений пока нет.</p>
              )}
              <div className="repair-attachment-upload">
                <FileUpload
                  label="Добавить вложение"
                  accept={[
                    '.png',
                    '.jpg',
                    '.jpeg',
                    '.webp',
                    '.pdf',
                    '.txt',
                    '.doc',
                    '.docx',
                    '.xls',
                    '.xlsx',
                  ]}
                  maxSize={10 * 1024 * 1024}
                  value={attachment}
                  onChange={setAttachment}
                  disabled={attachmentMutation.isPending}
                />
                {attachment ? (
                  <Button
                    tone="primary"
                    icon="upload"
                    disabled={attachmentMutation.isPending}
                    onClick={() =>
                      attachmentMutation.mutate({
                        id: detailTarget.id,
                        file: attachment,
                      })
                    }
                  >
                    {attachmentMutation.isPending ? 'Загружаем…' : 'Загрузить'}
                  </Button>
                ) : null}
              </div>
            </section>
            <section className="span-2">
              <h3>Последние события</h3>
              <ul className="repair-history">
                {detailTarget.history
                  .slice(-8)
                  .reverse()
                  .map((item) => (
                    <li key={item.id}>
                      <time>{formatDate(item.timestamp, true)}</time>
                      <strong>{item.action}</strong>
                      <span>{item.comment || item.new_value || item.actor}</span>
                    </li>
                  ))}
              </ul>
            </section>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={shipmentTarget !== null}
        title="Новая накладная"
        description={shipmentTarget?.repair_number}
        onClose={() => setShipmentTarget(null)}
        footer={
          <>
            <Button tone="secondary" onClick={() => setShipmentTarget(null)}>
              Отмена
            </Button>
            <Button
              tone="primary"
              disabled={!shipment.track_number.trim() || shipmentMutation.isPending}
              onClick={() => {
                if (shipmentTarget) shipmentMutation.mutate({ id: shipmentTarget.id });
              }}
            >
              {shipmentMutation.isPending ? 'Добавляем…' : 'Добавить'}
            </Button>
          </>
        }
      >
        <div className="erp-form">
          <label className="form-field">
            <span>Направление *</span>
            <select
              value={shipment.direction}
              onChange={(event) =>
                setShipment((value) => ({ ...value, direction: event.target.value }))
              }
            >
              <option value="inbound">К нам</option>
              <option value="outbound">Клиенту</option>
              <option value="unknown">Требует уточнения</option>
            </select>
          </label>
          <label className="form-field">
            <span>Служба доставки</span>
            <input
              value={shipment.carrier}
              onChange={(event) =>
                setShipment((value) => ({ ...value, carrier: event.target.value }))
              }
            />
          </label>
          <label className="form-field span-2">
            <span>Трек-номер *</span>
            <input
              value={shipment.track_number}
              onChange={(event) =>
                setShipment((value) => ({ ...value, track_number: event.target.value }))
              }
            />
          </label>
          <label className="form-field">
            <span>Дата отправки</span>
            <input
              type="date"
              value={shipment.sent_at}
              onChange={(event) =>
                setShipment((value) => ({ ...value, sent_at: event.target.value }))
              }
            />
          </label>
          <label className="form-field">
            <span>Статус доставки</span>
            <input
              value={shipment.status}
              onChange={(event) =>
                setShipment((value) => ({ ...value, status: event.target.value }))
              }
            />
          </label>
        </div>
      </Modal>

      <ConfirmDialog
        open={archiveTarget !== null}
        title="Перенести обращение в архив?"
        message={`${archiveTarget?.repair_number ?? ''} останется в системе и может быть восстановлено.`}
        confirmLabel="В архив"
        pending={archiveMutation.isPending}
        onClose={() => setArchiveTarget(null)}
        onConfirm={() => {
          if (archiveTarget) archiveMutation.mutate(archiveTarget.id);
        }}
      />
      {toast ? <Toast {...toast} onClose={() => setToast(null)} /> : null}
    </AppShell>
  );
}
