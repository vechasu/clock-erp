import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { useEffect, useMemo, useState, type ReactNode } from 'react';

interface DataTableProps<TData> {
  columns: ColumnDef<TData>[];
  data: TData[];
  sorting: SortingState;
  onSortingChange: (sorting: SortingState) => void;
  getRowId: (row: TData) => string;
  renderMobileCard: (row: TData) => ReactNode;
  storageKey?: string;
}

function readPreference<T>(storageKey: string | undefined, suffix: string, fallback: T): T {
  if (!storageKey) return fallback;
  try {
    const stored = window.localStorage.getItem(`${storageKey}:${suffix}`);
    return stored ? (JSON.parse(stored) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function DataTable<TData>({
  columns,
  data,
  sorting,
  onSortingChange,
  getRowId,
  renderMobileCard,
  storageKey,
}: DataTableProps<TData>) {
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() =>
    readPreference(storageKey, 'visibility', {}),
  );
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(() =>
    readPreference(storageKey, 'order', []),
  );
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(() =>
    readPreference(storageKey, 'sizing', {}),
  );
  const stableColumns = useMemo(() => columns, [columns]);
  const table = useReactTable({
    data,
    columns: stableColumns,
    state: { sorting, columnVisibility, columnOrder, columnSizing },
    manualSorting: true,
    enableColumnResizing: true,
    columnResizeMode: 'onChange',
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    onSortingChange: (updater) => {
      onSortingChange(typeof updater === 'function' ? updater(sorting) : updater);
    },
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    onColumnSizingChange: setColumnSizing,
  });

  useEffect(() => {
    if (!storageKey) return;
    window.localStorage.setItem(
      `${storageKey}:visibility`,
      JSON.stringify(columnVisibility),
    );
    window.localStorage.setItem(`${storageKey}:order`, JSON.stringify(columnOrder));
    window.localStorage.setItem(`${storageKey}:sizing`, JSON.stringify(columnSizing));
  }, [columnOrder, columnSizing, columnVisibility, storageKey]);

  const moveColumn = (columnId: string, direction: -1 | 1) => {
    const current = table.getAllLeafColumns().map((column) => column.id);
    const index = current.indexOf(columnId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= current.length) return;
    [current[index], current[target]] = [current[target], current[index]];
    setColumnOrder(current);
  };

  return (
    <>
      <div className="table-tools">
        <details className="column-settings">
          <summary>Столбцы</summary>
          <div>
            {table.getAllLeafColumns().map((column) => (
              <div className="column-setting-row" key={column.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={column.getIsVisible()}
                    onChange={column.getToggleVisibilityHandler()}
                    disabled={!column.getCanHide()}
                  />
                  {String(
                    (column.columnDef.meta as { label?: string } | undefined)?.label
                      ?? column.columnDef.header
                      ?? column.id,
                  )}
                </label>
                <span>
                  <button
                    type="button"
                    aria-label={`Сдвинуть ${column.id} влево`}
                    onClick={() => moveColumn(column.id, -1)}
                  >
                    ←
                  </button>
                  <button
                    type="button"
                    aria-label={`Сдвинуть ${column.id} вправо`}
                    onClick={() => moveColumn(column.id, 1)}
                  >
                    →
                  </button>
                </span>
              </div>
            ))}
            <button
              type="button"
              onClick={() => {
                setColumnVisibility({});
                setColumnOrder([]);
                setColumnSizing({});
              }}
            >
              Сбросить
            </button>
          </div>
        </details>
      </div>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id} style={{ width: header.getSize() }}>
                    {header.isPlaceholder ? null : (
                      <button
                        className="sort-button"
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        disabled={!header.column.getCanSort()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {{
                          asc: ' ↑',
                          desc: ' ↓',
                        }[header.column.getIsSorted() as string] ?? ''}
                      </button>
                    )}
                    {header.column.getCanResize() ? (
                      <button
                        type="button"
                        className={`column-resizer${
                          header.column.getIsResizing() ? ' is-resizing' : ''
                        }`}
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                        aria-label={`Изменить ширину столбца ${header.id}`}
                      />
                    ) : null}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} style={{ width: cell.column.getSize() }}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mobile-card-list">
        {data.map((row) => (
          <div key={getRowId(row)}>{renderMobileCard(row)}</div>
        ))}
      </div>
    </>
  );
}
