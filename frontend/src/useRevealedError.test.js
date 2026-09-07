import test from "node:test";
import assert from "node:assert/strict";
import { shouldReveal } from "./useRevealedError.js";

// The rule, not the hook: `node --test` has no renderer, and the hook around it
// is one effect that calls scrollIntoView.

test("a dialog that opens on a warning does not scroll itself", () => {
  assert.equal(
    shouldReveal({ attempt: 0, message: "This run cannot start yet", revealedFor: 0 }),
    false
  );
});

test("a message that arrives after the button was pressed is revealed", () => {
  // The run dialog spends a model call on the way past, so the attempt is
  // counted before anything is known to be wrong.
  assert.equal(shouldReveal({ attempt: 1, message: "", revealedFor: 0 }), false);
  assert.equal(shouldReveal({ attempt: 1, message: "401", revealedFor: 0 }), true);
});

test("the page does not move twice for one press", () => {
  assert.equal(shouldReveal({ attempt: 1, message: "401", revealedFor: 1 }), false);
  // A second message under the same press is left where it is: the reader is
  // already looking at that part of the form.
  assert.equal(shouldReveal({ attempt: 1, message: "another one", revealedFor: 1 }), false);
});

test("pressing again reveals the failure again", () => {
  assert.equal(shouldReveal({ attempt: 2, message: "401", revealedFor: 1 }), true);
});
