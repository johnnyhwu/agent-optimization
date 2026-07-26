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

export const api = {
  me: () => req("GET", "/me"),
  listEvalSets: () => req("GET", "/eval-sets"),
  getEvalSet: (id) => req("GET", `/eval-sets/${id}`),
  createEvalSet: (payload) => req("POST", "/eval-sets", payload),
  updateEvalSet: (id, payload) => req("PATCH", `/eval-sets/${id}`, payload),
  metadataKeys: () => req("GET", "/eval-sets/metadata/keys"),
  listQuestions: (id) => req("GET", `/eval-sets/${id}/questions`),
  updateQuestion: (id, qpk, payload) =>
    req("PATCH", `/eval-sets/${id}/questions/${qpk}`, payload),
  listRuns: (id) => req("GET", `/eval-sets/${id}/runs`),
  triggerRun: (id) => req("POST", `/eval-sets/${id}/runs`),
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
};
