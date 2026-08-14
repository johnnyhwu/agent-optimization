import React, { useState } from "react";
import { api, getSubject } from "../api.js";
import { usePagedList } from "../usePagedList.js";
import ListFooter from "./ListFooter.jsx";
import RunProgress from "./RunProgress.jsx";
import QuestionEditor from "./QuestionEditor.jsx";
import RunConfigDialog from "./RunConfigDialog.jsx";
import RunConfigView from "./RunConfigView.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import ConfigDialog from "./ConfigDialog.jsx";
import DownloadDialog from "./DownloadDialog.jsx";
import EvalSetMenu from "./EvalSetMenu.jsx";
import { useToast } from "./Toast.jsx";
import Button, { IconButton } from "./ui/Button.jsx";
import Badge from "./ui/Badge.jsx";
import DataTable from "./ui/DataTable.jsx";
import EmptyState from "./ui/EmptyState.jsx";
import PageHeader from "./ui/PageHeader.jsx";
import Skeleton from "./ui/Skeleton.jsx";
import Toolbar, { SegmentedControl } from "./ui/Toolbar.jsx";
import {
  IconFileText, IconInbox, IconPlay, IconStop, IconTrash,
} from "./icons.jsx";

// Which questions the detail view treats as incorrect when several runs are
// compared. Named for what they do rather than for the set operation they are:
// "Union" and "Intersection" describe the implementation, and left the developer
// to work out which one finds a stubborn failure and which one finds every
// failure ever seen.
const MODES = [
  { value: "union", label: "Ever failed", title: "Wrong in at least one of the selected runs" },
  { value: "intersection", label: "Always fails", title: "Wrong in every selected run — the stubborn ones" },
  { value: "last_n", label: "Newly failing", title: "Wrong in the last N runs — a recent regression" },
];

const PAGE_SIZE = 20;

const STATUS_TONE = {
  completed: "success",
  running: "accent",
  failed: "danger",
  cancelled: "neutral",
};

function runLabel(r) {
  return r.name || new Date(r.started_at).toLocaleString();
}

// Middle tier: run history for a set; multi-select runs + the three incorrect
// modes; trigger new runs (owner or viewer). Owner can edit questions.
//
// Runs page in newest-first as you scroll. Selection is held as a list of run
// ids rather than indices, so multi-select survives an append — the whole point
// of the multi-run modes is comparing runs that may be pages apart.
export default function RunHistory({ evalSet, myRole, onOpenRuns, onEvalSetChanged }) {
  const toast = useToast();
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState("union");
  const [lastN, setLastN] = useState(2);
  const [showEditor, setShowEditor] = useState(false);
  const [showRunConfig, setShowRunConfig] = useState(false);
  const [viewConfigRun, setViewConfigRun] = useState(null);
  const [deleteRun, setDeleteRun] = useState(null);
  const [showDownload, setShowDownload] = useState(false);
  const [configTab, setConfigTab] = useState(null);
  const subject = getSubject();

  // The set's current grading criteria. Every run below records the prompt it
  // actually used, so a row whose fingerprint differs from this was graded by
  // different words — and its pass rate is not comparable with the others.
  const currentFingerprint = evalSet.judge_prompt?.fingerprint;
  const unreviewedJudging = !evalSet.judge_prompt?.reviewed_at;

  const {
    items: runs, total, hasMore, loadingMore, error: loadError, loadMore,
    refresh: load,
  } = usePagedList(
    ({ offset, limit }) => api.listRuns(evalSet.id, { offset, limit }),
    { pageSize: PAGE_SIZE, deps: [evalSet.id] }
  );

  const toggle = (r) =>
    setSelected((s) => (s.includes(r.id) ? s.filter((x) => x !== r.id) : [...s, r.id]));

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

  // A viewer may trigger a run, so they must be able to stop it.
  const canCancel = (r) => myRole === "owner" || r.triggered_by === subject;
  const comparing = selected.length > 1;

  const columns = [
    {
      key: "run",
      header: "Run",
      width: "minmax(0, 1fr)",
      render: (r) => (
        <>
          <div className="ui-table-primary">{runLabel(r)}</div>
          <div className="ui-table-sub">
            by {r.triggered_by}
            {r.name && ` · ${new Date(r.started_at).toLocaleString()}`}
          </div>
        </>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "110px",
      render: (r) => (
        <Badge tone={STATUS_TONE[r.status] || "neutral"}>
          {r.status === "running" && r.cancel_requested ? "cancelling" : r.status}
        </Badge>
      ),
    },
    {
      key: "pass",
      header: "Pass rate",
      width: "92px",
      align: "end",
      className: "ui-num",
      render: (r) => (r.pass_rate === null ? "—" : `${Math.round(r.pass_rate * 100)}%`),
    },
    {
      key: "wrong",
      header: "Wrong",
      width: "70px",
      align: "end",
      className: "ui-num",
      render: (r) => r.incorrect_count ?? 0,
    },
    {
      key: "flags",
      header: "Notes",
      width: "150px",
      render: (r) => (
        <div className="run-flags">
          {/* Questions whose judge replied unparseably. Not folded into the pass
              rate (they stay in the denominator — an ungraded question is not a
              pass), but a rate that fell because the judge broke is a different
              problem from one that fell because the agent did. */}
          {r.judge_invalid_count > 0 && (
            <Badge
              tone="warning"
              title="The judge's reply could not be read for these questions, so they count as not passed"
            >
              {r.judge_invalid_count} ungraded
            </Badge>
          )}
          {r.config?.judge_prompt_fingerprint &&
            r.config.judge_prompt_fingerprint !== currentFingerprint && (
              <Badge
                tone="warning"
                outline
                title="Graded with different criteria than this set uses now — its pass rate is not directly comparable with the others"
              >
                other criteria
              </Badge>
            )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={evalSet.name}
        subtitle="Every run recorded against this set. Open one to see where its wrong answers went off the rails."
        primary={
          <Button variant="primary" icon={<IconPlay size={14} />} onClick={() => setShowRunConfig(true)}>
            Run eval
          </Button>
        }
        menu={
          // Same menu as the set's card in the grid, minus Delete — see
          // EvalSetMenu.
          <EvalSetMenu
            label="Eval set actions"
            owner={myRole === "owner"}
            unreviewedJudging={unreviewedJudging}
            onDownload={() => setShowDownload(true)}
            onEditQuestions={() => setShowEditor(true)}
            onConfigure={() => setConfigTab("judging")}
          />
        }
      />
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
            label={runLabel(r)}
            onDone={load}
          />
        ))}

      {/* The compare bar appears only once there is something to compare. The
          three modes are meaningless against a single run — all three answer the
          same question — so offering them permanently, above a list nothing was
          yet ticked in, was asking for a decision that had no consequences. */}
      {selected.length > 0 && (
        <Toolbar
          className="run-compare-bar"
          end={
            <>
              <Button variant="ghost" onClick={() => setSelected([])}>Clear</Button>
              <Button variant="primary" onClick={() => onOpenRuns(selected, mode, lastN)}>
                {comparing ? `Compare ${selected.length} runs` : "Open run"}
              </Button>
            </>
          }
        >
          <span className="ui-toolbar-label">
            <strong>{selected.length}</strong> selected
          </span>
          {comparing && (
            <>
              <span className="ui-toolbar-label">Count a question wrong when it</span>
              <SegmentedControl
                value={mode}
                onChange={setMode}
                options={MODES}
                size="sm"
                ariaLabel="Which questions count as incorrect"
              />
              {mode === "last_n" && (
                <>
                  <span className="ui-toolbar-label">in the last</span>
                  <input
                    type="number"
                    min="1"
                    value={lastN}
                    onChange={(e) => setLastN(Number(e.target.value))}
                    className="ui-inline-input"
                    style={{ width: 64 }}
                    aria-label="How many recent runs must fail"
                  />
                  <span className="ui-toolbar-label">runs</span>
                </>
              )}
            </>
          )}
        </Toolbar>
      )}

      {runs === null && <Skeleton variant="row" count={5} />}
      {runs && (
        <DataTable
          columns={columns}
          rows={runs}
          staggerWithin={PAGE_SIZE}
          onRowClick={(r) => onOpenRuns([r.id], "union", 2)}
          isSelected={(r) => selected.includes(r.id)}
          onToggleSelect={toggle}
          selectLabel="Select run to compare"
          empty={
            <EmptyState
              icon={<IconInbox size={22} />}
              title="No runs yet"
              action={
                <Button variant="primary" icon={<IconPlay size={14} />} onClick={() => setShowRunConfig(true)}>
                  Run eval
                </Button>
              }
            >
              Run this eval set to see how the agent scores, and where its wrong
              answers came from.
            </EmptyState>
          }
          rowActions={(r) => (
            <>
              <IconButton
                label="View the settings this run used"
                icon={<IconFileText size={16} />}
                onClick={() => setViewConfigRun(r)}
              />
              {/* A run in flight offers stop; only a finished one offers delete. */}
              {r.status === "running"
                ? canCancel(r) && (
                    <IconButton
                      label="Stop this run"
                      icon={<IconStop size={14} />}
                      disabled={r.cancel_requested}
                      onClick={() => cancel(r)}
                    />
                  )
                : myRole === "owner" && (
                    <IconButton
                      label="Delete this run"
                      icon={<IconTrash size={16} />}
                      className="ui-btn-destructive-hover"
                      onClick={() => setDeleteRun(r)}
                    />
                  )}
            </>
          )}
        />
      )}

      {runs && runs.length > 0 && (
        <ListFooter
          shown={runs.length}
          total={total}
          hasMore={hasMore}
          loading={loadingMore}
          onLoadMore={loadMore}
        />
      )}

      {showDownload && (
        // Runs already ticked here are what the developer is looking at, so the
        // dialog opens on that selection rather than making them re-pick it.
        <DownloadDialog
          evalSet={evalSet}
          subject={subject}
          seedRunIds={selected}
          onClose={() => setShowDownload(false)}
        />
      )}
      {showEditor && <QuestionEditor evalSet={evalSet} onClose={() => setShowEditor(false)} />}
      {configTab && (
        <ConfigDialog
          evalSet={evalSet}
          subject={subject}
          initialTab={configTab}
          onClose={async () => {
            // Opening the tab is the review. Recorded on close rather than on
            // save so the marker also clears for an owner who looked, decided
            // the default was right, and changed nothing.
            if (unreviewedJudging) {
              try {
                onEvalSetChanged?.(await api.markJudgePromptReviewed(evalSet.id));
              } catch {
                /* the marker staying lit is not worth an error toast */
              }
            }
            setConfigTab(null);
          }}
          onSaved={async () => {
            setConfigTab(null);
            try {
              onEvalSetChanged?.(await api.getEvalSet(evalSet.id));
            } catch {
              /* the dialog already reported anything that mattered */
            }
          }}
        />
      )}
      {showRunConfig && (
        <RunConfigDialog
          evalSetId={evalSet.id}
          evalSet={evalSet}
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
          message={`“${runLabel(deleteRun)}” and everything recorded for it will be removed.`}
          detail="Its per-question results and stored diagnoses go with it. Other runs in this eval set are untouched."
          confirmLabel="Delete run"
          onConfirm={confirmDelete}
          onClose={() => setDeleteRun(null)}
        />
      )}
    </div>
  );
}
