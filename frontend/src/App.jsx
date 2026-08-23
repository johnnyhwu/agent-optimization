import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { isKeycloak } from "./app_config.js";
import { getUsername, setUsername } from "./auth.js";
import { href, navigate, useHashRoute } from "./useHashRoute.js";
import EvalSetList from "./components/EvalSetList.jsx";
import RunHistory from "./components/RunHistory.jsx";
import RunDetail from "./components/RunDetail.jsx";
import Playground from "./components/Playground.jsx";
import OptimizeSection from "./components/optimize/OptimizeSection.jsx";
import SettingsSection from "./components/settings/SettingsSection.jsx";
import Breadcrumb from "./components/Breadcrumb.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import SideRail, { useRailCollapsed } from "./components/SideRail.jsx";
import UserMenu from "./components/UserMenu.jsx";
import { ToastProvider } from "./components/Toast.jsx";
import Skeleton from "./components/ui/Skeleton.jsx";

// The whole view state lives in the URL (see useHashRoute): which section, which
// eval set, which runs, which incorrect mode. The three tiers of §6.13 are the
// depth of the evaluation route rather than a separate state machine, so Back
// walks back up them and a run detail is a link you can send to someone.
//
// The playground is a sibling section, not a fourth tier, because it belongs to
// no eval set (§10.5) — and Optimize will join it as a third.
export default function App() {
  const route = useHashRoute();
  const [subject, setSubj] = useState(getUsername());
  const [users, setUsers] = useState([subject]);
  const [collapsed, setCollapsed] = useRailCollapsed();
  // The set named by the route. Held as an object because the run history and
  // the breadcrumb show its name, while the URL can only carry its id.
  const [evalSet, setEvalSet] = useState(null);
  const [setError, setSetError] = useState(null);
  // A question handed over from the three-column view, to prefill the composer.
  // Deliberately not in the URL: it is a one-shot handoff, not a location.
  const [playgroundSeed, setPlaygroundSeed] = useState(null);
  // How many things on the settings page are worth a look: settings added since
  // this developer last opened it, and overrides whose deployment value has
  // since moved. Its own tiny endpoint rather than a field on the defaults
  // payload, because it is asked once per session and the defaults are asked by
  // every page. Zero for anyone who has never opened the page — see
  // `services/user_settings.ensure_row` on why that has to be the answer.
  const [settingsAttention, setSettingsAttention] = useState(0);

  // Only fake mode has a directory to switch between; against Keycloak the
  // endpoint returns an empty list and the switcher is not rendered at all.
  useEffect(() => {
    if (isKeycloak) return;
    api.users().then((r) => setUsers(r.users)).catch(() => {});
  }, []);

  // Re-asked when the identity changes, because in demo mode it can, and the
  // dot belongs to whoever is signed in now. A failure is silent: this is a dot.
  useEffect(() => {
    api
      .userSettingsStatus()
      .then((r) => setSettingsAttention((r.unseen || 0) + (r.drifted || 0)))
      .catch(() => setSettingsAttention(0));
  }, [subject, route.section]);

  // Resolve the route's eval-set id. Opening a set from the list hands the
  // object over directly (see onOpen below), so this only actually fetches when
  // someone arrives by link, reload or Back.
  const esId = route.section === "evaluation" ? route.esId : undefined;
  useEffect(() => {
    if (!esId) {
      setEvalSet(null);
      setSetError(null);
      return undefined;
    }
    if (evalSet && String(evalSet.id) === String(esId)) return undefined;
    let cancelled = false;
    setSetError(null);
    api
      .getEvalSet(esId)
      .then((es) => !cancelled && setEvalSet(es))
      .catch((e) =>
        !cancelled &&
        setSetError(
          e.status === 404
            ? "That eval set no longer exists, or isn't shared with you."
            : e.message
        )
      );
    return () => {
      cancelled = true;
    };
  }, [esId, subject]);

  function switchUser(s) {
    setUsername(s);
    setSubj(s);
    setEvalSet(null);
    navigate(href.evaluation()); // roles change; go home
  }

  // The set is resolved when it matches the route. Rendering the run history
  // against the *previous* set for a frame would fire its requests at the wrong
  // id, so the tiers below wait for this.
  const resolved = evalSet && String(evalSet.id) === String(esId) ? evalSet : null;

  // The role comes from the set that is open, not from a session-wide map.
  //
  // It used to come from `GET /me`, fetched once on mount: a set created *during*
  // the session was therefore absent from that map, `myRole` came back undefined,
  // and the owner-only controls (Edit questions, re-diagnose) stayed hidden on a
  // set the developer had just created and owned. The shortlist hit this every
  // time, because promoting a shortlist navigates straight into the new set.
  // Every payload that can put a set in `evalSet` — the card list and
  // `GET /eval-sets/{id}` — already carries the caller's role for that set, and
  // reading it there means the answer is as fresh as the set itself.
  const myRole = resolved ? resolved.my_role : undefined;

  useDocumentTitle(route, resolved);

  return (
    <ToastProvider>
      <div className="app">
        <SideRail section={route.section} collapsed={collapsed} onToggle={setCollapsed} />

        <div className="main">
          <header className="topbar">
            <div className="topbar-inner">
              <div className="topbar-title">{sectionTitle(route.section)}</div>
              <UserMenu
                subject={subject}
                users={users}
                onSwitchUser={switchUser}
                attention={settingsAttention}
              />
            </div>
          </header>

          {/* Inside the page rather than around the whole shell, so a crash in
              a section leaves the rail, the top bar and the breadcrumb intact —
              the parts that get you out of it. Keyed on the route so navigating
              away from whatever broke clears the error by itself. */}
          <div className="page">
            <ErrorBoundary
              where={sectionTitle(route.section)}
              resetKey={[
                route.section, route.tier, route.esId, route.runId, route.stepNo,
                route.split, route.panel,
              ].join("|")}
            >
            {route.section === "evaluation" && (
              <>
                <Breadcrumb route={route} evalSet={resolved} />
                {setError && (
                  <div className="error">
                    {setError} <a href={href.evaluation()}>Back to eval sets</a>
                  </div>
                )}
                {route.tier === "sets" && (
                  <EvalSetList
                    key={subject}
                    subject={subject}
                    onOpen={(es) => {
                      setEvalSet(es); // already loaded — don't refetch it
                      navigate(href.evalSet(es.id));
                    }}
                  />
                )}
                {route.tier === "runs" &&
                  (resolved ? (
                    <RunHistory
                      evalSet={resolved}
                      myRole={myRole}
                      // The run history can now edit the set (its judging
                      // settings), so the copy held up here has to follow —
                      // otherwise the fingerprint chips keep comparing against
                      // the prompt as it was when the page loaded.
                      onEvalSetChanged={setEvalSet}
                      onOpenRuns={(runIds, mode, lastN) =>
                        navigate(href.runs(resolved.id, runIds, mode, lastN))
                      }
                    />
                  ) : (
                    !setError && <Skeleton variant="row" count={4} />
                  ))}
                {route.tier === "detail" &&
                  (resolved ? (
                    <RunDetail
                      key={`${resolved.id}:${route.runIds.join(",")}:${route.mode}:${route.lastN}`}
                      evalSet={resolved}
                      runIds={route.runIds}
                      mode={route.mode}
                      lastN={route.lastN}
                      myRole={myRole}
                      // Carrying a question over is the whole reason the
                      // playground exists: the hypothesis being tested was
                      // formed while looking at this trace.
                      onSendToPlayground={(seed) => {
                        setPlaygroundSeed(seed);
                        navigate(href.playground());
                      }}
                    />
                  ) : (
                    !setError && <Skeleton variant="row" count={4} />
                  ))}
              </>
            )}

            {route.section === "playground" && (
              <Playground
                subject={subject}
                seed={playgroundSeed}
                onSeedApplied={() => setPlaygroundSeed(null)}
              />
            )}

            {route.section === "optimize" && (
              <OptimizeSection route={route} subject={subject} />
            )}

            {route.section === "settings" && (
              <SettingsSection key={subject} route={route} unseen={settingsAttention} />
            )}
            </ErrorBoundary>
          </div>
        </div>
      </div>
    </ToastProvider>
  );
}

function sectionTitle(section) {
  if (section === "playground") return "Playground";
  if (section === "optimize") return "Optimize";
  if (section === "settings") return "Settings";
  return "Evaluation";
}

// The browser tab said "Agent Eval" no matter where you were, so two tabs open on
// two different eval sets were indistinguishable — and this is a tool people keep
// several tabs of.
function useDocumentTitle(route, evalSet) {
  useEffect(() => {
    const where =
      route.section === "evaluation" && evalSet ? evalSet.name : sectionTitle(route.section);
    document.title = `${where} · Agent Eval`;
  }, [route.section, route.tier, evalSet?.name]);
}

