// Thin API client. Identity comes from auth.js and rides on every request —
// `X-User-Subject` in fake mode, `Authorization: Bearer` against Keycloak.
//
// Progress streams do NOT use EventSource. See `openStream` at the bottom for
// why: EventSource cannot set headers, and it reconnects by replaying the URL it
// was created with, which with a 60-second access token means retrying forever
// with a dead one.
import { cfg } from "./app_config.js";
import { getAuthHeaders, getUsername } from "./auth.js";

const BASE = cfg.apiBase;

export function apiBase() {
  return BASE;
}
// Kept under the original name: several components read "who am I" to decide
// what to render, and they do not care where it came from.
export { getUsername as getSubject } from "./auth.js";

async function req(method, path, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(await getAuthHeaders()),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = res.statusText;
    }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

// Only appends params the caller actually set, so an omitted filter keeps the
// endpoint's own default rather than pinning it to a value chosen here.
function qs(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  });
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // Kept for the API surface, but the UI does not gate anything on it: a role map
  // fetched once per session goes stale the moment a set is created or shared,
  // and each eval-set payload already carries `my_role` for the caller.
  me: () => req("GET", "/me"),
  users: () => req("GET", "/users"),
  // Resolve a typed username against the employee directory before it is shared
  // with. 404 means the directory denied it; a 200 with verified=false means the
  // directory could not be reached, which is a different answer (see
  // ShareEditor).
  lookupUser: (username) => req("GET", `/users/lookup${qs({ username })}`),
  // Returns a page: { items, total, has_more }.
  listEvalSets: (params = {}) => req("GET", `/eval-sets${qs(params)}`),
  getEvalSet: (id) => req("GET", `/eval-sets/${id}`),
  createEvalSet: (payload) => req("POST", "/eval-sets", payload),
  // Promote shortlisted playground questions into a new eval set, optionally
  // copying in the questions of sets that already exist (§10.8). A set is locked
  // after creation, so "the old questions plus these" can only be a new set.
  createEvalSetFromShortlist: (payload) =>
    req("POST", "/eval-sets/from-shortlist", payload),
  updateEvalSet: (id, payload) => req("PATCH", `/eval-sets/${id}`, payload),
  deleteEvalSet: (id) => req("DELETE", `/eval-sets/${id}`),
  updateRoles: (id, shares) => req("PUT", `/eval-sets/${id}/roles`, { shares }),
  metadataKeys: () => req("GET", "/eval-sets/metadata/keys"),
  listQuestions: (id) => req("GET", `/eval-sets/${id}/questions`),
  updateQuestion: (id, qpk, payload) =>
    req("PATCH", `/eval-sets/${id}/questions/${qpk}`, payload),
  // Returns a page: { items, total, has_more }.
  listRuns: (id, params = {}) => req("GET", `/eval-sets/${id}/runs${qs(params)}`),
  getRun: (id, runId) => req("GET", `/eval-sets/${id}/runs/${runId}`),
  // Env-derived prefill for the run-config dialog + which seams are live.
  runConfigDefaults: () => req("GET", "/run-config/defaults"),
  triggerRun: (id, payload) => req("POST", `/eval-sets/${id}/runs`, payload),
  cancelRun: (id, runId) => req("POST", `/eval-sets/${id}/runs/${runId}/cancel`),
  deleteRun: (id, runId) => req("DELETE", `/eval-sets/${id}/runs/${runId}`),
  results: (id, runIds, mode, lastN) => {
    const qs = new URLSearchParams();
    runIds.forEach((r) => qs.append("run_ids", r));
    qs.set("mode", mode);
    qs.set("last_n", String(lastN));
    return req("GET", `/eval-sets/${id}/results?${qs.toString()}`);
  },
  trace: (id, resultId) => req("GET", `/eval-sets/${id}/results/${resultId}/trace`),
  reDiagnose: (id, resultId) =>
    req("POST", `/eval-sets/${id}/results/${resultId}/re-diagnose`),
  // Live run progress (§6.15). Returns an EventSource-shaped object; see
  // `openStream`.
  openRunProgress: (id, runId) =>
    openStream(`/eval-sets/${id}/runs/${runId}/progress`),

  // --- Playground (§10). Attempts live in the backend's memory, not the DB, so
  // there is nothing to paginate and a backend restart empties the list.
  getWorkspace: () => req("GET", "/playground/workspace"),
  // Cheap enough to call before every send, which is what turns "your snapshot
  // is stale" into a question asked before the experiment rather than a mystery
  // after it.
  getWorkspaceVersion: () => req("GET", "/playground/workspace/version"),
  listAttempts: () => req("GET", "/playground/attempts"),
  createAttempt: (payload) => req("POST", "/playground/attempts", payload),
  getAttempt: (attemptId) => req("GET", `/playground/attempts/${attemptId}`),
  cancelAttempt: (attemptId) => req("POST", `/playground/attempts/${attemptId}/cancel`),
  deleteAttempt: (attemptId) => req("DELETE", `/playground/attempts/${attemptId}`),
  reDiagnoseAttempt: (attemptId) =>
    req("POST", `/playground/attempts/${attemptId}/re-diagnose`),
  // Draft an expected process from an attempt's trace. On a button, never
  // automatic: the draft says what the agent did, and only a person can decide
  // whether that is what should be expected.
  synthesizeReasoning: (attemptId) =>
    req("POST", `/playground/attempts/${attemptId}/synthesize-reasoning`),
  openAttemptProgress: (attemptId) =>
    openStream(`/playground/attempts/${attemptId}/progress`),
};

// --- Server-sent events over fetch ------------------------------------------
//
// A drop-in for the `EventSource` this used to use: same `addEventListener` /
// `close()` / `onerror`, so the three call sites changed by one line each.
//
// It exists because `EventSource` cannot set request headers, which left the
// identity travelling as `?subject=`. That was fine for a fake login and is not
// fine for a bearer token: it would land the token in the proxy's access log,
// and — worse — `EventSource` reconnects by replaying the exact URL it was
// constructed with. With a 60-second access token, the first network blip on a
// twenty-minute run turns into an endless retry loop against an expired token.
// The symptom is "the progress bar sometimes freezes; reloading fixes it", which
// is about as hard to diagnose as bugs get.
//
// So: fetch, with fresh headers on every attempt.
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];

function openStream(path) {
  const listeners = new Map();
  const controller = new AbortController();
  let closed = false;
  let timer = null;
  let attempt = 0;

  const stream = {
    addEventListener(name, fn) {
      listeners.set(name, fn);
    },
    close() {
      closed = true;
      clearTimeout(timer);
      controller.abort();
    },
    onerror: null,
  };

  const emit = (name, data) => {
    const fn = listeners.get(name);
    if (fn) fn({ data });
  };
  const fail = (err) => {
    if (!closed && stream.onerror) stream.onerror(err);
  };

  async function connect() {
    const res = await fetch(BASE + path, {
      headers: { Accept: "text/event-stream", ...(await getAuthHeaders()) },
      signal: controller.signal,
    });
    // A rejected identity will be rejected again next time, so retrying only
    // hides it. Anything else (5xx, a proxy hiccup) is worth another attempt.
    if (res.status === 401 || res.status === 403 || res.status === 404) {
      const err = new Error(`stream refused: ${res.status}`);
      err.status = res.status;
      err.permanent = true;
      throw err;
    }
    if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);

    attempt = 0; // a connection that opened resets the backoff
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) return; // server closed: terminal event already delivered, or a drop
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. The buffer is essential rather
      // than tidy: a chunk boundary can fall anywhere, including the middle of
      // a JSON payload, so frames must be reassembled before being parsed.
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let name = "message";
        const data = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
          // ":" comment lines and "id:"/"retry:" are not used by this protocol.
        }
        if (data.length) emit(name, data.join("\n"));
      }
    }
  }

  async function run() {
    while (!closed) {
      try {
        await connect();
      } catch (err) {
        if (closed || err.name === "AbortError") return;
        if (err.permanent) return fail(err);
        if (attempt >= RETRY_DELAYS_MS.length) return fail(err);
        const delay = RETRY_DELAYS_MS[attempt++];
        await new Promise((resolve) => {
          timer = setTimeout(resolve, delay);
        });
        continue;
      }
      if (closed) return;
      // The stream ended without an error. A finished run is the normal case and
      // its terminal event already fired; the components close on that, so
      // reaching here means the connection dropped mid-run. Reconnect — the
      // backend replays a snapshot to late subscribers, so nothing is lost.
      if (attempt >= RETRY_DELAYS_MS.length) return;
      const delay = RETRY_DELAYS_MS[attempt++];
      await new Promise((resolve) => {
        timer = setTimeout(resolve, delay);
      });
    }
  }

  run();
  return stream;
}
