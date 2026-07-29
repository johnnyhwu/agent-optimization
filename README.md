# Agent Eval — Stage 1 POC

A runnable end-to-end demo of **Stage 1** from [`docs/spec.md`](docs/spec.md)
(§6.6–§6.16, §7.1): upload an eval set, run an eval through a platform-owned
orchestrator, and for wrong answers show an LLM **clue-style diagnosis** that
jumps the UI straight to the suspect span with its input/output/token detail.

Every external dependency sits behind a swappable interface with **two
implementations**: a fake one with realistic latency, and a real one (HTTP agent
server, LLM judge, LLM diagnosis, Langfuse trace fetch). All four default to
fake, so the demo runs on nothing but Docker; each can be switched to real
independently — see [Going from fake to real](#going-from-fake-to-real). The app
DB schema is the real thing, created by Alembic migrations.

> **Out of scope (Stage 2/3):** per-span probability/heatmap, manual span
> re-labeling, SkillOpt, skill write-back, annotation score sync,
> multi-tenant isolation. Writing back to Langfuse (verdicts as Scores, §6.3) is
> also not done — the trace seam reads only.

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
| `AGENT_IMPL` | `AgentClient` | POST `{"message", "metadata"}` to the agent server's `/execute` (`AGENT_BASE_URL`), with the correlation id, run trigger, and eval set tag in `metadata.trace_data` (§6.2) |
| `JUDGE_IMPL` | `JudgeClient` | LLM-as-judge over an OpenAI-compatible endpoint (`LLM_BASE_URL`, `JUDGE_MODEL`) |
| `TRACE_IMPL` | `TraceClient` | read the trace back from Langfuse (`LANGFUSE_HOST` + key pair) |
| `DIAGNOSIS_IMPL` | `DiagnosisClient` | §6.9 clue-style diagnosis over the same LLM endpoint (`DIAGNOSIS_MODEL`) |

Put the settings in a repo-root `.env` (or export them) — `docker-compose.yml`
forwards them into the backend container, and credentials never enter the image.
See [`backend/.env.example`](backend/.env.example) for the full list.

The `*_IMPL` switches are the master switch, but the connection settings are only
**defaults**. "Run eval" opens a config dialog prefilled from them, where each run
gets its own name, agent base URL and timeout, Langfuse host/keys/timeout, LLM
endpoint and models, and concurrency (how many questions go to the agent at once).
Each run stores what it was triggered with, so two runs can target different agent
servers, and viewing a run's trace later uses the endpoints *that* run used. Blank
fields fall back to the environment, so the fake demo still runs from an empty form.

A blank field is resolved to the environment's value **when the run is triggered**,
not left blank, so each run records a complete picture of what it used rather than
a set of deltas against an environment that may since have changed. Every run row
has a button opening that config, read-only — a finished run's settings are history.

Credentials are write-only: `runs.secrets` is never serialized into a response
(`list_runs` is open to viewers too). To avoid retyping them, pick an earlier run
under "Use config from" — the backend copies that run's keys server-side, and only
while the endpoint they authenticate against is unchanged.

```bash
# minimum for "upload a real eval set, run it, see real results"
AGENT_IMPL=real  AGENT_BASE_URL=https://your-agent-server
JUDGE_IMPL=real  LLM_BASE_URL=https://your-llm/v1  LLM_API_KEY=...  JUDGE_MODEL=...
```
Then check the wiring before spending a run on it:
```bash
make preflight   # OK / FAIL per seam, with the reason
```

**Prerequisite for the trace seam (§6.2):** the agent server must read
`metadata.trace_data.trace_id` out of the `/execute` request body and use it
as its Langfuse trace id. Without that the platform has no way to find the
trace it just caused. The full metadata shape sent on every call is:
```json
{"trace_data": {"trace_id": "...", "session_id": "...", "user_id": "...", "tags": ["eval_<eval_set_name>"]}}
```
`trace_id` and `session_id` are the same value (each question is its own
correlation unit); `user_id` is the subject who triggered the run.

Notes:
- A question that fails (agent unreachable, judge unparseable, timeout) is
  recorded as `failed` **with the reason**, and the run continues and completes —
  it is never left hanging in `running`.
- A diagnosis failure never fails the question; the verdict stands, the reason is
  recorded on the question, and the owner can retry from the UI.
- **Integration failures are shown in the UI, not only in the backend log.** A
  Langfuse host that is unreachable or rejects its key shows as a red banner on
  the trace with the host, the status code and the server's own message — the
  "trace is generating" banner now means only what it says. Likewise a failed
  judge call appears on the question, and a failed diagnosis in the middle column.
- `RUN_CONCURRENCY` defaults to 1 (strictly sequential) and is the default for
  the dialog's **Concurrency** field, which sets it per run.
- `AGENT_TIMEOUT_S` and `LANGFUSE_TIMEOUT_S` are settable per run; `LLM_TIMEOUT_S`
  is not — it stays process-wide.
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
- **Live run + partial completion:** "▶ Run eval" opens the config dialog
  (prefilled from the environment; seams still set to `fake` are greyed out) and
  then drops you straight into the run's detail view. Every question is listed
  from the first second — grey while it waits, plain once the agent has answered,
  green/red once judged — with a percentage bar above the columns. A question
  whose text contains the `⟦timeout⟧` marker fails while the run finishes
  (partial completion).
- **Stopping a run:** "Stop run" in the detail view, or the stop button on a
  running row in the run history. It abandons the in-flight agent call rather
  than waiting for it, keeps every question already judged, and leaves the rest
  `pending`; the run ends as `cancelled` with no pass rate (a partial run's
  pass rate would distort the eval set's trend). An owner may stop any run;
  a viewer may stop the ones they started.
- **Deleting:** owners get a trash button on each eval-set card and on each
  finished run, both behind a confirmation that spells out what else goes. A run
  still executing offers stop instead — cancel it first.
- **Per-run config:** each run row has a button showing, read-only, the settings
  that run used. Click a row anywhere to open the run itself; the checkbox and
  the config button keep their own clicks.

## Where the important pieces live
| Concern | File |
|---|---|
| Container topology (db + backend + frontend) | `docker-compose.yml` |
| Backend image (deps via uv) | `backend/Dockerfile` |
| Frontend image (deps via pnpm) | `frontend/Dockerfile` |
| App DB schema (§6.14), the 7 tables | `backend/alembic/versions/0001_stage1_schema.py` |
| Columns the real integrations need | `backend/alembic/versions/0002_real_integration.py` |
| Per-run config columns (`name`/`config`/`secrets`) | `backend/alembic/versions/0003_run_config.py` |
| Cancellation flag + the two error columns | `backend/alembic/versions/0004_run_lifecycle.py` |
| ORM models | `backend/app/models.py` |
| **The four swappable seams** (Protocols) | `backend/app/integrations/base.py` |
| **Fake impls** (each `# REPLACE WITH REAL IMPL`) | `backend/app/integrations/fake.py` |
| **Real impls** (agent / judge / Langfuse / diagnosis) | `backend/app/integrations/real/` |
| Which impl backs each seam + per-run clients (`build_seams`) | `backend/app/integrations/__init__.py` |
| Run-config defaults + trigger-time resolution | `backend/app/services/run_config.py` |
| Run-config dialog / read-only view | `frontend/src/components/RunConfigDialog.jsx`, `RunConfigView.jsx` |
| Judge + diagnosis prompts (§6.9 contract) | `backend/app/integrations/real/prompts.py` |
| Integration preflight | `backend/app/check_integrations.py` |
| **All latency values, one file** | `backend/app/fake_config.py` |
| Orchestrator (§6.15) | `backend/app/orchestrator.py` |
| Run cancellation signal (durable flag + in-process event) | `backend/app/cancellation.py` |
| FK-safe delete order (run / eval set) | `backend/app/services/deletion.py` |
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
`build_seams()` in `integrations/__init__.py` picks between them per seam from the
`*_IMPL` settings — see [Going from fake to real](#going-from-fake-to-real).
Nothing downstream imports a concrete class, so the orchestrator and routers are
untouched by the choice. Simulated latencies live only in `app/fake_config.py`.

`build_seams` takes the *run's* config, so which endpoint a real seam talks to is
decided per run rather than per process. That is also why the clients are built
per run instead of being module-level singletons: `trigger_run` spawns background
tasks without a lock, so two concurrent runs would otherwise race over shared
state. The same call rebuilds the seams on the view path (trace fetch,
re-diagnose) from the run that produced the result, so a past run is always read
back through the endpoints it actually used.

## Langfuse read strategies (and the `events` table error)

A trace's observations can be read from either of two Langfuse endpoints:

| `LANGFUSE_TRACE_READ_STRATEGY` | Reads from |
|---|---|
| `auto` (default) | `GET /api/public/traces/{id}`, falling back to the list endpoint |
| `trace_api` | `GET /api/public/traces/{id}` only |
| `observations_api` | `GET /api/public/v2/observations?traceId=` only |

Both return the same observation fields, so the span mapping is identical — but
Langfuse serves them with **different internal queries**, which matters if you
hit this:

```
SQL Error: Unknown table expression 'events' in scope
SELECT e._span_id AS id, e.trace_id AS trace_id, ...
```

**That error comes from your Langfuse server, not from this platform.** This
client sends no SQL; Langfuse generates it against its own ClickHouse.
Self-hosted builds from around 3.152.0 query an `events` / `events_core` table
belonging to the v4 wide-observations schema whose production migration has not
shipped ([langfuse#11924](https://github.com/langfuse/langfuse/issues/11924),
[langfuse#12223](https://github.com/langfuse/langfuse/issues/12223),
[discussion#12777](https://github.com/orgs/langfuse/discussions/12777)).

Fix it on the Langfuse deployment:

1. `SELECT * FROM default.schema_migrations WHERE dirty = 1` — rows here mean a
   failed migration left the database marked dirty.
2. Re-run the ClickHouse migrations (check `LANGFUSE_AUTO_CLICKHOUSE_MIGRATION_DISABLED`,
   and that `langfuse-web` can reach ClickHouse at startup).
3. Failing that, pin the Langfuse image below 3.152.

`auto` is a hedge, not a cure: if both endpoints fail the same way, only fixing
the deployment helps. The UI says so explicitly rather than showing a raw SQL
dump — the banner explains the cause and keeps the original error behind a
"Technical detail" disclosure. Switching to the official `langfuse` Python SDK
would *not* help: it is a generated client over these same REST endpoints.

## Paging the lists

`GET /eval-sets` and `GET /eval-sets/{id}/runs` both take `limit` and `offset`
and return `{items, total, has_more}`. The UI appends pages as you scroll, with a
Load-more button for keyboard users and for when the page itself doesn't scroll.

`GET /eval-sets` also takes `q` (name substring), `metadata_key` /
`metadata_value`, and `sort` (`created_at` | `name`). These are applied in SQL,
so searching looks at every eval set you can see rather than only the pages
already loaded.

Both endpoints issue a **fixed number of queries regardless of page size**;
`backend/tests/test_pagination.py` asserts it. Those tests need a database and
skip without one, so `make test` stays DB-free:

```bash
createdb agenteval_test
TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' pytest tests/test_pagination.py
```

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
