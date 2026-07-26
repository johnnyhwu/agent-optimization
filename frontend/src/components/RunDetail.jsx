import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import QuestionList from "./QuestionList.jsx";
import SpanList from "./SpanList.jsx";
import SpanDetail from "./SpanDetail.jsx";
import { useToast } from "./Toast.jsx";

// Bottom tier (§6.13): three columns. Left = question list (per-mode incorrect),
// middle = trace + diagnosis + caveat, right = span detail. Clicking a question
// auto-selects the top suspect. Diagnosis is read from DB (§6.12).
export default function RunDetail({ evalSet, runIds, mode, lastN, myRole }) {
  const toast = useToast();
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [onlyWrong, setOnlyWrong] = useState(false);
  const [activeResult, setActiveResult] = useState(null);
  const [trace, setTrace] = useState(null);
  const [activeSpan, setActiveSpan] = useState(null);
  const [reDiagnosing, setReDiagnosing] = useState(false);

  useEffect(() => {
    setError(null);
    api
      .results(evalSet.id, runIds, mode, lastN)
      .then(setResults)
      .catch((e) => setError(e.message));
  }, [evalSet.id, runIds.join(","), mode, lastN]);

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

  return (
    <div>
      {error && <div className="error">{error}</div>}
      <p className="detail-meta">
        {runIds.length} run(s) · incorrect mode: <strong>{mode === "last_n" ? `last-${lastN}` : mode}</strong>
      </p>
      <div className="three">
        {results && (
          <QuestionList
            results={results}
            activeId={activeResult?.id}
            onlyWrong={onlyWrong}
            setOnlyWrong={setOnlyWrong}
            onPick={pick}
          />
        )}
        <SpanList
          trace={trace}
          activeSpan={activeSpan}
          onPickSpan={setActiveSpan}
          canReDiagnose={myRole === "owner" && trace?.analysis}
          onReDiagnose={reDiagnose}
          reDiagnosing={reDiagnosing}
        />
        <SpanDetail span={activeSpanObj} suspect={activeSpanObj ? suspectByIndex[activeSpanObj.index] : null} />
      </div>
    </div>
  );
}
