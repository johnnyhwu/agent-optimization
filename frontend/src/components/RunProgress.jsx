import React, { useEffect, useState } from "react";
import { api } from "../api.js";

// Live run progress via SSE (§6.15). One-way, short-lived; closes on completion.
export default function RunProgress({ evalSetId, runId, label, onDone }) {
  const [done, setDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [correct, setCorrect] = useState(0);
  const [status, setStatus] = useState("running");

  useEffect(() => {
    const es = new EventSource(api.progressUrl(evalSetId, runId));
    const onQ = (e) => {
      const d = JSON.parse(e.data);
      setDone(d.done);
      setTotal(d.total);
      setCorrect(d.correct);
    };
    es.addEventListener("snapshot", (e) => {
      const d = JSON.parse(e.data);
      setTotal(d.total);
      setDone(d.done);
      if (d.correct !== undefined) setCorrect(d.correct);
    });
    es.addEventListener("run_started", (e) => setTotal(JSON.parse(e.data).total));
    es.addEventListener("question_done", onQ);
    es.addEventListener("run_completed", (e) => {
      let s = "completed";
      try {
        s = JSON.parse(e.data).status || "completed";
      } catch {
        /* terminal event without a body still means "stop listening" */
      }
      setStatus(s);
      es.close();
      onDone && onDone();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [evalSetId, runId]);

  const pct = total ? Math.round((done / total) * 100) : 0;
  const heading =
    status === "running"
      ? "Running…"
      : status === "cancelled"
      ? "Run cancelled"
      : status === "failed"
      ? "Run failed"
      : "Run complete";
  return (
    <div className="progress">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span>
          {heading}
          {label && <span className="muted"> · {label}</span>}
        </span>
        <span className="muted">
          {done}/{total} done · {correct} correct
        </span>
      </div>
      <div className="bar">
        <div style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
