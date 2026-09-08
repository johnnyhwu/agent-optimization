import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Why Start is disabled is said in exactly one place, and every announcement
// reads that place.
//
// `RunConfigDialog.jsx` cannot be imported here — `node --test` loads no JSX —
// so this reads the source, the way `session_header_contract.test.js` and
// `jsx_state_setters.test.js` do.
//
// The bug it exists for: the dialog has two ways to be unstartable. The
// pre-flight gate (`gateFor`) carries its own `reason`; an ended sign-in does
// not — `blockedReason(session)` is a separate string and `gate.reason` is `""`
// while it is the only thing wrong. `blocked` was widened to include the
// session, but the banner detail and the revealed-error text still read
// `gate.reason` directly, so a session-only block disabled the button and then
// announced nothing: an empty "This run cannot start yet" banner, with the
// actual reason reachable only by hovering the disabled button — which a
// keyboard or screen-reader user never does.
//
// The rule is structural rather than a rendering assertion because that is what
// it is: any new site that reaches past `blockedReasonText` reintroduces the
// same silence, and nothing about it looks wrong in a diff.

const HERE = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  join(HERE, "components", "RunConfigDialog.jsx"),
  "utf8",
);

// Comments explain the rule and must not be mistaken for a use of it.
const code = source
  .split("\n")
  .filter((line) => !line.trim().startsWith("//"))
  .join("\n");

test("gate.reason is read once, to build the one reason text", () => {
  const uses = code.match(/gate\.reason/g) || [];
  assert.equal(
    uses.length,
    1,
    "`gate.reason` is empty for a session-only block; read `blockedReasonText`",
  );
  assert.match(code, /const blockedReasonText = sessionBlocked \|\| gate\.reason;/);
});

test("everything that announces the block reads blockedReasonText", () => {
  // The three: the revealed error, the disabled button's title, the banner
  // detail. Named individually so deleting one is a failure rather than a
  // smaller number.
  assert.match(code, /const failure = error \|\| \(blocked \? blockedReasonText : ""\)/);
  assert.match(code, /title=\{blocked \? blockedReasonText : undefined\}/);
  assert.match(code, /<BannerDetail>\{blockedReasonText\}<\/BannerDetail>/);
});

test("the block itself still counts both ways of being blocked", () => {
  // The guard for the line the other two depend on: if `blocked` stopped
  // including the session, the reason text would be right and never shown.
  assert.match(code, /const blocked = gate\.blocked \|\| Boolean\(sessionBlocked\);/);
});
