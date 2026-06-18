import type { ReactNode } from "react";

export type Column<T> = {
  key: string;
  header: string;
  numeric?: boolean;
  sortable?: boolean;
  render?: (row: T) => ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  getRowId,
  selectedIds,
  onToggleRow,
  onSort,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  selectedIds?: Set<string>;
  onToggleRow?: (id: string) => void;
  onSort?: (key: string) => void;
}) {
  return (
    <div className="flex-1 overflow-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {onToggleRow && <th className="sticky top-0 w-[34px] bg-surface" />}
            {columns.map((col) => {
              const sortable = col.sortable && onSort;
              return (
                <th
                  key={col.key}
                  aria-sort={sortable ? "none" : undefined}
                  className={`sticky top-0 whitespace-nowrap border-b border-border bg-surface px-3.5 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted ${
                    col.numeric ? "text-right" : "text-left"
                  }`}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => onSort(col.key)}
                      className={`-mx-1 rounded-[4px] px-1 uppercase tracking-wide hover:text-accent-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-tint ${
                        col.numeric ? "w-full text-right" : "text-left"
                      }`}
                    >
                      {col.header}
                    </button>
                  ) : (
                    col.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = getRowId(row);
            const selected = selectedIds?.has(id);
            return (
              <tr
                key={id}
                onClick={onToggleRow ? () => onToggleRow(id) : undefined}
                className={`border-b border-border ${
                  onToggleRow ? "cursor-pointer" : ""
                } ${selected ? "bg-accent-tint" : onToggleRow ? "hover:bg-raised" : ""}`}
              >
                {onToggleRow && (
                  <td className="px-3.5">
                    <span
                      role="checkbox"
                      aria-checked={!!selected}
                      aria-label={selected ? "Deselect row" : "Select row"}
                      className={`inline-block h-[15px] w-[15px] rounded-[4px] border ${
                        selected ? "border-accent bg-accent" : "border-border"
                      }`}
                    />
                  </td>
                )}
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={`h-[38px] whitespace-nowrap px-3.5 ${
                      col.numeric ? "text-right font-mono tabular-nums" : ""
                    }`}
                  >
                    {col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
