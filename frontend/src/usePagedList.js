import { useCallback, useEffect, useRef, useState } from "react";

// Append-as-you-scroll paging for the eval-set grid and the run history.
//
// Both lists are browse surfaces — you scan for the one you recognise — so they
// append rather than replacing the page under you. That makes three things the
// caller must not have to get right on its own, and they live here so the two
// lists can't drift apart:
//
//  1. **Stale responses.** Change the filter while a page is in flight and the
//     old response can land after the new one. Every load carries a token and
//     anything but the newest is dropped.
//  2. **Duplicate rows.** Rows arriving while an earlier page is still being
//     appended, or a row that shifted across a page boundary, would otherwise
//     render twice and break React keys. Appends are de-duplicated by id.
//  3. **Concurrent loads.** A scroll sentinel fires repeatedly; without a guard
//     it launches the same page several times.
//
// `refresh()` reloads only what is already on screen (offset 0 through the
// current length), so a delete or a rename doesn't collapse a list the developer
// has scrolled halfway down.
export function usePagedList(fetchPage, { pageSize = 24, deps = [] } = {}) {
  const [items, setItems] = useState(null); // null = first load not finished
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  const token = useRef(0);
  const inFlight = useRef(false);
  const itemsRef = useRef(null);
  itemsRef.current = items;

  // Read through a ref so a caller can pass an inline closure without
  // re-triggering the effect on every render.
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  const load = useCallback(async ({ offset, limit, append }) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const mine = ++token.current;
    if (append) setLoadingMore(true);
    try {
      const page = await fetchRef.current({ offset, limit });
      if (mine !== token.current) return; // superseded by a newer request
      const incoming = page.items || [];
      setItems((prev) => {
        if (!append || !prev) return incoming;
        const seen = new Set(prev.map((i) => i.id));
        return [...prev, ...incoming.filter((i) => !seen.has(i.id))];
      });
      setTotal(page.total ?? incoming.length);
      setHasMore(Boolean(page.has_more));
      setError(null);
    } catch (e) {
      if (mine === token.current) setError(e.message);
    } finally {
      inFlight.current = false;
      if (mine === token.current) setLoadingMore(false);
    }
  }, []);

  // First page, and a fresh first page whenever a filter changes.
  useEffect(() => {
    setItems(null);
    setError(null);
    load({ offset: 0, limit: pageSize, append: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const loadMore = useCallback(() => {
    const current = itemsRef.current;
    if (!current || !hasMore || inFlight.current) return;
    load({ offset: current.length, limit: pageSize, append: true });
  }, [hasMore, load, pageSize]);

  // Re-read what is on screen without discarding the reader's scroll position.
  const refresh = useCallback(() => {
    const shown = itemsRef.current?.length || 0;
    load({ offset: 0, limit: Math.max(shown, pageSize), append: false });
  }, [load, pageSize]);

  // One row, changed in place.
  //
  // `refresh()` is the wrong tool for a rename: it re-reads every page on
  // screen, which costs a request per page to change one string, and any row
  // that crossed a page boundary in between moves under the reader. A rename
  // affects exactly one row and the server has already returned it.
  const patchItem = useCallback((id, patch) => {
    setItems((prev) =>
      prev ? prev.map((item) => (item.id === id ? { ...item, ...patch } : item)) : prev,
    );
  }, []);

  return { items, total, hasMore, loadingMore, error, loadMore, refresh, patchItem };
}

// Sentinel that calls `onVisible` when scrolled into view. The button is not a
// fallback for looks: IntersectionObserver never fires for a keyboard user
// tabbing through, and never fires at all if the list container isn't the thing
// that scrolls.
export function useInfiniteScroll(onVisible, enabled) {
  const ref = useRef(null);
  const handler = useRef(onVisible);
  handler.current = onVisible;

  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver(
      (entries) => entries[0]?.isIntersecting && handler.current(),
      // Start the next page slightly before the sentinel is actually on screen,
      // so scrolling stays continuous instead of stopping at every boundary.
      { rootMargin: "300px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled]);

  return ref;
}
