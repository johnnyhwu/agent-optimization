import { useEffect, useRef } from "react";

// Taking somebody to the message that explains why the button they pressed did
// nothing.
//
// Both of these dialogs are taller than the window and put their failures at
// the top of the form, while the button that produces them is pinned to the
// bottom. Pressing "Run eval" on a chat endpoint that refuses the call greyed
// the button out and changed nothing else in view: the sentence saying why was
// several hundred pixels above the fold, and nothing said so. The upload dialog
// does the same with its row validation.
//
// The banner sits inside `.dialog-body`, which is the dialog's scroll
// container, so scrolling it into view is the whole mechanic — Modal does not
// have to hand anything out.
//
// The rule is separated from the DOM call for the usual reason: `node --test`
// has no renderer, so a condition left inside the effect is a condition nothing
// ever checks. And this one is easy to get wrong in a way nobody notices —
// scrolling on every render of a message would yank the page while somebody is
// typing in the field that produced it.

/**
 * Is this message worth jumping to, or is it already old news?
 *
 * Keyed on the attempt rather than on the text. A failure is worth revealing
 * once per press of the button:
 *
 *   * before the first press there is nothing to reveal — a dialog that opens
 *     on a warning should not scroll itself the moment it appears;
 *   * the message usually arrives *after* the attempt (the run dialog spends a
 *     model call on the way past), so "the counter went up" cannot be the
 *     trigger on its own — the message has to exist as well;
 *   * a second message within the same attempt does not move the page again:
 *     the reader is already looking at that part of the form.
 */
export function shouldReveal({ attempt, message, revealedFor }) {
  return Boolean(message) && attempt > 0 && attempt !== revealedFor;
}

/**
 * A ref to put on the element that carries `message`.
 *
 * `attempt` is a counter the caller bumps when the user asks for the thing that
 * can fail. Focus moves with the scroll so the message is announced rather than
 * only shown, which needs `tabIndex={-1}` on the element.
 */
export function useRevealedError(message, attempt) {
  const ref = useRef(null);
  const revealedFor = useRef(0);
  useEffect(() => {
    if (!shouldReveal({ attempt, message, revealedFor: revealedFor.current })) return;
    const node = ref.current;
    if (!node) return;
    revealedFor.current = attempt;
    // Smooth, unless the reader has asked for less motion. A scroll started
    // from JavaScript does not pass through the `prefers-reduced-motion` block
    // in styles.css, so the preference has to be read here or this is the one
    // animation in the app that ignores it.
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    node.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    node.focus({ preventScroll: true });
  }, [attempt, message]);
  return ref;
}
