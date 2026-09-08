import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The refresh token is read *after* the access token has been renewed, not
// before.
//
// `api.js` cannot be imported here — it reaches for `window` through
// `app_config.js`, which is why `optimize_stream.test.js` reimplements its
// frame parser rather than importing it. So this reads the source, the way
// `settings_catalog.test.js` and `jsx_state_setters.test.js` do, and checks the
// one property that cannot be seen by reading a diff.
//
// The bug it exists for: `req(..., sessionHeader())` evaluates the header
// before `req` awaits `getAuthHeaders()`, and `getAuthHeaders` calls
// `keycloak.updateToken(30)` — which on a realm with refresh-token rotation
// hands back a *new* refresh token and retires the one just read. The backend
// would then register a token the identity provider had already thrown away,
// and the run would stop at its first question with a message about the
// sign-in ending. The whole path exists to prevent exactly that.
//
// So `sessionHeader` is passed as a function and called inside `req`, after the
// await. Two ways to get that wrong and both look right in review: calling it
// at the call site, or hoisting the read above the await inside `req`.

const HERE = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(HERE, "api.js"), "utf8");

test("sessionHeader is never called at a call site", () => {
  // `sessionHeader()` anywhere outside its own declaration is the bug.
  const calls = [...source.matchAll(/sessionHeader\(\)/g)];
  const declaration = /function sessionHeader\(\)/.test(source) ? 1 : 0;
  assert.equal(
    calls.length,
    declaration,
    "sessionHeader() is being evaluated before req awaits getAuthHeaders(); " +
      "pass `sessionHeader` itself so req calls it after the token may have rotated",
  );
});

test("every call that starts background work still sends the header", () => {
  // The other direction: a rename that quietly stopped forwarding the token
  // would make the test above pass by having nothing to check.
  const passes = [...source.matchAll(/,\s*sessionHeader\)/g)];
  assert.equal(
    passes.length,
    4,
    "expected exactly the four calls that start work outliving their request: " +
      "trigger run, create attempt, create optimization run, resume",
  );
});

test("req resolves the auth headers before it reads anything else", () => {
  const body = source.slice(source.indexOf("async function req("));
  const await_ = body.indexOf("await getAuthHeaders()");
  const extra = body.indexOf("extraHeaders === \"function\"");
  assert.ok(await_ > -1, "req no longer awaits getAuthHeaders");
  assert.ok(
    extra > await_,
    "extraHeaders is resolved before the token may have been renewed",
  );
});
