# Frontend conventions

React 18 + Vite, no router, no state library, no CSS framework, no chart
library. `pnpm test` is `node --test src/*.test.js` — plain Node, no DOM, no
test renderer.

## Where logic goes

Components render. Anything with a rule in it goes in a pure `src/*.js` module
with a `*.test.js` beside it — `optimize_split.js`, `optimize_chart.js`,
`optimize_cost.js`, `optimize_warnings.js`, `optimize_steps.js`,
`optimize_wizard.js` are all this. The reason is mechanical: `node --test` can
load a pure module and cannot load JSX, so logic left inside a component is
logic that will never be tested.

Do not export a component's internals to make them testable. Extract the rule
instead.

## The two contracts that fail silently

CSS and prop vocabularies both fail without throwing, without a build error, and
without anything visible except the wrong pixels on one screen. Four bugs have
shipped this way (`--chrome-h`, `--text-sm`, `Badge tone="info"`,
`Banner tone="success"`), so both are now enforced by tests.

### Design tokens — `src/css_contract.test.js`

- The two dark blocks — `:root[data-theme="dark"]` and the `prefers-color-scheme`
  one — must be identical in name and value. Plain CSS cannot share a declaration
  list between a selector and a media query, so the test is the only thing
  keeping them equal. The system-dark block once carried 7 of 17 tokens, which
  put near-white code blocks under near-white text for anyone who never opened
  the toggle.
- A font size that has a token in the scale must use the token.
- Every `var(--x)` without a fallback must resolve. An unresolvable `var()` is
  *invalid at computed-value time*: the declaration falls back to the property's
  inherited value rather than being ignored, so the page renders wrong and
  nothing reports it. `--text-sm` was used 28 times and defined nowhere.
- A token must be defined outside `@media`. One defined only inside a
  `prefers-color-scheme` block is undefined for everyone set the other way.
- Two rules must not fight over the same property for the same selector. One
  component's rules may be split across the file by topic — `.dialog-body` does
  this deliberately — but two rules setting the same property means only the
  later one does anything, and if they belong to different components one is
  being laid out by the other's rules.

### Prop vocabularies — `src/ui_vocabulary.test.js`

`Badge` `tone`, `Banner` `tone` and `Button` `variant` are closed vocabularies.
The test checks both directions: every word the component offers must have a
bare `.ui-*-<word>` rule that sets a colour, and no call site may pass a word the
component does not offer.

Adding a tone means adding it in three places — the component's map, the CSS,
and `VOCABULARY` in the test. That is the point; the test exists because those
three drifted.

Dynamic tones are only partly checked. `tone={x ? "a" : "b"}` and
`tone={MAP[k] || "neutral"}` are read; `tone={someFn(x)}` cannot be and is
skipped.

## Naming CSS classes

Prefix by **component**, not by section. `.opt-section` meant both the Optimize
page shell and a ReflectorIO block, so every payload block on the rollout page
was laid out as the page's 260px + 1fr grid. `.opt-groups` meant both the
wizard's skill cards and the rollout's list rows.

`ui-*` is reserved for the primitives in `src/components/ui/`. A class used by
exactly one component should carry that component's name.

## Streams

`api.js#openStream` hands a handler the SSE **frame**. Its `data` is raw JSON
text — call `JSON.parse(e.data)`. Every stream in the app does this; `RunPanel`
once did not, read `e.step_no` off the frame, and displayed the string
"step undefined · undefined" for the length of an hour-long run.

A stream carries a `snapshot` once, at connection open, then live events. Live
events patch; `snapshot` and a refetch replace. Anything the events cannot
reconstruct — a run's final status, its best step, its error message — needs a
refetch, which is also the recovery for a `resync`.

## Verifying UI changes

There is no test renderer and no jsdom. What is available, and has caught real
bugs here:

- `node --test` over pure modules — the default, and where new logic belongs.
- Headless Chromium at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` for
  computed styles. Write the harness HTML to a file and `goto('file://…')`;
  `setContent` uses an `about:blank` base and silently drops `file://`
  stylesheet links, which makes every measurement look like a broken theme.
- Replaying real backend wire bytes through a reimplemented frame parser —
  `optimize_stream.test.js`.

When checking a fix, measure the **before** as well. Two claims in the audit
that produced these changes were wrong until a before/after run contradicted
them.
