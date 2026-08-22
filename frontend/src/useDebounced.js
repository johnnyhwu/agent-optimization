import { useEffect, useState } from "react";

// Waiting for someone to stop typing before asking the server anything.
//
// Two screens do this. The share editor checks a typed username against the
// employee directory; the run-config dialog checks a typed agent URL against the
// agent server. Both fire between keystrokes, both are a network hop away, and
// both were the same fifteen lines of `useEffect` + `setTimeout` + a `cancelled`
// flag — written once, then copied.
//
// **What this deliberately does not do is delay the feedback.** Both call sites
// show something the moment a keystroke lands ("Checking…"), and only the
// request waits. A hook that returned "the value, later" and let the caller
// derive its whole state from that would take the 300ms of feedback with it, so
// the state is still the caller's to set immediately; this only decides when the
// asking happens.

/**
 * A function that runs `delayMs` after the last time it was asked to.
 *
 * The rule lives here rather than inside the hook because `node --test` has no
 * renderer: logic left inside a component is logic that never gets tested, and
 * this one has a cancel path that only ever runs on unmount — precisely the
 * case nobody exercises by hand.
 */
export function debounce(fn, delayMs) {
  let timer = null;
  return {
    run(...args) {
      if (timer !== null) clearTimeout(timer);
      // Deferred even at delayMs = 0: callers mean "as soon as possible", and
      // running inline would re-enter React's render from the keystroke that
      // scheduled it.
      timer = setTimeout(() => {
        timer = null;
        fn(...args);
      }, delayMs);
    },
    cancel() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}

/**
 * `value`, but only after it has stopped changing for `delayMs`.
 *
 * The first value is returned immediately rather than after a delay, which is
 * what lets a dialog check the URL it opened with the moment it opens, and only
 * wait once someone starts editing it.
 */
export function useDebounced(value, delayMs = 300) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const debounced = debounce(setSettled, delayMs);
    debounced.run(value);
    return debounced.cancel;
  }, [value, delayMs]);
  return settled;
}
