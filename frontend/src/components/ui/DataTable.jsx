import React from "react";

// A real table for the run history.
//
// What it replaces was a stack of flex rows whose columns were held in alignment
// by hand — `style={{ width: 96, textAlign: "right" }}` on the pass rate,
// `style={{ width: 80 }}` on the wrong count — with **no header row at all**. So
// every number on screen was unlabelled, and any row whose status pill happened to
// be wider than its neighbour's pushed its own columns out of line with the rest.
// That single detail is most of why the page read as unfinished.
//
// Columns are data:
//
//   { key, header, width, align, className, render(row) }
//
// `width` is a grid track (`"96px"`, `"1fr"`, `"minmax(0,2fr)"`), so the header and
// every body row are laid out by *one* grid definition and cannot drift apart —
// which is precisely the failure mode of the hand-rolled version.
export default function DataTable({
  columns,
  rows,
  rowKey = (r) => r.id,
  onRowClick,
  isSelected,
  onToggleSelect,
  selectLabel = "Select row",
  rowClassName,
  rowActions,
  empty,
  staggerWithin = 0,
}) {
  const selectable = Boolean(onToggleSelect);
  const template = [
    selectable && "34px",
    ...columns.map((c) => c.width || "1fr"),
    rowActions && "auto",
  ]
    .filter(Boolean)
    .join(" ");

  if (rows.length === 0 && empty) return empty;

  return (
    <div className="ui-table" role="table">
      <div className="ui-table-head" role="row" style={{ gridTemplateColumns: template }}>
        {selectable && <span role="columnheader" aria-label={selectLabel} />}
        {columns.map((c) => (
          <span
            key={c.key}
            role="columnheader"
            className={`ui-th ui-al-${c.align || "start"}`}
          >
            {c.header}
          </span>
        ))}
        {rowActions && <span role="columnheader" aria-label="Actions" />}
      </div>

      {rows.map((row, i) => {
        const selected = isSelected?.(row) || false;
        const cls = [
          "ui-table-row",
          selected && "is-selected",
          onRowClick && "is-clickable",
          rowClassName?.(row),
        ]
          .filter(Boolean)
          .join(" ");

        return (
          <div
            key={rowKey(row)}
            role="row"
            className={cls}
            style={{
              gridTemplateColumns: template,
              // Stagger within a page only: re-animating rows the developer is
              // already reading, every time another page is appended, is worse
              // than not animating at all.
              animationDelay: staggerWithin ? `${(i % staggerWithin) * 25}ms` : undefined,
            }}
            tabIndex={onRowClick ? 0 : undefined}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            onKeyDown={
              onRowClick
                ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onRowClick(row);
                    }
                  }
                : undefined
            }
          >
            {selectable && (
              <input
                type="checkbox"
                className="ui-table-check"
                checked={selected}
                onChange={() => onToggleSelect(row)}
                // The row opens the item; the checkbox adds it to a comparison.
                // Two jobs on one row, so the checkbox has to keep its click.
                onClick={(e) => e.stopPropagation()}
                aria-label={selectLabel}
              />
            )}
            {columns.map((c) => (
              <div
                key={c.key}
                role="cell"
                className={`ui-td ui-al-${c.align || "start"} ${c.className || ""}`.trim()}
              >
                {c.render(row)}
              </div>
            ))}
            {rowActions && (
              <div
                role="cell"
                className="ui-table-actions"
                onClick={(e) => e.stopPropagation()}
              >
                {rowActions(row)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
