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
> re-labeling, SkillOpt, skill write-back, annotation score sync, CSV upload,
> real Langfuse/A2A/LLM calls, multi-tenant isolation.

## Stack
- **Backend:** FastAPI (async) + SQLAlchemy + Alembic + Pydantic, SSE for live run
  progress.
- **DB:** PostgreSQL (via docker-compose).
- **Frontend:** React (Vite).
- **Upload:** JSONL only (Stage 1).

## Prerequisites
Docker (with compose), **Python 3.10–3.13** (3.14 is not yet supported — some
pinned deps have no 3.14 wheels and can't build from source against it), Node 18+.

`scripts/dev.sh` auto-selects a supported Python and rebuilds the venv if an
existing one was created with an unsupported interpreter. To force a specific
interpreter: `PYTHON_BIN=/path/to/python3.12 ./scripts/dev.sh`.

## Run it (one command)
```bash
SEED=1 ./scripts/dev.sh
```
This brings up Postgres, installs backend + frontend deps, runs the Alembic
migration, seeds fake data, and starts the backend (`:8000`) and frontend
(`:5173`). Open **http://localhost:5173**. Press Ctrl-C to stop backend +
frontend; Postgres stays up (`docker compose down` to stop it).

Drop `SEED=1` to start without (re)seeding. To (re)seed on its own later:
```bash
make seed
```

### Granular targets (Makefile)
```bash
make db        # docker compose up -d  (Postgres only)
make setup     # create venv + install backend & frontend deps
make migrate   # alembic upgrade head
make seed      # python -m app.seed
make backend   # uvicorn app.main:app --reload --port 8000
make frontend  # vite dev server on :5173
make down      # docker compose down
```

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
- **Upload:** "+ Upload eval set (JSONL)" — a sample is prefilled;
  `backend/sample_eval_set.jsonl` is another. The set is **locked** after
  creation (edit only, no add/delete). Editing keeps `question_id` and bumps
  `version`; a stale version returns **409**.
- **Live run + partial completion:** "▶ Run eval" streams progress over SSE. A
  question whose text contains the `⟦timeout⟧` marker fails while the run
  finishes (partial completion).

## Where the important pieces live
| Concern | File |
|---|---|
| App DB schema (§6.14), the 7 tables | `backend/alembic/versions/0001_stage1_schema.py` |
| ORM models | `backend/app/models.py` |
| **The four swappable seams** (Protocols) | `backend/app/integrations/base.py` |
| **Fake impls** (each `# REPLACE WITH REAL IMPL`) | `backend/app/integrations/fake.py` |
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
Each integration is a Python `Protocol` in `integrations/base.py` with a fake
implementation in `integrations/fake.py` tagged `# REPLACE WITH REAL IMPL`. Point
the four instances in `integrations/__init__.py` at real implementations of the
same interface — nothing else changes. Simulated latencies live only in
`app/fake_config.py`.

## JSONL upload schema (§6.11)
One JSON object per line:
```jsonl
{"question": "...", "ground_truth_response": "...", "ground_truth_reasoning_process_description": "...", "skill": ["billing"], "question_id": "q_optional"}
```
| field | required | notes |
|---|---|---|
| `question` | ✅ | |
| `ground_truth_response` | ✅ | |
| `ground_truth_reasoning_process_description` | ✅ | |
| `skill` | ✅ | list of strings |
| `question_id` | optional | system generates an immutable `q_<hex>` if omitted (not a content hash) |
