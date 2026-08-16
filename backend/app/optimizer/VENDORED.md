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

### The two epoch-boundary files, and the decision this port had to make

`slow_update.py` and `meta_skill.py` are reachable, off by default, and driven
from `app/optimizer/longitudinal.py` — the `slow_update` and `meta_skill` keys
in a run's config turn them on. They run once per epoch boundary, never per step.

**The comparison set is the validation split, and that was ours to choose.**
Upstream re-rolls a fixed sample of twenty tasks under each epoch's skill; this
loop cannot, because a training minibatch is a different draw of questions every
step, and comparing two different sets of questions would attribute the
difference between the *questions* to the difference between the *skills* —
which is the one thing the pass exists to measure. The validation split is the
only set answered under every skill a run produces, and it is already rolled out
at every step, so the comparison costs no extra agent calls. Which two rollouts
to compare falls out of `parent_step_no`: the last accepted step of each epoch is
the step whose validation rollout measured the skill that epoch ended on. An
epoch that accepted nothing ends on the skill it began with, and the boundary is
skipped rather than asking the optimizer to explain a change that did not happen.

Two consequences worth knowing when reading the diff:

* **The skill changes between steps**, so the boundary records a second snapshot
  against the last accepted step, `kind="slow_update"`. `GET .../steps/{n}/skill`
  resolves a `parent` base to that kind first — otherwise the next step's diff
  would show the guidance block as its own edit, which is the misattribution
  `parent_step_no` exists to prevent, arriving by another route.
* **Trajectories are not fed in.** `build_comparison_pairs` can read them from a
  `rollout_dir` of `conversation.json` files — upstream's checkpoint world, which
  this port replaced with database rows. Both directory arguments are left at
  `""`, so the trajectory fields are empty and the pairs carry the item, both
  results and the change category. Feeding real ones means a fork like
  `reflect.py`'s, and the pass works without it.

The protected block is defended from both directions.
`_strip_slow_update_markers` stops a step-level analyst forging the block by
writing `<!-- SLOW_UPDATE_START -->` into its own `content`, and `_marker_spans`
— which `_protected_spans` includes for every mode — makes an edit whose target
falls inside the block fail with `skipped_protected_region`. Both are tested in
`test_optimizer_skill_ops.py`.

`meta_skill` is the quieter of the two and is **never written into the skill**:
it is optimizer-side memory about how editing has been going, threaded into the
analyst prompt on later steps through `run_update_stage(meta_skill_context=…)`.
It lives for the length of a run. Carrying it across runs would need a table, and
there is not one.

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

**The analyst entry points are vendored but no longer called**, and neither is
`run_minibatch_reflect`. `_split_minibatches` and `_shuffle_for_minibatch` still
are, from `app/optimizer/update.py`. The whole file stays so the diff against
upstream stays honest.

What replaced them is `app/optimizer/analyst.py`, which builds the same prompt —
same system prompts, same section order, same JSON contract, same clip to the
edit budget — with three differences that are ours and could not be made from
outside the file:

* **The trajectory is folded first** (`app/optimizer/trajectory.py`). Upstream's
  trajectories are step records; ours are Langfuse spans, and each span is a
  whole chat-completions request. Formatted span by span they repeat the tool
  catalogue and the system prompt — which carries the skill — once per step, and
  the message history quadratically. That, not the size of the model, is what
  made analyst calls overflow a 100k-token context window; the truncation
  cascade could not fix it, because it refuses (rightly) to cut the system
  message the skill lives in, and there were N uncuttable copies of it.
* **The agent's own answer is shown.** The item always carried it and the
  formatter never rendered it.
* **`Task type` is gone and `Hidden Reference` is `Ground-truth Response`.** The
  first is always empty here — upstream's benchmarks classify tasks, ours do
  not. The second is a term no analyst prompt uses; the prompts talk about being
  shown the correct answers, so the heading now says that.

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
