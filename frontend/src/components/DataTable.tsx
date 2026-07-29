import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { useMemo, useState, type ReactNode } from 'react';

interface DataTableProps<TData> {
  columns: ColumnDef<TData>[];
  data: TData[];
  sorting: SortingState;
  onSortingChange: (sorting: SortingState) => void;
  getRowId: (row: TData) => string;
  renderMobileCard: (row: TData) => ReactNode;
}

export function DataTable<TData>({
  columns,
  data,
  sorting,
  onSortingChange,
  getRowId,
  renderMobileCard,
}: DataTableProps<TData>) {
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});
  const stableColumns = useMemo(() => columns, [columns]);
  const table = useReactTable({
    data,
    columns: stableColumns,
    state: { sorting, columnVisibility },
    manualSorting: true,
    enableColumnResizing: true,
    columnResizeMode: 'onChange',
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    onSortingChange: (updater) => {
      onSortingChange(typeof updater === 'function' ? updater(sorting) : updater);
    },
    onColumnVisibilityChange: setColumnVisibility,
  });

  return (
    <>
      <div className="table-tools">
        <details className="column-settings">
          <summary>Столбцы</summary>
          <div>
            {table.getAllLeafColumns().map((column) => (
              <label key={column.id}>
                <input
                  type="checkbox"
                  checked={column.getIsVisible()}
                  onChange={column.getToggleVisibilityHandler()}
                  disabled={!column.getCanHide()}
                />
                {String(column.columnDef.header ?? column.id)}
              </label>
            ))}
            <button type="button" onClick={() => setColumnVisibility({})}>
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
