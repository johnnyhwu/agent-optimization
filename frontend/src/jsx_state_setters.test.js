import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

// Every `setX(...)` a component calls is a setter something actually declares.
//
// A `useState` pair was renamed — `[skill, setSkill]` became `[skills,
// setSkills]` — and two call sites were left behind. The one inside a `try`
// turned a loaded preview into an error banner reading "setSkill is not
// defined", and skipped the two lines after it; the one inside an effect took
// the page to the error boundary. Neither is subtle in the running app, and
// nothing in this repo could see it: `npm test` runs `src/*.test.js`, so no
// `.jsx` file is imported by any test, and there is no linter to run
// `no-undef`.
//
// This is `css_contract.test.js`'s bargain in another language — no browser, no
// DOM, no rendering, and it fails on the exact class of mistake that is
// invisible in review and invisible until someone clicks the thing.
//
// **A name is bound when it appears somewhere other than in front of a `(`.**
// That is what every real binding looks like: `const [n, setN] = useState()`, a
// destructured prop `function Card({ setOpen })`, `onClick={setOpen}`, an
// import. A name that is only ever called is a name nothing declares — with one
// exception that also stands in front of a `(` and is the strongest binding
// there is: `function setUsername(next)`, whose parentheses open its parameters.

const HERE = dirname(fileURLToPath(import.meta.url));

// `setTimeout` and friends are the platform's, not a component's state.
const GLOBALS = new Set(["setTimeout", "setInterval", "setImmediate"]);

function sources(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.jsx?$/.test(path) && !path.endsWith(".test.js")) out.push(path);
  }
  return out;
}

// Comments and literals go first, or prose naming a setter reads as a binding
// for it — this file's own header would vouch for `setSkill`.
function strip(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1")
    .replace(/`(?:\\.|[^`\\])*`/g, "``")
    .replace(/"(?:\\.|[^"\\\n])*"/g, '""')
    .replace(/'(?:\\.|[^'\\\n])*'/g, "''");
}

// Not preceded by `.` or a word character: `el.setAttribute` is a method call on
// something else, and says nothing about what this file declares.
const SETTER = /(?<![.\w$])set[A-Z][\w$]*/g;

function unbound(code) {
  const called = new Set();
  const bound = new Set();
  for (const match of code.matchAll(SETTER)) {
    const before = code.slice(0, match.index);
    const rest = code.slice(match.index + match[0].length);
    const declared = /\bfunction\s+$/.test(before);
    (!declared && /^\s*\(/.test(rest) ? called : bound).add(match[0]);
  }
  return [...called].filter((name) => !bound.has(name) && !GLOBALS.has(name));
}

test("every state setter a component calls is one something declares", () => {
  const offenders = [];
  for (const path of sources(HERE)) {
    for (const name of unbound(strip(readFileSync(path, "utf8")))) {
      offenders.push(`${relative(HERE, path)}: ${name}`);
    }
  }
  assert.deepEqual(offenders, []);
});
