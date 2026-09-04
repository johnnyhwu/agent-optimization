import { useEffect, useState } from "react";

// The app's URL. Hash-based rather than history-based because the frontend is
// served as a static bundle with no server-side rewrite — a deep path like
// /evaluation/3 would 404 on reload, a deep hash never does.
//
// Routes:
//   #/evaluation                                    the eval-set list
//   #/evaluation/{esId}                             one set's run history
//   #/evaluation/{esId}/runs/{id,id}?mode=&n=       the three-column detail
//   #/playground
//   #/optimize
//   #/settings/{panel}                              personal defaults
//
// Everything the detail view needs is in the URL, so "look at this failing run"
// is a link you can paste to someone, and Back walks back up the tiers.

export const DEFAULT_ROUTE = "#/evaluation";

// Route → hash. The single place that knows the shape, so callers build links by
// intent rather than by string concatenation.
export const href = {
  evaluation: () => "#/evaluation",
  evalSet: (esId) => `#/evaluation/${esId}`,
  runs: (esId, runIds, mode, lastN) => {
    const q = new URLSearchParams();
    if (mode && mode !== "union") q.set("mode", mode);
    if (mode === "last_n") q.set("n", String(lastN));
    const s = q.toString();
    return `#/evaluation/${esId}/runs/${runIds.join(",")}${s ? `?${s}` : ""}`;
  },
  playground: () => "#/playground",
  optimize: () => "#/optimize",
  // The doc, and optionally the section of it being asked about. Callers pass
  // an anchor because the "?" beside a field is a specific question — landing
  // on a table of contents makes someone find the answer twice.
  docs: (doc = "agent-server", anchor = "") =>
    `#/documentation/${doc}${anchor ? `#${anchor}` : ""}`,
  // Reached from the user menu rather than the side rail: it is about the person
  // signed in, not about a fourth thing the product does.
  settings: (panel = "defaults") => `#/settings/${panel}`,
  optimizeNew: () => "#/optimize/new",
  optimizeRun: (runId) => `#/optimize/${runId}`,
  optimizeRollout: (runId, stepNo, split) =>
    `#/optimize/${runId}/steps/${stepNo}/${split}`,
  optimizeSkill: (runId, stepNo) => `#/optimize/${runId}/steps/${stepNo}/skill`,
};

export function navigate(to) {
  if (window.location.hash === to) return;
  window.location.hash = to;
}

// Replace rather than push, for corrections the user never asked for (an
// unparseable hash, a set that no longer exists). Those must not become Back
// targets, or Back would bounce off them forever.
export function replace(to) {
  window.location.replace(`${window.location.pathname}${window.location.search}${to}`);
}

export function parseHash(hash) {
  const raw = (hash || "").replace(/^#\/?/, "");
  const [path, search] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const q = new URLSearchParams(search || "");

  if (parts[0] === "playground") return { section: "playground" };
  // Before the evaluation fallthrough below, which swallows anything it does
  // not recognise — an unlisted section here is not a 404, it is the home page
  // opening instead, which reads as a broken link with no error.
  if (parts[0] === "documentation") {
    // The anchor rides inside the hash route, so it arrives glued to the doc
    // name: `#/documentation/agent-server#chat-endpoint`. The browser will not
    // scroll to it — there is only ever one `#` as far as it is concerned — so
    // it is handed to the page to scroll to itself.
    const [doc, anchor = ""] = (parts[1] || "agent-server").split("#");
    return { section: "documentation", doc: doc || "agent-server", anchor };
  }
  // The settings page's own left column is a route rather than component state,
  // for the same reason every other tier here is one: it survives a reload and
  // it is a link somebody can send.
  if (parts[0] === "settings") return { section: "settings", panel: parts[1] || "defaults" };
  if (parts[0] === "optimize") {
    // `new` is a reserved id rather than a query flag: the wizard is a whole
    // page, and a page deserves an address someone can link to.
    if (parts[1] === "new") return { section: "optimize", tier: "new" };
    // #/optimize/{runId}/steps/{n}/{split} — one rollout in detail. Deep, but
    // it is a page a developer sends to a colleague ("look at what the analyst
    // was shown here"), so every part of it belongs in the address.
    if (parts[1] && parts[2] === "steps" && parts[3] != null) {
      // `skill` occupies the same slot as the split, so it has to be taken
      // first: the split falls back to `train`, and without this a link to the
      // diff would quietly open the training rollout instead — a real page,
      // showing real numbers, that is not the one that was asked for.
      if (parts[4] === "skill") {
        return {
          section: "optimize",
          tier: "skill",
          runId: parts[1],
          stepNo: Number(parts[3]),
        };
      }
      return {
        section: "optimize",
        tier: "rollout",
        runId: parts[1],
        stepNo: Number(parts[3]),
        split: parts[4] === "val" ? "val" : "train",
      };
    }
    if (parts[1]) return { section: "optimize", tier: "run", runId: parts[1] };
    return { section: "optimize", tier: "runs" };
  }

  // Everything else is the evaluation section, including an empty hash — it is
  // the app's home.
  if (parts.length >= 3 && parts[1] && parts[2] === "runs" && parts[3]) {
    const runIds = parts[3].split(",").filter(Boolean);
    if (runIds.length) {
      const mode = ["union", "intersection", "last_n"].includes(q.get("mode"))
        ? q.get("mode")
        : "union";
      const n = Number(q.get("n"));
      return {
        section: "evaluation",
        tier: "detail",
        esId: parts[1],
        runIds,
        mode,
        lastN: Number.isFinite(n) && n > 0 ? n : 2,
      };
    }
  }
  if (parts.length >= 2 && parts[0] === "evaluation" && parts[1]) {
    return { section: "evaluation", tier: "runs", esId: parts[1] };
  }
  return { section: "evaluation", tier: "sets" };
}

export function useHashRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  // Anything unparseable renders the home section, so the address bar is
  // corrected to say so — a stale `#/nonsense` above the eval-set list is a URL
  // that lies about where you are, and it would be copied and shared as one.
  useEffect(() => {
    if (route.section === "evaluation" && route.tier === "sets" && window.location.hash !== DEFAULT_ROUTE) {
      replace(DEFAULT_ROUTE);
    }
  }, [route]);

  return route;
}
