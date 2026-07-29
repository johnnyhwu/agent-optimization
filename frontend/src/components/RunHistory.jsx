import React, { useState } from "react";
import { api, getSubject } from "../api.js";
import { usePagedList } from "../usePagedList.js";
import ListFooter from "./ListFooter.jsx";
import RunProgress from "./RunProgress.jsx";
import QuestionEditor from "./QuestionEditor.jsx";
import RunConfigDialog from "./RunConfigDialog.jsx";
import RunConfigView from "./RunConfigView.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlay, IconGear, IconFileText, IconStop, IconTrash } from "./icons.jsx";

const MODES = [
  ["union", "Union"],
  ["intersection", "Intersection"],
  ["last_n", "Last-N"],
];

const PAGE_SIZE = 20;

// Middle tier (§6.13): run history for a set; multi-select runs + the 3 incorrect
// modes; trigger new runs (owner or viewer). Owner can edit questions.
//
// Runs page in newest-first as you scroll. Selection is held as a list of run
// ids rather than indices, so multi-select survives an append — the whole point
// of the multi-run modes is comparing runs that may be pages apart.
export default function RunHistory({ evalSet, myRole, onOpenRuns }) {
  const toast = useToast();
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState("union");
  const [lastN, setLastN] = useState(2);
  const [showEditor, setShowEditor] = useState(false);
  const [showRunConfig, setShowRunConfig] = useState(false);
  const [viewConfigRun, setViewConfigRun] = useState(null);
  const [deleteRun, setDeleteRun] = useState(null);
  const subject = getSubject();

  const {
    items: runs, total, hasMore, loadingMore, error: loadError, loadMore,
    refresh: load,
  } = usePagedList(
    ({ offset, limit }) => api.listRuns(evalSet.id, { offset, limit }),
    { pageSize: PAGE_SIZE, deps: [evalSet.id] }
  );

  const toggle = (id) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  // Errors propagate so the dialog can show them inline and stay open with the
  // developer's settings intact.
  async function trigger(payload) {
    setError(null);
    const run = await api.triggerRun(evalSet.id, payload);
    setShowRunConfig(false);
    toast.info("Run started");
    // Straight into the detail view: that is where the live question list is,
    // and watching a run is the reason anyone starts one.
    onOpenRuns([run.id], "union", 2);
  }

  async function cancel(run) {
    try {
      await api.cancelRun(evalSet.id, run.id);
      toast.info("Cancelling run…");
      load();
    } catch (e) {
      toast.error(e.message);
    }
  }

  async function confirmDelete() {
    await api.deleteRun(evalSet.id, deleteRun.id);
    setSelected((s) => s.filter((x) => x !== deleteRun.id));
    setDeleteRun(null);
    toast.success("Run deleted");
    load();
  }

  // A viewer may trigger a run (§6.16), so they must be able to stop it.
  const canCancel = (r) => myRole === "owner" || r.triggered_by === subject;

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>{evalSet.name}</h2>
          <p className="muted" style={{ margin: "2px 0 0" }}>
            Any role may run an eval. Owner may edit questions (set is locked — no add/delete).
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {myRole === "owner" && (
            <button onClick={() => setShowEditor(true)}><IconGear size={15} /> Edit questions</button>
          )}
          <button className="primary" onClick={() => setShowRunConfig(true)}>
            <IconPlay size={14} /> Run eval
          </button>
        </div>
      </div>
      {(error || loadError) && <div className="error">{error || loadError}</div>}

      {/* Driven by the run list rather than by "did I start it in this tab", so
          coming back to this page mid-run still shows where the run is. */}
      {(runs || [])
        .filter((r) => r.status === "running")
        .map((r) => (
          <RunProgress
            key={r.id}
            evalSetId={evalSet.id}
            runId={r.id}
            label={r.name || new Date(r.started_at).toLocaleString()}
            onDone={load}
          />
        ))}

      <div className="toolbar">
        <span className="muted">Incorrect mode</span>
        <div className="segmented">
          {MODES.map(([m, label]) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>{label}</button>
          ))}
        </div>
        {mode === "last_n" && (
          <>
            <span className="muted">N =</span>
            <input type="number" min="1" value={lastN} onChange={(e) => setLastN(Number(e.target.value))} style={{ width: 64 }} />
          </>
        )}
        <button
          className="primary"
          disabled={selected.length === 0}
          onClick={() => onOpenRuns(selected, mode, lastN)}
          style={{ marginLeft: "auto" }}
        >
          Open detail ({selected.length})
        </button>
      </div>

      {runs === null && <p className="muted">Loading…</p>}
      {runs && runs.length === 0 && <div className="empty">No runs yet — hit “Run eval”.</div>}
      {runs &&
        runs.map((r, i) => (
          // The whole row opens the run (same pattern as the eval-set cards);
          // the checkbox keeps its own click for multi-select.
          <div
            className={`runrow ${selected.includes(r.id) ? "sel" : ""}`}
            key={r.id}
            // Stagger within a page only, so appending doesn't re-animate rows
            // the developer is already looking at.
            style={{ animationDelay: `${(i % PAGE_SIZE) * 25}ms` }}
            role="button"
            tabIndex={0}
            onClick={() => onOpenRuns([r.id], "union", 2)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpenRuns([r.id], "union", 2);
              }
            }}
          >
            <input
              type="checkbox"
              checked={selected.includes(r.id)}
              onChange={() => toggle(r.id)}
              onClick={(e) => e.stopPropagation()}
              aria-label="Select run"
              style={{ width: "auto" }}
            />
            <div className="grow">
              <div style={{ fontWeight: 600 }}>{r.name || new Date(r.started_at).toLocaleString()}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                by {r.triggered_by}
                {r.name && ` · ${new Date(r.started_at).toLocaleString()}`}
              </div>
            </div>
            <span className={`pill ${r.status}`}>
              {r.status === "running" && r.cancel_requested ? "cancelling" : r.status}
            </span>
            <div style={{ width: 96, textAlign: "right", fontWeight: 600 }}>
              {r.pass_rate === null ? "—" : `${Math.round(r.pass_rate * 100)}% pass`}
            </div>
            <div style={{ width: 80, textAlign: "right" }} className="muted">{r.incorrect_count ?? 0} wrong</div>
            <button
              className="icon-btn"
              aria-label="View run config"
              title="View the config this run used"
              onClick={(e) => { e.stopPropagation(); setViewConfigRun(r); }}
            >
              <IconFileText size={16} />
            </button>
            {/* A run in flight offers stop; only a finished one offers delete. */}
            {r.status === "running" ? (
              canCancel(r) && (
                <button
                  className="icon-btn danger-btn"
                  aria-label="Cancel run"
                  title="Stop this run"
                  disabled={r.cancel_requested}
                  onClick={(e) => { e.stopPropagation(); cancel(r); }}
                >
                  <IconStop size={14} />
                </button>
              )
            ) : (
              myRole === "owner" && (
                <button
                  className="icon-btn danger-btn"
                  aria-label="Delete run"
                  title="Delete this run"
                  onClick={(e) => { e.stopPropagation(); setDeleteRun(r); }}
                >
                  <IconTrash size={16} />
                </button>
              )
            )}
          </div>
        ))}

      {runs && runs.length > 0 && (
        <ListFooter
          shown={runs.length}
          total={total}
          hasMore={hasMore}
          loading={loadingMore}
          onLoadMore={loadMore}
        />
      )}

      {showEditor && <QuestionEditor evalSet={evalSet} onClose={() => setShowEditor(false)} />}
      {showRunConfig && (
        <RunConfigDialog
          evalSetId={evalSet.id}
          onClose={() => setShowRunConfig(false)}
          onRun={trigger}
        />
      )}
      {viewConfigRun && (
        <RunConfigView run={viewConfigRun} onClose={() => setViewConfigRun(null)} />
      )}
      {deleteRun && (
        <ConfirmDialog
          title="Delete this run?"
          message={`“${deleteRun.name || new Date(deleteRun.started_at).toLocaleString()}” and everything recorded for it will be removed.`}
          detail="Its per-question results and stored diagnoses go with it. Other runs in this eval set are untouched."
          confirmLabel="Delete run"
          onConfirm={confirmDelete}
          onClose={() => setDeleteRun(null)}
        />
      )}
    </div>
  );
}
