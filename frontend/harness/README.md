# Screenshot harness

A dev-only scaffold for looking at the optimize wizard without a backend.

There is no test renderer in this repo (see `../CLAUDE.md`), so the way to check
a claim about the wizard's *layout* — as opposed to its rules, which belong in
`src/optimize_*.js` beside a `node --test` file — is to drive the real
components in a real browser. This does that with `src/api.js` swapped for a
fixture, so no database, no agent server and no Docker are needed.

    npx vite --config harness/vite.config.js
    # http://localhost:5199/harness/index.html               the wizard
    # http://localhost:5199/harness/index.html?view=duration a run header's
    #   facts row, with ?secs= and ?w= to pin the elapsed time and the width

`vite.config.js` carries a `resolveId` hook that redirects every import of
`api.js` to `harness/api.js`. Everything else — `Wizard.jsx`, `SplitEditor.jsx`,
`RunDuration.jsx`, `styles.css`, `ui.css` — is the shipped code.

Nothing here is imported by `src/`, and the app's own build never reaches it:
`vite.config.js` at the frontend root builds from `index.html` only.
