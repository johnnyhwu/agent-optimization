import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import AgentConnectionBar from "./AgentConnectionBar.jsx";
import AttemptList from "./AttemptList.jsx";
import Modal from "./Modal.jsx";
import ShortlistDialog from "./ShortlistDialog.jsx";
import PhaseSteps from "./PhaseSteps.jsx";
import PlaygroundComposer from "./PlaygroundComposer.jsx";
import SpanDetail from "./SpanDetail.jsx";
import SpanList from "./SpanList.jsx";
import { useToast } from "./Toast.jsx";
import { IconAlert, IconBookmark, IconRefresh } from "./icons.jsx";
import {
  diffConfig,
  editedFiles,
  overrideCounts,
  sameSkills,
  stripRedacted,
} from "../workspace_util.js";
import * as shortlist from "../shortlist.js";
import { recentAgents, rememberAgent } from "../agent_recall.js";
import { href, navigate } from "../useHashRoute.js";
import { setServerTime } from "../useElapsed.js";
import Button, { IconButton } from "./ui/Button.jsx";
import Badge from "./ui/Badge.jsx";
import PageHeader from "./ui/PageHeader.jsx";

// Whether the attempt list is collapsed to a rail. Persisted, and separate from
// the side rail's own setting: reading a trace and picking between attempts are
// different tasks, and someone doing the first wants the width back.
const ATTEMPTS_KEY = "playground-attempts-collapsed";

function useAttemptsCollapsed() {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(ATTEMPTS_KEY) === "1"
  );
  return [
    collapsed,
    (v) => {
      localStorage.setItem(ATTEMPTS_KEY, v ? "1" : "0");
      setCollapsed(v);
    },
  ];
}

// The playground: one question at a time, against an editable copy of the
// agent's own config and skill files.
//
// Structurally this is the three-column detail view with a composer on top, and
// it reuses that view's two hard-won mechanisms:
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
//
// **Everything here hangs off one connected agent, and that is now explicit.**
// The workspace used to be read on mount from whatever `AGENT_BASE_URL` the
// backend was started with, while the question went to whichever URL was typed
// into the endpoints panel. Nothing enforced that these were the same server, so
// the failure was silent and total: the editor showed agent A's skill files, the
// override built from them went to agent B, the "N files edited" count was
// computed against A's baseline, and the staleness check compared A's version to
// A's — a check that cannot fail, guarding an experiment on B.
//
// So the agent is chosen first, and choosing it is what produces the snapshot:
// `connect` is `GET /playground/workspace` against that URL. `agent` below is
// the single answer to "which server is all of this about", and it is also what
// travels in the attempt's config — the connection bar writes straight into
// `form`, rather than keeping a second copy that could disagree with it.

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
  // The last state the SSE stream reported, **per attempt**. Kept separate from
  // the list row because two of its fields (trace_ready, has_analysis) exist only
  // on the stream.
  //
  // Keyed by id rather than held as one value, because the stream now reports
  // every attempt rather than only the open one. `live` below is still a single
  // object — whichever attempt is open — so `detailKey` and the whole
  // detail-refetch mechanism read exactly as they did.
  const [liveById, setLiveById] = useState({});
  // Kept after a send rather than cleared: most second questions are the first
  // one with a word changed, and re-typing it to compare two phrasings is the
  // work this screen exists to make cheap. The attempt list is the record of
  // what was asked, so nothing is lost by leaving the box as it was.
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [reDiagnosing, setReDiagnosing] = useState(false);
  const [error, setError] = useState(null);

  // Connection settings, prefilled from the environment exactly as the run dialog
  // is, so both agree about what a blank field means.
  const [form, setForm] = useState(null);
  const [impls, setImpls] = useState({});
  const [secrets, setSecrets] = useState({ llm_api_key: "", langfuse_secret_key: "" });

  // Which agent this screen is about: "disconnected" | "connecting" |
  // "connected" | "error" | "fake". The URL itself is not duplicated here — it
  // lives in `form.agent_base_url`, which is what an attempt is sent with.
  const [conn, setConn] = useState("disconnected");
  const [connError, setConnError] = useState(null);
  const [recent, setRecent] = useState([]);

  // The agent's workspace: what it served, and what the developer has done to it.
  const [workspace, setWorkspace] = useState(null);
  const [wsEdit, setWsEdit] = useState(null);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsError, setWsError] = useState(null);
  // Set when the agent's workspace moved on after the snapshot was taken. The
  // send waits on the answer rather than guessing: reloading throws away the
  // edit, and sending anyway may be exactly what was intended.
  const [stale, setStale] = useState(null);
  // Set when something carried in — a cloned attempt, a question handed over
  // from a run — names an agent other than the connected one.
  const [agentMismatch, setAgentMismatch] = useState(null);

  // Questions on their way out of the playground and into an eval set.
  // Copies, not references: an attempt is evicted at the per-user cap and lost
  // on a backend restart, and losing a shortlist entry to either would be worst
  // exactly when someone is iterating hardest.
  const [shortlistItems, setShortlistItems] = useState([]);
  const [shortlistOpen, setShortlistOpen] = useState(false);
  const [attemptsCollapsed, setAttemptsCollapsed] = useAttemptsCollapsed();

  const active = attempts.find((a) => a.id === activeId) || null;

  // The defaults decide where this screen starts: with a fake workspace seam
  // there is no agent to connect to, and with an AGENT_BASE_URL in the
  // environment there is one obvious answer — connecting to it keeps every
  // existing single-agent deployment working exactly as it did, rather than
  // making everyone press a button to get back to where they already were.
  useEffect(() => {
    api
      .runConfigDefaults()
      .then((r) => {
        const impls = r.impls || {};
        setImpls(impls);
        setForm(r.defaults);
        if (impls.workspace === "fake") {
          setConn("fake");
          // The canned workspace cannot really fail, but a rejection here would
          // be an unhandled one — and `loadWorkspace` has already put the reason
          // where the editor shows it.
          loadWorkspace({}).catch(() => {});
        } else if (r.defaults?.agent_base_url) {
          connect({
            base_url: r.defaults.agent_base_url,
            timeout_s: r.defaults.agent_timeout_s ?? null,
          });
        }
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    api
      .listAttempts()
      .then(setAttempts)
      .catch((e) => setError(e.message));
    // A different identity has a different set of attempts.
  }, [subject]);

  useEffect(() => {
    setShortlistItems(shortlist.readShortlist(subject));
    setRecent(recentAgents(subject));
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

  // The agent whose workspace this screen is showing, in the shape both the API
  // client and an attempt's config want. Read from `form` rather than kept
  // beside it, so there is one answer to "which agent" and not two that can
  // drift.
  function agentOf(f = form) {
    return {
      agent_base_url: f?.agent_base_url || "",
      agent_timeout_s: f?.agent_timeout_s ?? null,
    };
  }

  async function loadWorkspace(agent) {
    setWsLoading(true);
    setWsError(null);
    try {
      const ws = await api.getWorkspace(agent);
      setWorkspace(ws);
      // Reloading starts the edit over from what the agent has now. Replaying
      // the old edits onto new text would produce a third version that matches
      // neither, which is precisely the confusion the version check exists to
      // prevent.
      setWsEdit({ config: ws.config, skills: ws.skills });
      return ws;
    } catch (e) {
      // Never a blank editor: "this agent has no skills" and "the agent server
      // refused us" have to stay distinguishable, or the developer retypes a
      // skill from memory and tests the wrong text.
      setWsError(e.message);
      throw e;
    } finally {
      setWsLoading(false);
    }
  }

  // Connect to an agent: point the form at it, then read its workspace. The read
  // *is* the connection test — it proves the host answers, that it speaks the
  // workspace contract, and it produces the snapshot and version everything below
  // depends on. A separate ping would prove less and be one more thing to keep
  // in step.
  async function connect({ base_url, timeout_s }) {
    const agent = { agent_base_url: base_url, agent_timeout_s: timeout_s ?? null };
    setForm((f) => ({ ...(f || {}), ...agent }));
    setConn("connecting");
    setConnError(null);
    setStale(null);
    try {
      await loadWorkspace(agent);
      setConn("connected");
      // Remembered only once it worked: the URL that could not be reached is
      // exactly the one not worth offering back next time.
      setRecent(rememberAgent(subject, { base_url, timeout_s: timeout_s ?? null }));
    } catch (e) {
      setConn("error");
      setConnError(e.message);
      setWorkspace(null);
      setWsEdit(null);
    }
  }

  // How much of the workspace edit would be lost. Asked before anything that
  // replaces the snapshot, because an edit is the expensive thing on this screen
  // — the question can be retyped in seconds, a rewritten skill cannot.
  function dirtyCount() {
    if (!workspace || !wsEdit) return 0;
    return (
      Object.keys(diffConfig(workspace.config, wsEdit.config) || {}).length +
      editedFiles(workspace.skills, wsEdit.skills).length
    );
  }

  function reloadWorkspace() {
    if (
      dirtyCount() &&
      !window.confirm("Reloading discards your edits to the workspace. Continue?")
    ) {
      return;
    }
    loadWorkspace(agentOf()).catch(() => {});
  }

  // Switch straight to another agent, keeping the same confirm as changing by
  // hand — the edits being discarded are worth the same either way.
  function changeAgentTo({ url, timeout_s }) {
    if (
      dirtyCount() &&
      !window.confirm(
        "Connecting to another agent discards your edits to this one's workspace. Continue?"
      )
    ) {
      return;
    }
    connect({ base_url: url, timeout_s });
  }

  // Back to the connect form. The snapshot goes with it: it describes a server
  // this screen is no longer about, and keeping the edits would mean diffing the
  // next agent's files against the previous one's.
  function changeAgent() {
    if (
      dirtyCount() &&
      !window.confirm(
        "Changing agent discards your edits to this agent's workspace. Continue?"
      )
    ) {
      return;
    }
    setConn("disconnected");
    setConnError(null);
    setWorkspace(null);
    setWsEdit(null);
    setWsError(null);
    setStale(null);
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

  // A question handed over from the three-column view. Only the question
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
    if (seed.config) {
      setForm((f) => (f ? { ...f, ...stripBlank(otherAgent(seed.config)) } : f));
      noteAgentMismatch(seed.config, "The run this question came from");
    }
    onSeedApplied?.();
  }, [seed]);

  // A config arriving from somewhere else — a run, an earlier attempt — with its
  // own agent in it. The two agent fields are held back: repointing the
  // connection from under the developer would leave the workspace editor showing
  // one server's files while the next question went to another, which is the
  // exact failure this screen was restructured to make impossible. The mismatch
  // is said out loud instead, with the switch on a button.
  function otherAgent(config) {
    const { agent_base_url, agent_timeout_s, ...rest } = config;
    return rest;
  }

  function noteAgentMismatch(config, what) {
    const url = config?.agent_base_url;
    if (!url || !form || url === form.agent_base_url) return;
    setAgentMismatch({ url, timeout_s: config.agent_timeout_s ?? null, what });
  }

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

  // --- Live updates for every running attempt -------------------------------
  //
  // **One stream for the whole screen, not one per attempt.** Subscribing to the
  // open attempt was the bug: asking a second question moved the selection,
  // which closed the first attempt's stream, and everything it published
  // afterwards went to a topic nobody was listening on. The row stayed grey
  // until it was clicked — clicking it re-subscribed and pulled a snapshot,
  // which is why "click it again" appeared to fix it.
  //
  // Keyed on `subject`, so it is opened once and lives as long as the screen. A
  // different identity has a different set of attempts, which is the only reason
  // to tear it down.
  useEffect(() => {
    const es = api.openPlaygroundProgress();

    // One attempt's worth of event, applied wherever that attempt is. Note what
    // is *not* here: any comparison against `activeId`. Which attempt is open
    // decides what the middle column shows, and nothing else.
    const apply = (d) => {
      if (!d?.attempt_id) return;
      // Kept whether or not the row exists yet, and merged in when it arrives.
      // `attempt_started` genuinely races the POST that creates the row — the
      // backend starts the attempt before it responds — so on a quick server the
      // first event lands while the list still knows nothing about it. Dropping
      // it cost the row its start time, and therefore its timer, for the whole
      // of the one call it was meant to be timing.
      latestEvent.current[d.attempt_id] = d;
      setAttempts((prev) => prev.map((a) => (a.id === d.attempt_id ? merge(a, d) : a)));
      // trace_ready / has_analysis are part of the fingerprint but not of the
      // list row, so they are folded in here, per attempt.
      setLiveById((prev) => ({
        ...prev,
        [d.attempt_id]: {
          trace_ready: d.trace_ready,
          has_analysis: d.has_analysis,
          phase: d.phase,
          verdict: d.verdict,
          status: d.status,
        },
      }));
    };

    const patch = (e) => {
      try {
        apply(JSON.parse(e.data));
      } catch {
        /* a frame we cannot read is one to ignore, not to crash on */
      }
    };

    ["attempt_started", "attempt_answered", "attempt_judged", "attempt_traced",
     "attempt_completed"].forEach((name) => es.addEventListener(name, patch));

    // The snapshot is every attempt at once — what makes a reload, or a
    // reconnect after a blip, recover everything that ran while nobody was
    // listening.
    es.addEventListener("snapshot", (e) => {
      try {
        const d = JSON.parse(e.data);
        // Taken on every (re)connect. The elapsed times below are rendered as
        // `now - started_at` against timestamps this server produced, so a
        // machine whose clock has drifted needs the difference measured before
        // it can show an honest number.
        setServerTime(d.server_time);
        (d.attempts || []).forEach(apply);
      } catch {
        /* see above */
      }
    });

    // The stream dropped events to stay bounded, so what is on screen may be
    // incomplete. Refetching answers the question completely; replaying cannot.
    es.addEventListener("resync", () => reload());

    // Only ever a permanent refusal — a persistent stream retries everything
    // else itself — so there is nothing left to keep open.
    es.onerror = () => es.close();
    return () => es.close();
  }, [subject]);

  const live = liveById[activeId] || null;

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

  // The newest event seen per attempt id, including ones that arrived before the
  // list had a row to put them on. See `apply` and `sendNow`.
  const latestEvent = useRef({});

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

  // Arriving with attempts but nothing selected shows two empty columns beside a
  // list — the same wasted first move the eval detail view had. Open the newest,
  // which is the one the collapsed composer is talking about. Only while nothing
  // is selected, so a live attempt repainting the list cannot steal the view.
  useEffect(() => {
    if (activeId || !attempts.length) return;
    setActiveId(attempts[0].id);
  }, [attempts, activeId]);

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
        const { version } = await api.getWorkspaceVersion(agentOf());
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
      // Merged with anything the stream has already said about it: the backend
      // starts the attempt before it responds to this POST, so `attempt_started`
      // — the event carrying the instant the timer counts from — is often
      // already in hand by the time the row exists to receive it.
      setAttempts((prev) => [merge(created, latestEvent.current[created.id]), ...prev]);
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
      // are already in this session's state anyway. So is the agent, and for a
      // stronger reason — see `otherAgent`.
      if (full.config) {
        setForm((f) => ({ ...f, ...stripBlank(otherAgent(full.config)) }));
        noteAgentMismatch(full.config, "That attempt ran against");
      }
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

  const attemptStatus = active ? (
    <>
      <PhaseSteps attempt={active} />
      {active.workspace_overridden && (
        <span className="hint">
          sent with <strong>{describeOverride(active)}</strong> — the agent's own
          workspace was not changed
        </span>
      )}
    </>
  ) : null;

  const trace = detail?.trace || null;
  const suspectByIndex = {};
  (trace?.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const activeSpanObj = trace?.spans?.find((s) => s.index === activeSpan) || null;

  return (
    <div className="page-fill">
      <PageHeader
        title="Playground"
        // Shown while the screen is still being explained and dropped once it
        // obviously isn't: someone reading a trace has worked out what the
        // playground is, and two lines of description are two lines the trace
        // could have.
        subtitle={
          attempts.length
            ? null
            : "One question against an editable copy of the agent's config and skill files, run as often as you like. Nothing here is saved — attempts last until the backend restarts."
        }
        primary={
          <Button
            variant={shortlistItems.length ? "primary" : "secondary"}
            icon={<IconBookmark size={14} />}
            onClick={() => setShortlistOpen(true)}
            title="Review shortlisted questions and create an eval set from them"
          >
            Shortlist
            {shortlistItems.length > 0 && <Badge tone="neutral" size="sm">{shortlistItems.length}</Badge>}
          </Button>
        }
        menu={
          <IconButton label="Reload the attempt list" icon={<IconRefresh size={16} />} onClick={reload} />
        }
      />

      {error && <div className="error">{error}</div>}

      {form && (
        <AgentConnectionBar
          status={conn}
          baseUrl={form.agent_base_url}
          timeoutS={form.agent_timeout_s}
          version={workspace?.version}
          skillCount={workspace ? Object.keys(workspace.skills || {}).length : null}
          stale={stale?.version || null}
          error={connError}
          recent={recent}
          onConnect={connect}
          onChangeAgent={changeAgent}
          onReload={reloadWorkspace}
        />
      )}

      {/* Said rather than done: something carried in names a different agent.
          Switching is a button because it throws away the snapshot every
          workspace edit is measured against. */}
      {agentMismatch && (
        <div className="hint amber-text composer-alert agent-mismatch">
          <IconAlert size={13} />
          <span>
            {agentMismatch.what} <strong>{agentMismatch.url}</strong>, not the
            agent you are connected to. The question was copied in; the
            connection was left alone.
          </span>
          <button
            className="ui-btn ui-btn-link"
            onClick={() => {
              const target = agentMismatch;
              setAgentMismatch(null);
              changeAgentTo(target);
            }}
          >
            Connect to it
          </button>
          <Button variant="link" onClick={() => setAgentMismatch(null)}>
            Dismiss
          </Button>
        </div>
      )}

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
          connected={conn === "connected" || conn === "fake"}
          workspace={workspace}
          workspaceEdit={wsEdit}
          onWorkspaceEdit={setWsEdit}
          workspaceLoading={wsLoading}
          workspaceError={wsError}
          onReloadWorkspace={reloadWorkspace}
          // Rides in the composer's button row rather than on a row of its own:
          // the composer no longer collapses, so a second row of chrome above
          // the trace is height that has to be justified, and this is not.
          status={active ? attemptStatus : null}
        />
      )}

      <div className={`three playground-three${attemptsCollapsed ? " attempts-collapsed" : ""}`}>
        <AttemptList
          attempts={attempts}
          activeId={activeId}
          collapsed={attemptsCollapsed}
          onToggleCollapsed={setAttemptsCollapsed}
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
              <Button variant="ghost" onClick={() => setStale(null)}>Cancel</Button>
              <button
                onClick={async () => {
                  setStale(null);
                  // The connected agent, explicitly: an argument-less call here
                  // would re-read the environment's agent instead, which is the
                  // whole class of bug this screen was restructured to remove.
                  await loadWorkspace(agentOf()).catch(() => {});
                }}
              >
                Reload workspace
              </button>
              <Button variant="primary" onClick={sendNow}>
                Send anyway
              </Button>
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

// A list row updated by one progress event. `??` throughout, so a field the
// event does not carry keeps what the row already had rather than being blanked.
function merge(row, event) {
  if (!event) return row;
  return {
    ...row,
    phase: event.phase ?? row.phase,
    status: event.status ?? row.status,
    verdict: event.verdict ?? row.verdict,
    error_message: event.error_message ?? row.error_message,
    // Carried on the event, so a finished row no longer costs a refetch just to
    // learn how long the agent took.
    agent_started_at: event.agent_started_at ?? row.agent_started_at,
    agent_latency_ms: event.agent_latency_ms ?? row.agent_latency_ms,
  };
}

// A sparse config overlay merged onto a full config, the same deep merge the
// agent server does with it (the agent-server contract): a key the
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
