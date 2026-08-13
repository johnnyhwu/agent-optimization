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
diff /tmp/skillopt/skillopt/evaluation/gate.py       backend/app/optimizer/vendor/gate.py
diff /tmp/skillopt/skillopt/optimizer/scheduler.py   backend/app/optimizer/vendor/scheduler.py
diff /tmp/skillopt/skillopt/optimizer/update_modes.py backend/app/optimizer/vendor/update_modes.py
```

## File by file

| File | Upstream path | Difference |
|---|---|---|
| `gate.py` | `skillopt/evaluation/gate.py` | **byte-identical** |
| `scheduler.py` | `skillopt/optimizer/scheduler.py` | **byte-identical** |
| `update_modes.py` | `skillopt/optimizer/update_modes.py` | **byte-identical** |
| `skill.py` | `skillopt/optimizer/skill.py` | **forked** — see below |

The three byte-identical files import nothing from `skillopt`, which is why they
copy cleanly.

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

## Not vendored yet

These call the model through a single upstream import
(`from skillopt.model import chat_optimizer`), so they arrive with the shim that
replaces it (`vendor/_model.py`) rather than as broken imports:

| File | Upstream path | Planned difference |
|---|---|---|
| `reflect.py` | `skillopt/gradient/reflect.py` | one import line |
| `aggregate.py` | `skillopt/gradient/aggregate.py` | one import line |
| `clip.py` | `skillopt/optimizer/clip.py` | one import line |
| `slow_update.py` | `skillopt/optimizer/slow_update.py` | one import line |
| `meta_skill.py` | `skillopt/optimizer/meta_skill.py` | one import line |

`chat_optimizer(system, user, max_completion_tokens, retries, stage, timeout)
-> (text, usage)` is the whole seam — four files, one line each.

## Sync inside async

The vendored code is synchronous and parallelises reflection with its own
`ThreadPoolExecutor` (`analyst_workers`). The engine therefore runs
reflect → aggregate → select → update inside `asyncio.to_thread()`, where
`chat_optimizer` uses the **synchronous** `openai.OpenAI` client (the `openai`
package is already a dependency for `AsyncOpenAI`). Rollout — the agent and
judge calls — stays async and never enters a worker thread. Nothing re-enters
the event loop from a thread, and upstream's own parallelism works untouched.
