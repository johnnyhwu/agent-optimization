# Agent Eval — Stage 1 POC + Playground

A runnable end-to-end demo of **Stage 1** from
[`docs/spec.md`](docs/spec.md): upload an eval set, run an eval through a
platform-owned orchestrator, and for wrong answers show an LLM **clue-style
diagnosis** that jumps the UI straight to the suspect span with its
input/output/token detail.

Plus the **Playground** (Stage 4) — a second section where one ad-hoc question
goes to the agent with an **editable copy of its config and skill files**, so the
hypothesis you form while reading a failed trace can be tested without editing an
eval set and running the whole thing — and the questions worth keeping can be
promoted into a new eval set. See [The playground](#the-playground).

Every external dependency sits behind a swappable interface with **two
implementations**: a fake one with realistic latency, and a real one (HTTP agent
server, LLM judge, LLM diagnosis, LLM synthesis, Langfuse trace fetch, the agent's
own workspace).
All six default to fake, so the demo runs on nothing but Docker; each can be
switched to real independently — see
[Going from fake to real](#going-from-fake-to-real). The app DB schema is the
real thing, created by Alembic migrations.

> **Out of scope (Stage 2/3):** per-span probability/heatmap, manual span
> re-labeling, SkillOpt, skill write-back, annotation score sync,
> multi-tenant isolation. Writing back to Langfuse (verdicts as Scores) is
> also not done — the trace seam reads only.

**Contents** — [The problem](#the-problem) · [How it works](#how-it-works) ·
[Life of a run](#life-of-a-run) · [The playground](#the-playground) · [Stack](#stack) ·
[Run it](#run-it-one-command) · [Fake → real](#going-from-fake-to-real) ·
[Trying the flows](#trying-the-flows) · [Where things live](#where-the-important-pieces-live) ·
[API](#api-surface) · [Langfuse read strategies](#langfuse-read-strategies-and-the-events-table-error) ·
[Paging](#paging-the-lists) · [Upload schema](#upload-schema)

> **New to this codebase?** Read [The problem](#the-problem) and
> [Life of a run](#life-of-a-run) below, then
> **[`docs/spec.md`](docs/spec.md)** — the single authoritative technical
> document, covering what the system is for, why it is designed this way, and
> exactly what is and isn't built. It is self-contained: it can be read without
> the code. This README is the operating manual; the spec is the design and
> implementation record.
>
> ⚠️ **The `§` numbers in code comments are stale.** Around 179 comments cite an
> older spec that has been deleted (it lives on in git history only). Their
> numbering does **not** line up with today's `docs/spec.md` — a comment saying
> `§6.13` means the frontend's three tiers, which is now §10.1. Treat a `§` in
> the source as a historical marker, not a lookup key.

## The problem

There is a **stateless domain agent** hosted behind an HTTP endpoint. Each
question it answers produces a **trace** in [Langfuse](https://langfuse.com) —
a sequence of spans, each one a tool call or the final response generation. The
agent picks a **skill** (a developer-written playbook for a class of question)
and then tool-calls its way to an answer.

Evaluating it was already possible: push questions in, LLM-as-judge the answers,
get a pass rate. **The expensive part was what came next** — for every question
judged wrong, a developer had to open that question's trace in Langfuse and read
through the spans by hand to find where it went off the rails. That is the cost
this platform removes.

So on top of running the eval, it asks an LLM: *given the developer's plain-language
description of how this question should have been answered, and the trace of what
actually happened, where did the two diverge?* The answer is deliberately phrased
as **a clue, not a verdict** — several suspect spans are allowed, confidence is
high/medium/low rather than a fake percentage, and a `caveat` field lets the model
say "this isn't attributable to one span at all". The UI then jumps straight to
the top suspect with its input, output and token counts.

Why the hedging matters: the whole feature rests on the assumption that an error
can be pinned to a single span. That assumption is often wrong (compounding
errors, several valid paths, faults in a tool rather than the skill). Overstating
confidence would send developers down the wrong path with false authority — see
spec §4.1 and §4.4.

## How it works

```
                    ┌─────────────────────────────────────────────┐
  browser ────────► │  Eval platform  (this repo)                 │
  (React, :5173)    │                                             │
                    │  FastAPI ──► Orchestrator (asyncio task)    │
                    │     │        └── Playground (in memory)     │
                    │     │              │                        │
                    │     │              ├─► AgentClient  ────────┼─► agent server
                    │     │              ├─► JudgeClient  ────────┼─► LLM endpoint
                    │     │              ├─► TraceClient  ────────┼─► Langfuse
                    │     │              ├─► DiagnosisClient ─────┼─► LLM endpoint
                    │     │              └─► WorkspaceClient ─────┼─► agent server
                    │     ▼                                       │
                    │  Postgres: eval sets, questions, runs,      │
                    │            results, diagnoses, roles        │
                    └─────────────────────────────────────────────┘
                          ▲ SSE: live per-question progress
```

Two ideas carry most of the design:

**1. Six swappable seams.** Each external dependency is a Python `Protocol`
with two implementations — a fake one with realistic latency, and a real one.
`AGENT_IMPL` / `JUDGE_IMPL` / `TRACE_IMPL` / `DIAGNOSIS_IMPL` / `SYNTHESIS_IMPL` /
`WORKSPACE_IMPL` pick
between them **independently**, all defaulting to fake. So the whole product runs
on nothing but Docker, and you can bring up one real service at a time.

**2. Langfuse owns traces; this app owns everything Langfuse has no concept of.**
Span input/output/token counts are fetched live from Langfuse at view time and
**never copied into our database**. They arrive whole — the UI collapses long
bodies rather than cutting them, because a truncated span body destroys the
evidence you opened the span to read. Our database holds what Langfuse cannot
express: eval sets, stable question ids, runs, verdicts, and the LLM diagnoses.
The link between the two is a **correlation id**.

> **The correlation id is the linchpin.** Before calling the agent, the platform
> generates an id and passes it in the request metadata. The agent server must
> use it as its Langfuse trace id. Without that, the platform has no way to find
> the trace it just caused, and error localization cannot work at all. This is
> the one change required **outside this repo** — see
> [the trace-seam prerequisite](#going-from-fake-to-real).

## Life of a run

What happens when someone presses "Run eval", end to end:

1. `POST /eval-sets/{id}/runs` records the run **with the exact settings it was
   triggered with** (endpoints, models, timeouts, concurrency) and starts a
   background asyncio task. Blank fields are resolved to the environment's values
   *now*, so a run is a complete record rather than a set of deltas against an
   environment that may since have changed.
2. The orchestrator takes a **snapshot of the questions**. Editing a question
   afterwards does not affect this run — a run is a historical execution.
3. It creates **every** `question_results` row up front as `pending`, so the UI
   lists the whole question set from the first second instead of having questions
   pop into existence one at a time.
4. Per question: **agent** (correlation id in the metadata) → **judge** → write
   the verdict → **poll Langfuse with backoff** until the trace lands (ingestion
   is asynchronous) → if the answer was wrong, fetch and truncate the trace and
   ask the **diagnosis** model, storing the result.
5. Each of those boundaries publishes an **SSE event**, so all three columns of
   the detail view update live. The diagnosis is generated **once** and stored;
   opening the question later reads the database.

Failure is expected and never fatal to the run: a question that fails records
*why* and the run continues to completion. A judge failure is never silently
treated as a pass. A diagnosis failure leaves the verdict intact. An unexpected
error still finalizes the run and still closes the SSE stream — a run is never
left stuck in `running`.

## The playground

The diagnosis tells you *where* a trace went wrong. The usual next thought is
"if the skill said X instead, this would have worked" — and before the playground
the only way to test that was to edit an eval set and run the whole thing. The
**Playground** section is the cheap path: one question, one editable skill, one
button.

- **Only the question is required.** The two ground-truth fields are switches, not
  paperwork: an **expected answer** turns judging on, an **expected reasoning
  process** turns diagnosis on. With neither, you get the answer and the trace —
  which is often all you wanted.
- **Workspace override.** The composer loads the agent's own `config.json` (minus
  its secrets) and every file under its `skills/` directory. Change a config value,
  edit a `SKILL.md`, add or delete a reference file — and it all travels with
  *this one call* as `metadata.workspace`. Nothing is written back to the agent
  server.
- **Stale-snapshot check.** Before each send the platform asks the agent server
  for its workspace version. If it moved since the editor read it, you are asked
  whether to reload (discarding your edits) or send anyway — a question answered
  against a skill that changed underneath you is not a result you can trust, and
  you would have no way of telling afterwards.
- **Attempts are not saved.** They live in the backend's memory (capped per user),
  so a backend restart clears the list. That is deliberate — an attempt is scratch
  work, a run is a record — and it means no migration and nothing to clean up.
- **Iterating.** The left column lists this session's attempts; **Clone** copies an
  attempt's question, workspace edits and settings back into the composer so the next
  attempt differs by exactly the one thing you are testing. There is no automatic
  "did it improve" — LLMs have temperature, so pressing the button twice is the
  honest comparison (spec §16, risk 8).
- **Shortlist → eval set.** A playground question that turned out to be worth
  keeping goes on the **shortlist**, and the shortlist becomes a new eval set.
  The dialog is a review step, not a form: the expected answer is prefilled with
  the agent's own and **labelled unverified** (kept as-is, that question asserts
  the agent is already right — it will always pass and can never catch the answer
  being wrong), and the expected process has a **Draft from trace** button that
  asks an LLM to summarise what the agent did, step by step, as a starting point
  to edit. An attempt that ran against an edited workspace is flagged, because the
  deployed agent has none of those edits.
  Because a set is locked once created (§4.6), the dialog also lets you tick
  existing eval sets whose questions should be **copied** into the new one — that
  is the only way to end up with "the old questions plus these new ones".
  The shortlist itself lives in the browser and holds copies, so it survives both
  a backend restart and the per-user attempt cap.
- **Coming from a failed question:** the three-column view has a *"Try this in the
  playground"* link that carries the question, both ground-truth fields and that
  run's endpoints over.

> **The platform cannot verify that the agent honoured your override.** The one
> piece of evidence is that the injected text shows up in the trace's first
> span system message, which the span view renders — so you can see it. The UI
> says as much rather than implying a check that does not exist.

Three things are needed on the **agent server** for the real path (all additive) —
the full contract is [docs/agent_server_stage4_endpoints.md](docs/agent_server_stage4_endpoints.md):

```
GET  /get_workspace       -> {"version", "config", "redacted_paths", "skills"}
                             config.json minus its secrets, plus every skill file
                             as {relative path: text}
GET  /get_config_version  -> {"version"}   the same string, on its own
POST /execute             also reads metadata.workspace = {"config", "skills"},
                             applying it to this call only and never persisting it
```

Two rules on the agent's side carry the design: the incoming `config` is
**deep-merged** onto its own `config.json` (it must be — the snapshot arrived with
the secrets stripped, so replacing the file wholesale would leave the agent with
no API key), while `skills` **replaces** its directory for that call (only
replacement can express deleting a file).

With `WORKSPACE_IMPL=fake` (the default) the workspace is canned, and the editor
says so — so the whole flow, including seeing an override appear in a span, is
demonstrable on nothing but Docker.

## Stack
- **Backend:** FastAPI (async) + SQLAlchemy + Alembic + Pydantic, SSE for live run
  progress. Containerized; Python deps installed with **uv**.
- **DB:** PostgreSQL (via docker-compose).
- **Frontend:** React (Vite). Containerized; Node deps installed with **pnpm**.
  Hand-written CSS design system — no UI framework, no state library, and no
  router package: the view lives in the URL hash, parsed by a ~50-line
  `useHashRoute.js`. Fonts are bundled rather than fetched from a CDN, so the
  app renders identically offline and on every OS.
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
make test       # backend unit tests (no DB or external service needed; the 23
                #   database-backed tests skip — see "Paging the lists")
make preflight  # ping whichever integrations are set to real
make down       # docker compose down
```

## Deploying it

Development and deployment are two compose files layered over one shared
service definition:

| file | what's in it |
|---|---|
| `docker-compose.yml` | the three services, minus anything development-only |
| `docker-compose.override.yml` | published ports, source bind-mounts, both reload loops. **Compose loads this automatically**, so everything above this section is unchanged |
| `docker-compose.prod.yml` | the deployed form — `make prod-up` names its files explicitly, which is what leaves the override out |

The split is not stylistic: Compose *appends* list-valued keys like `ports` and
`volumes` when layering files, so an overlay cannot take a published port back
out. Keeping the development-only entries out of the base is what makes them
absent in deployment rather than merely overridden.

```bash
# in a repo-root .env  (gitignored)
POSTGRES_PASSWORD=…
DATABASE_URL=postgresql+asyncpg://agentopt:…@db:5432/agentopt
SYNC_DATABASE_URL=postgresql+psycopg://agentopt:…@db:5432/agentopt
KEYCLOAK_URL=https://keycloak.example.com/auth

./scripts/prod.sh   # or: make prod-up
make prod-logs
make prod-down
```

The script refuses to start if any of those are missing, rather than falling
back to the development password or a fake login — and prints the whole set,
since compose itself reports only the first one.

`scripts/prod.sh` is written to mirror `scripts/dev.sh` step for step, so

```bash
diff scripts/dev.sh scripts/prod.sh
```

reads as a list of exactly what deployment changes — six things, each marked in
the source.

**What changes.** The frontend becomes a `vite build` bundle served by nginx,
which also proxies `/api/` to the backend — one origin, so the bundle calls a
relative `/api` and CORS stops applying. nginx keeps port **5173** so the app's
URL is identical in both modes and a single Keycloak redirect URI covers them.
The backend drops `--reload` and its bind-mount, publishes nothing, and applies
migrations from its entrypoint. Postgres is no longer published to the host.

**Keycloak setup.** Register all three of these for the client, or sign-in
breaks in ways that name nothing useful:

| field | value | symptom if missing |
|---|---|---|
| Valid Redirect URIs | `http://<host>:5173/*` | `Invalid parameter: redirect_uri` at login |
| Valid Post Logout Redirect URIs | `http://<host>:5173/*` | login works, **sign-out** fails |
| Web Origins | `http://<host>:5173` | login redirects fine, then hangs on a blank page (the token exchange is blocked by CORS) |

`KEYCLOAK_AUDIENCE` is the one value worth expecting to get wrong: Keycloak only
writes the client id into `aud` when an audience mapper says so, and otherwise
writes something else (`account`, usually). A wrong value rejects every token.
The 401 names the value the token actually carried, so the first failed sign-in
tells you what to set — or leave it blank to skip the check.

**One worker, deliberately.** The playground's attempt store and the SSE hub
both live in the backend process's memory (spec §5.3, §15.2), so a second worker
makes attempts 404 and progress bars stall at random. Scaling out needs a shared
bus and persisted attempts first.

**Verifying SSE isn't buffered** — the one thing worth checking by hand after a
deployment, because it fails silently:

```bash
curl -N -H "Authorization: Bearer <token>" \
  http://localhost:5173/api/eval-sets/<id>/runs/<run-id>/progress
```

Events must trickle out one at a time. All at once at the end means
`proxy_buffering` isn't off and every progress bar in the app will look frozen.

## Going from fake to real
Out of the box every external dependency is faked, so the demo runs with nothing
but Docker. The six seams (spec §3.2) each have their own switch, so you can
bring them up **one at a time** — a real agent while the judge is still fake, and
so on.

| env var | seam | what `real` means |
|---|---|---|
| `AGENT_IMPL` | `AgentClient` | POST `{"message", "metadata"}` to the agent server's `/execute` (`AGENT_BASE_URL`), with the correlation id, run trigger, and eval set tag in `metadata.trace_data` (spec §3.3) |
| `JUDGE_IMPL` | `JudgeClient` | LLM-as-judge over an OpenAI-compatible endpoint (`LLM_BASE_URL`, `JUDGE_MODEL`) |
| `TRACE_IMPL` | `TraceClient` | read the trace back from Langfuse (`LANGFUSE_HOST` + key pair) |
| `DIAGNOSIS_IMPL` | `DiagnosisClient` | clue-style diagnosis (spec §8.2) over the same LLM endpoint (`DIAGNOSIS_MODEL`) |
| `SYNTHESIS_IMPL` | `SynthesisClient` | draft an expected reasoning process from a trace, for a question being promoted out of the playground. Shares the LLM endpoint with the judge and the diagnosis; `SYNTHESIS_MODEL` picks the model |
| `WORKSPACE_IMPL` | `WorkspaceClient` | read the agent's config + skill files for the playground: `GET {AGENT_BASE_URL}/get_workspace` and `/get_config_version` (spec §3.2). Read-only, so it is the cheapest one to switch on first |

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

# the playground's view of the agent's config + skills — read-only, so safe first
WORKSPACE_IMPL=real  AGENT_BASE_URL=https://your-agent-server
```
Then check the wiring before spending a run on it:
```bash
make preflight   # OK / FAIL per seam, with the reason
```

**Prerequisite for the trace seam (spec §3.3):** the agent server must read
`metadata.trace_data.trace_id` out of the `/execute` request body and use it
as its Langfuse trace id. Without that the platform has no way to find the
trace it just caused. The full metadata shape sent on every call is:
```json
{"trace_data": {"trace_id": "...", "session_id": "...", "user_id": "...", "tags": ["eval_<eval_set_name>"]}}
```
`trace_id` and `session_id` are the same value (each question is its own
correlation unit); `user_id` is the subject who triggered the run. A playground
attempt sends the same shape with `tags: ["playground"]`, plus
`metadata.workspace` when the agent's config or skill files were edited
([the playground](#the-playground)) — an eval run never sends that key at all.

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
- **A trace is re-read until it stops growing before it is used** (spec §6.1a).
  Langfuse ingests a trace incrementally, so the first read that returns any
  observation at all can be a trace that is still filling up — and the span that
  loses that race is the last one, the agent's final answer generation. Polling
  answers "does this trace exist"; nothing answers "is it complete", so the read
  is repeated until the span count stops growing. `TRACE_SETTLE_DELAY_S` (1.0)
  and `TRACE_SETTLE_MAX_READS` (3) size that window; `0` reads goes back to
  trusting the first read. When nothing is pending this costs one extra request.
- `JUDGE_SCORE_THRESHOLD` is empty by default, meaning the judge's own verdict is
  used. Set a 0–1 number to derive the verdict from its score instead, so the
  pass/fail boundary can be retuned without touching the prompt.

## Trying the flows
- **Fake login switch (spec §11.2):** top-right dropdown flips between the seeded users
  `alice` (**owner**) and `bob` (**viewer**). As `bob`, the "Edit questions" and
  "Re-diagnose" controls disappear and write APIs return 403; runs are still
  allowed. (Backend default identity is `FAKE_USER_SUBJECT`, default `alice`.)
- **Getting around (spec §10.1):** the left rail holds the three sections —
  **Evaluation**, **Playground**, and **Optimize**, which is disabled and marked
  *Soon* because SkillOpt is Stage 3. Collapse the rail to icons with the button
  at its foot; the choice sticks. Inside Evaluation it's three tiers — cards →
  run history → 3-column detail — with a breadcrumb for one-click back.
- **The URL is the view.** Drill into a run and the address bar reads
  `#/evaluation/1/runs/11,10?mode=intersection`. Back walks the tiers, reload
  keeps your place, and a failing run's detail view is a link you can paste to
  whoever should look at it.
- **Playground:** the second section. Ask anything and watch the phase steps
  (Agent → Judge → Trace → Diagnosis) advance **without leaving the page**; the
  stages you gave no ground truth for are struck through rather than left looking
  pending. Pick `billing` under *Skill override*, edit the text, ask again, then
  open the first span — the edited text is there in the system message, which is
  the only evidence that an override took effect. From a failed question, *"Try
  this in the playground"* carries everything over.
- **Finding an eval set:** the toolbar above the cards searches by name, filters
  by a custom metadata key/value, and sorts by newest or name. All of it runs in
  SQL, so it searches every set you can see — not just the pages already loaded.
- **Incorrect modes:** in run history, multi-select runs and pick
  **union / intersection / last-N** — the seed data makes all three differ.
  Selection is kept by run id, so it survives loading more pages.
- **Diagnosis:** click an incorrect question. The middle column reads as three
  labelled sections — **Answer** (what the agent said, what was expected, the
  judge's comment), **Diagnosis** (the clue-style `overall_diagnosis` plus a
  **caveat** banner when present), and **Trace** (the span list with suspects
  marked high/med/low, top one auto-selected, and any trace-state banner).
- **Reading a span:** the right column renders the span body as the LLM call it
  is, not as a JSON dump — the tool catalogue in one collapsible, then each
  `messages[]` entry with a role chip (system / user / assistant / tool), a
  one-line summary and its `tool_calls` with re-indented arguments. **Nothing is
  truncated**: tools and earlier turns start collapsed, the last turn and the
  output start open, and a **Pretty | JSON** toggle shows the full raw payload
  for anything the renderer doesn't recognise. Below that, the span's diagnosis
  reason + evidence (or "not flagged").
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
- **The detail view is live in all three columns.** Open a question that hasn't
  run yet and *stay on it*: the middle column fills in by itself as the agent
  answers, the judge rules and the diagnosis lands — no navigating away and back.
  The open question is tracked by id and its trace payload is refetched whenever
  the fields that change it move (`phase`, `verdict`, `trace_ready`,
  `has_analysis`), driven by the SSE stream rather than polling. A span you
  selected by hand is never stolen by a background refresh, and a question the
  agent hasn't reached yet says "waiting for the agent" instead of reaching for a
  trace that cannot exist yet.
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
| App DB schema (spec §5.1), the 7 tables | `backend/alembic/versions/0001_stage1_schema.py` |
| Columns the real integrations need | `backend/alembic/versions/0002_real_integration.py` |
| Per-run config columns (`name`/`config`/`secrets`) | `backend/alembic/versions/0003_run_config.py` |
| Cancellation flag + the two error columns | `backend/alembic/versions/0004_run_lifecycle.py` |
| Indexes for the two list endpoints | `backend/alembic/versions/0005_list_indexes.py` |
| ORM models | `backend/app/models.py` |
| Request/response models (incl. `Page`) | `backend/app/schemas.py` |
| **The five swappable seams** (Protocols) | `backend/app/integrations/base.py` |
| **Fake impls** (each `# REPLACE WITH REAL IMPL`) | `backend/app/integrations/fake.py` |
| **Real impls** (agent / judge / Langfuse / diagnosis / skills) | `backend/app/integrations/real/` |
| Which impl backs each seam + per-run clients (`build_seams`) | `backend/app/integrations/__init__.py` |
| **The four per-question steps** + retry/timeout/cancel policy | `backend/app/pipeline.py` |
| **Playground store + executor** (in memory, no tables) | `backend/app/playground.py` |
| Playground endpoints (creator-only, 404 for others) | `backend/app/routers/playground.py` |
| Playground UI (composer, attempts, phase steps) | `frontend/src/components/Playground.jsx` and `PlaygroundComposer/SkillEditor/AttemptList/PhaseSteps.jsx` |
| Config fields shared by the run dialog and the playground | `frontend/src/components/RunConfigFields.jsx` |
| View-path trace read + span mapping (never truncated) | `backend/app/services/trace_view.py` |
| Run-config defaults + trigger-time resolution | `backend/app/services/run_config.py` |
| Run-config dialog / read-only view | `frontend/src/components/RunConfigDialog.jsx`, `RunConfigView.jsx` |
| Judge + diagnosis prompts (spec §8 contract) | `backend/app/integrations/real/prompts.py` |
| Integration preflight | `backend/app/check_integrations.py` |
| **All latency values, one file** | `backend/app/fake_config.py` |
| Orchestrator (spec §6) | `backend/app/orchestrator.py` |
| Run cancellation signal (durable flag + in-process event) | `backend/app/cancellation.py` |
| FK-safe delete order (run / eval set) | `backend/app/services/deletion.py` |
| Optimistic-lock 409 (spec §4.12) | `backend/app/routers/eval_sets.py`, `questions.py` |
| Card aggregates + paging/filter/sort | `backend/app/routers/eval_sets.py` |
| Trace view state machine (incl. `not_started`) | `backend/app/routers/results.py` |
| Manual re-diagnose (owner-only) | `backend/app/routers/diagnosis.py` |
| Roles / fake login (spec §11) | `backend/app/auth.py` |
| Body truncation, diagnosis prompt only (spec §4.4) | `backend/app/services/truncation.py` |
| Span input/output rendered as a chat exchange | `frontend/src/components/SpanPayload.jsx` |
| Incorrect modes + regression + `phase` | `backend/app/services/aggregation.py` |
| SSE hub | `backend/app/sse.py` |
| Seed data | `backend/app/seed.py` |
| Frontend three tiers | `frontend/src/components/` |
| App shell: rail + topbar + page, route dispatch | `frontend/src/App.jsx` |
| Section registry (add a section here, not in App) | `frontend/src/components/SideRail.jsx` |
| URL ↔ view state (hash parse, `href` builders) | `frontend/src/useHashRoute.js` |
| Design tokens, shell layout, the flex height chain | `frontend/src/styles.css` |
| Live 3-column update (trace fingerprint) | `frontend/src/components/RunDetail.jsx` |
| Paging hook: append, dedupe, drop stale responses | `frontend/src/usePagedList.js` |
| Load-more footer + scroll sentinel | `frontend/src/components/ListFooter.jsx` |
| Bounded "Use config from" picker | `frontend/src/components/RunPicker.jsx` |
| Live run progress bar + stop button | `frontend/src/components/RunStatusBar.jsx` |

## API surface

Interactive docs are served by the running backend at
**http://localhost:8000/docs** (OpenAPI schema at `/openapi.json`). The annotated
list, with the authorization rule for each endpoint, is spec §9. In brief:

| Group | Endpoints |
|---|---|
| Session | `GET /health`, `/users`, `/me`, `/run-config/defaults` |
| Eval sets | `POST /eval-sets`, `GET /eval-sets` (paged + filtered), `GET·PATCH·DELETE /eval-sets/{id}`, `PUT /eval-sets/{id}/roles`, `GET /eval-sets/metadata/keys` |
| Questions | `GET /eval-sets/{id}/questions`, `PATCH .../questions/{qpk}` (optimistic lock → 409) |
| Runs | `POST·GET /eval-sets/{id}/runs` (paged), `GET·DELETE .../runs/{run_id}`, `POST .../runs/{run_id}/cancel`, `GET .../runs/{run_id}/progress` (SSE) |
| Results | `GET /eval-sets/{id}/results`, `GET .../results/{rid}/trace`, `POST .../results/{rid}/re-diagnose` |
| Playground | `GET /playground/workspace`, `/workspace/version`, `POST /playground/attempts/{id}/synthesize-reasoning`, `POST /eval-sets/from-shortlist`, `POST·GET /playground/attempts`, `GET·DELETE /playground/attempts/{id}`, `POST .../cancel`, `POST .../re-diagnose`, `GET .../progress` (SSE) |

Authorization is a FastAPI dependency, not scattered per-endpoint: writes and
re-diagnose require **owner**; reads and triggering a run accept **owner or
viewer**; cancelling accepts an owner *or* whoever started that run. Identity
comes from the `X-User-Subject` header (`?subject=` for SSE, which cannot set
headers).

The playground endpoints are outside that scheme, because an attempt belongs to no
eval set: an attempt is visible only to the subject who created it, and someone
else's is a **404 rather than a 403** — whether an attempt exists at a given id is
not theirs to learn either. A 404 is also what you get after a backend restart
dropped the in-memory store, and the UI says so in those words.

`GET .../results/{rid}/trace` returns a `trace_state` of `ready`, `generating`
(ingestion still landing), `not_started` (the agent hasn't been asked yet — no
request is made to the trace store), `no_trace` (the question failed), or `error`
(the trace store could not be read, with the reason).

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
skip without one, so `make test` stays DB-free. The same is true of the half of
`test_shortlist.py` that creates eval sets — copying questions, checking
permissions and de-duplicating are all SQL:

```bash
createdb agenteval_test
TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' pytest
```

## Upload schema
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
