import React from "react";
import { IconChevronRight } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import { PAGE_SIZES, pageCount } from "../upload_parse.js";

// Paging for the upload preview, for every upload source.
//
// It exists because the preview renders a live <textarea> per field per row: a
// three-thousand-row script result is twelve thousand editable controls, which
// takes seconds to mount and makes typing in any one of them stutter. The rows
// are all still in memory and all still submitted — this only bounds what the
// document holds at once.
//
// Renders nothing at all below one page. A pager over eleven rows is noise, and
// the CSV user who never sees more than a screenful should not have gained a
// control when this feature landed.
export default function PreviewPager({ total, page, size, onPage, onSize, className = "" }) {
  const pages = pageCount(total, size);
  if (total <= PAGE_SIZES[0] && pages <= 1) return null;

  const first = total === 0 ? 0 : (page - 1) * size + 1;
  const last = Math.min(page * size, total);

  return (
    <div className={`preview-pager ${className}`.trim()}>
      <span className="hint">
        rows {first.toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}
      </span>
      <span className="grow" />
      <label className="preview-pager-size">
        <span className="hint">per page</span>
        <select
          value={size}
          onChange={(e) => onSize(Number(e.target.value))}
          aria-label="Rows per page"
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
      </label>
      <div className="preview-pager-steps">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
          icon={<IconChevronRight size={14} style={{ transform: "rotate(180deg)" }} />}
        />
        <span className="hint preview-pager-count">
          {page} / {pages}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onPage(page + 1)}
          disabled={page >= pages}
          aria-label="Next page"
          icon={<IconChevronRight size={14} />}
        />
      </div>
    </div>
  );
}
