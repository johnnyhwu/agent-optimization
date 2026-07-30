import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import AttemptList from "./AttemptList.jsx";
import PhaseSteps from "./PhaseSteps.jsx";
import PlaygroundComposer from "./PlaygroundComposer.jsx";
import SpanDetail from "./SpanDetail.jsx";
import SpanList from "./SpanList.jsx";
import { useToast } from "./Toast.jsx";
import { IconRefresh } from "./icons.jsx";

// The playground (§10): one question at a time, against one editable skill.
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

const EMPTY_DRAFT = {
  question: "",
  ground_truth_response: "",
  ground_truth_reasoning: "",
  skill_override: null,
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

  // A question handed over from the three-column view (§10.5). The skill comes
  // over as a name with no text, so the picker loads the agent's current version
  // and the edit starts from what the agent actually has.
  useEffect(() => {
    if (!seed) return;
    setDraft({
      question: seed.question || "",
      ground_truth_response: seed.ground_truth_response || "",
      ground_truth_reasoning: seed.ground_truth_reasoning || "",
      skill_override: null,
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
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAttempt({
        question: draft.question,
        ground_truth_response: draft.ground_truth_response || null,
        ground_truth_reasoning: draft.ground_truth_reasoning || null,
        skill_override: draft.skill_override?.content
          ? { name: draft.skill_override.name, content: draft.skill_override.content }
          : null,
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
    // skill text are only on the detail payload, and a clone that silently
    // dropped them would change two variables at once — which is exactly what
    // makes a before/after comparison worthless.
    try {
      const full = await api.getAttempt(a.id);
      setDraft({
        question: full.question,
        ground_truth_response: full.ground_truth_response || "",
        ground_truth_reasoning: full.ground_truth_reasoning || "",
        skill_override: full.skill_name
          ? { name: full.skill_name, content: full.skill_content || "" }
          : null,
      });
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
    <div>
      <div className="page-head">
        <div>
          <h2>Playground</h2>
          <p className="muted">
            One question, one editable skill, run as often as you like. Nothing here
            is saved — attempts live in the backend's memory until it restarts.
          </p>
        </div>
        <button onClick={reload} title="Reload the attempt list">
          <IconRefresh size={14} /> Refresh
        </button>
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
        />
      )}

      {active && (
        <div className="attempt-head">
          <PhaseSteps attempt={active} />
          {active.skill_overridden && (
            <span className="hint">
              sent with an override of <strong>{active.skill_name}</strong>
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
    </div>
  );
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
