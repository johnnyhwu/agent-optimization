# Vendored SkillOpt

**Upstream:** https://github.com/microsoft/SkillOpt
**Pinned commit:** `0f76ab4c1d5f3b01c47fa7b4926015389aab3748` (2026-08-12)

Everything in `vendor/` came from that commit. The point of vendoring rather
than reimplementing is that **"did we change the algorithm?" has a mechanical
answer** — `diff` against upstream. The point of vendoring rather than
`pip install skillopt` is that upstream's trainer is synchronous, keeps its state
in a directory of files, reports progress to stdout, and treats a skill as a
single string; this backend is async, keeps state in Postgres, reports over SSE,
and treats a skill as a directory. Bridging four impedance mismatches through a
subprocess is more code and less observable than replacing the ~300 lines of
loop orchestration while keeping the algorithm modules intact.

## Rule

Do not improve the algorithm in here. If something needs to change, change it
outside `vendor/` and call in. Every difference below is either forced by our
runtime or is the multi-file fork, and each one has a reason written next to it.

## Checking the diff

```sh
git clone --depth 1 https://github.com/microsoft/SkillOpt /tmp/skillopt
cd /tmp/skillopt && git checkout 0f76ab4
diff /tmp/skillopt/skillopt/evaluation/gate.py        backend/app/optimizer/vendor/gate.py
diff /tmp/skillopt/skillopt/optimizer/scheduler.py    backend/app/optimizer/vendor/scheduler.py
diff /tmp/skillopt/skillopt/optimizer/update_modes.py backend/app/optimizer/vendor/update_modes.py
diff /tmp/skillopt/skillopt/optimizer/skill_aware.py  backend/app/optimizer/vendor/skill_aware.py
diff /tmp/skillopt/skillopt/utils/json_utils.py       backend/app/optimizer/vendor/json_utils.py
# the import-only forks: the diff should be the import block and nothing else
diff /tmp/skillopt/skillopt/gradient/aggregate.py     backend/app/optimizer/vendor/aggregate.py
diff /tmp/skillopt/skillopt/optimizer/clip.py         backend/app/optimizer/vendor/clip.py
diff /tmp/skillopt/skillopt/optimizer/slow_update.py  backend/app/optimizer/vendor/slow_update.py
diff /tmp/skillopt/skillopt/optimizer/meta_skill.py   backend/app/optimizer/vendor/meta_skill.py
# reflect.py is the one file upstream ships with CRLF endings; the copy is LF,
# so compare it with --strip-trailing-cr or every line reads as changed.
diff --strip-trailing-cr \
     /tmp/skillopt/skillopt/gradient/reflect.py       backend/app/optimizer/vendor/reflect.py
diff /tmp/skillopt/skillopt/prompts/__init__.py       backend/app/optimizer/vendor/prompts/__init__.py
```

As of this commit those produce: nothing for the four byte-identical files, the
import block for the five import-only forks, and ~60 lines for `reflect.py` —
all of them in the two regions described below.

## File by file

| File | Upstream path | Difference |
|---|---|---|
| `gate.py` | `skillopt/evaluation/gate.py` | **byte-identical** |
| `scheduler.py` | `skillopt/optimizer/scheduler.py` | **byte-identical** |
| `update_modes.py` | `skillopt/optimizer/update_modes.py` | **byte-identical** |
| `skill_aware.py` | `skillopt/optimizer/skill_aware.py` | one import line (a lazy `extract_json`) |
| `json_utils.py` | `skillopt/utils/json_utils.py` | **byte-identical** |
| `aggregate.py` | `skillopt/gradient/aggregate.py` | import block only |
| `clip.py` | `skillopt/optimizer/clip.py` | import block only |
| `slow_update.py` | `skillopt/optimizer/slow_update.py` | import block only |
| `meta_skill.py` | `skillopt/optimizer/meta_skill.py` | import block only |
| `reflect.py` | `skillopt/gradient/reflect.py` | import block + where a trajectory comes from |
| `prompts/__init__.py` | `skillopt/prompts/__init__.py` | the override directory — see below |
| `skill.py` | `skillopt/optimizer/skill.py` | **forked** — see below |
| `_model.py` | — | ours; the `chat_optimizer` seam |

The byte-identical files import nothing from `skillopt`, which is why they copy
cleanly. `import block only` means literally that: the module's own code is
untouched, and the diff is the five lines that redirect
`from skillopt.…` to `from app.optimizer.vendor.…`.

### Two files are vendored and deliberately not reachable

`slow_update.py` and `meta_skill.py` are here, redirected and diffable, and
**nothing calls them.** There is no config key that turns either on, so the
state is "not a feature", not "a feature that silently does nothing".

That is a smaller claim than the plan's ("implemented and wired, default off")
and it is the honest one. What is actually left is this, in order of difficulty:

1. **The comparison set does not exist in our data model.** A slow update
   compares *the same samples* rolled out under the previous epoch's skill and
   under the current one (`results_prev` / `results_curr`, Markov — adjacent
   epochs only). This loop never produces that pairing: the training minibatch
   is a different draw of questions every step. The validation split *is* a
   fixed set rolled out every step, so it is the obvious candidate — but
   deciding that, and deciding which two step rows are "the epoch boundary",
   is a design choice nobody has made. It is not a wiring task.
2. **No epoch-boundary hook.** `engine.py` knows `epoch_no` but has nowhere to
   run anything when one ends.
3. **No config key**, in `OptimizationConfig` or anywhere else.
4. `inject_empty_slow_update_field` is never called, so the protected block does
   not exist on any skill this system produces.
5. Trajectories would be degraded. `build_comparison_pairs` takes
   `prev_rollout_dir` / `curr_rollout_dir` and reads `conversation.json` from
   them — upstream's checkpoint world, which this port replaced with database
   rows. Both arguments default to `""`, in which case the trajectory fields are
   simply empty strings, so this degrades rather than breaking: the comparison
   pairs still carry the item, both results and the change category.
   Feeding real trajectories means a fork like `reflect.py`'s. Worth being
   precise about — this is the *smallest* of the five, not the blocker.

`meta_skill.py` is further from reachable, not closer. `format_meta_skill_context`
is called by the vendored `reflect.py`, but `update.py` never passes a
`meta_skill_context`, so it is always `""` and always a no-op; `run_meta_skill`
is never called at all, and optimizer-side memory would need somewhere to live
across epochs — arguably across *runs*, which is a table that does not exist.

None of it would ship on, so all of it would be a code path nobody exercises. A
default-off path that has never run is not a feature in reserve; it is a
liability that looks like one.

What *is* in place is the half that would otherwise be dangerous. `skill.py`
carries `_strip_slow_update_markers`, so a step-level analyst cannot forge the
block by writing `<!-- SLOW_UPDATE_START -->` into its own `content`; and
`_marker_spans` — which `_protected_spans` includes for every mode — means that
if the block ever does exist, an edit targeting text inside it is refused with
`skipped_protected_region`. Both are tested in `test_optimizer_skill_ops.py` and
are worth having whether or not the feature ever arrives.

`skill_aware.py` is vendored although the feature it implements
(EmbodiSkill appendix notes) is off by default — `reflect.py` imports it at
module scope, and a stub would be a bigger and less honest difference than the
file itself.

### `skill.py` — the one deliberate fork

Upstream applies edits to **one string**. A skill here is a **directory**
(`billing/SKILL.md` plus everything under `billing/references/`). Four changes,
and nothing else about the update stage differs:

1. **`dict[path, str]` instead of `str`.** Every entry point takes and returns a
   mapping, and every edit carries a `path`. An edit with no `path` at all is an
   upstream-shaped edit and is applied to the entry point, with
   `path_defaulted: True` in its report so the choice is visible.
2. **`append` creates a missing file.** Upstream has no create-file operation;
   the optimizer routinely wants to split a reference file out. Reported
   distinctly (`applied_append_created_file`) so a typo'd path cannot silently
   become a new file.
3. **`path` is validated** (`_normalise_path`). It is model output that becomes
   a key in the workspace override sent to the agent server, so `../`, absolute
   paths, Windows drive letters and anything outside the skill directory are
   rejected as `skipped_invalid_path`. `docs/spec.md` §17 makes the same demand
   of the agent server; there is no reason for this end to author one either.
   An emptied `SKILL.md` is rejected for the same reason
   (`skipped_would_empty_entry_point`) — it is deletion by another name.
4. **`_PROTECTED_REGIONS` became a `Protection` parameter.** The two marker
   regions upstream named (`SLOW_UPDATE`, `APPENDIX`) are still protected exactly
   as before. The parameter adds the mode-dependent half, which upstream has no
   concept of because upstream has no routing mode:

   | mode | editable | frozen |
   |---|---|---|
   | `isolated` | body + reference files | `SKILL.md` frontmatter |
   | `routing` | `SKILL.md` frontmatter | body + every other file |

   The **append anchor still considers only the marker regions**, as upstream
   does — those sit at the document tail and an append must land before them.
   The frontmatter is a head region, so it is a veto and never an anchor.

`app/optimizer/skillio.py` builds the `Protection` for a mode; the fork itself
knows nothing about modes.

### `reflect.py` — where a trajectory comes from

Upstream's trajectories are files a local rollout wrote:
`fmt_minibatch_trajectories(items, prediction_dir)` reads
`{prediction_dir}/{task_id}/conversation.json` per item. Ours are Langfuse
traces held in memory, and they must be **truncated before they are formatted**
— otherwise the analyst prompt for a step is not a fixed cost but whatever the
chattiest trajectory in the batch happened to do, times the minibatch size.

So an item may carry its conversation inline under `"conversation"`, and the
file path is the fallback:

```python
conversation = item.get("conversation")
if conversation is None:
    ...upstream's file read, unchanged...
```

`prediction_dir` becomes optional on that function and on the two analyst entry
points, and the four optional per-item context reads (`target_system_prompt`,
`target_user_prompt`, the codex trace summary, the spreadsheet preview) are
guarded with `and prediction_dir` so they are skipped rather than joining a path
to `None`. `run_minibatch_reflect`'s signature is untouched.

Everything else — the header assembly, the two conversation formats, the budget
wording, the JSON contract — is upstream's.

**`run_minibatch_reflect` is vendored but not called.** Most of it is
resume-by-checkpoint-file, and our checkpoint is a database row; the parts we do
want (`_split_minibatches`, `_shuffle_for_minibatch`, and the two analyst
functions) are called directly from `app/optimizer/update.py`. It stays in the
file so the diff against upstream stays honest.

### `prompts/__init__.py` — the override directory

Upstream keys the prompt override on the *environment* (alfworld, searchqa, …)
and looks under `skillopt/envs/{env}/prompts/{name}.md`. There is one
environment here — an HTTP agent — and what varies instead is the optimization
**mode**: `isolated` rewrites a skill's body, `routing` rewrites its
description, and the two ask an analyst for entirely different things. The
mechanism is kept and the directory moves to `vendor/prompts/{mode}/{name}.md`.

`vendor/prompts/*.md` are upstream's, for the `patch` update mode only:
`analyst_error`, `analyst_success`, `merge_failure`, `merge_success`,
`merge_final`, `ranking`, `meta_skill`, `slow_update`. The `_rewrite` and
`_full_rewrite` variants are **not** vendored — those update modes are out of
scope (`docs/spec.md`, Optimize §11) and copying prompts for a path that cannot
be reached would only make the next person wonder which of them is live.

`vendor/prompts/isolated/` and `vendor/prompts/routing/` are ours. They say the
things upstream's generic prompts have no reason to: that an edit must name a
`path` in a directory, which half of `SKILL.md` this mode may touch, and that
copying a gold answer into the skill will be caught.

### `_model.py` — the whole LLM seam

Upstream reaches a model through exactly one import, in five files:

```
from skillopt.model import chat_optimizer
chat_optimizer(system, user, max_completion_tokens, retries, stage, timeout)
    -> (text, usage)
```

`vendor/_model.py` reimplements that signature over `Seams.optimizer`. It holds
the client in module-level state rather than passing it through, because
`reflect` and `aggregate` fan out over their own `ThreadPoolExecutor` and a
`ThreadPoolExecutor` does not propagate `contextvars` — a worker thread has no
way to ask which run it is serving. Upstream solves the same problem the same
way (`configure_azure_openai`). `use_optimizer()` holds a lock for the duration
of a stage, so two runs reflecting at once take turns rather than one silently
answering with the other's model.

## Sync inside async

The vendored code is synchronous and parallelises reflection with its own
`ThreadPoolExecutor` (`analyst_workers`). The engine therefore runs
reflect → aggregate → select → update inside `asyncio.to_thread()`, where
`chat_optimizer` uses the **synchronous** `openai.OpenAI` client (the `openai`
package is already a dependency for `AsyncOpenAI`). Rollout — the agent and
judge calls — stays async and never enters a worker thread. Nothing re-enters
the event loop from a thread, and upstream's own parallelism works untouched.
