# Agent Server API

**What your agent server must expose to be evaluated, explored and optimised by
Skill Studio.**

This document is self-contained. You do not need to know anything about Skill
Studio to implement against it, and you should not need to read any other file in
this repository. If something here is ambiguous, that is a bug in this document —
please say so rather than guessing.

**Two endpoints, and only the first is required.**

```
POST {chat endpoint}     answer one question   (OpenAI chat completions)
GET  {skills endpoint}   list your skill files (optional)
```

They are two **absolute URLs**, entered separately in Skill Studio. Nothing is
appended to a base URL and no path is imposed: your chat endpoint may sit under
any prefix, behind any gateway, on a different host from your skills endpoint.

**Start with the chat endpoint you already have.** If your agent is fronted by an
OpenAI-compatible chat completions endpoint — which most are — you can be
evaluated today, and read the rest of this when you want more.

---

## Table of contents

1. [What each endpoint unlocks](#1-what-each-endpoint-unlocks)
2. [Vocabulary](#2-vocabulary)
3. [Chat endpoint](#3-chat-endpoint)
4. [Skills endpoint](#4-skills-endpoint)
5. [The skills override, in detail](#5-the-skills-override-in-detail)
6. [Path safety](#6-path-safety)
7. [The version string](#7-the-version-string)
8. [Authentication](#8-authentication)
9. [Errors, and what each one causes](#9-errors-and-what-each-one-causes)
10. [The probes you will see in your logs](#10-the-probes-you-will-see-in-your-logs)
11. [Acceptance checklist](#11-acceptance-checklist)
12. [A minimal reference implementation](#12-a-minimal-reference-implementation)
13. [Migrating from the previous protocol](#13-migrating-from-the-previous-protocol)

---

## 1. What each endpoint unlocks

Skill Studio works with what you give it, and says what you are missing rather
than refusing to start.

| You implement | You get |
|---|---|
| Chat endpoint | **Evaluation** — run an eval set against your agent and see the score, the answers and the grader's verdicts. |
| ＋ Skills endpoint | **Playground** (view and edit your skill files, ask one question at a time), skill-coverage warnings, and staleness detection. |
| ＋ Skills override applied<br>＋ Trace id reused | **Optimization** — hundreds of rollouts against candidate versions of a skill, scored and compared. |

The last row is not a third endpoint. It is two behaviours of the chat endpoint
described in §5 and §3.4, and Skill Studio checks both before it lets an
optimization run start.

Authentication is not in this table because it unlocks nothing: a server that
requires a credential and one that requires none are equally usable here. See
§8 if yours sits behind a gateway.

---

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **agent server** | Your service. It hosts an agent that answers questions. |
| **skill** | A **directory** of instructions your agent can load — typically `SKILL.md` plus reference files beside it. Not a single string. |
| **skill file** | One file inside a skill directory, addressed by a path relative to the root of your skills directory, e.g. `billing/references/refunds.md`. |
| **workspace** | All your skill files together, as one flat `{path: text}` map. |
| **override** | A set of skill files supplied with a single chat call, to be used **for that call only**. |
| **trace** | The record of what your agent did, written to Langfuse. Skill Studio reads it back to show the developer each step. |

---

## 3. Chat endpoint

Answer one question. This is a **single-shot** endpoint — there is no
conversation, no session to keep, and no state carried between calls.

It is an ordinary OpenAI chat completions endpoint. Everything Skill Studio needs
beyond the standard rides in one extra top-level key, `skill_studio`.

### Request

```jsonc
{
  "model": "default",
  "messages": [
    {"role": "user",
     "content": "What was ACME's outstanding balance at the end of Q2?"}
  ],
  "stream": false,
  "skill_studio": {
    "timeout_s": 115.0,
    "trace_data": {
      "trace_id": "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",
      "session_id": "9f3e11c8a2b04d7e8c1f5a6b7d8e9f01",
      "user_id": "alice",
      "tags": ["eval_billing"]
    },
    "skills": {
      "billing/SKILL.md": "---\nname: billing\n---\n# Billing\n1. ...",
      "billing/references/refunds.md": "# Refund rules\n..."
    }
  }
}
```

| Field | Type | Always sent | Meaning |
|---|---|---|---|
| `model` | string | yes | A constant, `"default"`. **You may ignore it.** It is sent because the OpenAI request schema requires it, and a gateway in front of you will reject a request without one. |
| `messages` | array | yes | Exactly one `user` message. No system message: your prompt is yours. |
| `stream` | bool | yes | Always `false`. |
| `skill_studio.timeout_s` | float | yes | Your budget for this call, in seconds. See §3.3. |
| `skill_studio.trace_data.trace_id` | string | yes | **Use this as your Langfuse trace id.** See §3.4. |
| `skill_studio.trace_data.session_id` | string | yes | Currently the same value as `trace_id`. Pass through to Langfuse. |
| `skill_studio.trace_data.user_id` | string | yes | Who or what triggered the call. Pass through to Langfuse. |
| `skill_studio.trace_data.tags` | string[] | yes | Labels for the trace. May be empty. Pass through to Langfuse. |
| `skill_studio.skills` | object | **no** | Skill files to use for this call only. See §5. |

**Ignore any key you do not recognise.** Skill Studio may add fields; a server
that rejects unknown keys will break on the next release.

### 3.1 Why `skill_studio` and not `metadata`

OpenAI's own `metadata` field is specified as at most 16 string→string pairs of
512 characters. A skill file does not fit, and a strict gateway rejects it
outright. A namespaced top-level key beside `messages` is the conventional
alternative — it is exactly what the OpenAI SDKs' `extra_body` parameter flattens
into — and keeping everything under one key means a gateway that filters unknown
fields has one thing to allow rather than three.

If you use the OpenAI SDK as a client elsewhere, note that `extra_body` is a
parameter name, not a wire field: the JSON on the wire has `skill_studio` at the
top level, as above.

### 3.2 Response

An ordinary chat completion:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "choices": [
    {"index": 0,
     "message": {"role": "assistant",
                 "content": "ACME's outstanding balance at the end of Q2 was $42,180.00."},
     "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280}
}
```

The answer is read from `choices[0].message.content`. It may be a string or the
content-parts array (`[{"type": "text", "text": "..."}]`), in which case the text
parts are concatenated.

* `finish_reason: "length"` — the answer is **accepted and graded**, and marked
  as truncated. It is not a failure.
* `usage` — recorded if you send it. Optional.
* `id` — currently ignored.

**Never return an empty or whitespace-only answer, and never `content: null`
with only tool calls.** An empty answer is treated as a failure, not as a wrong
answer — grading `""` would produce a meaningless verdict and hide the real
problem. If you have nothing to say, return a 5xx with a reason.

**A body that is not JSON and opens with `<` is rejected**, because a proxy or
framework error page returning HTTP 200 is not your agent answering, and if it
were accepted the platform's LLM judge would grade the markup and record a
confident wrong verdict against you. (A JSON body is never subject to this — if
you deliberately answer with markup, put it in `content` and it passes through
untouched.)

### 3.3 `timeout_s` is your budget for this call

`skill_studio.timeout_s` replaces whatever fixed limit you would otherwise apply
to one call.

* **Honour it.** A server that ignores it and keeps its own hard-coded limit
  makes the platform's timeout setting one-directional: lowering it works,
  raising it does nothing, and long questions can never finish.
* **Still clamp it to a ceiling of your own** (say a `MAX_TIMEOUT_S` env var).
  The point of this field is to make your limit *adjustable*, not to remove it.
  Callers legitimately send 600 or 1800; a hung request with no ceiling occupies
  a worker forever.
* **On expiry, return 504 (or another 5xx) with a reason.** Do not silently
  return a truncated answer.
* If the field is absent, fall back to your own default.

The value you receive is already **5 seconds less** than the platform's own wait,
so under normal conditions **you expire first**. That margin exists precisely so
your 5xx has time to reach the wire; if the platform gives up first it sees a
dropped connection, which is far less informative than your error.

> **Note:** a 5xx fails that question and is **not retried** — retrying an
> already-timed-out call twice more just spends three times as long reaching the
> same answer. Be aware that **a refused or reset connection is not retried
> either** today: only the platform's own timeout is. Do not size a restart or
> redeploy window on the assumption that brief unavailability is absorbed —
> every question in flight will fail on the first refusal.

### 3.4 The trace id is load-bearing

Skill Studio mints `trace_id` before calling you and then uses it to find your
trace in Langfuse afterwards. **If you generate your own trace id instead, every
feature that reads a trace stops working** — the step-by-step view, the failure
diagnosis, and the optimizer's reflection stage all go dark, while the chat
endpoint itself keeps looking perfectly healthy. **Optimization will not start
at all**, because the check that proves a candidate skill reached your model is
a read of that trace.

Apply it as the trace id on the trace you write for this call. Nothing else is
required of your Langfuse usage: whatever you already log — generations, tool
calls, their inputs and outputs — is what the developer will see.

---

## 4. Skills endpoint

Report the skill files you are currently running with. No parameters.

**Optional.** Without it, evaluation runs normally; the playground, the
skill-coverage warning and optimization are what go without.

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
| `version` | string | no | Changes whenever your behaviour changes. See §7. |

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
  configuration: evaluation runs against it normally, and the playground opens
  with an empty file list you can still add to.

If you cannot read your own skills directory, return **5xx with the reason**.
Never return `{"skills": {}}` to mean "something went wrong" — "this agent has no
skills" and "this agent is broken" must stay distinguishable, or a developer will
conclude their text vanished and retype it from memory.

---

## 5. The skills override, in detail

When `skill_studio.skills` is present on a chat call, use exactly those files for
that call instead of your own.

**This is the mechanism the whole optimization feature rests on.** An
optimization run answers hundreds of questions with candidate versions of a skill
and compares the results. If you ignore this field, every rollout answers from
your deployed files, the accuracy curve sits flat, and the run produces nothing —
so this is worth getting right.

### 5.1 Three distinct states

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
if payload["skill_studio"].get("skills"):
    ...

# ✅ right
skills = payload["skill_studio"].get("skills")
if skills is not None:
    ...
```

### 5.2 Replacement, never a patch

The map is the **complete** file set for the call:

* A path in the map — use the supplied text, even if you have a file there.
* A path **not** in the map — that file **does not exist for this call**, even if
  it is on your disk.

To be explicit, because the wording invites a much worse reading: **this is not a
deletion.** Nothing on your disk changes. The call simply sees a different set of
files. Writing the supplied files to a per-request temporary directory and
pointing that one call at it is the usual approach; so is keeping them in memory
if your agent can load skills from a map.

Replacement rather than merging is what makes "does it still answer without this
reference file?" expressible at all — a merge could never remove anything. It is
also what lets an optimization run send *one* skill and measure that skill alone,
rather than that skill mixed with everything else you have deployed.

### 5.3 Never persist it

The override applies to one call, in isolation:

* Do not write it into your real skills directory.
* Do not let it affect any other in-flight or subsequent request.
* Concurrent calls with different overrides must not see each other's files.

Skill Studio sends overrides constantly while a developer iterates, and it
assumes your deployed agent is never disturbed by any of it.

---

## 6. Path safety

The keys of `skills` are attacker-influenced strings that many implementations
will turn into filesystem paths. Reject the whole request with **400** if any
key:

* contains `..`
* starts with `/`
* contains a backslash
* contains a NUL byte
* is empty

Then resolve `temp_dir / relative_path` to an absolute path and confirm it is
**still inside** `temp_dir`. If not, 400.

---

## 7. The version string

`version` in the skills response is optional but **strongly recommended**.

**What it means:** the string changes whenever anything that would change your
answers changes. Not only skill file edits — a model swap, a system-prompt
change, a temperature change, a redeploy. It is opaque to Skill Studio; a git
commit hash plus a dirty marker (`a1b2c3d-dirty.9f3e11c`) is a good choice, and
so is a hash over your whole effective configuration.

**What it is used for:**

* **Playground staleness.** Before each question, Skill Studio re-reads the
  version. If it moved since the editor loaded your files, the developer is asked
  whether to reload or send anyway — because a question answered against a skill
  that changed underneath them is not a result they can trust, and there would be
  no way to tell afterwards.
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

## 8. Authentication

**Nothing in this document requires your server to authenticate anything.** A
server that accepts any request is a supported server, it passes every case in
the acceptance checklist, and it is what most agents behind this platform are.
Skip this section entirely if that is you.

What the platform can do is *send* a credential, so that an agent sitting behind
a gateway is reachable at all. Whether that credential is demanded, ignored, or
never sent is your decision and the platform has no opinion about it.

### What gets sent

A developer may enter an API key beside the two URLs (or save one as a personal
default). When they have, every request carries one header:

```http
Authorization: Bearer <key>
```

If your gateway wants a different header, they name it — `X-Api-Key` — and the
key is sent as that header's value with **no** `Bearer` prefix.

With no key entered, no such header is sent at all. Not an empty one: the
request is byte for byte what it was before this existed.

### Where it goes

The key is entered against the **chat endpoint**, and it is sent to the skills
endpoint only when that is the same server — same scheme, host and port. A
skills endpoint on another host gets no credential, and the developer is told
so on screen rather than discovering it as a 401.

If your two endpoints are on different hosts and both need authentication, put
them behind one address; there is deliberately no second key field.

The same rule survives a redirect. If your endpoint answers `302` to another
host, the platform follows it — the request is retried there, but **without the
credential**. A credential goes to the address it was entered for and nowhere
else, and a `Location` header is not a place to decide that from. A redirect
within your own origin keeps it, as does the plain `http://` → `https://`
upgrade of the same host.

### What to return when a credential is missing or wrong

**401** or **403**, with an OpenAI error envelope if you have one. The platform
recognises both and turns them into a sentence naming what to do — "this agent
server requires a credential and none was sent", or "the API key configured for
this agent was refused" — rather than showing a bare status code beside a URL.

That is the whole reason to prefer 401 over, say, a 200 carrying a refusal in
the text: the second reads to this platform as an agent that answered, and the
answer gets graded.

---

## 9. Errors, and what each one causes

Skill Studio never guesses what went wrong — it shows the developer your status
code and the beginning of your response body. If you answer with an OpenAI error
envelope, the sentence inside it is what gets shown:

```json
{"error": {"message": "This model's maximum context length is 8192 tokens.",
           "type": "invalid_request_error"}}
```

### Chat endpoint

| You return | What happens |
|---|---|
| 200 + a chat completion with text | The answer is graded. |
| 200 + `content: null`, or only tool calls | The question fails: "not a chat completion carrying a text answer". |
| 200 + empty/whitespace `content` | The question fails: "empty answer". |
| 200 + no `choices` | The question fails, with your body quoted. |
| 200 + a body starting with `<` | The question fails: "markup, not a chat completion". |
| **401 / 403** | The question fails, and the check that reported it says a credential is missing or was refused (§8). **Not retried.** |
| **4xx** (other) | The question fails immediately, carrying your status and message. **Not retried** — a bad request fails identically every time. |
| **5xx** | The question fails, carrying your status and message. **Not retried.** |
| Connection refused / reset | Fails the question. **Not** retried — see the note in §3.3. |
| The platform's own timeout elapses | **Retried** with exponential backoff, then failed. |

### Skills endpoint

| You return | What happens |
|---|---|
| 200 + a valid body | Normal operation. |
| 200 + `skills` that is not a `{string: string}` map | Hard error naming the offending entry. Not treated as empty. |
| 200 + a non-JSON body | Hard error quoting the body. |
| **401 / 403** | The check fails and says a credential is missing or was refused (§8). Note that the key is only sent here when this endpoint is on the same host as the chat endpoint. |
| **any other 4xx/5xx** | The check fails, carrying your status and body. Evaluation still runs; the playground will not connect and optimization will not start. |
| Unreachable | Same, naming the URL tried. |
| **Not configured at all** | Not an error. Evaluation runs; the rest is unavailable and says so. |

---

## 10. The probes you will see in your logs

Skill Studio sends a small number of synthetic calls. They are deliberate. If you
see one and assume the platform is sending you corrupt data, you will go looking
for a bug that does not exist.

### 9.1 The connection probe

Sent when a developer presses **Test endpoint**, on the way to starting a run, or
when the playground connects. One call, carrying a skills override with a single
file:

```
skill_studio_probe/SKILL.md
```

and a question asking for the "magic value" that file contains. The value is a
random token, different every time — a constant would eventually be hard-coded to
make the check pass, and a check that can be satisfied without reading the file
we just sent is not a check.

**What it proves:** that your chat endpoint answers, and that the override
reached your model. If you answer but the token is not in your reply, Skill
Studio says so without accusing you of anything specific — a refusal, an unloaded
tool and a prompt pipeline that strips the file's contents all produce the same
symptom.

### 9.2 The optimization pre-flight

Before an optimization run starts, **one** call is sent that looks slightly odd:

1. The question has a sentence appended:
   `(you must first read the billing skill)`
2. The `skills` map contains one extra line, inserted as the first line of the
   skill's body, right after the YAML frontmatter:
   `<!-- probe-8f3a91c2d4e6: platform override check, ignore this line -->`

**Why.** Skill Studio needs to know whether you actually applied
`skill_studio.skills` — and it cannot tell from the answer, because a candidate
skill is usually a light edit of the file you already have, so both produce nearly
identical traces. The marker exists only in the copy that was sent. If it turns up
in the trace, the override reached the model; if the trace shows the skill being
read and the marker is *not* there, you are answering from your own copy and the
run is stopped rather than spending an hour producing a flat line.

The appended sentence is there so the file's contents land in the trace whether
you inject skills into a prompt or read them with a tool — a tool result comes
back into the conversation either way.

This happens on one call per optimization run — twice at most, since a run
interrupted before its first scored step re-probes when it resumes. Every scored
rollout carries the candidate text and the unmodified question, because anything
else would be a second variable in the measurement.

Treat the marker as what it says it is: a comment, to be ignored.

> ⚠️ **One thing to know if you render markdown into your prompt.** The marker is
> an HTML comment, and it is the *only* difference between the copy we send and
> the copy you already have — the pre-flight deliberately sends your own files so
> that nothing else varies. If your pipeline strips comments while building the
> prompt, the marker vanishes and an override you applied correctly looks like one
> you ignored, which stops the run. If you see that accusation and believe it is
> wrong, this is the first thing to check.

---

## 11. Acceptance checklist

Nothing here checks authentication. If your server requires a credential, add
`-H "Authorization: Bearer $KEY"` to every call below; if it does not, that is
not a case you can fail (§8).

```bash
CHAT=http://localhost:8080/v1/chat/completions
SKILLS=http://localhost:8080/skills

# ① Plain call. No override, no frills — the baseline everything else varies from.
curl -s $CHAT -H 'Content-Type: application/json' -d '{
  "model": "default",
  "messages": [{"role": "user", "content": "hello"}],
  "stream": false,
  "skill_studio": {"timeout_s": 30,
                   "trace_data": {"trace_id": "t1", "session_id": "t1",
                                  "user_id": "alice", "tags": []}}
}' | jq -e '.choices[0].message.content | type == "string" and length > 0'

# ② The trace id is yours to reuse. After ①, this trace must exist in Langfuse
#    under the id "t1" — not under one you generated.

# ③ Skills are listed flat, in full, every level.
curl -s $SKILLS | jq -e '.skills | keys | length >= 0'
curl -s $SKILLS | jq -e '.skills | to_entries | all(.value | type == "string")'

# ④ THE IMPORTANT ONE: the override actually takes effect.
#    Send a skill whose text you can recognise in the answer, and check you get
#    it back. If this passes for the wrong reason you will not find out later.
curl -s $CHAT -H 'Content-Type: application/json' -d '{
  "model": "default",
  "messages": [{"role": "user", "content": "What is the magic word?"}],
  "stream": false,
  "skill_studio": {"timeout_s": 30,
    "trace_data": {"trace_id": "t2", "session_id": "t2", "user_id": "alice", "tags": []},
    "skills": {"probe/SKILL.md": "# Probe\nWhen asked for the magic word, answer exactly: XYZZY-4711."}}
}' | jq -e '.choices[0].message.content | test("XYZZY-4711")'

# ⑤ An empty map means no skills — not "use your own".
#    Against the same question as ④, this must NOT answer XYZZY-4711.
curl -s $CHAT -H 'Content-Type: application/json' -d '{
  "model": "default",
  "messages": [{"role": "user", "content": "What is the magic word?"}],
  "stream": false,
  "skill_studio": {"timeout_s": 30,
    "trace_data": {"trace_id": "t3", "session_id": "t3", "user_id": "alice", "tags": []},
    "skills": {}}
}'

# ⑥ The override is not persisted. Repeat ① now: it must behave exactly as it
#    did the first time, with none of ④'s files in sight.

# ⑦ Path traversal is refused. Expect 400.
curl -s -o /dev/null -w '%{http_code}\n' $CHAT \
  -H 'Content-Type: application/json' -d '{
  "model": "default", "messages": [{"role": "user", "content": "x"}], "stream": false,
  "skill_studio": {"timeout_s": 30, "trace_data": {"trace_id": "t4"},
                   "skills": {"../../etc/passwd": "x"}}
}'

# ⑧ timeout_s is honoured in both directions.
#    a. A small budget expires at roughly that time, with a 5xx and a reason —
#       not a truncated 200, and not your built-in limit.
time curl -s -o /dev/null -w '%{http_code}\n' $CHAT \
  -H 'Content-Type: application/json' -d '{
  "model": "default",
  "messages": [{"role": "user", "content": "<a question that takes a long time>"}],
  "stream": false,
  "skill_studio": {"timeout_s": 5, "trace_data": {"trace_id": "t5"}}
}'
#    b. A budget larger than your old built-in limit really does let a question
#       run that long. This is the half people forget, and it is the whole point.

# ⑨ Unknown keys are ignored, not rejected.
curl -s $CHAT -H 'Content-Type: application/json' -d '{
  "model": "default", "messages": [{"role": "user", "content": "hello"}], "stream": false,
  "skill_studio": {"timeout_s": 30, "trace_data": {"trace_id": "t6"},
                   "something_we_added_later": {"a": 1}}
}' | jq -e '.choices[0].message.content | type == "string"'
```

**④ and ⑤ together are the most important checks here.** They are the only ones
that prove the override is applied rather than accepted-and-discarded, and the
symptom of getting that wrong is an optimization run that completes successfully
and means nothing.

---

## 12. A minimal reference implementation

FastAPI, for illustration. It is complete enough to pass §11 and small enough to
read in one sitting; the parts specific to your agent are marked.

```python
import os
import shutil
import tempfile
import time
import uuid
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
    """Write an override to a private directory for one call.

    The whole body is wrapped, not just the containment check: `safe_relative`
    raises too, and checklist item ⑦ drives exactly that path. Cleaning up on
    only one of the two rejection branches leaks a directory per hostile
    request — attacker-influenced and unbounded.
    """
    root = Path(tempfile.mkdtemp(prefix="skills-"))
    try:
        for key, text in skills.items():
            target = (root / safe_relative(key)).resolve()
            if not target.is_relative_to(root.resolve()):
                raise HTTPException(400, f"unsafe skill path: {key!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, "utf-8")
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


@app.get("/skills")
def get_skills():
    try:
        skills = read_skills(SKILLS_DIR)
    except OSError as exc:
        # Loud, never an empty map: "no skills" and "broken" must differ.
        raise HTTPException(500, f"could not read {SKILLS_DIR}: {exc}") from exc
    return {"version": current_version(), "skills": skills}


class ChatRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    stream: bool = False
    skill_studio: dict = {}
    model_config = {"extra": "allow"}  # tolerate keys added later


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    ss = req.skill_studio or {}
    trace_data = ss.get("trace_data") or {}
    budget = min(float(ss.get("timeout_s") or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S)

    # Single-shot: the platform sends exactly one user message.
    question = next(
        (m.get("content") for m in reversed(req.messages) if m.get("role") == "user"),
        "",
    )

    # `is not None`, not truthiness: {} means "no skills for this call".
    override = ss.get("skills")
    root, temporary = SKILLS_DIR, False
    if override is not None:
        root, temporary = materialise(override), True

    try:
        answer = await run_agent(                       # <- your agent
            question,
            skills=read_skills(root),
            timeout_s=budget,
            # Use the caller's id. Generating your own breaks every feature
            # that reads the trace afterwards, and blocks optimization outright.
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

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model or "default",
        "choices": [
            {"index": 0,
             "message": {"role": "assistant", "content": answer},
             "finish_reason": "stop"}
        ],
    }


def current_version() -> str:                            # <- your versioning
    """Anything that changes when your behaviour changes: a commit hash, a
    hash of your effective config, a deploy id. Never a constant."""
    return os.environ.get("AGENT_VERSION", "")
```

Note what this does **not** do, on purpose: it never writes an override back to
`SKILLS_DIR`, it never lets one request's temporary directory outlive that
request, and it never returns an empty skills map to signal a failure.

It also has **no authentication**, which is not an omission — see §8. If yours
does, the whole of it is one dependency on both routes:

```python
from fastapi import Depends, Header

API_KEY = os.environ.get("API_KEY", "")


def require_key(authorization: str = Header(default="")):
    """401 when a credential is missing or wrong, so the platform can say which.

    Answering 200 with a refusal in the text instead reads to Skill Studio as
    an agent that answered, and the refusal gets graded as the answer.
    """
    if not API_KEY:
        return                                  # open server; a supported choice
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "missing or invalid credential")


# then: @app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
```

---

## 13. Migrating from the previous protocol

The previous contract was `POST {base}/execute` plus `GET {base}/skills`, both
derived from one base URL. Nothing about what your agent *does* changes — only
where the fields are.

| Before | Now |
|---|---|
| `POST {base}/execute` | `POST {chat endpoint}`, an absolute URL you choose |
| `GET {base}/skills` | `GET {skills endpoint}`, an absolute URL you choose, now optional |
| `message` | `messages[0].content` (role `user`) |
| `metadata` | `skill_studio` |
| `metadata.skills` | `skill_studio.skills` — **identical contents and semantics** |
| `metadata.timeout_s` | `skill_studio.timeout_s` — identical |
| `metadata.trace_data` | `skill_studio.trace_data` — identical |
| `{"content": "..."}` | `{"choices": [{"message": {"content": "..."}}]}` |
| A bare JSON string reply | No longer accepted |
| A `text/plain` reply | No longer accepted |
| (nothing) | An optional `Authorization: Bearer` header, when the developer configures one — see §8. Servers that need no credential are unaffected. |

In practice: rename `metadata` to `skill_studio`, read the question out of
`messages` instead of `message`, and wrap your answer in a chat completion. The
whole of §5, §6 and §7 is unchanged.

Two things Skill Studio no longer accepts are the two lax reply shapes. They
existed because the old protocol had no standard to point at; this one does, and
every extra accepted shape is a way for a gateway's stray response to be graded
as though your agent had answered it.
