import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Sparkline from "./Sparkline.jsx";
import UploadDialog from "./UploadDialog.jsx";

// Top tier (§6.13): one card per eval set — run count, latest pass rate, trend
// sparkline, regression summary number.
export default function EvalSetList({ onOpen }) {
  const [sets, setSets] = useState(null);
  const [error, setError] = useState(null);
  const [showUpload, setShowUpload] = useState(false);

  function load() {
    setError(null);
    api.listEvalSets().then(setSets).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Eval Sets</h2>
        <button className="primary" onClick={() => setShowUpload(true)}>+ Upload eval set (JSONL)</button>
      </div>
      {error && <div className="error">{error}</div>}
      {sets === null && <p className="muted">Loading…</p>}
      {sets && sets.length === 0 && <p className="muted">No eval sets yet. Upload one, or run the seed script.</p>}
      <div className="cards">
        {sets &&
          sets.map((s) => (
            <div className="card" key={s.id} onClick={() => onOpen(s)}>
              <h3>{s.name}</h3>
              <div className="meta">
                {new Date(s.created_at).toLocaleDateString()} ·{" "}
                <span className={`rolechip ${s.my_role}`}>{s.my_role}</span>
              </div>
              <div className="stats">
                <div className="stat">
                  <div className="num">{s.run_count}</div>
                  <div className="lbl">runs</div>
                </div>
                <div className="stat">
                  <div className="num">
                    {s.latest_pass_rate === null ? "—" : `${Math.round(s.latest_pass_rate * 100)}%`}
                  </div>
                  <div className="lbl">latest pass</div>
                </div>
                <div className="stat" style={{ marginLeft: "auto" }}>
                  <Sparkline values={s.trend} />
                </div>
              </div>
              {s.regressed > 0 && <div className="regress">⚠ {s.regressed} regressed</div>}
              {s.improved > 0 && <div className="improve">▲ {s.improved} improved</div>}
              {Object.keys(s.metadata || {}).length > 0 && (
                <div className="tags">
                  {Object.entries(s.metadata).map(([k, v]) => (
                    <span className="tag" key={k}>
                      {k}: {String(v)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
      </div>
      {showUpload && (
        <UploadDialog
          onClose={() => setShowUpload(false)}
          onCreated={() => {
            setShowUpload(false);
            load();
          }}
        />
      )}
    </div>
  );
}
