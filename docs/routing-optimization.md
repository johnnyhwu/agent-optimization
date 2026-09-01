# Why routing mode departs from SkillOpt

`isolated` mode is SkillOpt, run against an HTTP agent instead of a benchmark.
`routing` mode started as the same algorithm pointed at a different field, and
that is the mistake this document records — along with what replaced it.

The code enforces the difference; it cannot explain it. Every branch below is a
`mode == "routing"` test, and every one of them looks arbitrary until you know
which failure it was written against.

## The two modes optimise different kinds of thing

|                     | `isolated`                             | `routing`                                   |
| ------------------- | -------------------------------------- | ------------------------------------------- |
| The parameter       | a skill's **body** — thousands of words | a skill's **description** — one line of YAML |
| One edit            | `append` to a section                  | `replace` of the whole line                  |
| What it is graded on| the judge's verdict on the answer      | whether the agent opened exactly the tagged skills |
| Targets per run     | one                                    | several, moved together                      |

Everything that follows is downstream of the second row. An isolated edit adds
to a long document; a routing edit *is* the parameter.

## Three arguments

### 1. Edits collide, and the stage meant to reconcile them cannot

Two isolated minibatches append to different sections. Their edits are close to
orthogonal, and `vendor/aggregate.merge_patches` combining them is real
merging — it is holding two additions to one document.

Two routing minibatches each return a `replace` of the same line. They are
mutually exclusive by construction, and merge picks between them **having been
given the edits and none of the questions behind them**. The evidence that would
decide it was discarded one stage earlier.

So routing makes exactly one analyst call per step, over the whole batch. There
is then nothing to choose between, and the choice is made where the evidence is.

This is not a saving in optimizer calls that had to be engineered for. Upstream
already short-circuits both stages for a single patch —
`_hierarchical_merge` returns a one-element list unchanged (`vendor/aggregate.py`),
`rank_and_select` returns a pool inside its budget unchanged (`vendor/clip.py`),
neither calling the model. A routing step therefore makes one optimizer call in
total and records no stage calls at all, without a skip branch anywhere.

### 2. Failures and successes are two sides of one boundary

Upstream reflects on them separately, and for a body that is right: a failure
says what to add, a success says what not to disturb. They are different
questions.

About a decision boundary they are halves of one question. The failures are the
questions that should have come in; the successes are the ones already inside
that must not be lost. Asked separately:

- the failure analyst proposes a narrowing, blind to what the narrowing breaks;
- the success analyst proposes a widening, blind to the misfires;
- merge picks one of the two full-line rewrites.

That is an oscillation generator, and it is the mechanism behind a routing run
whose score moves and never settles. Routing sends both to one analyst as a
single constrained problem: cover these, exclude those.

`failure_only` exists to withhold the successes. In this mode they are the
constraint that stops a description narrowing until it wins nothing, so the flag
is **ignored** in routing and the run overview says so rather than letting a
deployment believe it took effect.

### 3. The trajectory is not evidence about routing

A routing decision is made from descriptions alone, *before* the agent acts. The
complete observation of it is

```
(the question, the skills it was tagged for, the skills the agent opened)
```

and `reflection.analyst_item` already extracted exactly that triple. Everything
else in the old prompt — the tool catalogue, the folded conversation, the
agent's answer, the ground-truth answer — is paid for and cannot inform the
edit.

At a few thousand characters per question, that cost is what forced the
minibatch down to eight. At about a line per question, a whole training batch
fits in less than one of the old eight-trajectory prompts. **Argument 3 is what
makes arguments 1 and 2 affordable**; without it, "one call over the whole
batch" is a prompt that does not fit.

A side effect worth naming: routing sends no gold answers, so the answer-leak
surface is gone from this path rather than merely guarded.

## What the analyst reads

`app/optimizer/routing_digest.py`, in the order the prompt carries them.

### The agent's setup, once, frozen

```
## The agent's setup (FROZEN — you cannot change this)
You are a support agent for Acme Cloud.
«varies (100 distinct values across 100 runs), e.g. "Today is 2026-08-30 14:02 UTC" / …»
Consult the skills below when one applies.
...
```

A routing failure caused by the agent's own instructions — "answer directly; do
not open a skill" — is indistinguishable from one caused by a bad description
until you can read the instructions. Without this the analyst rewrites
descriptions forever against a cause no description controls.

It could not simply be hoisted. `trajectory.shared_preamble` requires **exact**
equality across the batch and a single injected timestamp defeats it, so
`system_prompt_view` folds the variants with a line-level LCS
(`skillio._opcodes`, applied pairwise) and marks what varied. Three outcomes:

| condition | result |
| --- | --- |
| one distinct prompt | printed verbatim |
| common lines ÷ longest ≥ `SIMILARITY_FLOOR` (0.7) | shared lines verbatim, differing runs replaced by a `«varies»` marker with up to two samples |
| below the floor | **no splice.** The majority variant whole, labelled as a stand-in, and `diverged` set |

The third case is the one worth defending. A prompt assembled from lines that
never appeared together is a document no question was answered under, and an
analyst reasoning from it is reasoning about a system that does not exist. When
it happens the step really did average two systems into one routing accuracy, so
it is recorded (`optimization_steps.setup_divergence`) and raised on the run
overview — the same class of finding as `workspace-drift`.

Tool catalogues are compared name-sorted: a server returning its tools in a
different order each call is not an agent that was told something different.

### The confusion matrix

```
## Routing Results (168 questions, 160 measured, 71% routed exactly right)

### billing — 42 questions tagged · reached by 31 of the 41 measured (76%) · 1 not measured
✓ opened billing (31)
- 退款要多久才會入帳？
✗ opened reporting instead (8)
- 上個月的扣款明細在哪裡看？
✗ opened nothing at all (3)
· not measured (no trace landed) (1)

### Misfired into billing — 8 questions tagged elsewhere opened it
← tagged reporting (8)
```

Grouped rather than listed, because the grouping *is* the finding: a blurred
boundary between two descriptions is a visible block rather than a pattern to
infer from a hundred rows.

Five decisions inside it:

- **Two different percentages, labelled apart.** The header carries the *gated*
  metric — the exact set match `routing.py` scores — over measured questions
  only. Each skill's heading carries how many of its own questions reached it,
  which is what an edit to *that* description can move. Reporting one as the
  other optimises against a number the run is not judged on.
- **`opened nothing at all` is its own bucket.** A large share of it points at
  the agent's setup, not at any description, and the prompt says so. Folded in
  with "opened the wrong skill" it would read as a routing error to fix.
- **`not measured` is not a miss.** A trace that never landed says nothing about
  how the agent routed. `routing_scores` already leaves those out of the score;
  counting them as failures in the prompt would invite an edit against evidence
  that does not exist.
- **A target with no questions still gets a section saying so.** Omitting it
  reads as "this one is fine". It is not fine — it has no evidence at all, and
  it is still rewritable, so it would be edited from the other skills' evidence.

- **A skill whose traces all went missing is not "reached by 0 (0%)".** Every
  percentage is over what was *measured*, never over what was tagged. The two
  differ only when traces went missing, and that is exactly when the difference
  decides whether a description gets rewritten on no evidence.

The budget is `DEFAULT_DIGEST_BUDGET_CHARS`, internal and deliberately **not**
`reflect_budget_chars`: that setting is named for trajectories and measured
against them, and this mode sends none. Over budget, every bucket loses depth
together — the widest cap that fits, from a ladder — rather than the first
buckets rendering whole while the last vanish; each truncated bucket says how
many it dropped. Only when every bucket is down to one question do whole
sections go, with a notice, because an analyst that is never told a group exists
edits that description blind.

Like `truncate_trajectory`'s `min_keep`, it has a floor: a header, one section
and one question is the smallest well-formed digest, and a budget below that is
exceeded rather than met. The floor is a few hundred characters against a
default of 120,000, and at the default a batch of 600 questions across a dozen
skills fits with room to spare.

For the same reason `build_routing_items` skips `build_analyst_items` entirely.
That function shares a character budget between folded trajectories and drops
whole questions when the batch will not fit. Applied here it would leave the
analyst a confusion matrix whose totals described a batch larger than its own
rows: the counts right, the questions missing.

## Stratified batches

`engine.train_batch` orders a routing split by interleaving the skills instead
of shuffling it flat.

A step rewrites every target's description from whatever its batch held, and a
flat shuffle gives skill *i* about `batch_size × nᵢ / n_train` of it. Routing
groups are rarely even — forty questions for one skill and three for another is
ordinary — so the small ones contribute to almost no step and have their
descriptions rewritten anyway, from the evidence of the skills that turned up.

It is a change to the **ordering**, not to the selection, and that is
load-bearing: an epoch covering the split exactly once is a property of
contiguous slices over a permutation. Selecting per step would buy per-skill
coverage by giving that up, and `test_optimizer_engine.py` pins it.

Two details that would otherwise be silent: a question tagged for several skills
is placed once (routing files it under every group it names, and placing it per
group would train and score on it twice), and a question tagged for nothing is
still placed (it cannot be scored, but dropping it would shrink the split
without saying so).

## What was deliberately not done

- **No per-question routing rationale.** The agent's own text before its first
  skill read — "I'll check billing, the question mentions a charge" — is the
  only direct evidence of *which word* pulled it. It is the obvious next thing
  to add and is left out until the digest is shown to need it, because it is the
  one piece that would put routing back in the business of folding trajectories.
- **No scheduler choice.** Both modes are `constant` and the wizard offers no
  control. A decaying edit budget is meaningful for a body, where late steps
  should refine rather than restructure; for a three-sentence parameter an edit
  is a rewrite, and "fewer rewrites later" is not a knob with a meaning.
  `min_learning_rate` is unused on the default path.
- **`reflect_budget_chars` retired from this path**, and hidden in the wizard
  under routing rather than left as a field that appears to bound something it
  no longer touches.
- **Skill bodies still travel in full.** Only the descriptions of *competing*
  skills are sent (`render_competing_skills`), but the skills under optimisation
  go whole. A description is a promise about what the body can deliver, and an
  analyst that cannot see the body writes promises it cannot keep.

## What `isolated` must never notice

Every path above is a `mode == "routing"` branch. `trajectory.shared_preamble`
and `reflection.build_analyst_items` are unmodified — isolated depends on both,
and the tests pinning them pass unchanged. An isolated analyst prompt is
byte-for-byte what it was.

When a change here breaks an isolated test, that is the change being wrong, not
the test being stale.
