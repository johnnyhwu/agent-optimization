// Thin API client. The fake-login subject (§6.16) is sent as X-User-Subject on
// every request; the SSE stream gets it as ?subject= since EventSource can't set
// headers.
const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

let subject = localStorage.getItem("subject") || "alice";

export function getSubject() {
  return subject;
}
export function setSubject(s) {
  subject = s;
  localStorage.setItem("subject", s);
}
export function apiBase() {
  return BASE;
}

async function req(method, path, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-User-Subject": subject,
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
  me: () => req("GET", "/me"),
  users: () => req("GET", "/users"),
  // Returns a page: { items, total, has_more }.
  listEvalSets: (params = {}) => req("GET", `/eval-sets${qs(params)}`),
  getEvalSet: (id) => req("GET", `/eval-sets/${id}`),
  createEvalSet: (payload) => req("POST", "/eval-sets", payload),
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
  progressUrl: (id, runId) =>
    `${BASE}/eval-sets/${id}/runs/${runId}/progress?subject=${encodeURIComponent(subject)}`,

  // --- Playground (§10). Attempts live in the backend's memory, not the DB, so
  // there is nothing to paginate and a backend restart empties the list.
  listSkills: () => req("GET", "/playground/skills"),
  getSkill: (name) => req("GET", `/playground/skills/${encodeURIComponent(name)}`),
  listAttempts: () => req("GET", "/playground/attempts"),
  createAttempt: (payload) => req("POST", "/playground/attempts", payload),
  getAttempt: (attemptId) => req("GET", `/playground/attempts/${attemptId}`),
  cancelAttempt: (attemptId) => req("POST", `/playground/attempts/${attemptId}/cancel`),
  deleteAttempt: (attemptId) => req("DELETE", `/playground/attempts/${attemptId}`),
  reDiagnoseAttempt: (attemptId) =>
    req("POST", `/playground/attempts/${attemptId}/re-diagnose`),
  attemptProgressUrl: (attemptId) =>
    `${BASE}/playground/attempts/${attemptId}/progress?subject=${encodeURIComponent(subject)}`,
};
