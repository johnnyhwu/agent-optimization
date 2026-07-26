import React, { useEffect, useState } from "react";
import { api, getSubject, setSubject } from "./api.js";
import EvalSetList from "./components/EvalSetList.jsx";
import RunHistory from "./components/RunHistory.jsx";
import RunDetail from "./components/RunDetail.jsx";
import Breadcrumb from "./components/Breadcrumb.jsx";

// Simple in-app view state machine for the three tiers (§6.13).
export default function App() {
  const [subject, setSubj] = useState(getSubject());
  const [me, setMe] = useState(null);
  const [view, setView] = useState({ tier: "sets" }); // sets | runs | detail

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, [subject]);

  function switchUser(s) {
    setSubject(s);
    setSubj(s);
    setView({ tier: "sets" }); // roles change; go home
  }

  const roleFor = (esId) => (me && me.roles ? me.roles[esId] : undefined);

  return (
    <div>
      <div className="topbar">
        <h1>Agent Eval — Stage 1 POC</h1>
        <div className="userbox">
          <span>logged in as</span>
          <select value={subject} onChange={(e) => switchUser(e.target.value)}>
            <option value="alice">alice</option>
            <option value="bob">bob</option>
          </select>
          <span className="muted">(flip to test owner/viewer guards)</span>
        </div>
      </div>

      <Breadcrumb view={view} setView={setView} />

      <div className="container">
        {view.tier === "sets" && (
          <EvalSetList
            onOpen={(es) => setView({ tier: "runs", es })}
          />
        )}
        {view.tier === "runs" && (
          <RunHistory
            evalSet={view.es}
            myRole={roleFor(view.es.id)}
            onOpenRuns={(runIds, mode, lastN) =>
              setView({ tier: "detail", es: view.es, runIds, mode, lastN })
            }
          />
        )}
        {view.tier === "detail" && (
          <RunDetail
            evalSet={view.es}
            runIds={view.runIds}
            mode={view.mode}
            lastN={view.lastN}
            myRole={roleFor(view.es.id)}
          />
        )}
      </div>
    </div>
  );
}
