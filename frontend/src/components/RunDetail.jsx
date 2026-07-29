import React, { useEffect, useRef, useState } from "react";
import { api, getSubject } from "../api.js";
import QuestionList from "./QuestionList.jsx";
import RunStatusBar from "./RunStatusBar.jsx";
import SpanList from "./SpanList.jsx";
import SpanDetail from "./SpanDetail.jsx";
import { useToast } from "./Toast.jsx";

// Bottom tier (§6.13): three columns. Left = question list (per-mode incorrect),
// middle = trace + diagnosis + caveat, right = span detail. Clicking a question
// auto-selects the top suspect. Diagnosis is read from DB (§6.12).
//
// While a single run is executing this view is also the live one: the question
// list is fully populated from the first second (the orchestrator creates every
// result row up front) and each row is repainted from the run's SSE stream.
export default function RunDetail({ evalSet, runIds, mode, lastN, myRole }) {
  const toast = useToast();
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  const [activeResult, setActiveResult] = useState(null);
  const [trace, setTrace] = useState(null);
  const [activeSpan, setActiveSpan] = useState(null);
  const [reDiagnosing, setReDiagnosing] = useState(false);
  const [runStatus, setRunStatus] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [triggeredBy, setTriggeredBy] = useState(null);
  const subject = getSubject();

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
      .listRuns(evalSet.id)
      .then((runs) => {
        const run = runs.find((r) => r.id === liveRunId);
        if (run) {
          setRunStatus(run.status);
          setTriggeredBy(run.triggered_by);
          setCancelling(Boolean(run.cancel_requested));
        }
      })
      .catch(() => {});
  }, [evalSet.id, liveRunId]);

  // Live repaint. The backend closes the stream immediately for a finished run,
  // so subscribing unconditionally costs nothing for historical runs.
  const resultsRef = useRef(null);
  resultsRef.current = results;
  useEffect(() => {
    if (!liveRunId) return undefined;
    const es = new EventSource(api.progressUrl(evalSet.id, liveRunId));

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
      // The rows are created before the first question runs, but a subscriber
      // that arrives in that window would otherwise sit on a stale empty list.
      if (!resultsRef.current || resultsRef.current.length !== d.total) loadResults();
    });
    es.addEventListener("question_started", patch);
    es.addEventListener("question_answered", patch);
    es.addEventListener("question_done", patch);
    es.addEventListener("run_completed", (e) => {
      let status = "completed";
      try {
        status = JSON.parse(e.data).status || "completed";
      } catch {
        /* a terminal event without a body still ends the run */
      }
      setRunStatus(status);
      es.close();
      // Final reload picks up what the stream doesn't carry: has_analysis and
      // the trace_ready flag settled after diagnosis.
      loadResults();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [evalSet.id, liveRunId]);

  async function pick(r) {
    setActiveResult(r);
    setTrace(null);
    setActiveSpan(null);
    try {
      const t = await api.trace(evalSet.id, r.id);
      setTrace(t);
      // Auto-select the top suspect (§6.13).
      const top = t.analysis?.suspects?.[0]?.span_index;
      setActiveSpan(top !== undefined ? top : t.spans[0]?.index ?? null);
    } catch (e) {
      setError(e.message);
    }
  }

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
      await pick(activeResult); // reload trace+analysis
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
    <div>
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
      <p className="detail-meta">
        {runIds.length} run(s) · incorrect mode: <strong>{mode === "last_n" ? `last-${lastN}` : mode}</strong>
        {runStatus && runStatus !== "running" && runIds.length === 1 && (
          <> · run <strong>{runStatus}</strong></>
        )}
      </p>
      <div className="three">
        {results && (
          <QuestionList
            results={results}
            activeId={activeResult?.id}
            filter={filter}
            setFilter={setFilter}
            onPick={pick}
          />
        )}
        <SpanList
          trace={trace}
          activeSpan={activeSpan}
          onPickSpan={setActiveSpan}
          canReDiagnose={canReDiagnose}
          onReDiagnose={reDiagnose}
          reDiagnosing={reDiagnosing}
          onRetryTrace={() => activeResult && pick(activeResult)}
        />
        <SpanDetail span={activeSpanObj} suspect={activeSpanObj ? suspectByIndex[activeSpanObj.index] : null} />
      </div>
    </div>
  );
}
