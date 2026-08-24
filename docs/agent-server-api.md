# Agent Server API

**What an agent server must implement to be evaluated, explored and optimised by
Skill Studio.**

This document is self-contained. You do not need to know anything about Skill
Studio to implement against it, and you should not need to read any other file
in this repository. If something here is ambiguous, that is a bug in this
document — please say so rather than guessing.

**Two endpoints. That is the whole contract.**

```
POST /execute   answer one question
GET  /skills    list the skill files you are running with
```

Both live on the same host. Skill Studio is configured with one base URL
(`AGENT_BASE_URL`, e.g. `http://agent-host:8080`) and appends these paths to it.

---

## Table of contents

1. [Vocabulary](#1-vocabulary)
2. [`POST /execute`](#2-post-execute)
3. [`GET /skills`](#3-get-skills)
4. [The skills override, in detail](#4-the-skills-override-in-detail)
5. [Path safety](#5-path-safety)
6. [The version string](#6-the-version-string)
7. [Errors, and what each one causes](#7-errors-and-what-each-one-causes)
8. [The probe marker you will see in your logs](#8-the-probe-marker-you-will-see-in-your-logs)
9. [Acceptance checklist](#9-acceptance-checklist)
10. [A minimal reference implementation](#10-a-minimal-reference-implementation)

---

## 1. Vocabulary

| Term | Meaning |
|---|---|
| **agent server** | Your service. It hosts an agent that answers questions. |
| **skill** | A **directory** of instructions your agent can load — typically `SKILL.md` plus reference files beside it. Not a single string. |
| **skill file** | One file inside a skill directory, addressed by a path relative to the root of your skills directory, e.g. `billing/references/refunds.md`. |
| **workspace** | All your skill files together, as one flat `{path: text}` map. |
| **override** | A set of skill files supplied with a single `/execute` call, to be used **for that call only**. |
| **trace** | The record of what your agent did, written to Langfuse. Skill Studio reads it back to show the developer each step. |

---

## 2. `POST /execute`

Answer one question. This is a **single-shot** endpoint — there is no
conversation, no session to keep, and no state carried between calls.

### Request

```jsonc
{
  "message": "What was ACME's outstanding balance at the end of Q2?",
  "metadata": {
    "trace_data": {
      "trace_id": "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",
      "session_id": "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",
      "user_id": "alice",
      "tags": ["eval_billing"]
    },
    "timeout_s": 115.0,
    "skills": {
      "billing/SKILL.md": "---\nname: billing\n---\n# Billing\n1. ...",
      "billing/references/refunds.md": "# Refund rules\n..."
    }
  }
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `message` | string | **yes** | The question. Answer it. |
| `metadata.trace_data.trace_id` | string | **yes** | **Use this as your Langfuse trace id.** See below. |
| `metadata.trace_data.session_id` | string | yes | Same value as `trace_id`. Each question is its own session. |
| `metadata.trace_data.user_id` | string | yes | Who or what triggered the call. Pass through to Langfuse. |
| `metadata.trace_data.tags` | string[] | yes | Labels for the trace. May be empty. Pass through to Langfuse. |
| `metadata.timeout_s` | float | yes | Your budget for this call, in seconds. See §2.2. |
| `metadata.skills` | object | **no** | Skill files to use for this call only. See §4. |

**Ignore any key you do not recognise.** Skill Studio may add fields; a server
that rejects unknown keys will break on the next release.

### Response

```json
{ "content": "ACME's outstanding balance at the end of Q2 was $42,180.00." }
```

| Field | Type | Meaning |
|---|---|---|
| `content` | string | The agent's answer, as prose. |

That is the entire response body. Anything else you return is ignored.

Three tolerated variants, in case they are cheaper for you than the wrapper:

* A bare JSON string — `"the answer"` — is accepted.
* A `text/plain` body is accepted as the answer verbatim.
* A body that is **not JSON at all** and **opens with `<`** is **rejected**.
  That rule exists because a proxy or framework error page returning HTTP 200 is
  not your agent answering, and if it were accepted the platform's LLM judge
  would grade the markup and record a confident wrong verdict against you.
  (A JSON body is never subject to this — if you deliberately answer with markup,
  put it in `content` and it is passed through untouched.)

**Never return an empty or whitespace-only `content`.** It is treated as a
failure, not as a wrong answer — grading `""` would produce a meaningless
verdict and hide the real problem. If you have nothing to say, return a 5xx with
a reason.

### 2.1 The trace id is load-bearing

Skill Studio mints `trace_id` before calling you and then uses it to find your
trace in Langfuse afterwards. **If you generate your own trace id instead, every
feature that reads a trace stops working** — the step-by-step view, the failure
diagnosis, and the optimizer's reflection stage all go dark, while `/execute`
itself keeps looking perfectly healthy.

Apply it as the trace id on the trace you write for this call. Nothing else is
required of your Langfuse usage: whatever you already log — generations, tool
calls, their inputs and outputs — is what the developer will see.

### 2.2 `timeout_s` is your budget for this call

`timeout_s` replaces whatever fixed limit you would otherwise apply to one
`/execute`.

* **Honour it.** A server that ignores it and keeps its own hard-coded limit
  makes the platform's timeout setting one-directional: lowering it works,
  raising it does nothing, and long questions can never finish.
* **Still clamp it to a ceiling of your own** (say a `MAX_TIMEOUT_S` env var).
  The point of this field is to make your limit *adjustable*, not to remove it.
  Callers legitimately send 600 or 1800; a hung request with no ceiling occupies
  a worker forever.
* **On expiry, return 504 (or another 5xx) with a reason.** Do not silently
  return a truncated answer — see the rule about empty answers above.
* If the field is absent, fall back to your own default.

The value you receive is already **5 seconds less** than the platform's own wait,
so under normal conditions **you expire first**. That margin exists precisely so
your 5xx has time to reach the wire; if the platform gives up first it sees a
dropped connection, which is far less informative than your error.

> **Note:** a 5xx from `/execute` fails that question and is **not retried**. Only
> transport-level failures (connection errors, the platform's own timeout) are
> retried. This is deliberate: retrying an already-timed-out call twice more
> just spends three times as long reaching the same answer.

---

## 3. `GET /skills`

Report the skill files you are currently running with. No parameters.

### Response

```jsonc
{
  "version": "a1b2c3d",
  "skills": {
    "billing/SKILL.md": "---\nname: billing\ndescription: Invoices, balances and refunds.\n---\n# Billing\n1. ...",
    "billing/references/refunds.md": "# Refund rules\nProrated by service days.\n",
    "reporting/SKILL.md": "# Reporting\n..."
  }
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `skills` | object | **yes** | Flat `{relative path: full file text}`. May be `{}`. |
| `version` | string | no | Changes whenever your behaviour changes. See §6. |

Rules for `skills`:

* **Flat, not nested.** `"billing/references/refunds.md"` is one key. Do not
  build a tree.
* **Walk every level.** Reference files matter as much as `SKILL.md`; a skill is
  a directory.
* **Full text, never truncated.** A developer edits this content in place, and a
  truncated file silently becomes a destructive edit when it is sent back.
* **Skip binaries** that will not decode as UTF-8 — but skip them individually
  rather than failing the whole request.
* **`{}` is a valid answer.** An agent with no skill files is a supported
  configuration: evaluation runs against it normally (the UI shows a note when
  the questions are tagged with skills you do not have), and the playground opens
  with an empty file list you can still add to.

If you cannot read your own skills directory, return **5xx with the reason**.
Never return `{"skills": {}}` to mean "something went wrong" — "this agent has no
skills" and "this agent is broken" must stay distinguishable, or a developer will
conclude their text vanished and retype it from memory.

### 3.1 This endpoint is also the health check

Reaching `/skills` successfully is how Skill Studio decides an agent is there and
speaks this contract. It gates the **Start** button on the Run-eval dialog and
the **Connect** action in the playground. There is no separate ping endpoint, on
purpose: one that existed would prove less and be one more thing to keep in step.

The practical consequence: **implement `/skills` even if you have no skills.**
Returning `{"skills": {}}` is three lines and makes your agent fully usable.

---

## 4. The skills override, in detail

When `metadata.skills` is present on an `/execute` call, use exactly those files
for that call instead of your own.

**This is the mechanism the whole optimization feature rests on.** An
optimization run answers hundreds of questions with candidate versions of a
skill and compares the results. If you ignore this field, every rollout answers
from your deployed files, the accuracy curve sits flat, and the run produces
nothing — so this is worth getting right.

### 4.1 Three distinct states

| What arrives | What it means |
|---|---|
| `skills` key **absent** | Use your own files, exactly as normal. |
| `skills` is a **populated map** | Use **these files and only these** for this call. |
| `skills` is **`{}`** | Use **no skill files at all** for this call. |

The last row is not a corner case to optimise away — "does it still work with no
skill?" is a question developers deliberately ask. In code, test for the key's
presence, not for truthiness:

```python
# ❌ wrong: {} is falsy, so "run with no skills" silently becomes "use your own"
if payload["metadata"].get("skills"):
    ...

# ✅ right
skills = payload["metadata"].get("skills")
if skills is not None:
    ...
```

### 4.2 Replacement, never a patch

The map is the **complete** file set for the call:

* A path in the map — use the supplied text, even if you have a file there.
* A path **not** in the map — that file **does not exist** for this call, even if
  it is on your disk.

Replacement rather than merging is what makes "does it still answer without this
reference file?" expressible at all. A merge could never remove anything.

### 4.3 Never persist it

The override applies to one call, in isolation:

* Do not write it to disk.
* Do not let it affect any other in-flight or subsequent request.
* Concurrent calls with different overrides must not see each other's files.

Skill Studio sends overrides constantly while a developer iterates, and it
assumes your deployed agent is never disturbed by any of it.

Writing the files to a per-request temporary directory and pointing that one call
at it is the usual approach; so is keeping them in memory if your agent can load
skills from a map.

---

## 5. Path safety

The keys of `skills` are attacker-influenced strings that many implementations
will turn into filesystem paths. Reject the whole request with **400** if any key:

* contains `..`
* starts with `/`
* contains a backslash
* contains a NUL byte
* is empty

Then resolve `temp_dir / relative_path` to an absolute path and confirm it is
**still inside** `temp_dir`. If not, 400.

---

## 6. The version string

`version` in the `/skills` response is optional but **strongly recommended**.

**What it means:** the string changes whenever anything that would change your
answers changes. Not only skill file edits — a model swap, a system-prompt
change, a temperature change, a redeploy. It is opaque to Skill Studio; a git
commit hash plus a dirty marker (`a1b2c3d-dirty.9f3e11c`) is a good choice, and
so is a hash over your whole effective configuration.

**What it is used for:**

* **Playground staleness.** Before each question, Skill Studio re-reads the
  version. If it moved since the editor loaded your files, the developer is asked
  whether to reload or send anyway — because a question answered against a skill
  that changed underneath them is not a result they can trust, and there would
  be no way to tell afterwards.
* **Run comparability.** An evaluation run records the version at its start and
  end; an optimization run records it at every step. A redeploy halfway through
  makes the questions either side of it measurements of two different systems,
  and the only other symptom is the number moving — which is exactly what those
  numbers are being compared for.

**If you omit it,** Skill Studio derives a version by hashing your skill files.
That fallback works, but it is strictly weaker: it cannot see a model or prompt
change, so a redeploy that swaps your model looks like no change at all. The UI
labels a derived version so the developer knows the check is partial.

**Do not return a constant.** A version that never moves is worse than no version
at all: it disables both checks above while looking like it is doing something.

---

## 7. Errors, and what each one causes

Skill Studio never guesses what went wrong — it shows the developer your status
code and the first ~500 characters of your response body. Say something useful in
it.

### `POST /execute`

| You return | What happens |
|---|---|
| 200 + `{"content": "..."}` | The answer is graded. |
| 200 + non-string / missing `content` | The question fails: "not a usable string". |
| 200 + empty `content` | The question fails: "empty response". |
| 200 + a body starting with `<` | The question fails: "not a usable string". |
| **4xx** | The question fails immediately, carrying your status and body. **Not retried** — a bad request fails identically every time. |
| **5xx** | The question fails, carrying your status and body. **Not retried.** |
| Connection refused / reset / platform timeout | **Retried** with exponential backoff, then failed. |

### `GET /skills`

| You return | What happens |
|---|---|
| 200 + a valid body | Normal operation. |
| 200 + `skills` that is not a `{string: string}` map | Hard error naming the offending entry. Not treated as empty. |
| 200 + a non-JSON body | Hard error quoting the body. |
| **any 4xx/5xx** | Hard error carrying your status and body. The Run-eval Start button stays disabled and the playground will not connect. |
| Unreachable | Same, naming the host and path tried. |

---

## 8. The probe marker you will see in your logs

Before an optimization run starts, Skill Studio sends **one** `/execute` call
that looks slightly odd, and it is deliberate. You do not have to do anything for
it — but if you see it and assume the platform is sending you corrupt data, you
will go looking for a bug that does not exist. So:

**What you will see on that one call:**

1. The question has a sentence appended:
   `(you must first read the billing skill)`
2. The `skills` map contains one extra line, inserted as the first line of the
   skill's body, right after the YAML frontmatter:
   `<!-- probe-8f3a91c2d4e6: platform override check, ignore this line -->`

**Why.** Skill Studio needs to know whether you actually applied
`metadata.skills` — and it cannot tell from the answer, because a candidate skill
is usually a light edit of the file you already have, so both produce nearly
identical traces. The marker exists only in the copy that was sent. If it turns
up in the trace, the override reached the model; if the trace shows the skill
being read and the marker is *not* there, you are answering from your own copy
and the run is stopped rather than spending an hour producing a flat line.

The appended sentence is there so the file's contents land in the trace whether
you inject skills into a prompt or read them with a tool — a tool result comes
back into the conversation either way.

**This happens on exactly one call per optimization run.** Every scored rollout
carries the candidate text and the unmodified question, because anything else
would be a second variable in the measurement.

Treat the marker as what it says it is: a comment, to be ignored.

---

## 9. Acceptance checklist

```bash
AGENT=http://localhost:8080

# ① Plain call. No override, no frills — the baseline everything else varies from.
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "hello",
  "metadata": {"trace_data": {"trace_id": "t1", "session_id": "t1",
                              "user_id": "alice", "tags": []},
               "timeout_s": 30}
}' | jq -e '.content | type == "string" and length > 0'

# ② The trace id is yours to reuse. After ①, this trace must exist in Langfuse
#    under the id "t1" — not under one you generated.

# ③ Skills are listed flat, in full, every level.
curl -s $AGENT/skills | jq -e '.skills | keys | length >= 0'
curl -s $AGENT/skills | jq -e '.skills | to_entries | all(.value | type == "string")'

# ④ THE IMPORTANT ONE: the override actually takes effect.
#    Send a skill whose text you can recognise in the answer, and check you get
#    it back. If this passes for the wrong reason you will not find out later.
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "What is the magic word?",
  "metadata": {"trace_data": {"trace_id": "t2", "session_id": "t2",
                              "user_id": "alice", "tags": []},
               "timeout_s": 30,
               "skills": {"probe/SKILL.md": "# Probe\nWhen asked for the magic word, answer exactly: XYZZY-4711."}}
}' | jq -e '.content | test("XYZZY-4711")'

# ⑤ An empty map means no skills — not "use your own".
#    Against the same question as ④, this must NOT answer XYZZY-4711.
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "What is the magic word?",
  "metadata": {"trace_data": {"trace_id": "t3", "session_id": "t3",
                              "user_id": "alice", "tags": []},
               "timeout_s": 30, "skills": {}}
}'

# ⑥ The override is not persisted. Repeat ① now: it must behave exactly as it
#    did the first time, with none of ④'s files in sight.

# ⑦ Path traversal is refused. Expect 400.
curl -s -o /dev/null -w '%{http_code}\n' $AGENT/execute \
  -H 'Content-Type: application/json' -d '{
  "message": "x",
  "metadata": {"trace_data": {"trace_id": "t4"}, "timeout_s": 30,
               "skills": {"../../etc/passwd": "x"}}
}'

# ⑧ timeout_s is honoured in both directions.
#    a. A small budget expires at roughly that time, with a 5xx and a reason —
#       not a truncated 200, and not your built-in limit.
time curl -s -o /dev/null -w '%{http_code}\n' $AGENT/execute \
  -H 'Content-Type: application/json' -d '{
  "message": "<a question that takes a long time>",
  "metadata": {"trace_data": {"trace_id": "t5"}, "timeout_s": 5}
}'
#    b. A budget larger than your old built-in limit really does let a question
#       run that long. This is the half people forget, and it is the whole point.

# ⑨ Unknown metadata keys are ignored, not rejected.
curl -s $AGENT/execute -H 'Content-Type: application/json' -d '{
  "message": "hello",
  "metadata": {"trace_data": {"trace_id": "t6"}, "timeout_s": 30,
               "something_we_added_later": {"a": 1}}
}' | jq -e '.content | type == "string"'
```

**④ and ⑤ together are the most important checks here.** They are the only ones
that prove the override is applied rather than accepted-and-discarded, and the
symptom of getting that wrong is an optimization run that completes successfully
and means nothing.

---

## 10. A minimal reference implementation

FastAPI, for illustration. It is complete enough to pass §9 and small enough to
read in one sitting; the parts specific to your agent are marked.

```python
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "./skills"))
MAX_TIMEOUT_S = float(os.environ.get("MAX_TIMEOUT_S", "1800"))
DEFAULT_TIMEOUT_S = float(os.environ.get("DEFAULT_TIMEOUT_S", "120"))


def read_skills(root: Path) -> dict[str, str]:
    """Every file under `root`, as {path relative to root: text}."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            out[path.relative_to(root).as_posix()] = path.read_text("utf-8")
        except UnicodeDecodeError:
            continue  # skip this file, never the whole request
    return out


def safe_relative(key: str) -> Path:
    if not key or key.startswith("/") or "\\" in key or "\0" in key or ".." in key:
        raise HTTPException(400, f"unsafe skill path: {key!r}")
    return Path(key)


def materialise(skills: dict[str, str]) -> Path:
    """Write an override to a private directory for one call."""
    root = Path(tempfile.mkdtemp(prefix="skills-"))
    for key, text in skills.items():
        target = (root / safe_relative(key)).resolve()
        if not target.is_relative_to(root.resolve()):
            shutil.rmtree(root, ignore_errors=True)
            raise HTTPException(400, f"unsafe skill path: {key!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, "utf-8")
    return root


@app.get("/skills")
def get_skills():
    try:
        skills = read_skills(SKILLS_DIR)
    except OSError as exc:
        # Loud, never an empty map: "no skills" and "broken" must differ.
        raise HTTPException(500, f"could not read {SKILLS_DIR}: {exc}") from exc
    return {"version": current_version(), "skills": skills}


class ExecuteRequest(BaseModel):
    message: str
    metadata: dict = {}
    model_config = {"extra": "allow"}  # tolerate keys added later


@app.post("/execute")
async def execute(req: ExecuteRequest):
    trace_data = req.metadata.get("trace_data") or {}
    budget = min(
        float(req.metadata.get("timeout_s") or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S
    )

    # `is not None`, not truthiness: {} means "no skills for this call".
    override = req.metadata.get("skills")
    root, temporary = SKILLS_DIR, False
    if override is not None:
        root, temporary = materialise(override), True

    try:
        answer = await run_agent(                       # <- your agent
            req.message,
            skills=read_skills(root),
            timeout_s=budget,
            # Use the caller's id. Generating your own breaks every feature
            # that reads the trace afterwards.
            trace_id=trace_data.get("trace_id"),
            session_id=trace_data.get("session_id"),
            user_id=trace_data.get("user_id"),
            tags=trace_data.get("tags") or [],
        )
    except TimeoutError as exc:
        raise HTTPException(504, f"exceeded the {budget}s budget") from exc
    finally:
        if temporary:
            shutil.rmtree(root, ignore_errors=True)

    answer = (answer or "").strip()
    if not answer:
        raise HTTPException(500, "the agent produced no answer")
    return {"content": answer}


def current_version() -> str:                            # <- your versioning
    """Anything that changes when your behaviour changes: a commit hash, a
    hash of your effective config, a deploy id. Never a constant."""
    return os.environ.get("AGENT_VERSION", "")
```

Note what this does **not** do, on purpose: it never writes an override back to
`SKILLS_DIR`, it never lets one request's temporary directory outlive that
request, and it never returns an empty skills map to signal a failure.
