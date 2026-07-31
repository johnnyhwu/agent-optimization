import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import AttemptList from "./AttemptList.jsx";
import Modal from "./Modal.jsx";
import ShortlistDialog from "./ShortlistDialog.jsx";
import PhaseSteps from "./PhaseSteps.jsx";
import PlaygroundComposer from "./PlaygroundComposer.jsx";
import SpanDetail from "./SpanDetail.jsx";
import SpanList from "./SpanList.jsx";
import { useToast } from "./Toast.jsx";
import { IconBookmark, IconRefresh } from "./icons.jsx";
import {
  diffConfig,
  editedFiles,
  overrideCounts,
  sameSkills,
  stripRedacted,
} from "../workspace_util.js";
import * as shortlist from "../shortlist.js";
import { href, navigate } from "../useHashRoute.js";

// The playground (§10): one question at a time, against an editable copy of the
// agent's own config and skill files.
//
// Structurally this is the three-column detail view with a composer on top, and
// it reuses that view's two hard-won mechanisms (§9.18a):
//
//   * The open attempt is held **by id** and re-derived from the list on every
//     render. Holding the object froze the verdict, because the SSE stream
//     replaces list entries rather than mutating them.
//   * A **fingerprint** of the fields that change what GET /attempts/{id}
//     returns drives the refetch, so the middle column follows a live attempt
//     without polling — and refetching never blanks what is on screen.
//
// Config and credentials live in this component's state, so they are typed once
// per browser session. That matches the store being ephemeral: there is no run
// row to borrow keys from the way the eval path does.
//
// The agent's workspace lives here too, as two values: `workspace` is the
// snapshot the agent server served, and `wsEdit` is the working copy. Keeping
// both is what makes "revert this field" and "what did I actually change?"
// answerable — and the snapshot's `version` is what the staleness check before
// each send compares against.

const EMPTY_DRAFT = {
  question: "",
  ground_truth_response: "",
  ground_truth_reasoning: "",
};

export default function Playground({ subject, seed, onSeedApplied }) {
  const toast = useToast();
  const [attempts, setAttempts] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailRefreshing, setDetailRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [activeSpan, setActiveSpan] = useState(null);
  // The last state the SSE stream reported for the open attempt. Kept separate
  // from the list row because two of its fields (trace_ready, has_analysis) exist
  // only on the stream.
  const [live, setLive] = useState(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [reDiagnosing, setReDiagnosing] = useState(false);
  const [error, setError] = useState(null);

  // Connection settings, prefilled from the environment exactly as the run dialog
  // is, so both agree about what a blank field means.
  const [form, setForm] = useState(null);
  const [impls, setImpls] = useState({});
  const [secrets, setSecrets] = useState({ llm_api_key: "", langfuse_secret_key: "" });

  // The agent's workspace: what it served, and what the developer has done to it.
  const [workspace, setWorkspace] = useState(null);
  const [wsEdit, setWsEdit] = useState(null);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsError, setWsError] = useState(null);
  // Set when the agent's workspace moved on after the snapshot was taken. The
  // send waits on the answer rather than guessing: reloading throws away the
  // edit, and sending anyway may be exactly what was intended.
  const [stale, setStale] = useState(null);

  // Questions on their way out of the playground and into an eval set (§10.8).
  // Copies, not references: an attempt is evicted at the per-user cap and lost
  // on a backend restart, and losing a shortlist entry to either would be worst
  // exactly when someone is iterating hardest.
  const [shortlistItems, setShortlistItems] = useState([]);
  const [shortlistOpen, setShortlistOpen] = useState(false);

  const active = attempts.find((a) => a.id === activeId) || null;

  useEffect(() => {
    api
      .runConfigDefaults()
      .then((r) => {
        setImpls(r.impls || {});
        setForm(r.defaults);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    reload();
    // A different identity has a different set of attempts.
  }, [subject]);

  useEffect(() => {
    loadWorkspace();
  }, []);

  useEffect(() => {
    setShortlistItems(shortlist.readShortlist(subject));
  }, [subject]);

  async function addToShortlist(attempt) {
    try {
      // Fetched rather than read off the list row for the same reason cloning
      // is: the answer and both ground-truth fields only exist on the detail
      // payload, and a shortlist entry missing them is one the developer has to
      // retype from memory.
      const full = await api.getAttempt(attempt.id);
      setShortlistItems(
        shortlist.add(subject, shortlist.itemFromAttempt(attempt, full))
      );
      toast.success("Shortlisted");
    } catch (e) {
      toast.error(e.message);
    }
  }

  async function loadWorkspace() {
    setWsLoading(true);
    setWsError(null);
    try {
      const ws = await api.getWorkspace();
      setWorkspace(ws);
      // Reloading starts the edit over from what the agent has now. Replaying
      // the old edits onto new text would produce a third version that matches
      // neither, which is precisely the confusion the version check exists to
      // prevent.
      setWsEdit({ config: ws.config, skills: ws.skills });
    } catch (e) {
      // Never a blank editor: "this agent has no skills" and "the agent server
      // refused us" have to stay distinguishable, or the developer retypes a
      // skill from memory and tests the wrong text.
      setWsError(e.message);
    } finally {
      setWsLoading(false);
    }
  }

  function reloadWorkspace() {
    const dirty =
      workspace && wsEdit
        ? Object.keys(diffConfig(workspace.config, wsEdit.config) || {}).length +
          editedFiles(workspace.skills, wsEdit.skills).length
        : 0;
    if (dirty && !window.confirm("Reloading discards your edits to the workspace. Continue?")) {
      return;
    }
    loadWorkspace();
  }

  // What the next question should carry, or null when nothing was edited. An
  // empty override is not sent: the agent server reads a present `workspace` as
  // "use this instead of mine", so sending one would claim an experiment that
  // never happened.
  function buildOverride() {
    if (!workspace || !wsEdit) return null;
    const config = stripRedacted(
      diffConfig(workspace.config, wsEdit.config), workspace.redacted_paths
    );
    const skills = sameSkills(workspace.skills, wsEdit.skills) ? null : wsEdit.skills;
    if (!config && skills === null) return null;
    return { config, skills };
  }

  // A question handed over from the three-column view (§10.5). Only the question
  // and its ground truth travel: the workspace stays as the agent server has it,
  // so the first run of a handed-over question reproduces what the eval run did
  // rather than silently testing somebody's leftover edit.
  useEffect(() => {
    if (!seed) return;
    setDraft({
      question: seed.question || "",
      ground_truth_response: seed.ground_truth_response || "",
      ground_truth_reasoning: seed.ground_truth_reasoning || "",
    });
    if (seed.config) setForm((f) => (f ? { ...f, ...stripBlank(seed.config) } : f));
    onSeedApplied?.();
  }, [seed]);

  async function reload() {
    try {
      setAttempts(await api.listAttempts());
    } catch (e) {
      setError(e.message);
    }
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // A cleared number input parses to 0/NaN, which the backend rejects. Send null
  // so it falls back to the environment, which is what blank means everywhere.
  const setNum = (k, raw) => {
    const n = Number(raw);
    set(k, raw === "" || !Number.isFinite(n) || n <= 0 ? null : n);
  };

  // --- Live updates for the running attempt ---------------------------------
  // Subscribing for the open attempt is enough: a finished one closes the stream
  // immediately, so a historical attempt costs nothing.
  useEffect(() => {
    if (!activeId) return undefined;
    const es = new EventSource(api.attemptProgressUrl(activeId));

    const patch = (e) => {
      let d;
      try {
        d = JSON.parse(e.data);
      } catch {
        return;
      }
      setAttempts((prev) =>
        prev.map((a) =>
          a.id === activeId
            ? {
                ...a,
                phase: d.phase ?? a.phase,
                status: d.status ?? a.status,
                verdict: d.verdict ?? a.verdict,
                error_message: d.error_message ?? a.error_message,
              }
            : a
        )
      );
      // trace_ready / has_analysis are part of the fingerprint but not of the
      // list row, so they are folded into it here.
      setLive({
        trace_ready: d.trace_ready,
        has_analysis: d.has_analysis,
        phase: d.phase,
        verdict: d.verdict,
        status: d.status,
      });
    };

    ["snapshot", "attempt_started", "attempt_answered", "attempt_judged",
     "attempt_traced", "attempt_completed"].forEach((name) =>
      es.addEventListener(name, patch)
    );
    es.addEventListener("attempt_completed", () => {
      es.close();
      // Reconcile once at the end: latency and the final flags are only on the
      // authoritative payload.
      reload();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [activeId]);

  useEffect(() => setLive(null), [activeId]);

  const detailKey = active
    ? [
        active.id,
        live?.phase ?? active.phase,
        live?.verdict ?? active.verdict,
        live?.status ?? active.status,
        live?.trace_ready ?? "",
        live?.has_analysis ?? "",
        nonce,
      ].join("|")
    : null;

  // Jump to the top suspect once per attempt, and once more when a diagnosis
  // first appears — never on a background refresh, which would pull the
  // developer off the span they are reading.
  const jumpedFor = useRef(null);

  useEffect(() => {
    if (!activeId || !detailKey) return undefined;
    let cancelled = false;
    setDetailRefreshing(true);
    api
      .getAttempt(activeId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        const top = d.trace.analysis?.suspects?.[0]?.span_index;
        const target = top !== undefined ? top : d.trace.spans[0]?.index;
        const jumpTo = d.trace.analysis ? `${activeId}:analysis` : activeId;
        if (target !== undefined && jumpedFor.current !== jumpTo) {
          jumpedFor.current = jumpTo;
          setActiveSpan(target);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        // A 404 here is the honest answer after a backend restart dropped the
        // in-memory store, so say that rather than "not found".
        setError(
          e.status === 404
            ? "That attempt is gone — attempts live in the backend's memory and are lost when it restarts."
            : e.message
        );
      })
      .finally(() => {
        if (!cancelled) setDetailRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId, detailKey]);

  function pick(a) {
    if (a.id === activeId) return;
    setActiveId(a.id);
    setDetail(null); // a different attempt: none of the old one still applies
    setActiveSpan(null);
  }

  async function send() {
    if (!draft.question.trim()) return;
    // Ask the agent whether its workspace moved since the snapshot was taken.
    // Cheap (one string) and asked before the call rather than after, because a
    // question answered against a stale skill is not a result you can trust —
    // and you would have no way of telling afterwards.
    if (workspace?.version) {
      try {
        const { version } = await api.getWorkspaceVersion();
        if (version && version !== workspace.version) {
          setStale({ version });
          return;
        }
      } catch {
        // The agent server not answering the version check is not a reason to
        // refuse the experiment: it costs the check, not the question.
      }
    }
    await sendNow();
  }

  async function sendNow() {
    setStale(null);
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAttempt({
        question: draft.question,
        ground_truth_response: draft.ground_truth_response || null,
        ground_truth_reasoning: draft.ground_truth_reasoning || null,
        workspace: buildOverride(),
        config: form || {},
        secrets,
      });
      setAttempts((prev) => [created, ...prev]);
      setDetail(created);
      setActiveId(created.id);
      setActiveSpan(null);
      jumpedFor.current = null;
    } catch (e) {
      setError(e.message);
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function clone(a) {
    // Fetched rather than read off the list row: the ground-truth fields and the
    // workspace override are only on the detail payload, and a clone that
    // silently dropped them would change two variables at once — which is
    // exactly what makes a before/after comparison worthless.
    try {
      const full = await api.getAttempt(a.id);
      setDraft({
        question: full.question,
        ground_truth_response: full.ground_truth_response || "",
        ground_truth_reasoning: full.ground_truth_reasoning || "",
      });
      if (full.workspace && workspace) {
        // Rebuilt against the current snapshot, each half the way the agent
        // server reads it: the config overlay is sparse and merges onto what the
        // agent has now, while the skills are the complete set that attempt ran
        // with and replace the working copy outright.
        setWsEdit({
          config: applyOverlay(workspace.config, full.workspace.config),
          skills: full.workspace.skills || workspace.skills,
        });
      }
      // Credentials are excluded: they are write-only and never come back. They
      // are already in this session's state anyway.
      if (full.config) setForm((f) => ({ ...f, ...stripBlank(full.config) }));
      toast.success("Copied into the composer");
    } catch (e) {
      toast.error(e.message);
    }
  }

  async function cancel(a) {
    try {
      await api.cancelAttempt(a.id);
      toast.info("Stopping…");
    } catch (e) {
      toast.error(e.message);
    }
  }

  async function remove(a) {
    try {
      await api.deleteAttempt(a.id);
      setAttempts((prev) => prev.filter((x) => x.id !== a.id));
      if (activeId === a.id) {
        setActiveId(null);
        setDetail(null);
      }
    } catch (e) {
      toast.error(e.message);
    }
  }

  async function reDiagnose() {
    if (!active) return;
    setReDiagnosing(true);
    try {
      await api.reDiagnoseAttempt(active.id);
      // Regenerating over an existing analysis leaves has_analysis unchanged, so
      // the fingerprint alone would not notice the new content.
      jumpedFor.current = null;
      setNonce((n) => n + 1);
      toast.success("Diagnosis regenerated");
    } catch (e) {
      setError(e.message);
      toast.error(e.message);
    } finally {
      setReDiagnosing(false);
    }
  }

  const trace = detail?.trace || null;
  const suspectByIndex = {};
  (trace?.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const activeSpanObj = trace?.spans?.find((s) => s.index === activeSpan) || null;

  return (
    <div className="page-fill">
      <div className="page-head">
        <div>
          <h2>Playground</h2>
          <p className="muted">
            One question against an editable copy of the agent's config and skill
            files, run as often as you like. Nothing here is saved — attempts live
            in the backend's memory until it restarts.
          </p>
        </div>
        <div className="page-head-actions">
          <button
            className={shortlistItems.length ? "active" : ""}
            onClick={() => setShortlistOpen(true)}
            title="Review shortlisted questions and create an eval set from them"
          >
            <IconBookmark size={14} /> Shortlist
            {shortlistItems.length > 0 && (
              <span className="count">{shortlistItems.length}</span>
            )}
          </button>
          <button onClick={reload} title="Reload the attempt list">
            <IconRefresh size={14} /> Refresh
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {form && (
        <PlaygroundComposer
          draft={draft}
          setDraft={setDraft}
          form={form}
          set={set}
          setNum={setNum}
          secrets={secrets}
          setSecrets={setSecrets}
          impls={impls}
          onSend={send}
          busy={busy}
          workspace={workspace}
          workspaceEdit={wsEdit}
          onWorkspaceEdit={setWsEdit}
          workspaceLoading={wsLoading}
          workspaceError={wsError}
          onReloadWorkspace={reloadWorkspace}
        />
      )}

      {active && (
        <div className="attempt-head">
          <PhaseSteps attempt={active} />
          {active.workspace_overridden && (
            <span className="hint">
              sent with{" "}
              <strong>{describeOverride(active)}</strong>
              {" "}— the agent's own workspace was not changed
            </span>
          )}
        </div>
      )}

      <div className="three playground-three">
        <AttemptList
          attempts={attempts}
          activeId={activeId}
          onPick={pick}
          onClone={clone}
          onCancel={cancel}
          onDelete={remove}
          shortlistedIds={new Set(shortlistItems.map((i) => i.id))}
          onShortlist={addToShortlist}
        />
        <SpanList
          trace={trace}
          refreshing={detailRefreshing}
          activeSpan={activeSpan}
          onPickSpan={setActiveSpan}
          canReDiagnose={Boolean(active?.has_expected_reasoning && trace?.spans?.length)}
          onReDiagnose={reDiagnose}
          reDiagnosing={reDiagnosing}
          onRetryTrace={() => setNonce((n) => n + 1)}
          playground
          emptyHint="Ask a question, or pick an earlier attempt."
        />
        <SpanDetail
          span={activeSpanObj}
          suspect={activeSpanObj ? suspectByIndex[activeSpanObj.index] : null}
        />
      </div>

      {shortlistOpen && (
        <ShortlistDialog
          items={shortlistItems}
          subject={subject}
          onChange={(id, fields) => setShortlistItems(shortlist.update(subject, id, fields))}
          onRemove={(id) => setShortlistItems(shortlist.remove(subject, id))}
          onClose={() => setShortlistOpen(false)}
          onCreated={(evalSetId) => {
            // The shortlist has done its job; keeping it would invite the same
            // questions being promoted twice into two different sets.
            setShortlistItems(shortlist.clear(subject));
            setShortlistOpen(false);
            // Built by intent rather than by string: the hash shape lives in
            // one place, and a hand-written one silently falls back to the
            // eval-set list instead of opening what was just created.
            navigate(href.evalSet(evalSetId));
          }}
        />
      )}

      {stale && (
        <Modal
          title="The agent's workspace has changed"
          subtitle={`You started from ${workspace?.version}; the agent server is now at ${stale.version}.`}
          onClose={() => setStale(null)}
          width={520}
          footer={
            <>
              <button onClick={() => setStale(null)}>Cancel</button>
              <button
                onClick={async () => {
                  setStale(null);
                  await loadWorkspace();
                }}
              >
                Reload workspace
              </button>
              <button className="primary" onClick={sendNow}>
                Send anyway
              </button>
            </>
          }
        >
          <p style={{ margin: "0 0 8px" }}>
            Someone changed the agent's config or skill files after this editor
            read them.
          </p>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            <strong>Reload workspace</strong> starts over from what the agent has
            now and discards your edits. <strong>Send anyway</strong> runs the
            question with what is in the editor — which is a real answer when the
            change was somebody else's and unrelated to what you are testing.
          </p>
        </Modal>
      )}
    </div>
  );
}

// What an attempt carried, for the line above the three columns. Counts rather
// than names: a config path and a file path are both long, and the point of the
// line is that an override happened at all.
function describeOverride(attempt) {
  const { configs, files } = overrideCounts(attempt);
  const parts = [];
  if (configs) parts.push(`${configs} config value${configs === 1 ? "" : "s"}`);
  if (files) parts.push(`${files} skill file${files === 1 ? "" : "s"}`);
  return parts.length ? `an override of ${parts.join(" and ")}` : "a workspace override";
}

// A sparse config overlay merged onto a full config, the same deep merge the
// agent server does with it (§5.2 of the agent-server contract): a key the
// overlay does not mention keeps the value it already had.
function applyOverlay(base, overlay) {
  if (!overlay) return base;
  const out = { ...(base || {}) };
  Object.entries(overlay).forEach(([key, value]) => {
    const isObject = (v) => v && typeof v === "object" && !Array.isArray(v);
    out[key] = isObject(value) && isObject(out[key]) ? applyOverlay(out[key], value) : value;
  });
  return out;
}

// Only the values an attempt actually recorded, so cloning never overwrites a
// field with a blank.
function stripBlank(config) {
  const out = {};
  Object.entries(config).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") out[k] = v;
  });
  return out;
}
