interface TablePaginationProps {
  page: number;
  pageSize: number;
  pages: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

function visiblePages(page: number, pages: number) {
  const result = new Set([1, pages, page - 1, page, page + 1]);
  return [...result].filter((item) => item >= 1 && item <= pages).sort((a, b) => a - b);
}

export function TablePagination({
  page,
  pageSize,
  pages,
  total,
  onPageChange,
  onPageSizeChange,
}: TablePaginationProps) {
  const pageNumbers = visiblePages(page, Math.max(pages, 1));
  return (
    <div className="table-pagination">
      <p>
        {total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}` : '0'} из{' '}
        {total}
      </p>
      <label>
        Показывать
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          {[50, 100, 200].map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <div className="pagination-buttons" aria-label="Страницы">
        <button
          type="button"
          aria-label="Предыдущая страница"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
        >
          ‹
        </button>
        {pageNumbers.map((number, index) => (
          <span className="pagination-unit" key={number}>
            {index > 0 && number - pageNumbers[index - 1] > 1 ? (
              <span className="pagination-gap">…</span>
            ) : null}
            <button
              type="button"
              className={number === page ? 'is-active' : ''}
              aria-current={number === page ? 'page' : undefined}
              onClick={() => onPageChange(number)}
            >
              {number}
            </button>
          </span>
        ))}
        <button
          type="button"
          aria-label="Следующая страница"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pages}
        >
          ›
        </button>
      </div>
    </div>
  );
}
