# Agent Eval — Stage 1 POC

A runnable end-to-end demo of **Stage 1** from [`docs/spec.md`](docs/spec.md)
(§6.6–§6.16, §7.1): upload an eval set, run an eval through a platform-owned
orchestrator, and for wrong answers show an LLM **clue-style diagnosis** that
jumps the UI straight to the suspect span with its input/output/token detail.

Everything external is **faked** behind a swappable interface — a real A2A agent,
LLM judge, LLM diagnosis, and Langfuse trace fetch are stubbed with realistic
latency. The point of the POC is to prove the **UI + data flow + real app-DB
schema (§6.14)**, not to integrate anything real yet. The app DB schema is the
real thing, created by an Alembic migration.

> **Out of scope (Stage 2/3):** per-span probability/heatmap, manual span
> re-labeling, SkillOpt, skill write-back, annotation score sync,
> real Langfuse/A2A/LLM calls, multi-tenant isolation.

## Stack
- **Backend:** FastAPI (async) + SQLAlchemy + Alembic + Pydantic, SSE for live run
  progress. Containerized; Python deps installed with **uv**.
- **DB:** PostgreSQL (via docker-compose).
- **Frontend:** React (Vite). Containerized; Node deps installed with **pnpm**.
- **Upload:** JSONL or CSV file, parsed in the browser into an editable preview
  table; serialized back to JSONL on submit (the API takes JSONL only).

## Prerequisites
**Docker (with compose)** — and nothing else. Postgres, the backend and the
frontend each run as their own container, so no host Python, venv, Node or
`node_modules` is required. The backend image pins CPython 3.12 (an interpreter
the pinned deps have wheels for), which is why the old "find a Python in
3.10–3.13 / `PYTHON_BIN=…`" dance is gone.

Dependencies are installed inside the images: **uv** for the backend
(`backend/Dockerfile`), **pnpm** for the frontend (`frontend/Dockerfile`).

## Run it (one command)
```bash
SEED=1 ./scripts/dev.sh
```
This builds both images, brings up Postgres, runs the Alembic migration, seeds
fake data, and starts the backend (`:8000`) and frontend (`:5173`) containers.
Open **http://localhost:5173**. Press Ctrl-C to stop backend + frontend;
Postgres stays up (`docker compose down` to stop it).

Drop `SEED=1` to start without (re)seeding. To (re)seed on its own later:
```bash
make seed
```

Both app containers bind-mount their source directory, so editing
`backend/app/**` triggers a uvicorn `--reload` and editing `frontend/src/**`
triggers Vite HMR — no rebuild needed. Rebuild (`make build`) only when
`requirements.txt` / `package.json` change.

### Granular targets (Makefile)
```bash
make db         # docker compose up -d db  (Postgres only)
make build      # build both images (backend deps via uv, frontend via pnpm)
make setup      # alias for make build
make migrate    # alembic upgrade head, in the backend container
make seed       # python -m app.seed, in the backend container
make backend    # backend container: uvicorn app.main:app --reload on :8000
make frontend   # frontend container: vite dev server on :5173
make test       # backend unit tests (no DB or external service needed)
make preflight  # ping whichever integrations are set to real
make down       # docker compose down
```

## Going from fake to real
Out of the box every external dependency is faked, so the demo runs with nothing
but Docker. The four seams of §9.2 each have their own switch, so you can bring
them up **one at a time** — a real agent while the judge is still fake, and so on.

| env var | seam | what `real` means |
|---|---|---|
| `AGENT_IMPL` | `AgentClient` | POST the question to the A2A server (`A2A_BASE_URL`), with the correlation id in request metadata (§6.2) |
| `JUDGE_IMPL` | `JudgeClient` | LLM-as-judge over an OpenAI-compatible endpoint (`LLM_BASE_URL`, `JUDGE_MODEL`) |
| `TRACE_IMPL` | `TraceClient` | read the trace back from Langfuse (`LANGFUSE_HOST` + key pair) |
| `DIAGNOSIS_IMPL` | `DiagnosisClient` | §6.9 clue-style diagnosis over the same LLM endpoint (`DIAGNOSIS_MODEL`) |

Put the settings in a repo-root `.env` (or export them) — `docker-compose.yml`
forwards them into the backend container, and credentials never enter the image.
See [`backend/.env.example`](backend/.env.example) for the full list.

```bash
# minimum for "upload a real eval set, run it, see real results"
AGENT_IMPL=real  A2A_BASE_URL=https://your-a2a-server/rpc
JUDGE_IMPL=real  LLM_BASE_URL=https://your-llm/v1  LLM_API_KEY=...  JUDGE_MODEL=...
```
Then check the wiring before spending a run on it:
```bash
make preflight   # OK / FAIL per seam, with the reason
```

**Prerequisite for the trace seam (§6.2):** the A2A server must read the
`trace_id` key out of the request metadata and use it as its Langfuse trace id.
Without that the platform has no way to find the trace it just caused. The
metadata key is configurable via `A2A_CORRELATION_METADATA_KEY`.

Notes:
- A question that fails (agent unreachable, judge unparseable, timeout) is
  recorded as `failed` **with the reason**, and the run continues and completes —
  it is never left hanging in `running`.
- A diagnosis failure never fails the question; the verdict stands and the owner
  can retry from the UI.
- `RUN_CONCURRENCY` defaults to 1 (strictly sequential). Raise it to run
  questions against the agent in parallel.
- `JUDGE_SCORE_THRESHOLD` is empty by default, meaning the judge's own verdict is
  used. Set a 0–1 number to derive the verdict from its score instead, so the
  pass/fail boundary can be retuned without touching the prompt.

## Trying the flows
- **Fake login switch (§6.16):** top-right dropdown flips between the seeded users
  `alice` (**owner**) and `bob` (**viewer**). As `bob`, the "Edit questions" and
  "Re-diagnose" controls disappear and write APIs return 403; runs are still
  allowed. (Backend default identity is `FAKE_USER_SUBJECT`, default `alice`.)
- **Three tiers (§6.13):** cards → run history → 3-column detail, with a
  breadcrumb for one-click back.
- **Incorrect modes:** in run history, multi-select runs and pick
  **union / intersection / last-N** — the seed data makes all three differ.
- **Diagnosis:** click an incorrect question; the middle column shows the
  clue-style `overall_diagnosis`, a **caveat** banner when present, and suspect
  spans marked high/med/low with the top one auto-selected. The right column
  shows that span's input/output/token and its reason + evidence (or
  "not flagged").
- **Upload:** "+ Upload eval set" — choose a **JSONL or CSV** file (or click
  "load sample"); it is parsed into an **editable preview table** where you can
  fix any cell and add/remove rows before saving. `backend/sample_eval_set.jsonl`
  and `backend/sample_eval_set.csv` are equivalent test files. The set is
  **locked** after creation (edit only, no add/delete). Editing keeps
  `question_id` and bumps `version`; a stale version returns **409**.
- **Live run + partial completion:** "▶ Run eval" streams progress over SSE. A
  question whose text contains the `⟦timeout⟧` marker fails while the run
  finishes (partial completion).

## Where the important pieces live
| Concern | File |
|---|---|
| Container topology (db + backend + frontend) | `docker-compose.yml` |
| Backend image (deps via uv) | `backend/Dockerfile` |
| Frontend image (deps via pnpm) | `frontend/Dockerfile` |
| App DB schema (§6.14), the 7 tables | `backend/alembic/versions/0001_stage1_schema.py` |
| ORM models | `backend/app/models.py` |
| **The four swappable seams** (Protocols) | `backend/app/integrations/base.py` |
| **Fake impls** (each `# REPLACE WITH REAL IMPL`) | `backend/app/integrations/fake.py` |
| **Real impls** (A2A / judge / Langfuse / diagnosis) | `backend/app/integrations/real/` |
| Which impl backs each seam | `backend/app/integrations/__init__.py` |
| Judge + diagnosis prompts (§6.9 contract) | `backend/app/integrations/real/prompts.py` |
| Integration preflight | `backend/app/check_integrations.py` |
| **All latency values, one file** | `backend/app/fake_config.py` |
| Orchestrator (§6.15) | `backend/app/orchestrator.py` |
| Optimistic-lock 409 (§6.16) | `backend/app/routers/eval_sets.py`, `questions.py` |
| Roles / fake login (§6.16) | `backend/app/auth.py` |
| §6.7 body truncation | `backend/app/services/truncation.py` |
| Incorrect modes + regression | `backend/app/services/aggregation.py` |
| SSE hub | `backend/app/sse.py` |
| Seed data | `backend/app/seed.py` |
| Frontend three tiers | `frontend/src/components/` |

## Swapping fake → real
Each integration is a Python `Protocol` in `integrations/base.py`, with a fake
implementation in `integrations/fake.py` and a real one in `integrations/real/`.
`integrations/__init__.py` picks between them per seam from the `*_IMPL` settings
— see [Going from fake to real](#going-from-fake-to-real). Nothing downstream
imports a concrete class, so the orchestrator and routers are untouched by the
choice. Simulated latencies live only in `app/fake_config.py`.

## Upload schema (§6.11)
Both formats carry the same fields. **JSONL** — one JSON object per line:
```jsonl
{"question": "...", "ground_truth_response": "...", "ground_truth_reasoning_process_description": "...", "skill": ["billing"], "question_id": "q_optional"}
```
**CSV** — a header row with the same field names (standard quoting for values
containing commas/newlines). The `skill` cell may be a JSON array literal
(`["billing","reports"]`) or a `,`/`;`/`|`-delimited string (`billing, reports`):
```csv
question,ground_truth_response,ground_truth_reasoning_process_description,skill,question_id
"...","...","...",billing,
```
CSV is parsed in the browser and converted to JSONL before it is sent, so the
API has a single JSONL write path; the format you uploaded is recorded on the
eval set as `source_format`.
| field | required | notes |
|---|---|---|
| `question` | ✅ | |
| `ground_truth_response` | ✅ | |
| `ground_truth_reasoning_process_description` | ✅ | |
| `skill` | ✅ | list of strings |
| `question_id` | optional | system generates an immutable `q_<hex>` if omitted (not a content hash) |
