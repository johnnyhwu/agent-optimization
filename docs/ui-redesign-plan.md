# UI Audit and Redesign Plan

Audit run with the `redesign-existing-projects` skill from
[Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), installed into
`.claude/skills/`. The skill's sibling `design-taste-frontend` was installed too
but explicitly scopes itself out of this codebase ("Not dashboards, not data
tables, not multi-step product UI") — this app is all three, so the redesign
skill's audit-first sequence (Scan → Diagnose → Fix, working inside the existing
stack) is the one that applies.

**Design read:** internal ML-tooling console for the engineer who owns a skill —
dense, numeric, trace-heavy, read for hours at a time. The correct aesthetic
family is Linear / Vercel-dashboard: one accent, tabular numerals, tight
vertical rhythm, motion only where it reports state. The existing token system
in `frontend/src/styles.css` already commits to exactly that. **The problem is
not the design language. The problem is that the Optimize page does not
actually run on it.**

---

## Verdict in one paragraph

`styles.css` (tokens + shell) and `ui.css` (the `ui-*` primitives) are a
genuinely good, well-reasoned design system. Every other section of the product
consumes it correctly. The Optimize section — added later, in one block at
`ui.css:643–887` — was written against a token that does not exist, two class
names that collide with other components, and a Badge tone that was never
implemented. On top of that its live-run screen has a data-flow bug that makes
it show stale numbers for the entire duration of a run. So "many bugs" is right,
and most of them are one-line CSS-contract violations rather than layout taste.

---

## Part 1 — Optimize page: confirmed defects

### 1.1 `--text-sm` does not exist (28 broken rules) — **critical**

`ui.css` uses `font-size: var(--text-sm)` 28 times. All 28 are inside the
Optimize block. The token is never defined anywhere; the scale in
`styles.css:22–28` is `--text-micro / --text-caption / --text-body / --text-ui /
--text-title / --text-section / --text-display`.

Per CSS custom-property semantics, an unresolvable `var()` makes the declaration
*invalid at computed-value time* — the property falls back to its **inherited**
value rather than being ignored. So every one of these elements silently renders
at its parent's size:

| Line | Selector | Intended | Actually renders |
|---|---|---|---|
| 651 | `.opt-runlist-head h3` | 13px | UA `1.17em` ≈ 16px bold |
| 664 | `.opt-runitem-name` | 13px | 14px |
| 669 | `.opt-run-meta` | 13px | 14px |
| 694 | `.opt-step-label` | 13px | 14px |
| 712 | `.opt-qtable` | 13px | 14px |
| 758 | `.opt-steptable` | 13px | 14px |
| 799 | `.opt-stepcard-row` | 13px | 14px |
| 815 | `.opt-group-head` | 13px | 14px |
| 820 | `.opt-qrow` | 13px | 14px |
| 859 | `.opt-difffile` | 13px | 14px |
| … | 18 more | 13px | inherited |

This is the single largest reason the page "looks buggy": the rail heading is
oversized, every table and list row is a size off from its equivalent elsewhere
in the app, and nested cases compound. No other page is affected, so the
Optimize section reads as visually foreign next to Evaluations.

**Fix:** define `--text-sm: var(--text-body)` as an alias in `styles.css`, then
in a follow-up mechanically rewrite the 28 sites to the real token names
(`--text-body` for rows/tables, `--text-caption` for hints) and delete the
alias. Add a CI guard (below) so this class of bug cannot return.

### 1.2 Live run shows stale numbers for the whole run — **critical**

`RunPanel.jsx:104`:

```js
const steps = live.steps.length ? live.steps : run.steps || [];
```

`live.steps` is written **only** by the `snapshot` handler
(`RunPanel.jsx:49`), and the backend emits `snapshot` exactly once, at
connection open (`backend/app/routers/optimization.py:934`). The live handlers
that follow — `step_started`, `gate_done` — write only `live.phase`, never
`live.steps`. The engine's per-step payloads (`rollout_done`, `update_done`,
`gate_done` in `backend/app/optimizer/engine.py`) are never consumed.

Consequences, all user-visible:

- The chart and the step table are frozen at whatever existed when the page
  opened. A run is ~an hour long; only the one-line `live.phase` text moves.
- Worse, after `run_completed` → `reload()` refetches the run with fresh steps,
  but `live.steps.length` is still truthy, so the stale snapshot **still wins**.
  The page never shows the finished run's real steps until a hard reload.

**Fix:** make step state a single reducer keyed by `step_no` — seed from
`snapshot`, upsert on `rollout_done` / `update_done` / `gate_done`, and replace
wholesale from `reload()`. Drop the `live.steps.length ? … : …` fallback
entirely.

### 1.3 `.opt-section` collides with itself — **high**

Two unrelated components claim the same class:

- `OptimizeSection.jsx:49` — the page-level shell, styled at `ui.css:646` as
  `display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 20px`.
- `ReflectorIO.jsx:158` — a small labelled block (`<h5>` + `<pre>`), styled at
  `ui.css:838–839`.

Every ReflectorIO block on the rollout-detail page therefore becomes a 260px +
1fr two-column grid: the `<h5>` label is stranded in a 260px column and the
payload `<pre>` sits beside it, in a pane that is already the narrow half of a
two-column page. **Fix:** rename ReflectorIO's to `.opt-io-block`.

### 1.4 `.opt-groups` / `.opt-group` collide across two pages — **high**

- `ui.css:707` — `.opt-groups { display:flex; flex-direction:column; gap: 16px }`
  for the wizard's skill cards (`SkillGroups.jsx:47`).
- `ui.css:813` — `.opt-groups { display:flex; flex-direction:column }` for the
  rollout list (`RolloutDetail.jsx:158`).

The damage is `ui.css:814` — `.opt-group + .opt-group { border-top: 1px solid
var(--border) }`, written for the rollout's flat list — also landing on the
wizard's `Card`s, drawing a divider between elevated surfaces that already have
a gap between them. Measured in Chromium: the wizard's second card carries
`border-top-width: 1px` before the rename and `0px` after, while the rollout's
own rows keep the `1px` they are supposed to have.

**Correction to an earlier draft of this document:** it claimed the duplicate
`.opt-groups` rule also collapsed the wizard's gap to zero. That was wrong. The
cascade resolves per *declaration*, not per rule, and the second `.opt-groups`
block declares only `display` and `flex-direction` — it never mentions `gap`, so
the first rule's `gap: var(--space-4)` survives untouched. Measured at 16px both
before and after. The collision was real and worth fixing; that particular
mechanism was not.

**Fix:** rename the rollout's set to `.opt-rollout-groups` /
`.opt-rollout-group` / `.opt-rollout-group-head`.

### 1.5 `Badge tone="info"` is not a tone that exists — **high**

`Badge.jsx` implements `neutral | success | danger | warning | accent`;
`ui.css` defines exactly those five `.ui-badge-*` classes. Three call sites ask
for a sixth:

- `SplitEditor.jsx:56` — the **validation count** badge, one of the two headline
  numbers on the split editor
- `StepCard.jsx:38` — "score reused"
- `RolloutDetail.jsx:109` — "skill from step N"

Each renders with base `.ui-badge` geometry and **no tone colours at all** —
transparent, borderless, visibly unfinished right next to a correctly-toned
`accent` badge on the same row. **Fix:** add `.ui-badge-info` to the palette
(this product does have a distinct "informational, not a judgement" meaning, so
adding the tone is more honest than remapping to `neutral`), and document it in
`Badge.jsx`'s tone list.

Related contract drift worth fixing at the same time: `Badge` says `danger`
while `Banner` says `error` for the same idea. Unify on `danger`, keep `error`
as a deprecated alias for one release.

### 1.6 Keyboard focus ring is dead on the step table — **high (a11y)**

`ui.css:763`:

```css
.opt-steptable tbody tr:focus-visible { outline: 2px solid var(--ring); }
```

`--ring` is a **box-shadow** value (`0 0 0 3px var(--accent-soft)`,
`ui.css:23`), not a colour. `outline: 2px solid 0 0 0 3px rgba(...)` is invalid
and dropped. The step table rows are documented in `RunPanel.jsx:241` as "the
keyboard's way to pin a step" — the one non-pointer path into the step detail —
and it has no visible focus at all. Every other focus style in both stylesheets
correctly uses `box-shadow: var(--ring)`; this is the only site that got it
wrong. **Fix:** `box-shadow: inset var(--ring)`.

### 1.7 Selected diff file is white-on-white in light mode — **high**

`ui.css:863`: `.opt-difffile.selected { color: var(--accent-fg); }`.
`--accent-fg` is `#ffffff` — the *foreground for use on the accent fill*. The
file-tree row has no accent background, so in light mode the selected file
becomes white text on a white panel: **invisible**. The user's selection
disappears the moment they make it. **Fix:** `color: var(--accent)` plus
`background: var(--accent-soft)`, matching how `.opt-qrow.selected` and
`.opt-group-head.selected` already do it.

### 1.8 Wizard: stale skill check after going back — **high (logic)**

`Wizard.jsx`. `chooseSkill()` (line 86) resets `split` but **not** `check`.
`loadPreview()` (line 71) resets `skill` and `split` but **not** `check`. And
the guard that triggers a re-check is `if (STEPS[next].id === "target" && !check)`
(line 235) — so once *any* check has run, changing the skill never re-checks.

Result: pick skill A → check runs → go back → pick skill B → the Target step
shows skill A's file list, character count and `has_frontmatter`, and
`blockingReason()` validates skill B against skill A's result. A run can be
started in `routing` mode against a skill that has no frontmatter.

Compounding it, `furthest()` (line 260) returns `5` as soon as `check` is
truthy, so after a back-navigation that cleared `skill`, **every** step is
reachable — and `{step.id === "split" && split && …}` (line 179) renders
**nothing**, giving a blank wizard body with a footer that says "Pick a skill
first."

**Fix:** clear `check` in both `chooseSkill` and `loadPreview`; derive
`furthest` from the actual prerequisite chain rather than a single flag; and
give every step an explicit "you need to go back" state instead of rendering
empty.

### 1.9 Wizard: number fields cannot be cleared — **medium**

`ReviewStep`, lines 538–539 and 570–581:

```js
const epochs = Number(hyper.num_epochs ?? defaults.defaults.num_epochs);
<input type="number" min="1" max="20" value={epochs} onChange={set("num_epochs")} />
```

Clearing the field sets `hyper.num_epochs = ""`; `Number("")` is `0`; the
controlled input snaps to `0`. The user cannot select-all-and-retype — the
standard way anyone edits a number. `min`/`max` are advisory only (no `<form>`
validation runs), so `0` reaches `createOptimizationRun` at line 127, and the
cost estimate above it renders `0 steps`. Same for `batch_size` and
`learning_rate`. **Fix:** hold the raw string in state, coerce only at submit,
and show an inline `Field` error — the redesign skill's "no form validation" and
"no error states" items both land here.

### 1.10 Wizard: "Checking the agent…" forever on check failure — **medium**

`runSkillCheck` (line 104) sets `error` and leaves `check` null on failure.
`blockingReason` (line 280) returns `"Checking the agent…"` for *any* null
`check`. So a failed check shows a permanent, false "in progress" message beside
a disabled Continue button. **Fix:** distinguish `idle | checking | ok | failed`
and surface the failure with a Retry action.

### 1.11 Two different denominators for the same run — **medium**

`RunList.jsx:80` renders `{run.steps_done}/{run.total_steps}`;
`RunPanel.jsx:156` renders `{steps.length}/{run.total_steps + 1}` (baseline
included). Both are on screen simultaneously — the rail says `4/12`, the panel
beside it says `5/13` for the identical run. **Fix:** one helper, baseline
counted once, used by both.

### 1.12 Run rail never refreshes — **medium**

`RunList` fetches once on `[subject]` and has no stream and no revalidation.
Start a run from the wizard, land on the run page, and the rail beside it shows
`pending` for the rest of the hour. **Fix:** lift the run list into a small
shared store that `RunPanel`'s terminal/stream events invalidate.

### 1.13 Smaller items

- `RunPanel.jsx:33` — one `downloading` flag drives both the header's "Download
  best skill" and the pinned card's "Download this skill". Clicking either puts
  **both** buttons into a spinner. Key it by target.
- `RunPanel.jsx:159` — `(run.best_score * 100).toFixed(0)` renders `NaN%` when
  `best_step` is set but `best_score` is null.
- `RunPanel.jsx:156` — `run.total_steps + 1` renders `NaN` while `total_steps`
  is null (pending runs).
- `RunPanel.jsx:105` — `pinned` survives a steps refresh; if the pinned step
  vanishes the card silently disappears with no explanation.
- `RunPanel.jsx:192` — the hard/soft metric toggle is `role="group"` with no
  `aria-pressed`; it is a radio group. Also, the toggle changes the **chart**
  only — `StepTable` (line 275) always renders `*_hard`, so the page shows two
  different metrics at once. Use the existing `.ui-segmented` primitive and make
  the table follow.
- `ProgressChart.jsx:60` — `onClick` on the `<svg>` pins whatever step is
  nearest in x, anywhere on the canvas, including the margins. Hit-test against
  the plot rect.
- `Wizard.jsx:265` — `return sourceIds.length ? 0 : 0;` is dead code.
- `Wizard.jsx:117` — `start()` never resets `starting` on the success path; it
  relies on navigation unmounting the component.

---

## Part 2 — System-wide findings

These apply beyond Optimize and are what the redesign skill's audit surfaces
once the Optimize-specific breakage is set aside.

**What is already right, and should not be touched.** Semantic token scale named
by job; `tabular-nums` on every numeric cell; a real focus-ring token; three
bundled font faces (offline-correct, and a display face used sparingly); one
accent, no AI-purple gradient; `Button`/`Badge`/`Banner`/`Card` primitives with
written rationale; light *and* dark themes plus a `prefers-color-scheme`
fallback. This is well above the baseline the skill is written to catch.

**S1 — No guard on the token contract.** `--text-sm` (28 uses) and `--chrome-h`
(referenced in a comment at `styles.css:436` as a bug that already happened
once) both prove that an undefined token fails silently and ships. This is the
root cause of §1.1, and it will recur. Add a `node --test` check that parses
both stylesheets, collects every `var(--x)` without a fallback, and asserts each
is defined. Roughly 30 lines, runs in the existing `pnpm test`.

**S2 — No guard on the component contract.** `tone="info"` (§1.5) is the same
class of failure one layer up: a prop value with no implementation, rendering an
unstyled element. Have `Badge`/`Banner`/`Button` export their allowed vocabulary
and assert in dev that the class exists; a test asserting `TONES ⊆ CSS classes`
is enough.

**S3 — No CSS namespace discipline.** `.opt-section` and `.opt-groups` each mean
two things (§1.3, §1.4) because the prefix is per-*section*, not per-*component*.
713 one-off `className`s across the app against 12 primitives means this will
happen again. Adopt `.<component>-<element>` (`.optrun-meta`, `.optio-block`)
and add a lint step that fails on a duplicated selector within a file.

**S4 — Hardcoded pixel font-sizes.** 52 in `styles.css`, 4 in `ui.css`, outside
the scale. Sweep them onto tokens; where none fits, the scale is missing a step
and should gain one.

**S5 — The scale has a hole where `--text-sm` was reached for.** 28 authors'
instinct was a size between `--text-caption` (12px) and `--text-ui` (14px), and
`--text-body` (13px) is it but is named for a different job. Rename the scale to
be size-shaped with job-shaped aliases, or add `--text-sm` as a real token. The
28 uses are evidence the vocabulary does not match how people reach for it.

**S6 — Theming completeness.** The `prefers-color-scheme` block
(`styles.css:108–113`) redefines only 7 of the ~15 tokens the explicit
`[data-theme="dark"]` block sets — so a user on system-dark who has never
touched the toggle gets dark surfaces with **light-mode shadows** and light-mode
`--green-soft`/`--red-soft`/`--accent-soft` tints. Make the media block
`@extend`-equivalent to the explicit one (one shared custom-property list,
referenced from both).

**S7 — Motion and reduced motion.** `--ease` exists and transitions are in the
200–300ms band the skill asks for, but there is no
`@media (prefers-reduced-motion: reduce)` block anywhere; `popIn` and `spin`
animate unconditionally.

**S8 — Missing states, per the skill's checklist.** `Skeleton` exists and is
used well on Optimize; audit the other sections for spinner-instead-of-skeleton.
`EmptyState` exists; `RunList`'s empty case (`RunList.jsx:52`) is a bare `<p>No
runs yet.</p>` beside a perfectly good `RunList.Intro` composed empty state.
There is no 404/unknown-route view.

**S9 — Content polish.** The copy in this app is unusually good — plain, active
voice, no exclamation marks, no AI clichés. Two nits the skill flags:
`Wizard.jsx:659` `"question(s)"` should pluralise properly (the pattern is
already done right at `SkillGroups.jsx:148`), and `RunList.jsx:67`'s
`toLocaleString()` fallback name is unreadable at rail width.

---

## Part 3 — The plan

Ordered by the skill's fix-priority rule — highest visual impact, lowest risk,
first — and shaped so each phase is independently shippable and reviewable.

### Phase 0 — Stop the bleeding — **DONE**

1. ✅ Define `--text-sm: var(--text-body)` in `styles.css`. Fixes 28 rules at once.
2. ✅ `.opt-steptable tbody tr:focus-visible` — `outline: 2px solid var(--ring)` → `box-shadow: inset var(--ring)`.
3. ✅ `.opt-difffile.selected` → `color: var(--accent); background: var(--accent-soft)`.
4. ✅ Add `.ui-badge-info` as an alias of `neutral` (not a sixth colour — see §1.5).

### Phase 1 — Kill the collisions — **DONE**

5. ✅ `ReflectorIO`'s `.opt-section` → `.opt-io-block` (§1.3).
6. ✅ `RolloutDetail`'s `.opt-group*` → `.opt-rollout-group*` (§1.4).
7. ✅ Stray `border-top` off the wizard's cards; selected card keeps its elevation.

Verified in headless Chromium against both stylesheets, before and after:

| Measure | Before | After |
|---|---|---|
| `.opt-runlist-head h3` font-size | 14px | 13px |
| `.opt-runitem-name` font-size | 14px | 13px |
| `.opt-steptable` font-size | 14px | 13px |
| `.ui-badge-info` background | `transparent` (untoned) | `#f4f5fa`, matches `neutral` |
| `.opt-difffile.selected` colour | `rgb(255,255,255)` on white | `rgb(99,102,241)` on accent-soft |
| step-row `:focus-visible` shadow | `none` | `inset 0 0 0 3px accent-soft` |
| ReflectorIO block layout | `grid 260px 1000px` | `block` |
| wizard 2nd card `border-top` | `1px` | `0px` |
| rollout 2nd row `border-top` | `1px` | `1px` (unchanged, as intended) |

184 existing tests pass; `vite build` clean.

### Phase 2 — The live-run data flow (the real bug, ~half a day)

8. Replace `RunPanel`'s `live`/`run.steps` fallback with a `stepsById` reducer;
   subscribe to `rollout_done`, `update_done`, `gate_done`; make `reload()`
   authoritative (§1.2).
9. Add tests in `optimize_*.test.js` style for the reducer: snapshot-then-events,
   out-of-order events, terminal-then-reload.
10. Fix `NaN` renders, the shared `downloading` flag, and the orphaned `pinned`
    step (§1.13).

### Phase 3 — Wizard correctness (~half a day)

11. Reset `check` on skill/preview change; rebuild `furthest()` from the real
    prerequisite chain; give every step a non-empty state (§1.8).
12. String-backed number inputs with inline validation on epochs / batch size /
    learning rate (§1.9).
13. Explicit check state machine with a Retry affordance (§1.10).

### Phase 4 — Consistency and a11y sweep (~half a day)

14. One step-count helper for rail and panel (§1.11).
15. Run-list revalidation on run state change (§1.12).
16. Metric toggle → `.ui-segmented`, `aria-pressed`, and make the step table
    follow the selected metric (§1.13).
17. Chart hit-testing confined to the plot rect (§1.13).
18. `RunList` empty case → `EmptyState`.

### Phase 5 — System guards, so none of this recurs (~half a day)

19. Undefined-token test (S1).
20. Tone-vocabulary test; unify `Banner`'s `error` onto `danger` with an alias (S2).
21. Duplicate-selector lint; adopt the component-prefixed naming rule and write
    it into `CLAUDE.md` (S3).

### Phase 6 — Design-system polish (optional, ~1 day)

22. Complete the `prefers-color-scheme` token set (S6).
23. `prefers-reduced-motion` block (S7).
24. Sweep the 56 hardcoded pixel font-sizes onto the scale; decide `--text-sm`'s
    permanent name and delete the Phase-0 alias (S4, S5).
25. 404 route, remaining empty/loading states, copy nits (S8, S9).

### Explicitly out of scope

The redesign skill also recommends breaking symmetry, asymmetric grids,
glassmorphism, grain overlays, parallax, spring physics, and swapping the icon
set. **None of that applies here** and the skill says so itself — its own
Layout section carves out "dense layouts work for data dashboards". This is an
hours-a-day operational console; visual novelty in it is a cost, not a feature.
The plan above is deliberately restorative: make the page obey the design system
it already has, rather than give it a new one.
