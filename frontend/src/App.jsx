import React, { useEffect, useState } from "react";
import { api, getSubject, setSubject } from "./api.js";
import EvalSetList from "./components/EvalSetList.jsx";
import RunHistory from "./components/RunHistory.jsx";
import RunDetail from "./components/RunDetail.jsx";
import Playground from "./components/Playground.jsx";
import Breadcrumb from "./components/Breadcrumb.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import { ToastProvider } from "./components/Toast.jsx";

const AVATAR_COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];
function avatarColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % AVATAR_COLORS.length;
  return AVATAR_COLORS[h];
}

// In-app view state machine: two tabs, and within the eval-set tab the three
// tiers of §6.13. `view` stays exactly what it was — the playground is a sibling
// of that whole state machine, not a fourth tier, because it belongs to no eval
// set (§10.5).
export default function App() {
  const [subject, setSubj] = useState(getSubject());
  const [users, setUsers] = useState([subject]);
  const [me, setMe] = useState(null);
  const [tab, setTab] = useState("sets");
  const [view, setView] = useState({ tier: "sets" });
  // A question handed over from the three-column view, to prefill the composer.
  const [playgroundSeed, setPlaygroundSeed] = useState(null);

  useEffect(() => {
    api.users().then((r) => setUsers(r.users)).catch(() => {});
  }, []);
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
    <ToastProvider>
      <div className="topbar">
        <div className="brand">
          <div className="logo">AE</div>
          <div>
            <h1>Agent Eval</h1>
            <div className="sub">Trace error-localization · Stage 1 + playground</div>
          </div>
        </div>
        <div className="userbox">
          <ThemeToggle />
          <span className="lbl">Signed in as</span>
          <div className="avatar" style={{ background: avatarColor(subject) }}>
            {subject.slice(0, 1)}
          </div>
          <select value={subject} onChange={(e) => switchUser(e.target.value)} style={{ width: "auto" }}>
            {users.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="tabbar">
        <div className="segmented">
          <button
            className={tab === "sets" ? "active" : ""}
            onClick={() => setTab("sets")}
          >
            Eval Sets
          </button>
          <button
            className={tab === "playground" ? "active" : ""}
            onClick={() => setTab("playground")}
          >
            Playground
          </button>
        </div>
      </div>

      {tab === "sets" && <Breadcrumb view={view} setView={setView} />}

      <div className="container">
        {tab === "playground" && (
          <Playground
            subject={subject}
            seed={playgroundSeed}
            onSeedApplied={() => setPlaygroundSeed(null)}
          />
        )}
        {tab === "sets" && view.tier === "sets" && (
          <EvalSetList key={subject} onOpen={(es) => setView({ tier: "runs", es })} subject={subject} />
        )}
        {tab === "sets" && view.tier === "runs" && (
          <RunHistory
            evalSet={view.es}
            myRole={roleFor(view.es.id)}
            onOpenRuns={(runIds, mode, lastN) => setView({ tier: "detail", es: view.es, runIds, mode, lastN })}
          />
        )}
        {tab === "sets" && view.tier === "detail" && (
          <RunDetail
            evalSet={view.es}
            runIds={view.runIds}
            mode={view.mode}
            lastN={view.lastN}
            myRole={roleFor(view.es.id)}
            // Carrying a question over is the whole reason the playground exists:
            // the hypothesis being tested was formed while looking at this trace.
            onSendToPlayground={(seed) => {
              setPlaygroundSeed(seed);
              setTab("playground");
            }}
          />
        )}
      </div>
    </ToastProvider>
  );
}
