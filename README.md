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

**Sign-in follows the same pattern.** `AUTH_MODE=fake` (the default) trusts a
header and gives you the owner/viewer switch in the top bar; `AUTH_MODE=keycloak`
runs real OIDC against a Keycloak realm — see [Signing in](#signing-in). And the
stack has two shapes: `./scripts/dev.sh` for development, `./scripts/prod.sh` for
a deployed build behind nginx — see [Deploying it](#deploying-it).

> **Out of scope (Stage 2/3):** per-span probability/heatmap, manual span
> re-labeling, SkillOpt, skill write-back, annotation score sync,
> multi-tenant isolation. Writing back to Langfuse (verdicts as Scores) is
> also not done — the trace seam reads only.

**Contents** — [The problem](#the-problem) · [How it works](#how-it-works) ·
[Life of a run](#life-of-a-run) · [The playground](#the-playground) · [Stack](#stack) ·
[Run it](#run-it-one-command) · [Signing in](#signing-in) · [Deploying it](#deploying-it) ·
[Fake → real](#going-from-fake-to-real) ·
[Trying the flows](#trying-the-flows) · [Where things live](#where-the-important-pieces-live) ·
[API](#api-surface) · [Langfuse read strategies](#langfuse-read-strategies-and-the-events-table-error) ·
[Paging](#paging-the-lists) · [Upload schema](#upload-schema) · [Download](#download-export)

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
  browser ────────► │  nginx (deployed form only) ────────────────┼─┐
    │  (:5173)      │    /       static bundle                    │ │ /api
    │               │    /api/*  ──────────────────────────────►  │ │
    │               │                                             │ │
    │               │  FastAPI ──► Orchestrator (asyncio task)    │◄┘
    │               │     │        └── Playground (in memory)     │
    │               │     │              │                        │
    │               │     │              ├─► AgentClient  ────────┼─► agent server
    │               │     │              ├─► JudgeClient  ────────┼─► LLM endpoint
    │               │     │              ├─► TraceClient  ────────┼─► Langfuse
    │               │     │              ├─► DiagnosisClient ─────┼─► LLM endpoint
    │               │     │              ├─► SynthesisClient ─────┼─► LLM endpoint
    │               │     │              └─► WorkspaceClient ─────┼─► agent server
    │               │     │                                       │
    │               │     └─► current_subject ───────────────────►┼─► Keycloak (JWKS)
    │               │     │   (AUTH_MODE)                         │
    │               │     └─► /users/lookup ─────────────────────►┼─► employee directory
    │               │     ▼                                       │
    │               │  Postgres: eval sets, questions, runs,      │
    │               │            results, diagnoses, roles        │
    │               └─────────────────────────────────────────────┘
    └── OIDC login ──► Keycloak            ▲ SSE: live per-question progress
```

Two ideas carry most of the design:

**1. Six swappable seams.** Each external dependency is a Python `Protocol`
with two implementations — a fake one with realistic latency, and a real one.
`AGENT_IMPL` / `JUDGE_IMPL` / `TRACE_IMPL` / `DIAGNOSIS_IMPL` / `SYNTHESIS_IMPL` /
`WORKSPACE_IMPL` / `OPTIMIZER_IMPL` pick
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
the full contract, written so the agent server team can implement from it alone,
is **spec §17**:

```
GET  /get_workspace       -> {"version", "config", "redacted_paths", "skills"}
                             config.json minus its secrets, plus every skill file
                             as {relative path: text}
GET  /get_config_version  -> {"version"}   the same string, on its own
POST /execute             also reads metadata.workspace = {"config", "skills"},
                             applying it to this call only and never persisting it
                          ...and metadata.timeout_s, this call's time budget,
                             replacing the server's own hard-coded limit
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
make test       # backend unit tests: 242 of 270, no DB or network needed. The 28
                #   database-backed ones skip — see "Paging the lists"
make preflight  # ping whichever integrations are set to real
make down       # docker compose down
```

## Signing in

Identity is a seam like the other six. `AUTH_MODE` picks the implementation, and
**only `current_subject` in `app/auth.py` branches on it** — every role check
below that takes a subject string and is identical either way.

| | `AUTH_MODE=fake` (default) | `AUTH_MODE=keycloak` |
|---|---|---|
| Who you are | an `X-User-Subject` header | the token's `preferred_username` |
| Top bar | a dropdown to switch identity | your username + sign out |
| Used by | local dev, `make test`, the seeded demo, testing owner/viewer | deployment |

```bash
# repo-root .env  (gitignored)
AUTH_MODE=keycloak
KEYCLOAK_URL=https://keycloak.example.com/auth   # include /auth if your realm serves it there
KEYCLOAK_REALM=…
KEYCLOAK_CLIENT_ID=…                             # a public client; the flow is Auth Code + PKCE

./scripts/dev.sh    # same command; AUTH_MODE decides
```

**Reaching a deployment over plain http takes no configuration, and costs PKCE.**
Browsers withhold two Web Crypto APIs from origins they consider insecure —
anything that is neither https nor `localhost`. `crypto.subtle` computes the PKCE
S256 challenge, and `crypto.randomUUID` produces the `state` and `nonce` that
keycloak-js needs on *every* sign-in, PKCE or not. So the stack works when you
open it yourself at `http://localhost:5173` and fails for a colleague opening
`http://<your-host>:5173`, as `Web Crypto API is not available` — the browser
withholding an API, not anything wrong on the network.

`initAuth` handles this by reading `window.isSecureContext`:

- **Secure context** — PKCE S256, always. There is no setting that can turn it
  off, so an https deployment cannot end up without it by forgetting something.
- **Insecure origin** — `crypto.randomUUID` is shimmed on top of
  `crypto.getRandomValues` (which insecure origins *do* get, so the bits still
  come from the same CSPRNG), and PKCE is dropped with a console warning. The
  authorization code is then redeemable by anyone who observes the redirect —
  which on a plain-http origin is already true of the access token it would be
  exchanged for, and of every API call after it.

The shim is `frontend/src/web_crypto_shim.js`, about forty lines and no
dependency. It deliberately does not shim `crypto.subtle`: a SHA-256 in
JavaScript would buy back a PKCE challenge protecting a code that travels in the
clear regardless. **Serving the app over https is what actually restores both**,
and it is the answer for anything that outlives a demo.

Pointing the **development** stack at a real Keycloak is the recommended way to
get a realm configuration right: reload and HMR still work, and there are two
fewer moving parts than the deployed form.

**The subject stored in the database is `preferred_username`, not `sub`.** The
columns that hold subjects already held usernames, the share picker is a person
typing a colleague's name, and the employee directory is keyed by the same
string — so this needed **no migration**. Everything that writes a subject
casefolds it through one function, because `eval_set_roles` is looked up by exact
match and a `TW12345` typed against a `tw12345` token is not an error, it is an
eval set shared with nobody.

**Three things to expect the first time:**

- **`KEYCLOAK_AUDIENCE` is probably wrong.** Keycloak only writes the client id
  into `aud` when an audience mapper says so; `account` is the common default,
  and a wrong value rejects every token. The 401 names the value the token
  actually carried, so the first failed sign-in tells you what to set. Blank
  skips the check.
- **Your eval set list will be empty.** The seeded data is owned by
  `alice`/`bob`/`carol`, and under real sign-in nobody is those users. That is
  correct, not broken — upload a set as yourself.
- **The backend may fail on TLS** if your Keycloak uses a corporate CA. See
  [the CA note](#deploying-it) below; the tell is that `curl` works from the host
  and fails from inside the container.

Sharing an eval set resolves the typed username against the employee directory
first (`GET /users/lookup`). A name the directory denies is blocked; a directory
that cannot be reached warns but allows — an outage there must not stop everyone
here from sharing anything.

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

**Internal certificate authority.** If any internal endpoint is HTTPS with a
certificate signed by a corporate CA, the backend will fail every call to it:

```
[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
```

The tell is that `curl` to the same URL works from the host and fails from
inside the container — the host trusts the corporate CA, the base image trusts
only the public roots shipped with `certifi`. Give the container the host's own
bundle:

```bash
cp /etc/ssl/certs/ca-certificates.crt backend/certs/ca-bundle.crt
echo 'SSL_CERT_FILE=/app/certs/ca-bundle.crt' >> .env
```

`httpx` reads that variable whenever `verify=True`, which is what every seam
uses, so one value covers Keycloak, Langfuse, the agent server and the LLM
endpoint at once. It **replaces** the trust store rather than adding to it, so
the file has to carry the public roots too — which is why this copies the whole
host bundle rather than just the corporate root. See `backend/certs/README.md`.

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
but Docker. The seven seams (spec §3.2) each have their own switch, so you can
bring them up **one at a time** — a real agent while the judge is still fake, and
so on.

| env var | seam | what `real` means |
|---|---|---|
| `AGENT_IMPL` | `AgentClient` | POST `{"message", "metadata"}` to the agent server's `/execute` (`AGENT_BASE_URL`), with the correlation id, run trigger, and eval set tag in `metadata.trace_data`, and this call's time budget in `metadata.timeout_s` (spec §3.3) |
| `JUDGE_IMPL` | `JudgeClient` | LLM-as-judge over an OpenAI-compatible endpoint (`LLM_BASE_URL`, `JUDGE_MODEL`) |
| `TRACE_IMPL` | `TraceClient` | read the trace back from Langfuse (`LANGFUSE_HOST` + key pair) |
| `DIAGNOSIS_IMPL` | `DiagnosisClient` | clue-style diagnosis (spec §8.2) over the same LLM endpoint (`DIAGNOSIS_MODEL`) |
| `SYNTHESIS_IMPL` | `SynthesisClient` | draft an expected reasoning process from a trace, for a question being promoted out of the playground. Shares the LLM endpoint with the judge and the diagnosis; `SYNTHESIS_MODEL` picks the model |
| `WORKSPACE_IMPL` | `WorkspaceClient` | read the agent's config + skill files for the playground: `GET {AGENT_BASE_URL}/get_workspace` and `/get_config_version` (spec §3.2). Read-only, so it is the cheapest one to switch on first |
| `OPTIMIZER_IMPL` | `OptimizerClient` | the model that edits the skill in Optimize — reflect, merge and rank all call it and nothing else (`LLM_BASE_URL`, `OPTIMIZER_MODEL`). The fake one returns deterministic patches, which is enough to exercise accept, reject and multi-file diffs end to end |

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
{"trace_data": {"trace_id": "...", "session_id": "...", "user_id": "...", "tags": ["eval_<eval_set_name>"]},
 "timeout_s": 115}
```
`trace_id` and `session_id` are the same value (each question is its own
correlation unit); `user_id` is the subject who triggered the run. A playground
attempt sends the same shape with `tags: ["playground"]`, plus
`metadata.workspace` when the agent's config or skill files were edited
([the playground](#the-playground)) — an eval run never sends that key at all.

`timeout_s` is the budget the agent server should give **itself** for this one
question (spec §17.0 #6), and it is sent on every call. Both ends need a
deadline: the agent server enforces its own limit, so until it is told ours it
uses a built-in default — which is why raising the timeout in the UI past that
default used to change nothing. The value sent is `AGENT_TIMEOUT_S` minus a
fixed 5s margin (`SERVER_TIMEOUT_MARGIN_S`), so the server runs out first and can
answer with a 5xx and a reason rather than leaving the platform to drop the
connection; what the platform itself waits is still the full `AGENT_TIMEOUT_S`.
An agent server that has not implemented this yet must ignore the unknown key
and answer as before.

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
  is not — it stays process-wide. The agent timeout also travels to the agent
  server on every call as `metadata.timeout_s` (above), so a per-run value larger
  than the server's own default is honoured instead of silently capped.
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
  **Evaluation**, **Playground**, and **Optimize**. Collapse the rail to icons
  with the button at its foot; the choice sticks. Inside Evaluation it's three
  tiers — cards → run history → 3-column detail — with a breadcrumb for one-click
  back.
- **Optimize (spec §2.3a):** train a skill against your eval questions the way
  you would train a model — epochs, steps, a learning rate that caps how many
  edits one step may apply, and a validation gate that throws away the ones that
  did not help. Six-step wizard, then a chart that grows a point per step while
  it runs. Click a step to see what it measured, which failures the analyst was
  shown together, and a side-by-side diff of what it did to the skill. The
  output is a zip you put back on the agent server yourself; re-run it through
  Evaluation for an unbiased number. All seven seams are fake by default, so the
  whole loop runs on `SEED=1 ./scripts/dev.sh` with nothing external attached.
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
| Session | `GET /health`, `/users`, `/users/lookup?username=`, `/me`, `/run-config/defaults` |
| Eval sets | `POST /eval-sets`, `GET /eval-sets` (paged + filtered), `GET·PATCH·DELETE /eval-sets/{id}`, `PUT /eval-sets/{id}/roles`, `GET /eval-sets/metadata/keys`, `POST .../judge-prompt/verify`, `POST .../judge-prompt/reviewed` |
| Questions | `GET /eval-sets/{id}/questions`, `PATCH .../questions/{qpk}` (optimistic lock → 409) |
| Runs | `POST·GET /eval-sets/{id}/runs` (paged), `GET·DELETE .../runs/{run_id}`, `POST .../runs/{run_id}/cancel`, `GET .../runs/{run_id}/progress` (SSE) |
| Results | `GET /eval-sets/{id}/results`, `GET .../results/{rid}/trace`, `POST .../results/{rid}/re-diagnose` |
| Export | `GET /eval-sets/{id}/export/preview`, `GET /eval-sets/{id}/export` — see [Download](#download-export) |
| Playground | `GET /playground/workspace`, `/workspace/version`, `POST /playground/attempts/{id}/synthesize-reasoning`, `POST /eval-sets/from-shortlist`, `POST·GET /playground/attempts`, `GET·DELETE /playground/attempts/{id}`, `POST .../cancel`, `POST .../re-diagnose`, `GET .../progress` (SSE) |

Authorization is a FastAPI dependency, not scattered per-endpoint: writes and
re-diagnose require **owner**; reads and triggering a run accept **owner or
viewer**; cancelling accepts an owner *or* whoever started that run. Identity
comes from an `X-User-Subject` header or an `Authorization: Bearer` token
depending on `AUTH_MODE` — see [Signing in](#signing-in).

**`GET /health` is the only endpoint that needs no identity**, for container and
proxy probes. That includes `/docs`, `/redoc` and `/openapi.json`: transparent in
fake mode, and effectively closed to browsers under Keycloak, since a plain
navigation cannot set an Authorization header. Read the schema with
`curl -H "Authorization: Bearer …" …/openapi.json`.

The two SSE progress streams are read with `fetch`, not `EventSource` —
`EventSource` cannot set headers, which would leave the token in the query
string, and it reconnects by replaying its original URL, which with a
60-second access token turns the first network blip into an endless retry
against an expired one. The client keeps the same
`addEventListener`/`close`/`onerror` surface, so the consumers did not change.

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
skip without one, so `make test` stays DB-free. The same is true of
`test_startup_reaper.py` and the half of `test_shortlist.py` that creates eval
sets — copying questions, checking permissions and de-duplicating are all SQL:

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

## How answers are graded (the judge prompt)

The judge is an LLM told what "correct" means for this eval set. That prompt is
editable — **on the eval set, by its owner**, under the config gear's *Judging*
tab (reachable from the card on the home page and from the run history, since
that is where someone is standing when they want to change it).

**Why it lives on the set and not in the run config.** Everything in the "Run
eval" dialog answers *where do I connect and how fast do I go* — the caller's
business, which is why a viewer may set it. This answers *what counts as
correct*, and that belongs to the question set: if every caller brought their
own, two runs of the same set would produce pass rates nobody could compare, and
comparing them is what the entire middle tier is for. It also means the existing
owner-only guard covers it, with no per-field permission rule to explain.

So the answer to "which run settings can a viewer change?" stays simple:
**all of them**. The judge prompt is not one of them — the run dialog shows which
prompt will be used and links to where it is changed. A posted prompt is
discarded server-side rather than refused, because there is nothing for the
caller to correct.

**Two halves, both yours.** The system prompt is unrestricted, including the JSON
contract. The user prompt is a template and must contain `{question}`,
`{ground_truth}` and `{agent_response}`; the editor checks that on every
keystroke, because a template missing `{ground_truth}` does not error — it grades
every answer against nothing and returns a pass rate that looks entirely normal.

**Verify prompt** grades one question you pick, twice: once with its own expected
answer (must come back *correct*), once with a deliberately contradictory one
(must come back *incorrect*). One call would only prove the reply parses — a
prompt that says "correct" to everything parses perfectly. It uses the
environment's LLM settings unless you override the model or key in the dialog,
and nothing typed there is stored. Verification is never required, and it is
cleared the moment either prompt is edited: a badge describing text that no
longer exists is worse than no badge.

**Blank means the built-in prompt**, and a set that never overrode it keeps
inheriting later improvements to it — the stored value is NULL, not a copy. The
frozen copy lives on each *run* instead (`runs.config`), so a finished run always
says exactly what it graded with even after the set has moved on. The run list
shows a short fingerprint of that text per row, highlighted when it differs from
the set's current prompt: same fingerprint means the pass rates are comparable,
and it is what answers "did the rate drop because the agent got worse, or because
I made the judge stricter?".

> There are no prompt *versions*. There is no need for a version table to see old
> prompts — every run carries the full text it used, visible under the run's
> config. What you don't get is a list to restore from.

**A new failure kind: "not judged".** If the judge replies with something that
cannot be parsed, the question is not marked incorrect and never counted as a
pass — it is recorded as `judge_invalid`, shown amber, and totalled per run as
"N unjudged". It stays in the pass rate's denominator (an ungraded question is an
unknown), but it points somewhere different from every other failure: at the eval
set's own judge prompt, the one thing an owner can go and fix.

With `JUDGE_IMPL=fake` (the default) the fake judge ignores prompts entirely. The
editor says so and disables Verify, rather than letting someone carefully tune
text that does nothing.

The playground is the exception to all of the above: an attempt belongs to no eval
set, so its judge prompt is freely editable there, and a question carried over
from a run arrives with that run's frozen prompt (the composer says which).

## Download (export)

Every eval-set card has a **Download** button (so does the run history, where
your ticked runs carry over). It opens a dialog that is a *preview of the
output* rather than a scope picker: each file is named, its real columns are
printed, and its row count is a number the server counted. What you tick is the
file you get.

| file | contents |
|---|---|
| `questions.{csv,jsonl}` | one row per question — **re-uploadable**, see below |
| `runs.{csv,jsonl}` | one row per run: status, pass rate, timings, resolved non-secret config |
| `results.{csv,jsonl}` | one row per **(run × question)**: agent answer, verdict, judge score/comment, latency |
| `traces.json` | agent spans + stored diagnosis per question; always JSON, off by default |
| `manifest.json` | source set, export time, what's included, question-id policy |

Selecting a single file downloads that file. A real bundle is zipped with a
manifest. Selecting nothing is a 422 rather than an empty archive.

**`questions.*` round-trips.** It uses the [upload schema](#upload-schema)
field names — `ground_truth_reasoning_process_description`, singular `skill` —
not the API's `ground_truth_reasoning` / `skills`, so an exported file goes
straight back through **Upload eval set**. Because a set is locked after
creation (no add/delete question endpoints exist), download → edit →
re-upload is the sanctioned way to *grow* a question set. Re-uploading creates
a **new** eval set; it does not update the source.

**Every table carries `eval_set_id` and `eval_set_name`.** `question_id` is
unique per eval set, not globally (`UNIQUE (eval_set_id, question_id)`), and a
download-edit-re-upload cycle routinely leaves two sets sharing ids. Nothing
internal cares — every join runs on the `question_pk` UUID — but an exported
file gets joined in pandas or Excel, where `question_id` alone silently merges
unrelated questions. **Join on `(eval_set_id, question_id)`**: the export's key
is the database's uniqueness key. Both parsers resolve columns by name and
ignore what they don't recognise, so these columns cost re-upload nothing.
Export preserves `question_id` (`POST /eval-sets/from-shortlist` deliberately
mints new ones — one is a copy, the other a derivation); `manifest.json` records
which rule produced the file.

**Credentials cannot reach a file.** Run rows are built through `RunConfig`,
which has no credential fields and drops unknown keys, so a secret mis-stored in
`config` still cannot be exported. Only slot names appear, via
`credentials_set`. Share lists are user subjects and are never exported.

Notes:
- **Viewers can download.** A viewer can already read every row an export
  contains, so withholding the file would protect nothing.
- Counts include the awkward ones — questions still running, traces not yet
  ingested — because a preview that rounds those away stops being believed.
- CSV is written with a BOM and CRLF so Excel opens UTF-8 (Chinese question
  text) intact. `pandas.read_csv` shows the BOM on the first column name unless
  read with `encoding="utf-8-sig"`.
- `EXPORT_MAX_TRACES` (1000) and `EXPORT_TRACE_CONCURRENCY` (8) size the only
  part of an export that leaves the database: one live read per result against
  the trace store. Past the cap the file records `truncated: true` and the
  dialog says so.
