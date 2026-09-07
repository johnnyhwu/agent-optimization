import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Nothing the agent's skills probe sends may come straight from a field.
//
// The probe is a real request to somebody else's agent server, fired from an
// effect. It was debounced on the skills URL alone, while the chat URL, the
// header name and the API key went into the same dependency array raw — so
// typing a twelve-character key sent twelve requests, each carrying a
// different prefix of it. Measured, not deduced: driving the running dialog
// and counting what arrived gave twelve GETs for twelve keystrokes. Against a
// gateway that counts failed authentications, that is not merely chatty.
//
// The fix is one debounced value per input. This test is here because nothing
// else in the repo can see it: `npm test` runs `src/*.test.js`, so no `.jsx`
// file is imported by any test, and a rendering test would not notice a
// request being made four times either.
//
// So it reads the source, the way `jsx_state_setters.test.js` does. The rule:
// in the `api.agentSkills({...})` call, every value must be a bare identifier
// — the name of a debounced constant — and never a `form.x` / `config.x` /
// `secrets.x` member expression, which is a field's live value.

const HERE = dirname(fileURLToPath(import.meta.url));

const SCREENS = [
  "components/RunConfigDialog.jsx",
  "components/optimize/Wizard.jsx",
];

function agentSkillsCall(source) {
  const at = source.indexOf("agentSkills({");
  assert.notEqual(at, -1, "expected an api.agentSkills({...}) call");
  // Balance braces from the opening one so the whole argument is captured
  // however it is formatted.
  let depth = 0;
  const start = source.indexOf("{", at);
  for (let i = start; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error("unbalanced braces in the agentSkills call");
}

for (const screen of SCREENS) {
  test(`${screen} sends the probe only settled values`, () => {
    const call = agentSkillsCall(
      readFileSync(join(HERE, screen), "utf8"),
    );
    // Every `key: value` in the request object, comments and nesting aside.
    const pairs = [...call.matchAll(/^\s*(agent_\w+|secrets):\s*([^,\n]+),?$/gm)];
    assert.ok(pairs.length >= 3, `expected the probe's fields, saw ${pairs.length}`);

    for (const [, key, rawValue] of pairs) {
      const value = rawValue.trim().replace(/,$/, "");
      if (value === "{") continue; // the nested `secrets: {` object
      assert.doesNotMatch(
        value,
        /\b(form|config|secrets)\s*[.?]/,
        `${screen}: ${key} is read straight from the field (${value}). ` +
          "Debounce it — every keystroke would otherwise be its own request " +
          "to the agent server.",
      );
    }
  });

  test(`${screen} debounces every input the probe reads`, () => {
    const source = readFileSync(join(HERE, screen), "utf8");
    const debounced = [...source.matchAll(/const (\w+) = useDebounced\(/g)].map(
      (m) => m[1],
    );
    // The four inputs that change what the probe asks: two URLs, the header
    // name and the key. The chat URL is one of them because it decides whether
    // the credential may travel to the skills endpoint at all.
    assert.ok(
      debounced.length >= 4,
      `${screen}: ${debounced.length} debounced inputs, expected the two URLs, ` +
        `the header name and the key (saw ${debounced.join(", ") || "none"})`,
    );
  });
}
