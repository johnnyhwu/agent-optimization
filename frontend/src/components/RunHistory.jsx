import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import RunProgress from "./RunProgress.jsx";
import QuestionEditor from "./QuestionEditor.jsx";

// Middle tier (§6.13): run history for a set; multi-select runs + the 3 incorrect
// modes; trigger new runs (owner or viewer). Also hosts owner question editing.
export default function RunHistory({ evalSet, myRole, onOpenRuns }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState("union");
  const [lastN, setLastN] = useState(2);
  const [activeRun, setActiveRun] = useState(null); // run being watched live
  const [showEditor, setShowEditor] = useState(false);

  function load() {
    setError(null);
    api.listRuns(evalSet.id).then(setRuns).catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  function toggle(id) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  async function trigger() {
    setError(null);
    try {
      const run = await api.triggerRun(evalSet.id);
      setActiveRun(run.id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>{evalSet.name}</h2>
        <div style={{ display: "flex", gap: 8 }}>
          {myRole === "owner" && <button onClick={() => setShowEditor(true)}>Edit questions</button>}
          <button className="primary" onClick={trigger}>▶ Run eval</button>
        </div>
      </div>
      <p className="muted">
        Any role may trigger a run. Owner may edit questions (the set is locked — no add/delete).
      </p>
      {error && <div className="error">{error}</div>}

      {activeRun && (
        <RunProgress evalSetId={evalSet.id} runId={activeRun} onDone={load} />
      )}

      <div className="modes">
        <span className="muted">Incorrect mode:</span>
        <span className="seg" style={{ display: "flex", gap: 6 }}>
          {["union", "intersection", "last_n"].map((m) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
              {m === "last_n" ? "last-N" : m}
            </button>
          ))}
        </span>
        {mode === "last_n" && (
          <>
            <span className="muted">N=</span>
            <input
              type="number"
              min="1"
              value={lastN}
              onChange={(e) => setLastN(Number(e.target.value))}
              style={{ width: 56 }}
            />
          </>
        )}
        <button
          className="primary"
          disabled={selected.length === 0}
          onClick={() => onOpenRuns(selected, mode, lastN)}
          style={{ marginLeft: "auto" }}
        >
          Open detail ({selected.length} selected)
        </button>
      </div>

      {runs === null && <p className="muted">Loading…</p>}
      {runs &&
        runs.map((r) => (
          <div className={`runrow ${selected.includes(r.id) ? "sel" : ""}`} key={r.id}>
            <input type="checkbox" checked={selected.includes(r.id)} onChange={() => toggle(r.id)} />
            <div className="grow">
              <div>{new Date(r.started_at).toLocaleString()}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                by {r.triggered_by}
              </div>
            </div>
            <span className={`pill ${r.status}`}>{r.status}</span>
            <div style={{ width: 90, textAlign: "right" }}>
              {r.pass_rate === null ? "—" : `${Math.round(r.pass_rate * 100)}% pass`}
            </div>
            <div style={{ width: 90, textAlign: "right" }} className="muted">
              {r.incorrect_count ?? 0} wrong
            </div>
            <button onClick={() => onOpenRuns([r.id], "union", 2)}>Open</button>
          </div>
        ))}

      {showEditor && (
        <QuestionEditor evalSet={evalSet} onClose={() => setShowEditor(false)} />
      )}
    </div>
  );
}
