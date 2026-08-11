import React, { useEffect, useRef, useState } from "react";
import { api, getSubject } from "../api.js";
import QuestionList from "./QuestionList.jsx";
import RunStatusBar from "./RunStatusBar.jsx";
import SpanList from "./SpanList.jsx";
import SpanDetail from "./SpanDetail.jsx";
import { useToast } from "./Toast.jsx";
import { setServerTime } from "../useElapsed.js";
import Badge from "./ui/Badge.jsx";
import Button from "./ui/Button.jsx";
import { IconSend } from "./icons.jsx";

// Bottom tier (§6.13): three columns. Left = question list (per-mode incorrect),
// middle = trace + diagnosis + caveat, right = span detail. Clicking a question
// auto-selects the top suspect. Diagnosis is read from DB (§6.12).
//
// While a single run is executing this view is also the live one: the question
// list is fully populated from the first second (the orchestrator creates every
// result row up front) and each row is repainted from the run's SSE stream.
//
// All *three* columns follow that stream, not just the left one. The open
// question is held by id and re-read from `results`, and its trace payload is
// refetched whenever the fields that change it move (see `traceKey` below), so
// the agent's answer, the verdict and the diagnosis appear as they happen
// rather than on the next navigation.
// Matches the wording of the compare bar on the run history, so the mode the
// developer chose there is described the same way here.
const INCORRECT_MODE_LABEL = {
  union: "ever failed",
  intersection: "always fails",
  last_n: "newly failing, last",
};

export default function RunDetail({
  evalSet, runIds, mode, lastN, myRole, onSendToPlayground,
}) {
  const toast = useToast();
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  // The *id* of the open question, not a copy of its row. The SSE stream
  // replaces row objects as a question progresses, so holding the object froze
  // the verdict, the re-diagnose affordance and everything else derived from it.
  const [activeResultId, setActiveResultId] = useState(null);
  const [trace, setTrace] = useState(null);
  const [traceRefreshing, setTraceRefreshing] = useState(false);
  // Bumped to force a refetch that the fingerprint below wouldn't catch — a
  // manual Retry, or a re-diagnose that replaces an analysis already present.
  const [traceNonce, setTraceNonce] = useState(0);
  const [activeSpan, setActiveSpan] = useState(null);
  const [reDiagnosing, setReDiagnosing] = useState(false);
  const [runStatus, setRunStatus] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [triggeredBy, setTriggeredBy] = useState(null);
  // The run's own settings, so a question handed to the playground is retried
  // against the endpoints it actually ran against rather than today's defaults.
  const [runConfig, setRunConfig] = useState(null);
  const subject = getSubject();

  const activeResult = results?.find((r) => r.id === activeResultId) || null;

  // A single selected run is the only case that can be live: the multi-run modes
  // compare finished history.
  const liveRunId = runIds.length === 1 ? runIds[0] : null;

  function loadResults() {
    return api
      .results(evalSet.id, runIds, mode, lastN)
      .then(setResults)
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    setError(null);
    loadResults();
  }, [evalSet.id, runIds.join(","), mode, lastN]);

  // Who started this run — needed to decide whether the stop button is offered
  // (a viewer may cancel their own run, §6.16).
  useEffect(() => {
    if (!liveRunId) return;
    api
      .getRun(evalSet.id, liveRunId)
      .then((run) => {
        setRunStatus(run.status);
        setTriggeredBy(run.triggered_by);
        setRunConfig(run.config || null);
        setCancelling(Boolean(run.cancel_requested));
      })
      .catch(() => {});
  }, [evalSet.id, liveRunId]);

  // Live repaint. The backend closes the stream immediately for a finished run,
  // so subscribing unconditionally costs nothing for historical runs.
  const resultsRef = useRef(null);
  resultsRef.current = results;
  useEffect(() => {
    if (!liveRunId) return undefined;
    const es = api.openRunProgress(evalSet.id, liveRunId);

    const patch = (e) => {
      const d = JSON.parse(e.data);
      setResults((prev) =>
        prev
          ? prev.map((r) =>
              r.question_pk === d.question_pk
                ? {
                    ...r,
                    phase: d.phase,
                    status: d.status,
                    verdict: d.verdict,
                    error_message: d.error_message,
                    trace_ready: d.trace_ready,
                    // The left column's timer: counts up from the first, settles
                    // on the second. Carried on every event so a row finishes
                    // itself rather than waiting for the end-of-run reload.
                    started_at: d.started_at ?? r.started_at,
                    agent_latency_ms: d.agent_latency_ms ?? r.agent_latency_ms,
                    // Part of the open question's trace fingerprint, so the
                    // middle column follows the run instead of freezing.
                    has_analysis: d.has_analysis ?? r.has_analysis,
                    trace_error: d.trace_error,
                    diagnosis_error: d.diagnosis_error,
                    is_incorrect: d.verdict === "incorrect" || r.is_incorrect,
                  }
                : r
            )
          : prev
      );
    };

    es.addEventListener("snapshot", (e) => {
      const d = JSON.parse(e.data);
      setRunStatus(d.status);
      // The question list renders elapsed times against timestamps this server
      // produced, so the difference between the two clocks is measured once per
      // connection rather than assumed to be zero.
      setServerTime(d.server_time);
      // The rows are created before the first question runs, but a subscriber
      // that arrives in that window would otherwise sit on a stale empty list.
      if (!resultsRef.current || resultsRef.current.length !== d.total) loadResults();
    });
    es.addEventListener("question_started", patch);
    es.addEventListener("question_answered", patch);
    es.addEventListener("question_judged", patch);
    es.addEventListener("question_traced", patch);
    es.addEventListener("question_done", patch);
    // The stream dropped events to stay bounded, so this view may be missing
    // something — including, in the worst case, `run_completed` itself, which
    // would otherwise leave this waiting on a run that finished long ago.
    // Refetching answers both questions authoritatively.
    es.addEventListener("resync", () => {
      loadResults();
      api
        .getRun(evalSet.id, liveRunId)
        .then((run) => {
          setRunStatus(run.status);
          setCancelling(Boolean(run.cancel_requested));
          if (run.status !== "running") es.close();
        })
        .catch(() => {});
    });
    es.addEventListener("run_completed", (e) => {
      let status = "completed";
      try {
        status = JSON.parse(e.data).status || "completed";
      } catch {
        /* a terminal event without a body still ends the run */
      }
      setRunStatus(status);
      es.close();
      // The stream now carries every per-question field, but a final reload is
      // still the cheap way to reconcile anything a dropped or out-of-order
      // event missed (concurrency > 1 makes ordering non-deterministic).
      loadResults();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [evalSet.id, liveRunId]);

  // Arriving at a run and being shown two empty panels wastes the first move:
  // the reason anyone opens a run is to read a failure, and the view already
  // knows which questions failed. So open one — the first wrong answer if there
  // is one, otherwise the first question. Only ever while nothing is selected,
  // so a live run repainting the list can't yank the developer off the row they
  // are reading.
  useEffect(() => {
    if (activeResultId || !results?.length) return;
    const first = results.find((r) => r.is_incorrect) || results[0];
    if (first) setActiveResultId(first.id);
  }, [results, activeResultId]);

  function pick(r) {
    if (r.id === activeResultId) return;
    setActiveResultId(r.id);
    setTrace(null); // a different question: nothing of the old one still applies
    setActiveSpan(null);
  }

  // Everything the middle and right columns render comes from GET .../trace, so
  // that payload has to be refetched as the open question progresses. This
  // fingerprint is exactly the set of fields that change what comes back —
  // updated in place by the SSE stream, so the refresh is event-driven rather
  // than polled.
  const traceKey = activeResult
    ? [
        activeResult.id,
        activeResult.phase,
        activeResult.verdict,
        activeResult.trace_ready,
        activeResult.has_analysis,
        traceNonce,
      ].join("|")
    : null;

  // Auto-jumping to the top suspect is right when the question changes or a
  // diagnosis first lands, and wrong on every background refresh — it would pull
  // the developer off the span they are reading. This tracks which question the
  // jump has already been spent on.
  const jumpedFor = useRef(null);

  useEffect(() => {
    if (!activeResultId || !traceKey) return undefined;
    let cancelled = false;
    // Keep the previous content mounted while refetching: blanking it made a
    // live question flicker back to the empty state on every event.
    setTraceRefreshing(true);
    api
      .trace(evalSet.id, activeResultId)
      .then((t) => {
        if (cancelled) return;
        setTrace(t);
        const top = t.analysis?.suspects?.[0]?.span_index;
        const target = top !== undefined ? top : t.spans[0]?.index;
        // Jump once per question, and again the moment a diagnosis appears —
        // landing on the top suspect is the point of the diagnosis (§6.13).
        const jumpTo = t.analysis ? `${activeResultId}:analysis` : activeResultId;
        if (target !== undefined && jumpedFor.current !== jumpTo) {
          jumpedFor.current = jumpTo;
          setActiveSpan(target);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setTraceRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [evalSet.id, activeResultId, traceKey]);

  async function cancelRun() {
    setCancelling(true);
    try {
      await api.cancelRun(evalSet.id, liveRunId);
      toast.info("Cancelling run…");
    } catch (e) {
      setCancelling(false);
      toast.error(e.message);
    }
  }

  async function reDiagnose() {
    if (!activeResult) return;
    setReDiagnosing(true);
    try {
      await api.reDiagnose(evalSet.id, activeResult.id);
      // Regenerating over an existing analysis leaves has_analysis unchanged, so
      // the fingerprint alone wouldn't notice the new content.
      jumpedFor.current = null; // a fresh diagnosis earns a fresh jump
      setTraceNonce((n) => n + 1);
      toast.success("Diagnosis regenerated");
    } catch (e) {
      const msg = e.status === 403 ? "Re-diagnose is owner-only." : e.message;
      setError(msg);
      toast.error(msg);
    } finally {
      setReDiagnosing(false);
    }
  }

  const suspectByIndex = {};
  (trace?.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const activeSpanObj = trace?.spans?.find((s) => s.index === activeSpan) || null;
  const running = runStatus === "running";
  // Owner-only (§6.16), and only where a diagnosis is meaningful — the endpoint
  // 400s on anything the judge didn't mark incorrect.
  const canReDiagnose = myRole === "owner" && activeResult?.verdict === "incorrect";

  return (
    <div className="page-fill">
      {error && <div className="error">{error}</div>}
      {results && liveRunId && (
        <RunStatusBar
          results={results}
          running={running}
          cancelling={cancelling}
          onCancel={cancelRun}
          canCancel={myRole === "owner" || triggeredBy === subject}
        />
      )}
      <div className="detail-meta">
        <div className="detail-meta-facts">
          {/* How many runs is the breadcrumb's job; this line says what was done
              with them. */}
          {runIds.length > 1 && (
            <Badge tone="neutral">Wrong = {INCORRECT_MODE_LABEL[mode] || mode}{mode === "last_n" ? ` ${lastN}` : ""}</Badge>
          )}
          {runStatus && runStatus !== "running" && runIds.length === 1 && (
            <Badge tone={runStatus === "completed" ? "success" : "neutral"}>run {runStatus}</Badge>
          )}
          {/* Across several runs the row on screen is a representative one that
              may predate the run being watched; say which, or an old run's trace
              and errors read as the current one's. */}
          {runIds.length > 1 && activeResult?.run_label && (
            <Badge tone="neutral">showing {activeResult.run_label}</Badge>
          )}
        </div>
        {/* The handoff to the playground. Offered on the open question rather
            than per row: the hypothesis worth testing is formed after reading a
            trace, not while scanning the list. */}
        {onSendToPlayground && activeResult && (
          <Button
            size="sm"
            icon={<IconSend size={13} />}
            onClick={() =>
              onSendToPlayground({
                question: activeResult.question,
                ground_truth_response: trace?.ground_truth_response || "",
                ground_truth_reasoning: trace?.ground_truth_reasoning || "",
                config: runConfig,
              })
            }
          >
            Try this in the playground
          </Button>
        )}
      </div>
      <div className="three">
        {results && (
          <QuestionList
            results={results}
            activeId={activeResultId}
            filter={filter}
            setFilter={setFilter}
            onPick={pick}
            // Only a single selected run can be live; the multi-run modes
            // compare finished history, where nothing is still counting.
            runLive={Boolean(liveRunId) && running}
          />
        )}
        <SpanList
          trace={trace}
          // The whole question, which the list on the left cannot show.
          question={activeResult?.question}
          refreshing={traceRefreshing}
          activeSpan={activeSpan}
          onPickSpan={setActiveSpan}
          canReDiagnose={canReDiagnose}
          onReDiagnose={reDiagnose}
          reDiagnosing={reDiagnosing}
          onRetryTrace={() => setTraceNonce((n) => n + 1)}
        />
        <SpanDetail span={activeSpanObj} suspect={activeSpanObj ? suspectByIndex[activeSpanObj.index] : null} />
      </div>
    </div>
  );
}
