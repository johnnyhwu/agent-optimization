import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import RunProgress from "./RunProgress.jsx";
import QuestionEditor from "./QuestionEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlay, IconGear } from "./icons.jsx";

const MODES = [
  ["union", "Union"],
  ["intersection", "Intersection"],
  ["last_n", "Last-N"],
];

// Middle tier (§6.13): run history for a set; multi-select runs + the 3 incorrect
// modes; trigger new runs (owner or viewer). Owner can edit questions.
export default function RunHistory({ evalSet, myRole, onOpenRuns }) {
  const toast = useToast();
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState("union");
  const [lastN, setLastN] = useState(2);
  const [activeRun, setActiveRun] = useState(null);
  const [showEditor, setShowEditor] = useState(false);

  function load() {
    setError(null);
    api.listRuns(evalSet.id).then(setRuns).catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  const toggle = (id) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  async function trigger() {
    setError(null);
    try {
      const run = await api.triggerRun(evalSet.id);
      setActiveRun(run.id);
      toast.info("Run started");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>{evalSet.name}</h2>
          <p className="muted" style={{ margin: "2px 0 0" }}>
            Any role may run an eval. Owner may edit questions (set is locked — no add/delete).
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {myRole === "owner" && (
            <button onClick={() => setShowEditor(true)}><IconGear size={15} /> Edit questions</button>
          )}
          <button className="primary" onClick={trigger}><IconPlay size={14} /> Run eval</button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}

      {activeRun && <RunProgress evalSetId={evalSet.id} runId={activeRun} onDone={load} />}

      <div className="toolbar">
        <span className="muted">Incorrect mode</span>
        <div className="segmented">
          {MODES.map(([m, label]) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>{label}</button>
          ))}
        </div>
        {mode === "last_n" && (
          <>
            <span className="muted">N =</span>
            <input type="number" min="1" value={lastN} onChange={(e) => setLastN(Number(e.target.value))} style={{ width: 64 }} />
          </>
        )}
        <button
          className="primary"
          disabled={selected.length === 0}
          onClick={() => onOpenRuns(selected, mode, lastN)}
          style={{ marginLeft: "auto" }}
        >
          Open detail ({selected.length})
        </button>
      </div>

      {runs === null && <p className="muted">Loading…</p>}
      {runs && runs.length === 0 && <div className="empty">No runs yet — hit “Run eval”.</div>}
      {runs &&
        runs.map((r, i) => (
          <div className={`runrow ${selected.includes(r.id) ? "sel" : ""}`} key={r.id} style={{ animationDelay: `${i * 30}ms` }}>
            <input type="checkbox" checked={selected.includes(r.id)} onChange={() => toggle(r.id)} style={{ width: "auto" }} />
            <div className="grow">
              <div style={{ fontWeight: 600 }}>{new Date(r.started_at).toLocaleString()}</div>
              <div className="muted" style={{ fontSize: 12 }}>by {r.triggered_by}</div>
            </div>
            <span className={`pill ${r.status}`}>{r.status}</span>
            <div style={{ width: 96, textAlign: "right", fontWeight: 600 }}>
              {r.pass_rate === null ? "—" : `${Math.round(r.pass_rate * 100)}% pass`}
            </div>
            <div style={{ width: 80, textAlign: "right" }} className="muted">{r.incorrect_count ?? 0} wrong</div>
            <button onClick={() => onOpenRuns([r.id], "union", 2)}>Open</button>
          </div>
        ))}

      {showEditor && <QuestionEditor evalSet={evalSet} onClose={() => setShowEditor(false)} />}
    </div>
  );
}
