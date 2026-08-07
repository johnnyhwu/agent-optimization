import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { IconChevronDown } from "./icons.jsx";

// "Use config from <run>" — a bounded picker over an unbounded list.
//
// This was a native <select> with one <option> per run, which after a few
// hundred runs is a wall of raw timestamps. A native dropdown also can't be told
// how tall to be (`size` doesn't apply), so "show ten and scroll the rest" is
// only achievable with a real listbox.
//
// It fetches its own page instead of taking the history list as a prop: that
// list is paged now, so a prop would only ever hold whatever the developer had
// scrolled past. Typing searches the whole history server-side.
const VISIBLE_ROWS = 10;
const ROW_HEIGHT = 46; // keep in sync with .runpicker-option in styles.css

function label(run) {
  return run.name || new Date(run.started_at).toLocaleString();
}

export default function RunPicker({ evalSetId, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [runs, setRuns] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(-1); // -1 = the "start from defaults" row
  const boxRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    const t = setTimeout(() => setSearch(query.trim()), 200);
    return () => clearTimeout(t);
  }, [query]);

  // One page is the whole point: the rest is reachable by scrolling the popup or
  // by narrowing the search, neither of which needs the client to hold it all.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listRuns(evalSetId, { limit: VISIBLE_ROWS * 3, q: search })
      .then((page) => {
        if (cancelled) return;
        setRuns(page.items || []);
        setTotal(page.total || 0);
      })
      .catch(() => !cancelled && setRuns([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [evalSetId, search]);

  // Close on an outside click or Escape — a popup that can only be dismissed by
  // choosing something is a trap.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const selected = useMemo(() => runs.find((r) => r.id === value), [runs, value]);
  // The chosen run may not be in the current (searched) page; the caller knows
  // the label it applied, so fall back to a neutral one rather than "none".
  const triggerLabel = value
    ? selected
      ? label(selected)
      : "Selected run"
    : "Start from the defaults";

  // Hands back the run object, not just its id: a choice can only ever come from
  // what is on screen, so the caller never has to re-fetch a run this component
  // already has — and it can't be re-fetched by id anyway, since the list
  // endpoint pages rather than addressing single runs.
  function choose(run) {
    onChange(run ? run.id : "", run || null);
    setOpen(false);
    setQuery("");
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      // Only when the popup is what's open: otherwise this would swallow the
      // Escape that closes the surrounding dialog. And when it *is* open,
      // Escape must dismiss the popup alone — closing the whole run-config
      // dialog would throw away everything the developer had filled in.
      if (!open) return;
      e.stopPropagation();
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const next = e.key === "ArrowDown" ? cursor + 1 : cursor - 1;
      setCursor(Math.max(-1, Math.min(runs.length - 1, next)));
      return;
    }
    if (e.key === "Enter" && open) {
      e.preventDefault();
      choose(cursor < 0 ? null : runs[cursor]);
    }
  }

  // Follow keyboard movement into the scroll region.
  useEffect(() => {
    if (!open || cursor < 0 || !listRef.current) return;
    listRef.current
      .querySelector(`[data-idx="${cursor}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  return (
    <div className="runpicker" ref={boxRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        className="runpicker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span>{triggerLabel}</span>
        <IconChevronDown size={14} className="chev" />
      </button>

      {open && (
        <div className="runpicker-pop">
          {/* Only worth the space once scrolling alone stops being practical. */}
          {(total > VISIBLE_ROWS || search) && (
            <input
              className="runpicker-search"
              type="search"
              autoFocus
              placeholder={`Search ${total} run${total === 1 ? "" : "s"}…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search runs by name"
            />
          )}
          <div
            className="runpicker-list"
            role="listbox"
            ref={listRef}
            style={{ maxHeight: VISIBLE_ROWS * ROW_HEIGHT }}
          >
            <div
              className={`runpicker-option ${cursor === -1 ? "cursor" : ""} ${!value ? "sel" : ""}`}
              role="option"
              aria-selected={!value}
              data-idx="-1"
              onMouseEnter={() => setCursor(-1)}
              onClick={() => choose(null)}
            >
              <div className="rp-name">Start from the defaults</div>
            </div>
            {runs.map((r, i) => (
              <div
                key={r.id}
                className={`runpicker-option ${cursor === i ? "cursor" : ""} ${
                  value === r.id ? "sel" : ""
                }`}
                role="option"
                aria-selected={value === r.id}
                data-idx={i}
                onMouseEnter={() => setCursor(i)}
                onClick={() => choose(r)}
              >
                {/* A bare timestamp is hard to pick out of a list; the pass rate
                    and key badge are how you recognise the run you meant. */}
                <div className="rp-name">{label(r)}</div>
                <div className="rp-meta">
                  {new Date(r.started_at).toLocaleString()}
                  {r.pass_rate !== null && r.pass_rate !== undefined && (
                    <> · {Math.round(r.pass_rate * 100)}% pass</>
                  )}
                  {(r.credentials_set || []).length > 0 && (
                    <> · <span className="rp-keys">{r.credentials_set.join(", ")} key</span></>
                  )}
                </div>
              </div>
            ))}
            {loading && <div className="runpicker-empty">Loading…</div>}
            {!loading && runs.length === 0 && (
              <div className="runpicker-empty">
                {search ? "No runs match that name." : "No earlier runs."}
              </div>
            )}
          </div>
          {!search && total > runs.length && (
            <div className="runpicker-foot">
              Showing the {runs.length} most recent of {total} — search to find older runs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
