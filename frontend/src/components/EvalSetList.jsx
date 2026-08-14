import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { usePagedList } from "../usePagedList.js";
import ListFooter from "./ListFooter.jsx";
import Sparkline from "./Sparkline.jsx";
import UploadDialog from "./UploadDialog.jsx";
import ConfigDialog from "./ConfigDialog.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import DownloadDialog from "./DownloadDialog.jsx";
import EvalSetMenu from "./EvalSetMenu.jsx";
import QuestionEditor from "./QuestionEditor.jsx";
import { useToast } from "./Toast.jsx";
import Button from "./ui/Button.jsx";
import Badge, { BadgeRow } from "./ui/Badge.jsx";
import Card from "./ui/Card.jsx";
import EmptyState from "./ui/EmptyState.jsx";
import PageHeader from "./ui/PageHeader.jsx";
import { SkeletonCards } from "./ui/Skeleton.jsx";
import Toolbar, { SearchInput, SegmentedControl } from "./ui/Toolbar.jsx";
import {
  IconInbox, IconSearch, IconTrendDown, IconTrendUp, IconUpload, IconUsers,
} from "./icons.jsx";

const PAGE_SIZE = 24;

const SORTS = [
  { value: "created_at", label: "Newest" },
  { value: "name", label: "Name" },
];

// Top tier: one card per eval set — run count, latest pass rate, trend
// sparkline, regression summary. Owners get the card's overflow menu.
//
// Cards page in as you scroll rather than rendering every set at once. The
// toolbar above them filters server-side, so searching looks at every set the
// developer can see, not just the ones already loaded — filtering only the loaded
// page would make the result depend on how far they had scrolled.
export default function EvalSetList({ onOpen, subject }) {
  const toast = useToast();
  const [showUpload, setShowUpload] = useState(false);
  const [configSet, setConfigSet] = useState(null);
  const [deleteSet, setDeleteSet] = useState(null);
  const [downloadSet, setDownloadSet] = useState(null);
  const [editSet, setEditSet] = useState(null);

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

  function clearFilters() {
    setQuery("");
    setSearch("");
    setMetadataKey("");
    setMetadataValue("");
  }

  async function confirmDelete() {
    await api.deleteEvalSet(deleteSet.id);
    setDeleteSet(null);
    toast.success(`Deleted “${deleteSet.name}”`);
    refresh();
  }

  // Opening the settings counts as reviewing how the set is graded, so an owner
  // who looked and decided the default was right isn't nagged forever.
  async function closeConfig() {
    if (!configSet.judge_prompt?.reviewed_at) {
      try {
        await api.markJudgePromptReviewed(configSet.id);
      } catch {
        /* a lingering marker is not worth an error toast */
      }
    }
    setConfigSet(null);
    refresh();
  }

  return (
    <div>
      <PageHeader
        title="Eval sets"
        subtitle="A set of questions, the answers they should get, and every run recorded against them."
        primary={
          <Button variant="primary" icon={<IconUpload size={15} />} onClick={() => setShowUpload(true)}>
            Upload eval set
          </Button>
        }
      />

      <Toolbar
        end={
          <>
            <span className="ui-toolbar-label">Sort</span>
            <SegmentedControl
              value={sort}
              onChange={setSort}
              options={SORTS}
              size="sm"
              ariaLabel="Sort eval sets"
            />
          </>
        }
      >
        <SearchInput
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
              className="ui-inline-select"
            >
              <option value="">Any label</option>
              {metaKeys.map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
            {/* Only meaningful once a key is chosen — an "equals" box with
                nothing to equal is just clutter. */}
            {metadataKey && (
              <input
                placeholder={`${metadataKey} = anything`}
                value={metadataValue}
                onChange={(e) => setMetadataValue(e.target.value)}
                aria-label={`Filter by ${metadataKey} value`}
                className="ui-inline-input"
              />
            )}
          </>
        )}
      </Toolbar>

      {error && <div className="error">{error}</div>}
      {sets === null && <SkeletonCards count={6} />}

      {sets && sets.length === 0 && (
        filtering ? (
          <EmptyState
            icon={<IconSearch size={22} />}
            title="Nothing matches those filters"
            action={<Button onClick={clearFilters}>Clear filters</Button>}
          >
            No eval set matches the search and labels you have applied.
          </EmptyState>
        ) : (
          <EmptyState
            icon={<IconInbox size={22} />}
            title="No eval sets yet"
            size="lg"
            action={
              <Button variant="primary" icon={<IconUpload size={15} />} onClick={() => setShowUpload(true)}>
                Upload eval set
              </Button>
            }
          >
            An eval set is a spreadsheet of questions and the answers the agent
            should give. Upload one to run your first evaluation.
          </EmptyState>
        )
      )}

      <div className="set-grid">
        {sets &&
          sets.map((s, i) => (
            <SetCard
              key={s.id}
              set={s}
              // Stagger only within a page: restarting the animation for every
              // card on each append would flash the whole grid.
              index={i % PAGE_SIZE}
              onOpen={() => onOpen(s)}
              onDownload={() => setDownloadSet(s)}
              onEditQuestions={() => setEditSet(s)}
              onConfigure={() => setConfigSet(s)}
              onDelete={() => setDeleteSet(s)}
            />
          ))}
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
      {/* No refresh on close: the editor rewrites a question's text and cannot
          add or remove one, so nothing the card shows about the set changes. */}
      {editSet && <QuestionEditor evalSet={editSet} onClose={() => setEditSet(null)} />}
      {configSet && (
        <ConfigDialog
          evalSet={configSet}
          subject={subject}
          onClose={closeConfig}
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

function SetCard({ set: s, index, onOpen, onDownload, onEditQuestions, onConfigure, onDelete }) {
  const owner = s.my_role === "owner";
  const members = (s.roles || []).length;
  // "Nobody has looked at how this set is graded yet" — not "your judge prompt is
  // the default one", which is true of nearly every set and would be background
  // noise inside a week.
  const unreviewed = owner && !s.judge_prompt?.reviewed_at;
  const labels = Object.entries(s.metadata || {});

  return (
    <Card
      interactive
      padded={false}
      onClick={onOpen}
      className="set-card"
      style={{ animationDelay: `${index * 40}ms` }}
    >
      <div className="set-card-top">
        <div className="set-card-heading">
          <h3>{s.name}</h3>
          <div className="set-card-meta">
            <span>{new Date(s.created_at).toLocaleDateString()}</span>
            <Badge tone={owner ? "success" : "neutral"} size="sm">{s.my_role}</Badge>
            {members > 1 && (
              <Badge tone="neutral" size="sm" icon={<IconUsers size={11} />}>
                {members}
              </Badge>
            )}
          </div>
        </div>

        {/* Always visible. These used to appear only on hover, which meant the
            only way to discover that a set could be downloaded or deleted was to
            happen to move the pointer over it. */}
        <div className="set-card-menu" onClick={(e) => e.stopPropagation()}>
          <EvalSetMenu
            label={`Actions for ${s.name}`}
            owner={owner}
            unreviewedJudging={unreviewed}
            onDownload={onDownload}
            onEditQuestions={onEditQuestions}
            onConfigure={onConfigure}
            // The only item this menu has and the set's own page does not:
            // deleting from the grid leaves the developer looking at a grid,
            // not at a page for a set that has just stopped existing.
            onDelete={owner ? onDelete : undefined}
          />
          {unreviewed && <span className="set-card-nudge" aria-hidden="true" />}
        </div>
      </div>

      <div className="set-card-stats">
        <div className="set-stat">
          <div className="set-stat-num">
            {s.latest_pass_rate === null ? "—" : `${Math.round(s.latest_pass_rate * 100)}%`}
          </div>
          <div className="set-stat-lbl">latest pass rate</div>
        </div>
        <div className="set-stat">
          <div className="set-stat-num set-stat-num-sm">{s.question_count}</div>
          <div className="set-stat-lbl">
            {s.question_count === 1 ? "question" : "questions"}
          </div>
        </div>
        <div className="set-stat">
          <div className="set-stat-num set-stat-num-sm">{s.run_count}</div>
          <div className="set-stat-lbl">{s.run_count === 1 ? "run" : "runs"}</div>
        </div>
        <div className="set-card-spark"><Sparkline values={s.trend} /></div>
      </div>

      {(s.regressed > 0 || s.improved > 0 || labels.length > 0) && (
        <div className="set-card-foot">
          <BadgeRow>
            {s.regressed > 0 && (
              <Badge tone="danger" icon={<IconTrendDown size={11} />}>
                {s.regressed} regressed
              </Badge>
            )}
            {s.improved > 0 && (
              <Badge tone="success" icon={<IconTrendUp size={11} />}>
                {s.improved} improved
              </Badge>
            )}
            {labels.map(([k, v]) => (
              <Badge key={k} tone="neutral">{k}: {String(v)}</Badge>
            ))}
          </BadgeRow>
        </div>
      )}
    </Card>
  );
}
