import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { usePagedList } from "../usePagedList.js";
import ListFooter from "./ListFooter.jsx";
import Sparkline from "./Sparkline.jsx";
import UploadDialog from "./UploadDialog.jsx";
import ConfigDialog from "./ConfigDialog.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import DownloadDialog from "./DownloadDialog.jsx";
import { useToast } from "./Toast.jsx";
import { IconDownload, IconGear, IconTrash, IconUpload, IconUsers } from "./icons.jsx";

const PAGE_SIZE = 24;

// Top tier (§6.13): one card per eval set — run count, latest pass rate, trend
// sparkline, regression summary. Owners get a config gear to edit the card and a
// trash button to delete it.
//
// Cards page in as you scroll rather than rendering every set at once. The
// toolbar above them (§6.10) filters server-side, so searching looks at every
// set the developer can see, not just the ones already loaded — filtering only
// the loaded page would make the result depend on how far they had scrolled.
export default function EvalSetList({ onOpen, subject }) {
  const toast = useToast();
  const [showUpload, setShowUpload] = useState(false);
  const [configSet, setConfigSet] = useState(null);
  const [deleteSet, setDeleteSet] = useState(null);
  const [downloadSet, setDownloadSet] = useState(null);

  // Search / filter / sort. `query` is what's typed; `search` is the debounced
  // value that actually hits the API — a request per keystroke would both
  // hammer the backend and let responses land out of order.
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [metadataKey, setMetadataKey] = useState("");
  const [metadataValue, setMetadataValue] = useState("");
  const [sort, setSort] = useState("created_at");
  const [metaKeys, setMetaKeys] = useState([]);

  useEffect(() => {
    const t = setTimeout(() => setSearch(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    api.metadataKeys().then(setMetaKeys).catch(() => setMetaKeys([]));
  }, [subject]);

  const params = useMemo(
    () => ({
      q: search,
      metadata_key: metadataKey,
      metadata_value: metadataKey ? metadataValue.trim() : "",
      sort,
    }),
    [search, metadataKey, metadataValue, sort]
  );

  const { items: sets, total, hasMore, loadingMore, error, loadMore, refresh } =
    usePagedList(
      ({ offset, limit }) => api.listEvalSets({ ...params, offset, limit }),
      { pageSize: PAGE_SIZE, deps: [subject, params] }
    );

  const filtering = Boolean(search || metadataKey);

  async function confirmDelete() {
    await api.deleteEvalSet(deleteSet.id);
    setDeleteSet(null);
    toast.success(`Deleted “${deleteSet.name}”`);
    refresh();
  }

  return (
    <div>
      <div className="page-head">
        <h2>Eval Sets</h2>
        <button className="primary" onClick={() => setShowUpload(true)}>
          <IconUpload size={15} /> Upload eval set
        </button>
      </div>

      <div className="toolbar">
        <input
          className="search"
          type="search"
          placeholder="Search eval sets…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search eval sets by name"
        />
        {metaKeys.length > 0 && (
          <>
            <select
              value={metadataKey}
              onChange={(e) => setMetadataKey(e.target.value)}
              aria-label="Filter by metadata key"
              style={{ width: "auto" }}
            >
              <option value="">Any metadata</option>
              {metaKeys.map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
            {/* Only meaningful once a key is chosen — an "equals" box with
                nothing to equal is just clutter. */}
            {metadataKey && (
              <input
                placeholder={`${metadataKey} = (any)`}
                value={metadataValue}
                onChange={(e) => setMetadataValue(e.target.value)}
                aria-label={`Filter by ${metadataKey} value`}
                style={{ width: 160 }}
              />
            )}
          </>
        )}
        <span className="muted">Sort</span>
        <div className="segmented sm">
          <button
            className={sort === "created_at" ? "active" : ""}
            onClick={() => setSort("created_at")}
          >
            Newest
          </button>
          <button className={sort === "name" ? "active" : ""} onClick={() => setSort("name")}>
            Name
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {sets === null && (
        <div className="cards">
          {[0, 1, 2].map((i) => <div className="skeleton" key={i} />)}
        </div>
      )}
      {sets && sets.length === 0 && (
        <div className="empty">
          {filtering
            ? "No eval sets match this filter."
            : "No eval sets yet. Upload one, or run the seed script."}
        </div>
      )}
      <div className="cards">
        {sets &&
          sets.map((s, i) => {
            const shared = (s.roles || []).length;
            return (
              <div
                className="card"
                key={s.id}
                // Stagger only within a page: restarting the animation for every
                // card on each append would flash the whole grid.
                style={{ animationDelay: `${(i % PAGE_SIZE) * 40}ms` }}
                onClick={() => onOpen(s)}
              >
                {/* Download is offered to every role, config and delete only to
                    owners. A viewer can already read every row an export
                    contains, so withholding the file would protect nothing
                    while denying it to most of the people who want it. */}
                <div className="card-actions">
                  <button
                    className="icon-btn"
                    aria-label="Download eval set"
                    title="Download this eval set"
                    onClick={(e) => { e.stopPropagation(); setDownloadSet(s); }}
                  >
                    <IconDownload size={16} />
                  </button>
                  {s.my_role === "owner" && (
                    <>
                      <button
                        className="icon-btn"
                        aria-label="Configure"
                        title="Configure"
                        onClick={(e) => { e.stopPropagation(); setConfigSet(s); }}
                      >
                        <IconGear size={16} />
                      </button>
                      <button
                        className="icon-btn danger-btn"
                        aria-label="Delete eval set"
                        title="Delete eval set"
                        onClick={(e) => { e.stopPropagation(); setDeleteSet(s); }}
                      >
                        <IconTrash size={16} />
                      </button>
                    </>
                  )}
                </div>
                <h3>{s.name}</h3>
                <div className="meta">
                  {new Date(s.created_at).toLocaleDateString()}
                  <span className={`rolechip ${s.my_role}`}>{s.my_role}</span>
                </div>
                <div className="stats">
                  <div className="stat">
                    <div className="num">{s.run_count}</div>
                    <div className="lbl">runs</div>
                  </div>
                  <div className="stat">
                    <div className="num">
                      {s.latest_pass_rate === null ? "—" : `${Math.round(s.latest_pass_rate * 100)}%`}
                    </div>
                    <div className="lbl">latest pass</div>
                  </div>
                  <div className="spark-wrap"><Sparkline values={s.trend} /></div>
                </div>
                {(s.regressed > 0 || s.improved > 0) && (
                  <div className="badges">
                    {s.regressed > 0 && <span className="badge reg">⚠ {s.regressed} regressed</span>}
                    {s.improved > 0 && <span className="badge imp">▲ {s.improved} improved</span>}
                  </div>
                )}
                <div className="tags">
                  {Object.entries(s.metadata || {}).map(([k, v]) => (
                    <span className="tag" key={k}>{k}: {String(v)}</span>
                  ))}
                  {shared > 1 && (
                    <span className="tag people"><IconUsers size={11} /> {shared} members</span>
                  )}
                </div>
              </div>
            );
          })}
      </div>

      {sets && sets.length > 0 && (
        <ListFooter
          shown={sets.length}
          total={total}
          hasMore={hasMore}
          loading={loadingMore}
          onLoadMore={loadMore}
        />
      )}

      {showUpload && (
        <UploadDialog
          subject={subject}
          onClose={() => setShowUpload(false)}
          onCreated={() => { setShowUpload(false); refresh(); }}
        />
      )}
      {downloadSet && (
        <DownloadDialog
          evalSet={downloadSet}
          subject={subject}
          onClose={() => setDownloadSet(null)}
        />
      )}
      {configSet && (
        <ConfigDialog
          evalSet={configSet}
          subject={subject}
          onClose={() => setConfigSet(null)}
          onSaved={() => { setConfigSet(null); refresh(); }}
        />
      )}
      {deleteSet && (
        <ConfirmDialog
          title={`Delete “${deleteSet.name}”?`}
          message="The eval set, its questions and every run recorded against it are removed. This cannot be undone."
          detail={
            deleteSet.run_count
              ? `${deleteSet.run_count} run${deleteSet.run_count === 1 ? "" : "s"} — with their results and diagnoses — will be deleted too.`
              : "No runs have been recorded against this set yet."
          }
          confirmLabel="Delete eval set"
          onConfirm={confirmDelete}
          onClose={() => setDeleteSet(null)}
        />
      )}
    </div>
  );
}
