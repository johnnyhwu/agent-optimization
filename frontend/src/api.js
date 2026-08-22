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

// Which agent a playground call is about. Blank is not the same as absent here
// only in spirit — `qs` drops both — but writing it this way keeps the two
// workspace calls agreeing on the parameter names with one edit rather than two.
function agentQuery({ agent_base_url, agent_timeout_s } = {}) {
  return qs({ agent_base_url, agent_timeout_s });
}

// Export params, with `run_ids` repeated rather than joined — the endpoint reads
// it as a list, and a comma-joined value would arrive as one malformed uuid.
function exportQuery({ questions, runs, traces, fmt, runScope, runIds = [], lastN }) {
  const search = new URLSearchParams();
  if (questions !== undefined) search.set("questions", String(Boolean(questions)));
  if (runs !== undefined) search.set("runs", String(Boolean(runs)));
  if (traces !== undefined) search.set("traces", String(Boolean(traces)));
  if (fmt) search.set("fmt", fmt);
  if (runScope) search.set("run_scope", runScope);
  if (lastN) search.set("last_n", String(lastN));
  runIds.forEach((id) => search.append("run_ids", id));
  return `?${search.toString()}`;
}

function filenameFrom(disposition) {
  const match = /filename="([^"]+)"/.exec(disposition || "");
  return match ? match[1] : null;
}

// A download cannot be a plain `<a href>`: an anchor navigation carries no
// headers, so in Keycloak mode it would arrive without the bearer token and
// 401. Fetching it here keeps the identity on the request exactly like every
// other call; the blob is then handed to a synthetic anchor to reach the disk.
//
// Returns the filename the server chose, so the caller can name it in a toast
// rather than saying "done" and leaving the developer to find it. `fallbackName`
// covers the case where Content-Disposition cannot be read — a cross-origin
// deployment whose CORS policy does not expose the header — since saving the
// file as a bare "export" with no extension is worse than guessing well.
async function download(path, fallbackName) {
  const res = await fetch(BASE + path, { headers: { ...(await getAuthHeaders()) } });
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = res.statusText;
    }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  const blob = await res.blob();
  const filename =
    filenameFrom(res.headers.get("Content-Disposition")) || fallbackName || "export";
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking synchronously cancels the save in some browsers, so the URL is
  // released on a later tick instead.
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  return filename;
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

  // The "Run eval" dialog's pre-flight: can we reach this agent, and what skills
  // does it have? Reaching it at all is the connection check — the same call the
  // playground connects with — so there is no separate ping to keep in step.
  //
  // A failure is a 503 carrying the agent server's own words, which is what the
  // dialog shows: "this agent has no skills" and "your URL is wrong" have to
  // stay distinguishable. No timeout parameter: the probe has a short budget of
  // its own on the server, because the Start button waits for it.
  agentSkills: (agentBaseUrl) =>
    req("GET", `/agent/skills${qs({ agent_base_url: agentBaseUrl })}`),
  // Returns a page: { items, total, has_more }.
  listEvalSets: (params = {}) => req("GET", `/eval-sets${qs(params)}`),
  getEvalSet: (id) => req("GET", `/eval-sets/${id}`),
  createEvalSet: (payload) => req("POST", "/eval-sets", payload),
  // Static checks on an uploaded .py: does it parse, is there a top-level
  // main(), does it take the one argument. No database, no execution — which is
  // what lets the dialog run this the moment a file is chosen and hold back the
  // credential prompt until the answer is yes.
  validateScript: (source) => req("POST", "/eval-sets/script/validate", { source }),
  // What this deployment will actually let a script do. Read so the dialog can
  // print the ceilings rather than leaving someone to discover one as an error
  // naming a setting that appears in no document.
  scriptLimits: () => req("GET", "/eval-sets/script/limits"),
  // Run the script against the caller's database and get preview rows back. The
  // connection (password included) is used for this one request and is never
  // stored; a script that fails comes back as 200 with `error` populated, since
  // its traceback and printed output are the point of the call.
  runScript: (source, connection) =>
    req("POST", "/eval-sets/script/run", { source, connection }),
  // A working example of one upload format (python | csv | jsonl).
  downloadTemplate: (kind) =>
    download(`/eval-sets/templates/${kind}`, `example_eval_set.${kind}`),
  // Promote shortlisted playground questions into a new eval set, optionally
  // copying in the questions of sets that already exist (§10.8). A set is locked
  // after creation, so "the old questions plus these" can only be a new set.
  createEvalSetFromShortlist: (payload) =>
    req("POST", "/eval-sets/from-shortlist", payload),
  updateEvalSet: (id, payload) => req("PATCH", `/eval-sets/${id}`, payload),
  deleteEvalSet: (id) => req("DELETE", `/eval-sets/${id}`),
  updateRoles: (id, shares) => req("PUT", `/eval-sets/${id}/roles`, { shares }),
  // Grade one of this set's own questions with a candidate prompt, both ways
  // round. Takes the prompt from the form rather than the database, so edits can
  // be checked before they are saved. Optional model/api_key override the
  // environment's; the key is inbound-only, like every other credential here.
  verifyJudgePrompt: (id, payload) =>
    req("POST", `/eval-sets/${id}/judge-prompt/verify`, payload),
  // Clears the "nobody has checked the grading criteria" badge on a new set.
  markJudgePromptReviewed: (id) =>
    req("POST", `/eval-sets/${id}/judge-prompt/reviewed`),
  metadataKeys: () => req("GET", "/eval-sets/metadata/keys"),
  // Row counts and column names for the download dialog's file preview. Counts
  // depend only on which runs are in scope, so this is refetched when the run
  // selector changes and not when a file is ticked.
  exportPreview: (id, params) =>
    req("GET", `/eval-sets/${id}/export/preview${exportQuery(params)}`),
  downloadExport: (id, params, fallbackName) =>
    download(`/eval-sets/${id}/export${exportQuery(params)}`, fallbackName),
  listQuestions: (id) => req("GET", `/eval-sets/${id}/questions`),
  // Just the tags, with a count each. Its own endpoint rather than a pass over
  // `listQuestions`, which would pull every question's text and ground truth
  // across the wire to read a handful of names.
  evalSetSkills: (id) => req("GET", `/eval-sets/${id}/skills`),
  updateQuestion: (id, qpk, payload) =>
    req("PATCH", `/eval-sets/${id}/questions/${qpk}`, payload),
  // Returns a page: { items, total, has_more }.
  listRuns: (id, params = {}) => req("GET", `/eval-sets/${id}/runs${qs(params)}`),
  getRun: (id, runId) => req("GET", `/eval-sets/${id}/runs/${runId}`),
  // Env-derived prefill for the run-config dialog + which seams are live.
  runConfigDefaults: () => req("GET", "/run-config/defaults"),
  triggerRun: (id, payload) => req("POST", `/eval-sets/${id}/runs`, payload),
  cancelRun: (id, runId) => req("POST", `/eval-sets/${id}/runs/${runId}/cancel`),
  // A run's name, after the fact. It could only be set when the run was
  // triggered, which is before anyone knows what it turned out to be about.
  renameRun: (id, runId, name) =>
    req("PATCH", `/eval-sets/${id}/runs/${runId}`, { name }),
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
  //
  // Both workspace calls name the agent explicitly, because the playground lets
  // the developer pick one. A blank pair still means "the environment's", which
  // is what a single-agent deployment sends.
  //
  // This one is also the playground's connect: it is the call that proves the
  // agent is reachable and speaks the contract, and it hands back the version
  // the staleness check needs. There is no separate ping to keep in step.
  getWorkspace: (agent = {}) =>
    req("GET", `/playground/workspace${agentQuery(agent)}`),
  // Cheap enough to call before every send, which is what turns "your snapshot
  // is stale" into a question asked before the experiment rather than a mystery
  // after it. Asked of the same agent the snapshot came from — a version from
  // anywhere else answers a different question.
  getWorkspaceVersion: (agent = {}) =>
    req("GET", `/playground/workspace/version${agentQuery(agent)}`),
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
  // Every attempt this person has, on one connection. The per-attempt stream
  // above can only follow the attempt that happens to be open, which is how a
  // second question asked while the first was still running left the first one's
  // row unfinished until it was clicked.
  //
  // `persistent` because this one lives as long as the page rather than as long
  // as an attempt: a stream that gave up after a blip would put the screen right
  // back into that stale state, silently and for the rest of the session.
  openPlaygroundProgress: () =>
    openStream("/playground/progress", { persistent: true }),

  // --- Optimize (Stage 3) ---------------------------------------------------
  //
  // The wizard's four calls, then the run's own.
  //
  // `optimizationDefaults` carries the split-size limits as well as the
  // prefills, so the browser enforces the same numbers the create endpoint
  // does. A copy in the bundle would drift, and Start would be enabled on a
  // request that 400s.
  optimizationDefaults: () => req("GET", "/optimization/defaults"),
  importPreview: (evalSetIds) =>
    req("POST", "/optimization/import-preview", { eval_set_ids: evalSetIds }),
  // Proves the skill tag and the agent's directory are the same name before a
  // run exists, rather than at step 0 after a batch of agent calls.
  //
  // The agent travels with the question. The wizard collects a base URL on its
  // first step and starts the run against it; a check that read the server's own
  // environment instead could clear a skill on one agent and hand the run to
  // another, and would look exactly like a check that passed.
  skillCheck: (skillName, agent = {}) =>
    req(
      "GET",
      `/optimization/skill-check${qs({
        skill_name: skillName,
        agent_base_url: agent.agent_base_url,
        agent_timeout_s: agent.agent_timeout_s,
      })}`,
    ),
  createOptimizationRun: (payload) => req("POST", "/optimization/runs", payload),

  listOptimizationRuns: (params = {}) =>
    req("GET", `/optimization/runs${qs(params)}`),
  getOptimizationRun: (runId) => req("GET", `/optimization/runs/${runId}`),
  cancelOptimizationRun: (runId) =>
    req("POST", `/optimization/runs/${runId}/cancel`),
  renameOptimizationRun: (runId, name) =>
    req("PATCH", `/optimization/runs/${runId}`, { name }),
  // Only an interrupted run — one a backend restart caught mid-loop. A
  // cancelled or failed run is a decision or a dead end, not something to
  // continue under the same id.
  resumeOptimizationRun: (runId) =>
    req("POST", `/optimization/runs/${runId}/resume`),
  deleteOptimizationRun: (runId) => req("DELETE", `/optimization/runs/${runId}`),
  openOptimizationProgress: (runId) =>
    openStream(`/optimization/runs/${runId}/progress`),
  // Part 1: one step, one split — the rollout's questions and the analyst calls
  // they fed, in a single payload because the parts mean nothing apart.
  getRolloutDetail: (runId, stepNo, split) =>
    req("GET", `/optimization/runs/${runId}/steps/${stepNo}/rollouts/${split}`),
  // One question's spans, in the same `TraceView` shape the evaluation pages
  // render — which is what lets the span viewer be reused unchanged.
  getRolloutResultTrace: (runId, stepNo, split, resultId) =>
    req(
      "GET",
      `/optimization/runs/${runId}/steps/${stepNo}/rollouts/${split}/results/${resultId}/trace`,
    ),
  // Part 2. `base` is "parent" (the last accepted step, which is usually not
  // the previous one) or "initial".
  getStepSkillDiff: (runId, stepNo, base = "parent") =>
    req("GET", `/optimization/runs/${runId}/steps/${stepNo}/skill${qs({ base })}`),
  // The run's one deliverable. `step` is "best" or a step number — any step is
  // fetchable, and the manifest inside says whether the gate kept it.
  downloadOptimizedSkill: (runId, step = "best", fallbackName) =>
    download(
      `/optimization/runs/${runId}/skill/download${qs({ step })}`,
      fallbackName || "skill.zip",
    ),
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

// `persistent` streams follow a *person* rather than a piece of work, so they
// have no natural end and must never stop trying: the playground's stream is
// open for as long as the tab is, and one that gave up after four failures would
// leave the attempt list frozen for the rest of the session — silently, and
// looking exactly like the bug it was built to fix. Work-shaped streams (a run,
// one attempt) keep the old behaviour, where giving up is honest because the
// thing being watched is finite.
function openStream(path, { persistent = false } = {}) {
  // A **Set** per event name, not a single handler. As a plain Map this silently
  // kept only the last registration, so a component that added two handlers for
  // one event name lost the first — which Playground.jsx did, for
  // `attempt_completed`, and only got away with because a full refetch happened
  // to paper over the missing patch.
  const listeners = new Map();
  const controller = new AbortController();
  let closed = false;
  let timer = null;
  let attempt = 0;

  const stream = {
    addEventListener(name, fn) {
      const fns = listeners.get(name);
      if (fns) fns.add(fn);
      else listeners.set(name, new Set([fn]));
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
    close() {
      closed = true;
      clearTimeout(timer);
      controller.abort();
    },
    onerror: null,
  };

  const emit = (name, data) => {
    // Copied before iterating: a handler is allowed to close the stream or
    // register another, and mutating the set mid-iteration is how that turns
    // into a dropped event.
    const fns = listeners.get(name);
    if (fns) [...fns].forEach((fn) => fn({ data }));
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

      // The SSE grammar allows CRLF, CR or LF as the line terminator, and
      // sse-starlette emits CRLF. Splitting on "\n\n" alone therefore never
      // matches anything: "\r\n\r\n" holds no two consecutive newlines, so the
      // buffer grows forever and not one event is ever dispatched. Normalising
      // first is what makes a single split rule correct for all three forms.
      //
      // A trailing CR is held back rather than normalised, because a CRLF split
      // across two chunks would otherwise turn into "\n\n" here and be read as
      // an end-of-frame that the server never sent.
      const danglingCR = buffer.endsWith("\r");
      let pending = (danglingCR ? buffer.slice(0, -1) : buffer).replace(/\r\n|\r/g, "\n");

      // A chunk boundary can fall anywhere, including the middle of a JSON
      // payload, so frames are reassembled before being parsed.
      let split;
      while ((split = pending.indexOf("\n\n")) !== -1) {
        const frame = pending.slice(0, split);
        pending = pending.slice(split + 2);

        let name = "message";
        const data = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
          // ":" comment lines and "id:"/"retry:" are not used by this protocol.
        }
        if (data.length) emit(name, data.join("\n"));
      }

      // Carry the partial frame over. Re-normalising it next round is a no-op
      // (it holds no CR by construction); the held-back CR is put back so the
      // CRLF it belongs to can be completed by the next chunk.
      buffer = pending + (danglingCR ? "\r" : "");
    }
  }

  // How long before the next attempt, or null to give up. A persistent stream
  // never gives up; it just stops backing off further, so a backend that is down
  // for an hour is retried every 8 seconds rather than abandoned after the first
  // fifteen. `attempt` is reset by a connection that opens, so a healthy
  // reconnect always starts from the short delays again.
  const nextDelay = () => {
    if (attempt < RETRY_DELAYS_MS.length) return RETRY_DELAYS_MS[attempt++];
    return persistent ? RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1] : null;
  };
  const wait = (delay) =>
    new Promise((resolve) => {
      timer = setTimeout(resolve, delay);
    });

  async function run() {
    while (!closed) {
      try {
        await connect();
      } catch (err) {
        if (closed || err.name === "AbortError") return;
        if (err.permanent) return fail(err);
        const delay = nextDelay();
        if (delay === null) return fail(err);
        await wait(delay);
        continue;
      }
      if (closed) return;
      // The stream ended without an error. A finished run is the normal case and
      // its terminal event already fired; the components close on that, so
      // reaching here means the connection dropped mid-run. Reconnect — the
      // backend replays a snapshot to late subscribers, so nothing is lost.
      const delay = nextDelay();
      if (delay === null) return;
      await wait(delay);
    }
  }

  run();
  return stream;
}
