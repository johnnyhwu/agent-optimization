# TASK: Build a working POC of Stage 1 only

Read `docs/spec.md` for full design rationale. Implement ONLY what
§6.6–§6.16 and §7.1 define as Stage 1. Explicitly DO NOT build Stage 2
or Stage 3 features (no per-span probability/heatmap, no manual
re-labeling of spans, no SkillOpt, no skill write-back, no annotation
score sync).

## What this POC is
A runnable end-to-end demo with a real React UI and the real app-DB
schema, but every external dependency (A2A agent, LLM judge, LLM
diagnosis, Langfuse trace fetch) is STUBBED behind a fake data layer.
The goal is to prove the UI + data flow + schema, not to integrate
anything real yet.

## Stack (fixed)
- Backend: Python. Use FastAPI (async, SSE-friendly) + SQLAlchemy +
  Alembic migrations. Pydantic for schemas.
- DB: PostgreSQL (via docker-compose, one command to bring up).
- Frontend: React.
- Upload format: implement JSONL only for the POC (avoids CSV quoting
  edge cases; spec keeps CSV as a later addition).

## Hard requirements
- All external/network calls return FAKE data BUT must simulate
  realistic latency. Put every latency value in ONE config file:
    - agent call: 1–3s
    - judge: 0.5–1s
    - trace-ready polling: first 1–2 polls return "not ready", then
      ready (this exercises §6.12 async ingestion handling)
    - diagnosis: 2–4s
- The fake layer must sit behind the SAME interface a real
  implementation would use, so swapping fake→real later is a one-file
  change per integration. Define these seams explicitly (Python
  Protocol/ABC), each with a fake impl clearly commented
  "# REPLACE WITH REAL IMPL":
    - AgentClient.call(question, correlation_id) -> response
    - JudgeClient.judge(response, ground_truth) -> verdict + score + comment
    - TraceClient.fetch_trace(correlation_id) -> trace | NotReady
    - DiagnosisClient.diagnose(trace, ground_truth, judge_verdict) -> diagnosis JSON
- App DB schema EXACTLY per §6.14, as Alembic migrations (not
  in-memory objects — the schema is the point). Tables: eval_sets,
  questions, question_skills, runs, question_results, span_analyses,
  eval_set_roles.
- Implement the optimistic-lock 409 flow (§6.16) for real — it's DB
  logic, not an external dep. version column on questions and
  eval_sets; UPDATE ... WHERE id + version; on miss return HTTP 409.
- Run progress pushed to the frontend live via SSE.

## Stage 1 flows that must work end-to-end
1. Upload eval set (JSONL) → questions created → immutable
   question_id generated per §6.11 → question set LOCKED (UI offers no
   add/delete question; editing a question keeps its question_id and
   bumps version).
2. Trigger a run: orchestrator (§6.15) reads a question snapshot at
   run start, then per question → fake AgentClient (correlation_id in
   metadata) → fake JudgeClient → write question_results (status
   pending/done/failed) → poll fake TraceClient until ready → set
   trace_ready → if incorrect, fake DiagnosisClient → write
   span_analyses (incl. caveat) → push progress. Run tolerates a
   failed question without stalling (partial completion).
3. Three-tier UI (§6.13):
   - Eval-set cards: run count, latest pass rate, trend sparkline,
     regression summary number.
   - Run history for a set: list of runs; multi-select runs with 3
     incorrect modes — union / intersection / last-N.
   - 3-column detail: [left] question list (correct/incorrect color,
     filter to only-wrong) | [middle] vertical span list + top
     overall_diagnosis + caveat banner + suspects marked with
     high/med/low, auto-select top suspect | [right] span detail
     (input/output/token) + that span's reason+evidence, or "not
     flagged".
   - Breadcrumb + one-click back to run / back to set.
4. Diagnosis is generated at run time and stored in DB; re-opening a
   question reads from DB (no re-run). Owner-only manual "re-diagnose".
5. Roles (§6.16): owner = full write; viewer = read + run-eval only,
   no writes, no re-diagnose. Fake the logged-in user via an env/config
   switch so I can flip owner/viewer to test the guards.

## Fake seed data must exercise the interesting cases
Seed script loads a fake eval set + a couple of runs including:
- a run with a mix of correct/incorrect
- a question that REGRESSED (correct in run 1, incorrect in run 2) —
  so the 3 incorrect modes visibly differ
- at least one incorrect question whose fake diagnosis returns a
  CAVEAT (shown prominently, and would be excluded from SkillOpt later)
- a fake trace with one span whose body is long enough to trigger the
  §6.7 single-span body truncation

## Deliverables
- README: single documented command to bring up DB + backend +
  frontend, and a command to run the seed script.
- docker-compose for Postgres.
- Alembic migration implementing §6.14.
- The four *Client seams isolated in one module, each fake impl
  commented "# REPLACE WITH REAL IMPL".
- Latency config in one file.

## Before writing code
Propose (and WAIT for my confirmation):
1. Folder structure (backend + frontend).
2. The Alembic migration for §6.14.
3. The JSONL upload schema (field names per §6.11).
Do not implement until I confirm the plan.

## Out of scope — do not build
Real Langfuse integration, real A2A/agent calls, real LLM calls,
CSV upload, per-span probability/heatmap, span re-labeling, SkillOpt,
skill write-back, multi-tenant isolation, edit-time push/polling
sync (reload-to-refresh is enough for Stage 1).
