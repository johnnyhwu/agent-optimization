// Run with: pnpm test  (node --test)
import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { debounce } from "./useDebounced.js";

// The rule, not the hook: `node --test` has no renderer, and the part worth
// testing is the timing anyway — the hook around it is four lines of useEffect.

test("nothing runs until the delay has passed", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const seen = [];
    const d = debounce((v) => seen.push(v), 300);

    d.run("a");
    mock.timers.tick(299);
    assert.deepEqual(seen, []);

    mock.timers.tick(1);
    assert.deepEqual(seen, ["a"]);
  } finally {
    mock.timers.reset();
  }
});

test("a burst of changes produces one call, with the last value", () => {
  // The whole point on a text field: this fires between keystrokes, and the
  // thing on the other end is a network hop.
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const seen = [];
    const d = debounce((v) => seen.push(v), 300);

    d.run("h");
    mock.timers.tick(100);
    d.run("ht");
    mock.timers.tick(100);
    d.run("http://agent");
    mock.timers.tick(300);

    assert.deepEqual(seen, ["http://agent"]);
  } finally {
    mock.timers.reset();
  }
});

test("cancelling stops a pending call", () => {
  // What the effect's cleanup calls. Without it an unmounted dialog still fires
  // its last request, and a reply arrives for a component that is gone.
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const seen = [];
    const d = debounce((v) => seen.push(v), 300);

    d.run("a");
    d.cancel();
    mock.timers.tick(1000);

    assert.deepEqual(seen, []);
  } finally {
    mock.timers.reset();
  }
});

test("cancelling twice, or before anything was scheduled, is harmless", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const d = debounce(() => {}, 300);
    d.cancel();
    d.run("a");
    d.cancel();
    d.cancel();
    mock.timers.tick(1000);
  } finally {
    mock.timers.reset();
  }
});

test("a zero delay still defers past the current turn", () => {
  // Callers pass 0 to mean "as soon as possible", not "synchronously" — running
  // inline would re-enter React's render on the keystroke that scheduled it.
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const seen = [];
    const d = debounce((v) => seen.push(v), 0);
    d.run("a");
    assert.deepEqual(seen, []);
    mock.timers.tick(0);
    assert.deepEqual(seen, ["a"]);
  } finally {
    mock.timers.reset();
  }
});
