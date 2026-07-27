import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Sparkline from "./Sparkline.jsx";
import UploadDialog from "./UploadDialog.jsx";
import ConfigDialog from "./ConfigDialog.jsx";
import { IconGear, IconUpload, IconUsers } from "./icons.jsx";

// Top tier (§6.13): one card per eval set — run count, latest pass rate, trend
// sparkline, regression summary. Owners get a config gear to edit the card.
export default function EvalSetList({ onOpen, subject }) {
  const [sets, setSets] = useState(null);
  const [error, setError] = useState(null);
  const [showUpload, setShowUpload] = useState(false);
  const [configSet, setConfigSet] = useState(null);

  function load() {
    setError(null);
    api.listEvalSets().then(setSets).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  return (
    <div>
      <div className="page-head">
        <h2>Eval Sets</h2>
        <button className="primary" onClick={() => setShowUpload(true)}>
          <IconUpload size={15} /> Upload eval set
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {sets === null && (
        <div className="cards">
          {[0, 1, 2].map((i) => <div className="skeleton" key={i} />)}
        </div>
      )}
      {sets && sets.length === 0 && (
        <div className="empty">No eval sets yet. Upload one, or run the seed script.</div>
      )}
      <div className="cards">
        {sets &&
          sets.map((s, i) => {
            const shared = (s.roles || []).length;
            return (
              <div className="card" key={s.id} style={{ animationDelay: `${i * 50}ms` }} onClick={() => onOpen(s)}>
                {s.my_role === "owner" && (
                  <button
                    className="icon-btn cfg-btn"
                    aria-label="Configure"
                    title="Configure"
                    onClick={(e) => { e.stopPropagation(); setConfigSet(s); }}
                  >
                    <IconGear size={16} />
                  </button>
                )}
                <h3>{s.name}</h3>
                <div className="meta">
                  {new Date(s.created_at).toLocaleDateString()}
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
                  <div className="spark-wrap"><Sparkline values={s.trend} /></div>
                </div>
                {(s.regressed > 0 || s.improved > 0) && (
                  <div className="badges">
                    {s.regressed > 0 && <span className="badge reg">⚠ {s.regressed} regressed</span>}
                    {s.improved > 0 && <span className="badge imp">▲ {s.improved} improved</span>}
                  </div>
                )}
                <div className="tags">
                  {Object.entries(s.metadata || {}).map(([k, v]) => (
                    <span className="tag" key={k}>{k}: {String(v)}</span>
                  ))}
                  {shared > 1 && (
                    <span className="tag people"><IconUsers size={11} /> {shared} members</span>
                  )}
                </div>
              </div>
            );
          })}
      </div>

      {showUpload && (
        <UploadDialog
          subject={subject}
          onClose={() => setShowUpload(false)}
          onCreated={() => { setShowUpload(false); load(); }}
        />
      )}
      {configSet && (
        <ConfigDialog
          evalSet={configSet}
          subject={subject}
          onClose={() => setConfigSet(null)}
          onSaved={() => { setConfigSet(null); load(); }}
        />
      )}
    </div>
  );
}
