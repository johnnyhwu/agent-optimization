import React from "react";
import { useInfiniteScroll } from "../usePagedList.js";

// The bottom of a paged list: an invisible sentinel that pulls the next page in
// on scroll, plus a real button.
//
// The button is not decoration. IntersectionObserver never fires for someone
// tabbing through with a keyboard, and it never fires at all if the page turns
// out not to scroll — so infinite scroll alone can leave rows permanently
// unreachable. The count tells the developer whether scrolling further is even
// worth it, which a bare spinner never does.
export default function ListFooter({ shown, total, hasMore, loading, onLoadMore }) {
  const sentinel = useInfiniteScroll(onLoadMore, hasMore && !loading);
  if (!total) return null;

  return (
    <div className="list-footer">
      <div ref={sentinel} aria-hidden="true" />
      <span className="muted">
        Showing {shown} of {total}
      </span>
      {hasMore && (
        <button onClick={onLoadMore} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
